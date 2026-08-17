"""Tests for reading Comtrade data correctly.

Every test runs against recorded responses in tests/fixtures, so the suite
needs no network and cannot break because the UN revised a number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trademirror.codes import WORLD, CodeBook
from trademirror.comtrade import (
    PREVIEW_ROW_CAP,
    Client,
    ComtradeError,
    Query,
    parse_response,
)
from trademirror.records import ALL, ALL_CUSTOMS, AggregationFilter, Flow, TradeFlow

from importlib import resources

FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE = resources.files("trademirror") / "reference"


@pytest.fixture(scope="session")
def uk_exports() -> dict:
    return json.loads((FIXTURES / "uk_exports_2022.json").read_text())


@pytest.fixture(scope="session")
def codes() -> CodeBook:
    return CodeBook.from_files(
        REFERENCE / "partnerAreas.json", REFERENCE / "reporters.json"
    )


UK_QUERY = Query(reporter=826, year=2022, flow=Flow.EXPORT)


# ---- the aggregation trap ----------------------------------------------


def test_unfiltered_sum_is_inflated(uk_exports):
    """The naive read. Documented here because it is the whole problem.

    Comtrade returns the same trade broken down by transport mode and by
    second partner, alongside the row that totals them. Keeping everything
    counts the same goods repeatedly.
    """
    naive = parse_response(uk_exports, UK_QUERY, AggregationFilter.unfiltered())
    correct = parse_response(uk_exports, UK_QUERY)

    naive_total = sum(f.value_usd for f in naive.flows)
    correct_total = sum(f.value_usd for f in correct.flows)

    assert naive_total > correct_total * 4, (
        "expected the unfiltered sum to be several times too large"
    )
    assert correct.rows_kept < naive.rows_kept


def test_default_filter_keeps_only_fully_aggregated_rows(uk_exports):
    rows = uk_exports["data"]
    keep = AggregationFilter.totals_only()
    kept = [r for r in rows if keep.keeps(r)]

    assert kept, "filter removed everything"
    assert all(r["motCode"] == ALL for r in kept)
    assert all(r["partner2Code"] == ALL for r in kept)


def test_each_partner_appears_once_after_filtering(uk_exports):
    """The property that makes a sum safe: one row per partner."""
    response = parse_response(uk_exports, UK_QUERY)
    partners = [f.partner for f in response.flows]
    assert len(partners) == len(set(partners))


def test_filter_can_be_disabled_deliberately(uk_exports):
    response = parse_response(uk_exports, UK_QUERY, AggregationFilter.unfiltered())
    assert response.rows_kept == response.rows_returned


# ---- truncation ---------------------------------------------------------


def test_hitting_the_row_cap_is_reported_as_truncated(uk_exports):
    """The endpoint gives no completeness signal, so this is the only clue."""
    response = parse_response(uk_exports, UK_QUERY)
    assert response.rows_returned == PREVIEW_ROW_CAP
    assert response.truncated
    assert not response.complete


def test_a_short_response_is_not_truncated():
    payload = {"data": [_row(partner=100, value=5.0)]}
    response = parse_response(payload, UK_QUERY)
    assert not response.truncated
    assert response.complete


def test_error_payload_raises():
    with pytest.raises(ComtradeError, match="rate limit"):
        parse_response({"error": "rate limit exceeded"}, UK_QUERY)


def test_rows_without_a_value_are_skipped():
    payload = {"data": [_row(partner=100, value=None), _row(partner=101, value=3.0)]}
    response = parse_response(payload, UK_QUERY)
    assert [f.partner for f in response.flows] == [101]


# ---- parsing ------------------------------------------------------------


def test_flows_carry_the_fields_that_matter(uk_exports):
    response = parse_response(uk_exports, UK_QUERY)
    flow = response.flows[0]
    assert flow.reporter == 826
    assert flow.year == 2022
    assert flow.flow is Flow.EXPORT
    assert flow.value_usd > 0
    assert flow.commodity == "TOTAL"


def test_year_is_an_integer_not_the_string_comtrade_sends(uk_exports):
    """Regression: `period` arrives as "2022", which prints as a number.

    Left alone it compares unequal to 2022, so any grouping or year filter
    quietly matches nothing rather than failing loudly.
    """
    response = parse_response(uk_exports, UK_QUERY)
    assert all(isinstance(f.year, int) for f in response.flows)
    assert all(isinstance(f.reporter, int) for f in response.flows)
    assert all(isinstance(f.partner, int) for f in response.flows)


def test_flow_mirror_direction():
    assert Flow.EXPORT.mirror is Flow.IMPORT
    assert Flow.IMPORT.mirror is Flow.EXPORT


def test_unordered_pair_is_direction_free():
    out = TradeFlow(826, 276, 2022, Flow.EXPORT, 1.0)
    back = TradeFlow(276, 826, 2022, Flow.IMPORT, 1.0)
    assert out.unordered_pair == back.unordered_pair


# ---- reference tables ---------------------------------------------------


def test_codebook_resolves_names(codes):
    assert codes.name(826) == "United Kingdom"
    assert codes.name(276) == "Germany"


def test_world_is_not_a_country(codes):
    assert not codes.is_country(WORLD)


def test_regional_aggregates_are_not_countries(codes):
    """Groups sit in the partner list looking exactly like countries."""
    groups = [a for a in codes.groups if a.code != WORLD]
    assert groups, "expected the reference data to contain groups"
    assert all(not codes.is_country(a.code) for a in groups)


def test_real_countries_are_countries(codes):
    for code in (826, 276, 250, 392, 156):
        assert codes.is_country(code), f"{code} should be a country"


def test_unknown_codes_are_not_countries(codes):
    assert not codes.is_country(999_999)
    assert "unknown" in codes.name(999_999)


# ---- the client ---------------------------------------------------------


def test_client_caches_and_does_not_refetch(tmp_path, uk_exports):
    calls = []

    def fetcher(url):
        calls.append(url)
        return uk_exports

    client = Client(cache_dir=tmp_path, fetcher=fetcher)
    first = client.get(UK_QUERY)
    second = client.get(UK_QUERY)

    assert len(calls) == 1, "second call should have come from cache"
    assert client.fetches == 1
    assert client.cache_hits == 1
    assert first.flows == second.flows


def test_refresh_forces_a_refetch(tmp_path, uk_exports):
    calls = []

    def fetcher(url):
        calls.append(url)
        return uk_exports

    client = Client(cache_dir=tmp_path, fetcher=fetcher)
    client.get(UK_QUERY)
    client.get(UK_QUERY, refresh=True)
    assert len(calls) == 2


def test_query_url_contains_the_query(uk_exports):
    url = Query(reporter=826, year=2022, flow=Flow.IMPORT, partner=276).url()
    assert "reporterCode=826" in url
    assert "period=2022" in url
    assert "flowCode=M" in url
    assert "partnerCode=276" in url


def test_cache_keys_separate_different_queries():
    a = Query(reporter=826, year=2022, flow=Flow.EXPORT).cache_key()
    b = Query(reporter=826, year=2022, flow=Flow.IMPORT).cache_key()
    c = Query(reporter=826, year=2021, flow=Flow.EXPORT).cache_key()
    assert len({a, b, c}) == 3


def _row(*, partner: int, value: float | None) -> dict:
    return {
        "reporterCode": 826,
        "partnerCode": partner,
        "period": 2022,
        "flowCode": "X",
        "cmdCode": "TOTAL",
        "motCode": ALL,
        "partner2Code": ALL,
        "customsCode": ALL_CUSTOMS,
        "primaryValue": value,
    }


def test_customs_procedure_is_the_third_aggregation_level():
    """Regression: filtering transport and second-partner is not enough.

    Comtrade also splits the same trade across customs procedures, C00 is
    every procedure combined, and C01/C03/C04/C06/C07/C20 sum to it. Missing
    this left six rows for a single country pair, and picking among them
    arbitrarily produced a 94% "discrepancy" that was purely an artefact.

    Uses a pairwise response, because an all-partners response happens to
    carry only C00 rows and would not catch this.
    """
    payload = json.loads((FIXTURES / "de_exports_to_uk_2022.json").read_text())
    rows = payload["data"]

    without = [r for r in rows if AggregationFilter(customs_procedure=None).keeps(r)]
    with_it = [r for r in rows if AggregationFilter.totals_only().keeps(r)]

    assert len(without) == 6, "fixture should show the six-row problem"
    assert len(with_it) == 1, "the customs filter should leave exactly one row"
    assert with_it[0]["customsCode"] == ALL_CUSTOMS

    # The breakdowns sum to the total, which is what makes them subtotals.
    total = with_it[0]["primaryValue"]
    parts = sum(r["primaryValue"] for r in without if r["customsCode"] != ALL_CUSTOMS)
    assert parts == pytest.approx(total, rel=1e-6)


def test_mos_code_is_a_string_not_an_int(uk_exports):
    """The trap that made an earlier filter silently drop every row.

    mosCode arrives as "0", so comparing it to 0 is always false. Recorded
    because the same string-for-number habit appears in `period` too.
    """
    values = {r.get("mosCode") for r in uk_exports["data"]}
    assert values == {"0"}
    assert 0 not in values
