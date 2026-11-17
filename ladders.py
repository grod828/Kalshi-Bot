"""Price a whole player yardage ladder from the few lines sharp books quote.

Kalshi lists ~10 rungs per player (10+, 15+, ... 80+ yards); Pinnacle & co.
usually quote one main O/U line (say 62.5), sometimes a couple of alternates.
We fit a distribution to the sharp lines and read every rung off it:

    yards ~ LogNormal(m, s)        P(yards > L) = 1 - Phi((ln L - m) / s)

  >= 2 sharp lines: fit m and s (least squares on ln L = m + s * z, where
                    z = Phi^-1(1 - p_over)), so the sharps set the spread too
     1 sharp line:  s is an ASSUMED per-stat spread (config.LADDER_SIGMA) and
                    the line sets m. Those sigmas are placeholders to calibrate.

Known limits: lognormal has no mass at 0 yards, so it overprices very low
rungs for low-usage players (and rushing yards can go negative). That's one
reason rungs far from any sharp line are not quoted (config.LADDER_MAX_MODEL_SD).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

N = NormalDist()
P_CLAMP = (0.01, 0.99)          # ignore near-certain lines: z blows up
SIGMA_RANGE = (0.15, 2.0)       # reject fitted spreads outside this as junk


@dataclass(frozen=True)
class LadderFit:
    m: float                    # mean of ln(yards)
    s: float                    # sd of ln(yards)
    anchors: tuple[float, ...]  # sharp-quoted lines the fit is based on
    method: str                 # "fit 2+ lines" | "1 line + assumed spread" | ...

    def p_over(self, line: float) -> float:
        if line <= 0:
            return 1.0
        return 1 - N.cdf((math.log(line) - self.m) / self.s)

    def distance_sd(self, line: float) -> float:
        """How far `line` is from the nearest sharp line, in model SDs.
        0 at an anchor; the further out, the more we're trusting the model."""
        return min(abs(math.log(line) - math.log(a)) for a in self.anchors) / self.s


def fit_ladder(points: list[tuple[float, float]], assumed_sigma: float) -> LadderFit | None:
    """points: [(line, sharp no-vig P(over line))]. None if nothing usable."""
    pts = sorted((L, p) for L, p in points if L > 0 and P_CLAMP[0] < p < P_CLAMP[1])
    if not pts:
        return None
    anchors = tuple(L for L, _ in pts)
    if len(pts) >= 2:
        z = [N.inv_cdf(1 - p) for _, p in pts]
        y = [math.log(L) for L, _ in pts]
        zbar, ybar = sum(z) / len(z), sum(y) / len(y)
        var = sum((zi - zbar) ** 2 for zi in z)
        if var > 1e-12:
            s = sum((zi - zbar) * (yi - ybar) for zi, yi in zip(z, y)) / var
            if SIGMA_RANGE[0] <= s <= SIGMA_RANGE[1]:
                return LadderFit(ybar - s * zbar, s, anchors, f"fit {len(pts)} lines")
        method = "1 line + assumed spread (multi-line fit rejected)"
    else:
        method = "1 line + assumed spread"
    # One anchor: use the line closest to 50/50 (the main line, most liquid).
    L0, p0 = min(pts, key=lambda lp: abs(lp[1] - 0.5))
    s = assumed_sigma
    return LadderFit(math.log(L0) - s * N.inv_cdf(1 - p0), s, (L0,), method)
