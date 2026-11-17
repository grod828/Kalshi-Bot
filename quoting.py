"""Quote band computation (beat DK/FD + keep edge) and bankroll sizing."""
from __future__ import annotations

from dataclasses import dataclass

from fees import WORST_CASE, FeeSchedule
from pricing import BookQuote, american_to_prob


@dataclass
class Config:
    bankroll: float = 50_000.0
    reserve_frac: float = 0.20        # never deployed
    event_risk_frac: float = 0.02     # max worst-case loss per event
    lines_per_event_hr: int = 4       # HR prop lines quoted per game
    min_edge: float = 0.02            # keep >= 2c vs sharp fair
    improvement: float = 0.01         # beat DK/FD by >= 1c
    depth_multiple: float = 3.0       # size <= 3x top-of-book depth
    hard_cap_contracts: int = 2000
    # Outlier guards: a huge apparent edge is more often OUR error (stale feed,
    # bad mapping, news we haven't seen) than free money — and since size is
    # budget / price, a cheap outlier would otherwise get MORE contracts.
    max_edge: float = 0.08            # refuse to quote above 8c edge vs fair
    max_sharp_spread: float = 0.04    # refuse if sharps disagree by > 4c
    # Fees: min_edge is enforced NET of Kalshi's fee. Resting quotes are maker
    # orders; `fees` is the fallback when a market's real schedule is unknown.
    fee_role: str = "maker"
    fees: FeeSchedule = WORST_CASE

    @property
    def deployable(self) -> float:
        return self.bankroll * (1 - self.reserve_frac)

    @property
    def event_budget(self) -> float:
        return self.bankroll * self.event_risk_frac

    @property
    def max_concurrent_events(self) -> int:
        return int(self.deployable / self.event_budget)


@dataclass
class Quote:
    side: str            # "yes" or "no"
    price_cents: int
    contracts: int
    edge: float          # expected edge vs fair AFTER fees, in probability points
    reason: str
    fee: float = 0.0     # Kalshi fee per contract at price_cents, in dollars


def retail_best_no_price(dk_fd: list[BookQuote]) -> float | None:
    """Best (lowest) vigged NO price a retail bettor can get at DK/FD."""
    prices = [american_to_prob(q.no_american) for q in dk_fd if q.no_american is not None]
    return min(prices) if prices else None


def retail_best_yes_price(dk_fd: list[BookQuote]) -> float | None:
    prices = [american_to_prob(q.yes_american) for q in dk_fd if q.yes_american is not None]
    return min(prices) if prices else None


def compute_quote(
    fair_p: float,
    dk_fd: list[BookQuote],
    side: str,
    line_budget: float,
    cfg: Config,
    book_depth: int = 500,
    sharp_spread: float | None = None,
    fees: FeeSchedule | None = None,
) -> Quote | None:
    """One-way quote on `side`. Bidding YES at b fills a NO-buyer at (1-b);
    that (1-b) must beat DK/FD's NO price by >= improvement. Symmetric for NO.

    sharp_spread: pricing.sharp_spread() of the books behind fair_p; above
    cfg.max_sharp_spread we don't trust fair_p and don't quote.
    fees: the market's Kalshi fee schedule (None -> cfg.fees, worst case).
    The min_edge floor and the sizing both include the fee.

    Returns None when there's no retail price; otherwise a Quote, with
    contracts=0 and a NO QUOTE reason when it declines."""
    if sharp_spread is not None and sharp_spread > cfg.max_sharp_spread:
        return Quote(side, 0, 0, 0.0,
                     f"NO QUOTE: sharps disagree by {sharp_spread*100:.1f}c "
                     f"(max {cfg.max_sharp_spread*100:.0f}c) — fair not trustworthy")
    if side == "yes":
        retail = retail_best_no_price(dk_fd)
        if retail is None:
            return None
        lo = 1 - retail + cfg.improvement   # counterparty does better than DK/FD
        hi = fair_p - cfg.min_edge          # we keep our edge
    else:
        retail = retail_best_yes_price(dk_fd)
        if retail is None:
            return None
        lo = 1 - retail + cfg.improvement
        hi = (1 - fair_p) - cfg.min_edge

    fees = fees or cfg.fees
    fair_side = fair_p if side == "yes" else 1 - fair_p

    def fee_at(c: int) -> float:
        return fees.per_contract(c / 100, cfg.fee_role)

    lo_c, hi_c = max(1, round(lo * 100)), min(99, int(hi * 100))
    # Edge floor is net of fees: lower the top of the band until it clears.
    while hi_c >= lo_c and fair_side - hi_c / 100 - fee_at(hi_c) < cfg.min_edge - 1e-9:
        hi_c -= 1
    if lo_c > hi_c:
        return Quote(side, 0, 0, 0.0, f"NO QUOTE: band empty (need >= {lo_c}c to beat DK/FD, "
                                      f"but edge floor after fees caps at {hi_c}c)")
    price = lo_c  # bottom of band = max edge; step up later if fills are scarce
    fee = fee_at(price)
    gross_edge = fair_side - price / 100
    edge = gross_edge - fee
    # Outlier check is on the gross edge: it's about our fair disagreeing with price.
    if gross_edge > cfg.max_edge + 1e-9:   # tolerance: 0.37 - 0.29 != 0.08 in floats
        return Quote(side, price, 0, edge,
                     f"NO QUOTE: OUTLIER edge {gross_edge*100:.1f}c > max {cfg.max_edge*100:.0f}c "
                     f"— check feed staleness, mapping, news before trusting it", fee)
    worst_case = price / 100 + fee  # bid price + fee is max loss per contract
    contracts = min(
        int(line_budget / worst_case),
        int(cfg.depth_multiple * book_depth),
        cfg.hard_cap_contracts,
    )
    if contracts < 1:
        return Quote(side, price, 0, edge, "NO QUOTE: budget too small for 1 contract", fee)
    return Quote(side, price, contracts, edge,
                 f"quote {side.upper()} {price}c x{contracts} | edge {edge:.3f} "
                 f"after {fee*100:.2f}c fee | "
                 f"counterparty beats DK/FD ({round(retail*100)}c) at {100-price}c", fee)


def size_ladder(
    ladder_fairs: list[tuple[float, float]],
    dk_fd_by_line: dict[float, list[BookQuote]],
    side: str,
    cfg: Config,
    spread_by_line: dict[float, float | None] | None = None,
    fees: FeeSchedule | None = None,
) -> list[tuple[float, Quote | None]]:
    """Corners ladder: ONE event budget across all rungs (correlated bucket).
    Weight rungs by proximity to 50% fair (most two-way flow), then quote each.
    spread_by_line: per-rung pricing.sharp_spread(), passed to compute_quote."""
    spread_by_line = spread_by_line or {}
    if not ladder_fairs:
        return []
    weights = [max(0.05, 1 - abs(p - 0.5) * 2) for _, p in ladder_fairs]
    total_w = sum(weights)
    out = []
    for (line, fair_p), w in zip(ladder_fairs, weights):
        budget = cfg.event_budget * (w / total_w)
        q = compute_quote(fair_p, dk_fd_by_line.get(line, []), side, budget, cfg,
                          sharp_spread=spread_by_line.get(line), fees=fees)
        out.append((line, q))
    return out
