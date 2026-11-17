"""Kalshi trading fees, so edge and sizing are measured AFTER fees.

Kalshi's fee is quadratic in price, largest at 50c:
    fee = round_up_to_cent(rate * multiplier * contracts * P * (1 - P))
    rate = 0.07 for takers, 0.0175 for makers ON SERIES WITH MAKER FEES.

Each series carries fee_type + fee_multiplier (GET /series/{ticker}); an event
can override both (fee_type_override / fee_multiplier_override). Values seen
on the live API (Nov 2026, ~4,300 series):
    "quadratic"                  -> takers pay, resting (maker) orders pay 0
    "quadratic_with_maker_fees"  -> makers pay too, at the 0.0175 rate
Our series (KXMLBHR, *CORNERS) are all "quadratic" today: resting quotes are
free, but a quote that crosses the book and fills immediately pays taker fees.

The rates come from public fee guides; Kalshi's fee schedule PDF is the source
of truth and they can change per series. When a market's schedule is unknown
(mock data, API failure) we assume the WORST case: maker fees on, multiplier 1.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

TAKER_RATE = 0.07
MAKER_RATE = 0.0175
FEE_TYPES_WITH_MAKER_FEES = {"quadratic_with_maker_fees"}
KNOWN_FEE_TYPES = {"quadratic"} | FEE_TYPES_WITH_MAKER_FEES


@dataclass(frozen=True)
class FeeSchedule:
    fee_type: str = "quadratic_with_maker_fees"
    multiplier: float = 1.0
    source: str = "assumed (worst case)"

    def rate(self, role: str) -> float:
        if role == "taker":
            return TAKER_RATE * self.multiplier
        if role != "maker":
            raise ValueError(f"role must be 'maker' or 'taker', not {role!r}")
        # Unknown fee types are treated as charging makers (conservative).
        if self.fee_type in FEE_TYPES_WITH_MAKER_FEES or self.fee_type not in KNOWN_FEE_TYPES:
            return MAKER_RATE * self.multiplier
        return 0.0

    def per_contract(self, price: float, role: str = "maker") -> float:
        """Unrounded fee per contract in dollars at `price` (0-1). Use for edge
        and sizing math; rounding is applied once per order, not per contract."""
        return self.rate(role) * price * (1 - price)

    def order_fee(self, price: float, contracts: int, role: str = "maker") -> float:
        """Fee Kalshi charges for one fill: rounded UP to the next cent."""
        raw_cents = self.per_contract(price, role) * contracts * 100
        return math.ceil(round(raw_cents, 9)) / 100   # round() strips float fuzz


WORST_CASE = FeeSchedule()


def schedule_from_api(series: dict, event: dict | None = None) -> FeeSchedule:
    """Build from GET /series/{t} 'series' object, applying event overrides."""
    event = event or {}
    ftype = event.get("fee_type_override") or series.get("fee_type")
    mult = event.get("fee_multiplier_override")
    if mult is None:
        mult = series.get("fee_multiplier")
    if not ftype or mult is None:
        return WORST_CASE
    src = "event override" if ("fee_type_override" in event
                               or "fee_multiplier_override" in event) else "series"
    return FeeSchedule(ftype, float(mult), src)
