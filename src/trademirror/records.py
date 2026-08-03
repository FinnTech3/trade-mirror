"""What one row of trade data means, once the noise is stripped out.

Comtrade returns 47 fields per row. Most of them are codes describing *which
slice* of a country's trade the row covers, and only a handful carry the
number you actually want. These types keep the distinction visible, because
confusing a slice for a total is the single easiest way to get an answer that
is wrong by a factor of six.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Flow(Enum):
    """Direction of trade, from the reporting country's point of view."""

    EXPORT = "X"
    IMPORT = "M"

    @property
    def mirror(self) -> Flow:
        """The flow the counterpart country should be reporting."""
        return Flow.IMPORT if self is Flow.EXPORT else Flow.EXPORT


#: Comtrade's code for "all of them, rolled up". It appears in several
#: dimensions and means the same thing in each: this row is the total, not a
#: breakdown. Rows carrying a real code are subsets of a row carrying this one.
ALL = 0

#: The same idea in the customs dimension, where it is spelled as a string.
#: C00 is every customs procedure combined; C01, C03, C04, C06, C07 and C20
#: are procedures that sum to it.
ALL_CUSTOMS = "C00"


@dataclass(frozen=True, slots=True)
class TradeFlow:
    """One country's reported trade with one partner, in one year.

    ``value_usd`` is the reported value in current US dollars. Comtrade calls
    it ``primaryValue``, which is the FOB value for exports and generally the
    CIF value for imports — a difference that matters enormously here and is
    discussed in :mod:`trademirror.mirror`.
    """

    reporter: int
    partner: int
    year: int
    flow: Flow
    value_usd: float
    commodity: str = "TOTAL"

    @property
    def pair(self) -> tuple[int, int]:
        """Reporter and partner, in reporting order."""
        return (self.reporter, self.partner)

    @property
    def unordered_pair(self) -> tuple[int, int]:
        """The country pair with direction removed, for matching mirrors."""
        return tuple(sorted((self.reporter, self.partner)))  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class AggregationFilter:
    """Keeps only the rows that are totals rather than breakdowns.

    This exists because of a trap that costs a factor of about six. A single
    Comtrade response contains, for the same reporter, partner, year and
    commodity, the same trade recorded at three nested aggregation levels:

    - broken down by mode of transport, *and*
    - broken down by second partner, *and*
    - broken down by customs procedure, *and*
    - the rows that total each of those

    Nothing on a row marks it as a subtotal. Sum them all and you count the
    same goods several times over, and the result is large but not so absurd
    that it announces itself.

    The customs dimension is the one most easily missed, because it is the
    only one spelled as a string. Filtering the first two still leaves up to
    six rows per country pair, and picking among them arbitrarily produces
    discrepancies of ninety percent that look like findings.

    The defaults keep only fully aggregated rows. Overriding them is almost
    always a mistake unless you genuinely want a breakdown, in which case you
    should be filtering to *one* specific code rather than keeping everything.
    """

    mode_of_transport: int | None = ALL
    second_partner: int | None = ALL
    customs_procedure: str | None = ALL_CUSTOMS

    def keeps(self, row: dict) -> bool:
        if self.mode_of_transport is not None:
            if row.get("motCode") != self.mode_of_transport:
                return False
        if self.second_partner is not None:
            if row.get("partner2Code") != self.second_partner:
                return False
        if self.customs_procedure is not None:
            if row.get("customsCode") != self.customs_procedure:
                return False
        return True

    @classmethod
    def totals_only(cls) -> AggregationFilter:
        return cls()

    @classmethod
    def unfiltered(cls) -> AggregationFilter:
        """Every row, subtotals included. Sums computed from this double count."""
        return cls(
            mode_of_transport=None, second_partner=None, customs_procedure=None
        )
