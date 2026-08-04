"""Breaking a country pair's mirror gap down by what was actually traded.

The aggregate gap says the Netherlands reports sending far more than its
partners report receiving. It does not say what. That matters, because the two
explanations look identical in the totals and completely different here:

- If goods are **transiting** — landing at Rotterdam, clearing customs and
  moving on under a Dutch export declaration while the buyer records the
  country the goods were made in — the gap should sit in the commodities a
  port handles for other people. Crude oil. Coffee. Bananas.
- If the reporting were simply **sloppy**, the gap would be spread more or less
  evenly across everything, because clerical error does not care what is in
  the container.

One request returns all 97 HS chapters for a country pair, so testing this
costs no more than the aggregate did.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .comtrade import Client, Query
from .mirror import DEFAULT_CIF_FOB_RATIO, MirrorPair
from .records import Flow

#: Comtrade's code for every commodity at the two-digit chapter level.
ALL_CHAPTERS = "AG2"


def load_chapter_names(path: Path | str) -> dict[str, str]:
    """Read the HS reference, tolerating its byte-order mark.

    The file Comtrade serves begins with a UTF-8 BOM, which makes a plain
    ``json.loads`` fail outright with a decode error rather than anything that
    hints at the cause. Read as ``utf-8-sig`` and it is ordinary JSON.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    payload = json.loads(text)
    names: dict[str, str] = {}
    for row in payload.get("results", []):
        code = str(row.get("id", ""))
        if len(code) != 2 or not code.isdigit():
            continue
        label = str(row.get("text", code))
        # Entries arrive as "27 - Mineral fuels, ..." — drop the repeated code.
        if label.startswith(f"{code} - "):
            label = label[len(code) + 3:]
        names[code] = label
    return names


@dataclass(frozen=True, slots=True)
class ChapterGap:
    """One HS chapter of one country pair, as both sides reported it."""

    chapter: str
    exporter_reported: float
    importer_reported: float

    def adjusted_gap_pct(
        self, cif_fob: float = DEFAULT_CIF_FOB_RATIO
    ) -> float | None:
        expected = self.exporter_reported * cif_fob
        if expected == 0:
            return None
        return (self.importer_reported - expected) / expected

    def name(self, names: dict[str, str]) -> str:
        return names.get(self.chapter, f"HS{self.chapter}")


def collect_chapters(
    client: Client,
    exporter: int,
    importer: int,
    year: int,
    *,
    minimum_value: float = 2e8,
) -> list[ChapterGap]:
    """Both sides of one country pair, split by HS chapter.

    Chapters below ``minimum_value`` on the exporter's side are dropped. Small
    chapters produce enormous percentage gaps from rounding and a single
    reclassified shipment, and letting them into a ranking fills the top of it
    with noise.
    """
    sent = client.get(
        Query(reporter=exporter, year=year, flow=Flow.EXPORT,
              commodity=ALL_CHAPTERS, partner=importer)
    )
    received = client.get(
        Query(reporter=importer, year=year, flow=Flow.IMPORT,
              commodity=ALL_CHAPTERS, partner=exporter)
    )

    by_exporter = {f.commodity: f.value_usd for f in sent.flows}
    by_importer = {f.commodity: f.value_usd for f in received.flows}

    gaps = []
    for chapter in sorted(by_exporter.keys() & by_importer.keys()):
        value = by_exporter[chapter]
        if value < minimum_value:
            continue
        gaps.append(
            ChapterGap(
                chapter=chapter,
                exporter_reported=value,
                importer_reported=by_importer[chapter],
            )
        )
    return gaps


def concentration(
    gaps: list[ChapterGap], chapter: str
) -> tuple[float, float, float]:
    """How much one chapter is doing to a pair's overall ratio.

    Returns the exporter-side share that chapter represents, the ratio with it,
    and the ratio without. A gap that survives its removal is spread; a gap
    that collapses was that chapter all along.
    """
    total_sent = sum(g.exporter_reported for g in gaps)
    total_got = sum(g.importer_reported for g in gaps)
    rest = [g for g in gaps if g.chapter != chapter]
    rest_sent = sum(g.exporter_reported for g in rest)
    rest_got = sum(g.importer_reported for g in rest)

    share = 0.0
    for gap in gaps:
        if gap.chapter == chapter and total_sent:
            share = gap.exporter_reported / total_sent

    with_it = total_got / total_sent if total_sent else 0.0
    without = rest_got / rest_sent if rest_sent else 0.0
    return share, with_it, without
