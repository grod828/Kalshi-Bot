"""Devig, consensus fair value, and ladder smoothing."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


def american_to_prob(odds: int) -> float:
    """American odds -> implied probability (with vig)."""
    if odds < 0:
        return -odds / (-odds + 100)
    return 100 / (odds + 100)


def devig_multiplicative(p_yes: float, p_no: float) -> float:
    """Normalize the two-sided overround away. Returns fair P(yes)."""
    return p_yes / (p_yes + p_no)


def devig_power(p_yes: float, p_no: float, tol: float = 1e-9) -> float:
    """Power devig: find k such that p_yes^k + p_no^k = 1.
    Penalizes longshot bias less crudely than multiplicative."""
    lo, hi = 0.5, 10.0
    for _ in range(200):
        k = (lo + hi) / 2
        s = p_yes**k + p_no**k
        if abs(s - 1) < tol:
            break
        if s > 1:
            lo = k
        else:
            hi = k
    return p_yes**k


@dataclass
class BookQuote:
    book: str
    yes_american: int
    no_american: int
    ts: float = field(default_factory=time.time)

    def fair_yes(self, method: str = "multiplicative") -> float:
        py, pn = american_to_prob(self.yes_american), american_to_prob(self.no_american)
        if method == "power":
            return devig_power(py, pn)
        return devig_multiplicative(py, pn)


DEFAULT_WEIGHTS = {"pinnacle": 0.50, "circa": 0.30, "bookmaker": 0.20}


def consensus_fair(
    quotes: list[BookQuote],
    weights: dict[str, float] | None = None,
    max_age_s: float = 120.0,
    method: str = "multiplicative",
    now: float | None = None,
) -> float | None:
    """Weighted no-vig consensus P(yes). None if no fresh sharp quotes."""
    weights = weights or DEFAULT_WEIGHTS
    now = now if now is not None else time.time()
    num = den = 0.0
    for q in quotes:
        w = weights.get(q.book.lower())
        if w is None or (now - q.ts) > max_age_s:
            continue
        num += w * q.fair_yes(method)
        den += w
    return num / den if den > 0 else None


def isotonic_decreasing(probs: list[float]) -> list[float]:
    """Pool-adjacent-violators: enforce p[0] >= p[1] >= ... (over-ladder).
    Used to smooth crossed corner-ladder rungs."""
    # PAV on the negated sequence for increasing, then negate back.
    vals = [-p for p in probs]
    weights = [1.0] * len(vals)
    blocks = [[v, w, [i]] for i, (v, w) in enumerate(zip(vals, weights))]
    merged = []
    for b in blocks:
        merged.append(b)
        while len(merged) > 1 and merged[-2][0] > merged[-1][0]:
            v2, w2, i2 = merged.pop()
            v1, w1, i1 = merged.pop()
            w = w1 + w2
            merged.append([(v1 * w1 + v2 * w2) / w, w, i1 + i2])
    out = [0.0] * len(probs)
    for v, _, idxs in merged:
        for i in idxs:
            out[i] = -v
    return out


def ladder_fair(
    rungs: list[tuple[float, list[BookQuote]]],
    crossed_flag_threshold: float = 0.03,
    **kw,
) -> tuple[list[tuple[float, float]], bool]:
    """rungs: [(line, quotes)] e.g. [(8.5, [...]), (9.5, [...])], ascending lines,
    all quoted as OVER (yes = over). Returns ([(line, fair_p_over)], flagged).
    flagged=True means a rung violated monotonicity badly (stale line -> don't trade)."""
    lines, raw = [], []
    for line, quotes in sorted(rungs, key=lambda r: r[0]):
        p = consensus_fair(quotes, **kw)
        if p is None:
            continue
        lines.append(line)
        raw.append(p)
    smooth = isotonic_decreasing(raw)
    flagged = any(abs(a - b) > crossed_flag_threshold for a, b in zip(raw, smooth))
    return list(zip(lines, smooth)), flagged
