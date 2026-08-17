"""Generates the figures in docs/figures from the cached Comtrade responses.

The numbers are read from the cache rather than typed in, so a figure cannot
drift away from the analysis that produced it. Run after changing anything that
affects the results:

    python3 scripts/make_figures.py

Emits a light and a dark variant of each chart. GitHub picks between them with
<picture>; a website can use either.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trademirror.codes import CodeBook  # noqa: E402
from trademirror.collect import DEFAULT_COUNTRIES, collect_pairs  # noqa: E402
from trademirror.comtrade import Client  # noqa: E402
from trademirror.mirror import summarise  # noqa: E402

OUT = ROOT / "docs" / "figures"
NETHERLANDS = 528


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    text_primary: str
    text_secondary: str
    muted: str
    gridline: str
    baseline: str
    negative: str
    positive: str
    band: str


LIGHT = Theme(
    name="light",
    surface="#fcfcfb",
    text_primary="#0b0b0b",
    text_secondary="#52514e",
    muted="#898781",
    gridline="#e1e0d9",
    baseline="#c3c2b7",
    negative="#e34948",
    positive="#2a78d6",
    band="#e1e0d9",
)

DARK = Theme(
    name="dark",
    surface="#1a1a19",
    text_primary="#ffffff",
    text_secondary="#c3c2b7",
    muted="#898781",
    gridline="#2c2c2a",
    baseline="#383835",
    negative="#e66767",
    positive="#3987e5",
    band="#2c2c2a",
)

# Single quotes around the family name on purpose: this string goes into a
# double-quoted SVG attribute, and the usual "Segoe UI" spelling closes the
# attribute early and produces an XML parse error rather than a bad-looking
# chart. The file still writes, and only rendering it reveals the damage.
FONT = "system-ui,-apple-system,'Segoe UI',sans-serif"


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def gap_chart(rows: list[tuple[str, float, int]], theme: Theme) -> str:
    """Diverging bars: mean freight-adjusted gap per exporting country."""
    width, row_h, top, bottom = 760, 34, 66, 52
    label_w, right_pad = 150, 74
    height = top + len(rows) * row_h + bottom
    plot_w = width - label_w - right_pad

    lo = min(v for _, v, _ in rows) - 0.04
    hi = max(v for _, v, _ in rows) + 0.05
    span = hi - lo

    def x(value: float) -> float:
        return label_w + (value - lo) / span * plot_w

    zero = x(0.0)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        f'font-family="{FONT}" role="img" '
        f'aria-label="Mean freight-adjusted trade gap by exporting country">',
        f'<rect width="{width}" height="{height}" fill="{theme.surface}"/>',
        f'<text x="24" y="30" font-size="15" font-weight="600" '
        f'fill="{theme.text_primary}">Mean freight-adjusted gap, by exporting '
        f'country</text>',
        f'<text x="24" y="50" font-size="12" fill="{theme.text_secondary}">'
        f'Negative means the exporter reports sending more than the buyer '
        f'reports receiving</text>',
    ]

    # Gridlines every 10 percentage points, recessive.
    tick = -0.30
    while tick <= hi:
        if lo <= tick <= hi and abs(tick) > 1e-9:
            gx = x(tick)
            parts.append(
                f'<line x1="{gx:.1f}" y1="{top - 8}" x2="{gx:.1f}" '
                f'y2="{top + len(rows) * row_h}" stroke="{theme.gridline}" '
                f'stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{gx:.1f}" y="{top + len(rows) * row_h + 18}" '
                f'font-size="11" fill="{theme.muted}" text-anchor="middle">'
                f'{tick * 100:.0f}%</text>'
            )
        tick += 0.10

    bar_h = 18
    for index, (name, value, count) in enumerate(rows):
        cy = top + index * row_h
        centre = cy + row_h / 2 - bar_h / 2
        colour = theme.negative if value < 0 else theme.positive
        bx = min(zero, x(value))
        bw = max(abs(x(value) - zero), 2.0)

        parts.append(
            f'<text x="{label_w - 14}" y="{centre + bar_h / 2 + 4:.1f}" '
            f'font-size="12.5" fill="{theme.text_primary}" text-anchor="end">'
            f'{esc(name)}</text>'
        )
        parts.append(
            f'<rect x="{bx:.1f}" y="{centre:.1f}" width="{bw:.1f}" '
            f'height="{bar_h}" rx="4" fill="{colour}"/>'
        )
        # Square off the end that meets the zero baseline.
        patch_x = zero - 4 if value < 0 else zero
        parts.append(
            f'<rect x="{patch_x:.1f}" y="{centre:.1f}" width="4" '
            f'height="{bar_h}" fill="{colour}"/>'
        )
        label_x = bx - 8 if value < 0 else bx + bw + 8
        anchor = "end" if value < 0 else "start"
        parts.append(
            f'<text x="{label_x:.1f}" y="{centre + bar_h / 2 + 4:.1f}" '
            f'font-size="12" fill="{theme.text_primary}" text-anchor="{anchor}">'
            f'{value * 100:+.1f}%</text>'
        )
        parts.append(
            f'<text x="{width - 22}" y="{centre + bar_h / 2 + 4:.1f}" '
            f'font-size="11" fill="{theme.muted}" text-anchor="end">'
            f'n={count}</text>'
        )

    parts.append(
        f'<line x1="{zero:.1f}" y1="{top - 8}" x2="{zero:.1f}" '
        f'y2="{top + len(rows) * row_h}" stroke="{theme.baseline}" '
        f'stroke-width="2"/>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def ratio_chart(all_pairs: float, without_nl: float, theme: Theme) -> str:
    """Where the implied freight wedge lands, against what is possible."""
    width, height = 620, 238
    left, right = 34, 34
    plot_w = width - left - right
    lo, hi = 0.962, 1.118
    axis_y, track_h = 152, 44

    def x(value: float) -> float:
        return left + (value - lo) / (hi - lo) * plot_w

    top = axis_y - track_h / 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        f'font-family="{FONT}" role="img" '
        f'aria-label="Implied CIF over FOB ratio, with and without the '
        f'Netherlands">',
        f'<rect width="{width}" height="{height}" fill="{theme.surface}"/>',
        f'<text x="{left}" y="30" font-size="15" font-weight="600" '
        f'fill="{theme.text_primary}">Implied CIF/FOB ratio</text>',
        f'<text x="{left}" y="50" font-size="12" fill="{theme.text_secondary}">'
        f'Freight can only push this above 1.0, below it is not possible</text>',
        # The region no honest figure can reach.
        f'<rect x="{left}" y="{top:.1f}" width="{x(1.0) - left:.1f}" '
        f'height="{track_h}" fill="{theme.negative}" opacity="0.10"/>',
        f'<text x="{(left + x(1.0)) / 2:.1f}" y="{top - 10:.1f}" '
        f'font-size="11" fill="{theme.muted}" text-anchor="middle">'
        f'impossible</text>',
        # What theory expects.
        f'<rect x="{x(1.05):.1f}" y="{top:.1f}" '
        f'width="{x(1.10) - x(1.05):.1f}" height="{track_h}" '
        f'fill="{theme.positive}" opacity="0.13"/>',
        f'<text x="{(x(1.05) + x(1.10)) / 2:.1f}" y="{top - 10:.1f}" '
        f'font-size="11" fill="{theme.muted}" text-anchor="middle">'
        f'expected 1.05 to 1.10</text>',
        f'<line x1="{x(1.0):.1f}" y1="{top:.1f}" x2="{x(1.0):.1f}" '
        f'y2="{top + track_h:.1f}" stroke="{theme.baseline}" '
        f'stroke-width="2" stroke-dasharray="4 3"/>',
        f'<text x="{x(1.0):.1f}" y="{top + track_h + 16:.1f}" font-size="11" '
        f'fill="{theme.muted}" text-anchor="middle">1.00</text>',
    ]

    # Markers sit on the same track, separated vertically so their labels
    # cannot collide even when the two values land close together.
    for value, label, colour, cy, label_above in (
        (all_pairs, "all pairs", theme.negative, top + 13, True),
        (without_nl, "excluding the Netherlands", theme.positive,
         top + track_h - 13, False),
    ):
        cx = x(value)
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="7" fill="{colour}" '
            f'stroke="{theme.surface}" stroke-width="2"/>'
        )
        if label_above:
            value_y, label_y = top - 30, top - 45
        else:
            value_y, label_y = top + track_h + 36, top + track_h + 52
        parts.append(
            f'<text x="{cx:.1f}" y="{value_y:.1f}" font-size="14" '
            f'font-weight="600" fill="{theme.text_primary}" '
            f'text-anchor="middle">{value:.4f}</text>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{label_y:.1f}" font-size="11.5" '
            f'fill="{theme.text_secondary}" text-anchor="middle">'
            f'{esc(label)}</text>'
        )
        # Leader from the number down (or up) to its marker.
        y1 = value_y + 6 if label_above else value_y - 20
        y2 = cy - 9 if label_above else cy + 9
        parts.append(
            f'<line x1="{cx:.1f}" y1="{y1:.1f}" x2="{cx:.1f}" '
            f'y2="{y2:.1f}" stroke="{colour}" stroke-width="1.5"/>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def offline(url: str) -> dict:
    raise RuntimeError(
        "figure generation reads the cache only; run the CLI once to populate it"
    )


def main() -> int:
    client = Client(cache_dir=ROOT / "data" / "cache", fetcher=offline)
    report = collect_pairs(client, year=2022, countries=DEFAULT_COUNTRIES)
    if not report.pairs:
        print("no cached pairs found", file=sys.stderr)
        return 1

    codes = CodeBook.from_files(
        ROOT / "src" / "trademirror" / "reference" / "partnerAreas.json",
        ROOT / "src" / "trademirror" / "reference" / "reporters.json",
    )

    by_exporter: dict[int, list[float]] = {}
    for pair in report.pairs:
        gap = pair.adjusted_gap_pct()
        if gap is not None:
            by_exporter.setdefault(pair.exporter, []).append(gap)

    rows = sorted(
        (
            (codes.name(code), sum(gaps) / len(gaps), len(gaps))
            for code, gaps in by_exporter.items()
        ),
        key=lambda row: row[1],
    )

    all_ratio = summarise(report.pairs).implied_cif_fob_ratio
    trimmed = [
        p for p in report.pairs
        if NETHERLANDS not in (p.exporter, p.importer)
    ]
    trimmed_ratio = summarise(trimmed).implied_cif_fob_ratio

    OUT.mkdir(parents=True, exist_ok=True)
    for theme in (LIGHT, DARK):
        (OUT / f"gaps-{theme.name}.svg").write_text(gap_chart(rows, theme))
        (OUT / f"ratio-{theme.name}.svg").write_text(
            ratio_chart(all_ratio, trimmed_ratio, theme)
        )

    print(f"wrote 4 figures to {OUT.relative_to(ROOT)}")
    print(f"  {len(rows)} exporters, {len(report.pairs)} pairs")
    print(f"  ratio all={all_ratio:.4f} excl-NL={trimmed_ratio:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
