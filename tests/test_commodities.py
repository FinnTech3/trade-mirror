"""Tests for the commodity breakdown.

Offline against recorded responses, like the rest of the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trademirror.commodities import (
    ALL_CHAPTERS,
    ChapterGap,
    collect_chapters,
    concentration,
    load_chapter_names,
    verify_chapter,
)
from trademirror.comtrade import Client

FIXTURES = Path(__file__).parent / "fixtures"

NETHERLANDS, GERMANY = 528, 276


@pytest.fixture(scope="session")
def names() -> dict[str, str]:
    return load_chapter_names(FIXTURES / "hs_chapters.json")


@pytest.fixture
def client(tmp_path) -> Client:
    """Serves the two recorded commodity responses, and nothing else."""
    responses = {
        (NETHERLANDS, "X"): json.loads(
            (FIXTURES / "nl_exports_to_de_ag2.json").read_text()
        ),
        (GERMANY, "M"): json.loads(
            (FIXTURES / "de_imports_from_nl_ag2.json").read_text()
        ),
    }

    def fetcher(url: str) -> dict:
        reporter = int(url.split("reporterCode=")[1].split("&")[0])
        flow = url.split("flowCode=")[1].split("&")[0]
        return responses[(reporter, flow)]

    return Client(cache_dir=tmp_path, fetcher=fetcher)


@pytest.fixture
def chapter_client(tmp_path) -> Client:
    """Serves the single-chapter verification responses."""
    responses = {
        (NETHERLANDS, "X"): json.loads(
            (FIXTURES / "nl_exports_to_de_hs27.json").read_text()
        ),
        (GERMANY, "M"): json.loads(
            (FIXTURES / "de_imports_from_nl_hs27.json").read_text()
        ),
    }

    def fetcher(url: str) -> dict:
        reporter = int(url.split("reporterCode=")[1].split("&")[0])
        flow = url.split("flowCode=")[1].split("&")[0]
        return responses[(reporter, flow)]

    return Client(cache_dir=tmp_path, fetcher=fetcher)


# ---- the reference file -------------------------------------------------


def test_chapter_names_load_despite_the_byte_order_mark(names):
    """Regression: the HS reference begins with a UTF-8 BOM.

    A plain json.loads fails on it with a decode error that says nothing about
    the cause. Reading as utf-8-sig makes it ordinary JSON.
    """
    assert len(names) > 90
    assert names["27"].startswith("Mineral fuels")


def test_plain_utf8_read_would_have_failed():
    """Shows the trap is real rather than theoretical."""
    raw = (FIXTURES / "hs_chapters.json").read_text(encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)


def test_chapter_names_drop_the_repeated_code(names):
    """Entries arrive as "08 - Fruit and nuts" and should not keep the code."""
    assert not names["08"].startswith("08")
    assert "Fruit" in names["08"]


def test_only_two_digit_chapters_are_kept(names):
    assert all(len(code) == 2 and code.isdigit() for code in names)


# ---- collection ---------------------------------------------------------


def test_collects_chapters_both_sides_reported(client):
    gaps = collect_chapters(client, NETHERLANDS, GERMANY, 2022)
    assert gaps
    chapters = [g.chapter for g in gaps]
    assert len(chapters) == len(set(chapters)), "a chapter appeared twice"
    assert "27" in chapters


def test_uses_the_all_chapters_code(client):
    collect_chapters(client, NETHERLANDS, GERMANY, 2022)
    # One request per side, both asking for every chapter.
    assert client.fetches == 2
    assert ALL_CHAPTERS == "AG2"


def test_small_chapters_are_dropped(client):
    """Tiny chapters produce enormous percentages from rounding alone."""
    everything = collect_chapters(client, NETHERLANDS, GERMANY, 2022,
                                  minimum_value=0)
    filtered = collect_chapters(client, NETHERLANDS, GERMANY, 2022,
                                minimum_value=2e8)
    assert len(filtered) < len(everything)
    assert all(g.exporter_reported >= 2e8 for g in filtered)


# ---- the finding --------------------------------------------------------


def test_the_breakdown_knows_it_is_incomplete(client):
    """Regression: shares were once quoted from a truncated subset.

    Asking for every chapter can hit the 500-row cap — breakdown rows fill it
    long before the chapter list runs out — and the response looks exactly
    like a complete one. A first version of this analysis reported mineral
    fuels as 58% of Dutch exports to Germany. It was 58% of the 32 chapters
    that survived truncation on the German side; against the real total it is
    32%. Nothing errored, and the number looked entirely reasonable.
    """
    breakdown = collect_chapters(client, NETHERLANDS, GERMANY, 2022)
    assert breakdown.importer_truncated, "the German side hit the row cap"
    assert not breakdown.complete, (
        "a truncated breakdown must not report itself as complete"
    )


def test_a_single_chapter_query_comes_back_whole(chapter_client):
    """How a headline number gets confirmed: ask for one chapter, not all.

    Sweep to discover, verify to quote.
    """
    confirmed = verify_chapter(chapter_client, NETHERLANDS, GERMANY, 2022, "27")
    assert confirmed is not None
    assert confirmed.exporter_reported == pytest.approx(60_308_142_050, rel=1e-6)
    assert confirmed.importer_reported == pytest.approx(24_567_683_524, rel=1e-6)
    # A third of the trade, and the gap is enormous.
    assert confirmed.adjusted_gap_pct() < -0.5


def test_the_worst_chapters_are_things_a_port_handles(client, names):
    """Fruit, coffee, oilseeds, fuels — none of them grown or drilled locally."""
    gaps = collect_chapters(client, NETHERLANDS, GERMANY, 2022)
    ranked = sorted(gaps, key=lambda g: g.adjusted_gap_pct() or 0)
    worst = {g.chapter for g in ranked[:5]}
    assert {"27", "08", "09"} <= worst


# ---- arithmetic ---------------------------------------------------------


def test_adjusted_gap_removes_the_freight_wedge():
    gap = ChapterGap("99", exporter_reported=100.0, importer_reported=108.0)
    assert gap.adjusted_gap_pct(1.08) == pytest.approx(0.0)


def test_adjusted_gap_is_negative_when_less_arrives():
    gap = ChapterGap("99", exporter_reported=100.0, importer_reported=54.0)
    assert gap.adjusted_gap_pct(1.08) == pytest.approx(-0.5)


def test_adjusted_gap_is_none_without_an_exporter_figure():
    assert ChapterGap("99", 0.0, 10.0).adjusted_gap_pct() is None


def test_concentration_of_an_absent_chapter_changes_nothing():
    gaps = [ChapterGap("01", 100.0, 100.0), ChapterGap("02", 100.0, 100.0)]
    share, with_it, without = concentration(gaps, "99")
    assert share == 0.0
    assert with_it == pytest.approx(without)


def test_name_falls_back_to_the_code(names):
    # Chapters run 01 to 97 plus 99 ("commodities not specified according to
    # kind"), so 00 is the code that genuinely has no entry.
    assert "00" not in names
    assert ChapterGap("00", 1.0, 1.0).name(names) == "HS00"
