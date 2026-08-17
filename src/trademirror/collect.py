"""Fetching both sides of a trade relationship.

The preview endpoint caps every response at 500 rows and gives no sign when
it has truncated. Asking for one reporter's trade with *all* partners returns
an arbitrary slice of them, the breakdown rows eat the budget long before the
partner list is exhausted, so the two halves of a mirror almost never arrive
in the same pair of responses.

The way around it is to name both countries in every query. That costs two
requests per ordered pair and makes the whole exercise quadratic in the number
of countries studied, which is why the country set here is small and chosen
rather than exhaustive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations

from .comtrade import Client, ComtradeError, Query, require_single_flow
from .mirror import MirrorPair
from .records import Flow

#: A deliberately small set of large traders. Every pair costs two requests
#: against a free public endpoint, so this grows as n^2 and is kept modest.
DEFAULT_COUNTRIES: tuple[int, ...] = (
    826,  # United Kingdom
    276,  # Germany
    842,  # United States
    156,  # China
    392,  # Japan
    250,  # France
    380,  # Italy
    528,  # Netherlands
)


@dataclass
class CollectionReport:
    pairs: list[MirrorPair] = field(default_factory=list)
    #: Pairs where one side reported and the other did not, or could not be read.
    unmatched: list[tuple[int, int, str]] = field(default_factory=list)
    requests: int = 0
    cache_hits: int = 0

    @property
    def coverage(self) -> float:
        attempted = len(self.pairs) + len(self.unmatched)
        return len(self.pairs) / attempted if attempted else 0.0


def collect_pairs(
    client: Client,
    year: int,
    countries: tuple[int, ...] = DEFAULT_COUNTRIES,
    commodity: str = "TOTAL",
) -> CollectionReport:
    """Fetch both sides of every ordered pair among ``countries``.

    A pair is only kept when both countries reported it. A missing side is
    recorded rather than filled with a zero: absence nearly always means that
    country did not report, and treating it as "reported nothing" would invent
    a 100% discrepancy out of a gap in the data.
    """
    report = CollectionReport()

    for exporter, importer in permutations(countries, 2):
        export_query = Query(
            reporter=exporter, year=year, flow=Flow.EXPORT,
            commodity=commodity, partner=importer,
        )
        import_query = Query(
            reporter=importer, year=year, flow=Flow.IMPORT,
            commodity=commodity, partner=exporter,
        )

        try:
            sent = require_single_flow(client.get(export_query), export_query)
            received = require_single_flow(client.get(import_query), import_query)
        except (ComtradeError, OSError) as problem:
            report.unmatched.append((exporter, importer, str(problem)))
            continue

        report.pairs.append(
            MirrorPair(
                exporter=exporter,
                importer=importer,
                year=year,
                exporter_reported=sent.value_usd,
                importer_reported=received.value_usd,
                commodity=commodity,
            )
        )

    report.requests = client.fetches
    report.cache_hits = client.cache_hits
    return report
