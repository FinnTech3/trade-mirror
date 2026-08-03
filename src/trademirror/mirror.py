"""Comparing what one country says it sent with what the other says it got.

Every trade between two countries gets reported twice — once by the exporter,
once by the importer. The two numbers never agree. The difference is called
the mirror gap, and it is one of the few places in economics where you get an
error term you can actually look at.

Before reading anything into a gap, one adjustment is mandatory.

**Exports are FOB, imports are CIF.** An exporter reports goods valued at its
own border, free on board. The importer reports the same goods valued on
arrival, including cost, insurance and freight. So the importer's figure is
*expected* to exceed the exporter's, by roughly the cost of shipping, before
any misreporting enters the picture at all. Historically that wedge runs
around 6 to 10 percent of value, wider for bulky goods and long routes.

Skip this adjustment and every pair on earth looks like it is under-reporting
exports, because you have measured freight and called it fraud. What is worth
investigating is the residual once the expected wedge is taken out — and
especially gaps that run the *wrong* way, where the exporter reports more than
the importer received.
"""

from __future__ import annotations

from dataclasses import dataclass

from .codes import CodeBook
from .records import Flow, TradeFlow

#: Typical CIF-over-FOB wedge. A rough global average standing in for freight
#: and insurance; the real figure varies by route and by commodity, which is a
#: known weakness of doing it this way.
DEFAULT_CIF_FOB_RATIO = 1.08


@dataclass(frozen=True, slots=True)
class MirrorPair:
    """One direction of trade, as reported by both sides."""

    exporter: int
    importer: int
    year: int
    #: What the exporter says it sent (FOB).
    exporter_reported: float
    #: What the importer says it received (CIF).
    importer_reported: float
    commodity: str = "TOTAL"

    @property
    def raw_gap(self) -> float:
        """Importer's figure minus exporter's, before any adjustment.

        Almost always positive, and mostly freight. Not evidence of anything
        on its own.
        """
        return self.importer_reported - self.exporter_reported

    def expected_importer_value(self, cif_fob: float = DEFAULT_CIF_FOB_RATIO) -> float:
        return self.exporter_reported * cif_fob

    def adjusted_gap(self, cif_fob: float = DEFAULT_CIF_FOB_RATIO) -> float:
        """Gap remaining once the expected freight wedge is removed.

        Positive: the importer recorded more than shipping alone explains.
        Negative: the exporter claims to have sent more than arrived.
        """
        return self.importer_reported - self.expected_importer_value(cif_fob)

    def adjusted_gap_pct(
        self, cif_fob: float = DEFAULT_CIF_FOB_RATIO
    ) -> float | None:
        """Adjusted gap as a share of the expected value. None if unmeasurable."""
        expected = self.expected_importer_value(cif_fob)
        if expected == 0:
            return None
        return self.adjusted_gap(cif_fob) / expected

    @property
    def both_sides_reported(self) -> bool:
        return self.exporter_reported > 0 and self.importer_reported > 0

    def describe(self, codes: CodeBook) -> str:
        return (
            f"{codes.name(self.exporter)} -> {codes.name(self.importer)} "
            f"({self.year})"
        )


def pair_flows(
    flows: list[TradeFlow],
    codes: CodeBook | None = None,
    *,
    drop_groups: bool = True,
    drop_self_trade: bool = True,
) -> list[MirrorPair]:
    """Match each export flow with the import flow that should mirror it.

    Only pairs where both sides reported are returned. A flow with no
    counterpart is not evidence of a zero on the other side — far more often
    it means that country did not report that year at all, and silently
    treating absence as zero would manufacture a 100% gap.
    """
    exports: dict[tuple[int, int, int, str], float] = {}
    imports: dict[tuple[int, int, int, str], float] = {}

    for flow in flows:
        if drop_self_trade and flow.reporter == flow.partner:
            continue
        if drop_groups and codes is not None:
            if not codes.is_country(flow.reporter):
                continue
            if not codes.is_country(flow.partner):
                continue

        if flow.flow is Flow.EXPORT:
            # reporter exported to partner
            key = (flow.reporter, flow.partner, flow.year, flow.commodity)
            exports[key] = exports.get(key, 0.0) + flow.value_usd
        else:
            # reporter imported from partner, so partner is the exporter
            key = (flow.partner, flow.reporter, flow.year, flow.commodity)
            imports[key] = imports.get(key, 0.0) + flow.value_usd

    pairs = []
    for key in exports.keys() & imports.keys():
        exporter, importer, year, commodity = key
        pairs.append(
            MirrorPair(
                exporter=exporter,
                importer=importer,
                year=year,
                exporter_reported=exports[key],
                importer_reported=imports[key],
                commodity=commodity,
            )
        )

    pairs.sort(key=lambda p: (-abs(p.raw_gap), p.exporter, p.importer))
    return pairs


@dataclass(frozen=True, slots=True)
class GapSummary:
    pairs_compared: int
    total_exporter_reported: float
    total_importer_reported: float
    median_adjusted_gap_pct: float | None
    exporter_overstates: int
    importer_overstates: int

    @property
    def implied_cif_fob_ratio(self) -> float | None:
        """The wedge the data itself implies, across all pairs.

        Worth comparing against the assumed ratio. If they are far apart, the
        assumption is doing more work than it should be.
        """
        if self.total_exporter_reported == 0:
            return None
        return self.total_importer_reported / self.total_exporter_reported


def summarise(
    pairs: list[MirrorPair], cif_fob: float = DEFAULT_CIF_FOB_RATIO
) -> GapSummary:
    usable = [p for p in pairs if p.both_sides_reported]
    gaps = [
        pct
        for p in usable
        if (pct := p.adjusted_gap_pct(cif_fob)) is not None
    ]
    gaps.sort()
    median = None
    if gaps:
        mid = len(gaps) // 2
        median = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2

    return GapSummary(
        pairs_compared=len(usable),
        total_exporter_reported=sum(p.exporter_reported for p in usable),
        total_importer_reported=sum(p.importer_reported for p in usable),
        median_adjusted_gap_pct=median,
        exporter_overstates=sum(1 for p in usable if p.adjusted_gap(cif_fob) < 0),
        importer_overstates=sum(1 for p in usable if p.adjusted_gap(cif_fob) > 0),
    )
