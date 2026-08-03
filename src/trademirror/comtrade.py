"""Reading trade data out of UN Comtrade.

Uses the public preview endpoint, which needs no API key. That comes with a
hard cap of 500 rows per response and no warning when you hit it — the
response looks exactly like a complete one. :func:`parse_response` therefore
reports truncation rather than letting a partial answer pass as a whole one.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .records import AggregationFilter, Flow, TradeFlow

PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

#: The preview endpoint returns at most this many rows and does not say so.
PREVIEW_ROW_CAP = 500


class ComtradeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Query:
    reporter: int
    year: int
    flow: Flow
    commodity: str = "TOTAL"
    partner: int | None = None

    def url(self) -> str:
        params = {
            "reporterCode": str(self.reporter),
            "period": str(self.year),
            "flowCode": self.flow.value,
            "cmdCode": self.commodity,
        }
        if self.partner is not None:
            params["partnerCode"] = str(self.partner)
        return f"{PREVIEW_URL}?{urllib.parse.urlencode(params)}"

    def cache_key(self) -> str:
        partner = "all" if self.partner is None else str(self.partner)
        return (
            f"{self.reporter}_{self.year}_{self.flow.value}_"
            f"{self.commodity}_{partner}.json"
        )


@dataclass(frozen=True, slots=True)
class Response:
    """Parsed rows, plus whether we can trust them to be complete."""

    flows: tuple[TradeFlow, ...]
    rows_returned: int
    rows_kept: int
    truncated: bool

    @property
    def complete(self) -> bool:
        return not self.truncated


def parse_response(
    payload: dict,
    query: Query,
    aggregation: AggregationFilter | None = None,
) -> Response:
    """Turn a raw Comtrade payload into flows, dropping subtotal rows."""
    if payload.get("error"):
        raise ComtradeError(str(payload["error"]))

    rows = payload.get("data") or []
    keep = aggregation if aggregation is not None else AggregationFilter.totals_only()

    flows = []
    for row in rows:
        if not keep.keeps(row):
            continue
        value = row.get("primaryValue")
        if value is None:
            continue
        flows.append(
            TradeFlow(
                # Types are coerced here rather than trusted. Comtrade sends
                # `period` as a string that looks like a number, so a year
                # read straight from the payload compares unequal to the int
                # it appears to be — and does so silently, only surfacing much
                # later as a grouping that mysteriously finds no matches.
                reporter=int(row["reporterCode"]),
                partner=int(row["partnerCode"]),
                year=int(row["period"]),
                flow=Flow(row["flowCode"]),
                value_usd=float(value),
                commodity=str(row.get("cmdCode", query.commodity)),
            )
        )

    return Response(
        flows=tuple(flows),
        rows_returned=len(rows),
        rows_kept=len(flows),
        # The endpoint gives no completeness signal, so hitting the cap exactly
        # is the only evidence available that rows were dropped.
        truncated=len(rows) >= PREVIEW_ROW_CAP,
    )


def require_single_flow(response: Response, query: Query) -> TradeFlow:
    """Pull the one flow a pairwise query should have produced.

    A query naming both reporter and partner describes exactly one number, so
    anything else means the response cannot be trusted. The dangerous case is
    a truncated response that happens to contain no aggregate row: the payload
    looks ordinary, the filter legitimately keeps nothing, and a caller
    reading `flows[0]` would either crash or — worse, if it defaulted — record
    a zero and later read that zero as a country reporting no trade at all.
    """
    if len(response.flows) == 1:
        return response.flows[0]

    if not response.flows:
        detail = (
            "no aggregate row survived filtering"
            + (" and the response was truncated at the row cap" if response.truncated
               else "")
        )
    else:
        detail = f"expected one flow, got {len(response.flows)}"

    raise ComtradeError(
        f"{detail} for reporter={query.reporter} partner={query.partner} "
        f"year={query.year} flow={query.flow.value} "
        f"({response.rows_returned} rows returned)"
    )


Fetcher = Callable[[str], dict]


def http_fetch(url: str, timeout: float = 60.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as handle:
        return json.loads(handle.read().decode("utf-8"))


class Client:
    """Fetches queries, caching every response to disk.

    The cache is not an optimisation. Analysis gets re-run constantly while
    the questions change, and going back to the network each time is slow,
    rude to a free public service, and makes results non-reproducible when the
    upstream data is revised. Cached responses make a run repeatable.
    """

    def __init__(
        self,
        cache_dir: Path | str = "data/cache",
        fetcher: Fetcher = http_fetch,
        aggregation: AggregationFilter | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.fetcher = fetcher
        self.aggregation = aggregation or AggregationFilter.totals_only()
        self.fetches = 0
        self.cache_hits = 0

    def raw(self, query: Query, *, refresh: bool = False) -> dict:
        path = self.cache_dir / query.cache_key()
        if path.exists() and not refresh:
            self.cache_hits += 1
            return json.loads(path.read_text())

        payload = self.fetcher(query.url())
        self.fetches += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
        return payload

    def get(self, query: Query, *, refresh: bool = False) -> Response:
        return parse_response(self.raw(query, refresh=refresh), query, self.aggregation)

    def get_many(self, queries: Iterable[Query]) -> list[Response]:
        return [self.get(query) for query in queries]
