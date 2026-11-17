"""Kalshi <-> vendor market mapping: normalization, fuzzy match, manual overrides.

The #1 silent-loss source in cross-book bots is a bad mapping (wrong player,
wrong game, wrong line). Rules here:
  - exact key match after normalization -> auto-accept
  - fuzzy match >= FUZZY_THRESHOLD      -> accept but log for review
  - below threshold                     -> UNMATCHED; never trade unmatched
  - overrides.csv always wins           -> your manual truth table
"""
from __future__ import annotations

import csv
import os
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

import config

SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv)\b\.?", re.I)

TEAM_ALIASES = {  # extend as mismatches appear in review logs
    "ny yankees": "yankees", "new york yankees": "yankees",
    "ny mets": "mets", "new york mets": "mets",
    "la dodgers": "dodgers", "los angeles dodgers": "dodgers",
    "sf giants": "giants", "san francisco giants": "giants",
    "arsenal fc": "arsenal", "chelsea fc": "chelsea",
    "man city": "manchester city", "man united": "manchester united",
}


def norm(s: str) -> str:
    """Lowercase, strip accents/punctuation/suffixes: 'Ronald Acuña Jr.' -> 'ronald acuna'."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = SUFFIXES.sub("", s.lower())
    s = re.sub(r"[^a-z0-9. ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return TEAM_ALIASES.get(s, s)


@dataclass
class MarketKey:
    """Canonical identity of a line, independent of platform naming."""
    market_type: str      # "mlb_hr" | "corners"
    entity: str           # normalized player name | "home_vs_away" teams string
    line: float | None    # corners line (e.g. 9.5); None for HR yes/no
    game_date: str        # YYYY-MM-DD

    def key(self) -> str:
        ln = f"|{self.line}" if self.line is not None else ""
        return f"{self.market_type}|{norm(self.entity)}{ln}|{self.game_date}"


@dataclass
class Mapping:
    kalshi_ticker: str
    vendor_id: str
    score: float          # 1.0 = exact/override
    status: str           # "exact" | "fuzzy" | "override" | "unmatched"


def load_overrides(path: str = "") -> dict[str, str]:
    """overrides.csv: kalshi_ticker,vendor_id  — manual truth, always wins."""
    path = path or config.OVERRIDES_CSV
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {r["kalshi_ticker"]: r["vendor_id"] for r in csv.DictReader(f)}


def match_markets(
    kalshi: dict[str, MarketKey],       # ticker -> key
    vendor: dict[str, MarketKey],       # vendor_id -> key
    overrides: dict[str, str] | None = None,
    threshold: float = config.FUZZY_THRESHOLD,
) -> list[Mapping]:
    overrides = overrides if overrides is not None else load_overrides()
    vendor_by_key = {mk.key(): vid for vid, mk in vendor.items()}
    out = []
    for ticker, kmk in kalshi.items():
        if ticker in overrides:
            out.append(Mapping(ticker, overrides[ticker], 1.0, "override"))
            continue
        vid = vendor_by_key.get(kmk.key())
        if vid:
            out.append(Mapping(ticker, vid, 1.0, "exact"))
            continue
        # fuzzy: same type/date/line, best entity similarity
        best_vid, best = "", 0.0
        for vid, vmk in vendor.items():
            if (vmk.market_type, vmk.game_date, vmk.line) != (kmk.market_type, kmk.game_date, kmk.line):
                continue
            r = SequenceMatcher(None, norm(kmk.entity), norm(vmk.entity)).ratio()
            if r > best:
                best, best_vid = r, vid
        if best >= threshold:
            out.append(Mapping(ticker, best_vid, best, "fuzzy"))
        else:
            out.append(Mapping(ticker, best_vid, best, "unmatched"))
    return out


def report(mappings: list[Mapping], max_listed: int = 25) -> str:
    by = lambda s: [m for m in mappings if m.status == s]
    lines = [f"mapped: {len(by('exact'))} exact, {len(by('override'))} override, "
             f"{len(by('fuzzy'))} fuzzy (REVIEW), {len(by('unmatched'))} unmatched (NO TRADE)"]
    for m in by("fuzzy"):
        lines.append(f"  REVIEW  {m.kalshi_ticker} -> {m.vendor_id}  ({m.score:.2f})")
    unmatched = by("unmatched")
    for m in unmatched[:max_listed]:
        lines.append(f"  UNMATCHED  {m.kalshi_ticker}  (best {m.vendor_id or '-'} {m.score:.2f})"
                     f"  -> add to overrides.csv if it's real")
    if len(unmatched) > max_listed:
        lines.append(f"  ... and {len(unmatched) - max_listed} more unmatched")
    return "\n".join(lines)
