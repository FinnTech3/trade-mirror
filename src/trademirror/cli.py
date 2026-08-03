"""Command line entry point."""

from __future__ import annotations

import argparse
import collections
import sys
import time
import urllib.error
from pathlib import Path

from .codes import CodeBook
from .collect import DEFAULT_COUNTRIES, collect_pairs
from .comtrade import Client, http_fetch
from .mirror import DEFAULT_CIF_FOB_RATIO, summarise

REFERENCE = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def polite_fetch(url: str) -> dict:
    """Fetch with backoff. The public endpoint rate-limits, and says so."""
    for attempt in range(5):
        try:
            time.sleep(1.2)
            return http_fetch(url, timeout=45)
        except urllib.error.HTTPError as problem:
            if problem.code != 429 or attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _load(args) -> tuple[list, CodeBook]:
    fetcher = _offline if args.offline else polite_fetch
    client = Client(cache_dir=args.cache, fetcher=fetcher)
    report = collect_pairs(client, year=args.year, countries=DEFAULT_COUNTRIES)
    codes = CodeBook.from_files(
        REFERENCE / "partnerAreas.json", REFERENCE / "reporters.json"
    )
    if not report.pairs:
        raise SystemExit(
            "no pairs collected. Run without --offline once to populate the cache."
        )
    print(
        f"{len(report.pairs)} of {len(report.pairs) + len(report.unmatched)} "
        f"country pairs had both sides reporting "
        f"({report.coverage:.0%} coverage, {report.requests} requests, "
        f"{report.cache_hits} from cache)\n"
    )
    return report.pairs, codes


def _offline(url: str) -> dict:
    raise RuntimeError("offline mode: response not in cache")


def cmd_gaps(args) -> int:
    pairs, codes = _load(args)
    ratio = args.cif_fob

    print(f"{'exporter':<16}{'importer':<16}{'reported out':>15}"
          f"{'reported in':>15}{'adj gap':>10}")
    print("-" * 72)
    ranked = sorted(pairs, key=lambda p: abs(p.adjusted_gap_pct(ratio) or 0),
                    reverse=True)
    for p in ranked[: args.top]:
        print(
            f"{codes.name(p.exporter)[:15]:<16}{codes.name(p.importer)[:15]:<16}"
            f"{p.exporter_reported / 1e9:>13,.1f}bn"
            f"{p.importer_reported / 1e9:>13,.1f}bn"
            f"{p.adjusted_gap_pct(ratio):>+10.1%}"
        )

    summary = summarise(pairs, ratio)
    print("-" * 72)
    print(f"\nimplied CIF/FOB ratio across all pairs: "
          f"{summary.implied_cif_fob_ratio:.4f}")
    print(f"median freight-adjusted gap:            "
          f"{summary.median_adjusted_gap_pct:+.2%}")
    print(f"importer reports more: {summary.importer_overstates}   "
          f"exporter reports more: {summary.exporter_overstates}")
    return 0


def cmd_transit(args) -> int:
    """Isolate the countries whose reported exports nobody else records."""
    pairs, codes = _load(args)
    ratio = args.cif_fob

    by_exporter: dict[int, list[float]] = collections.defaultdict(list)
    for pair in pairs:
        gap = pair.adjusted_gap_pct(ratio)
        if gap is not None:
            by_exporter[pair.exporter].append(gap)

    print(f"{'exporter':<24}{'mean adj gap':>14}{'pairs':>8}")
    print("-" * 46)
    for code, gaps in sorted(by_exporter.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
        print(f"{codes.name(code)[:23]:<24}{sum(gaps) / len(gaps):>+13.1%}{len(gaps):>8}")

    worst = min(by_exporter.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))[0]
    without = [p for p in pairs if worst not in (p.exporter, p.importer)]

    whole = summarise(pairs, ratio).implied_cif_fob_ratio
    trimmed = summarise(without, ratio).implied_cif_fob_ratio
    print(f"\nimplied CIF/FOB ratio, all pairs:            {whole:.4f}")
    print(f"implied CIF/FOB ratio, excluding {codes.name(worst)[:14]:<14} {trimmed:.4f}")
    print(
        f"\nFreight and insurance mean an importer should report roughly 5-10%\n"
        f"more than the exporter. Across everything the figure comes out below\n"
        f"1.0, which is impossible on those grounds alone. Dropping one country\n"
        f"restores it. That country is not mis-reporting: goods pass through its\n"
        f"ports and leave as its exports, while the buyer records the country the\n"
        f"goods actually came from."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trademirror",
        description="Compare what exporters report sending with what importers "
                    "report receiving.",
    )
    parser.add_argument("--year", type=int, default=2022)
    parser.add_argument("--cache", default="data/cache")
    parser.add_argument("--cif-fob", type=float, default=DEFAULT_CIF_FOB_RATIO)
    parser.add_argument("--offline", action="store_true",
                        help="use only cached responses, never the network")
    sub = parser.add_subparsers(dest="command", required=True)

    gaps = sub.add_parser("gaps", help="rank country pairs by discrepancy")
    gaps.add_argument("--top", type=int, default=15)
    gaps.set_defaults(func=cmd_gaps)

    transit = sub.add_parser(
        "transit", help="find the countries whose exports nobody records receiving"
    )
    transit.set_defaults(func=cmd_transit)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
