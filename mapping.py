"""Kalshi <-> vendor market mapping: normalization, fuzzy match, manual overrides.

The #1 silent-loss source in cross-book bots is a bad mapping (wrong player,
wrong game, wrong line). Rules here:
  - exact key match after normalization -> auto-accept
  - fuzzy match >= FUZZY_THRESHOLD      -> accept but log for review
  - below threshold                     -> UNMATCHED; never trade unmatched
  - overrides.csv always wins           -> your manual truth table
  - ambiguous keys (two markets look identical, e.g. a doubleheader)
                                        -> UNMATCHED; resolve with overrides.csv
Matchups ("Man City vs Fulham") are compared side by side: each team is
normalized and aliased (team_aliases.py), and home/away order is ignored.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import config
from team_aliases import TEAM_ALIASES

SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv)\b\.?", re.I)
MATCHUP_SEP = re.compile(r"\s+(?:vs\.?|v\.?|@)\s+", re.I)
# Generic club-name tokens dropped before aliasing ("AS Roma" -> "roma",
# "1. FC Köln" -> "koln"). Only tokens that never distinguish two clubs.
CLUB_TOKENS = {
    "fc", "afc", "cf", "cfc", "sc", "ac", "acf", "ssc", "as", "ss", "us", "calcio",
    "fsv", "vfb", "vfl", "tsg", "sv", "bv", "rc", "rcd", "ca", "ud", "cd", "sd",
    "ogc", "losc", "aj", "sco", "bc", "sk", "fk",
}


def norm(s: str) -> str:
    """Lowercase, strip accents/punctuation/suffixes: 'Ronald Acuña Jr.' -> 'ronald acuna'.
    Whole-string team aliases still apply (single-team entities)."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = SUFFIXES.sub("", s.lower())
    s = re.sub(r"[^a-z0-9. ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return TEAM_ALIASES.get(s, s)


def norm_team(s: str) -> str:
    """One team name -> canonical: 'Borussia Mönchengladbach' and 'M´gladbach'
    -> 'monchengladbach'; '1. FSV Mainz 05' -> 'mainz'; 'AS Roma' -> 'roma'."""
    n = norm(s)
    tokens = [t.strip(".") for t in n.split()]
    kept = [t for t in tokens if t and t not in CLUB_TOKENS and not t.isdigit()]
    n = " ".join(kept) or " ".join(t for t in tokens if t)  # never empty a name
    return TEAM_ALIASES.get(n, n)


def norm_entity(s: str) -> str:
    """Matchups are normalized per team and sorted, so 'Man City vs Fulham',
    'Fulham @ Manchester City FC' and 'Manchester City v Fulham' all agree.
    Anything else (a player name) goes through norm()."""
    parts = MATCHUP_SEP.split(s.strip())
    if len(parts) == 2 and all(p.strip() for p in parts):
        return " vs ".join(sorted(norm_team(p) for p in parts))
    return norm(s)


def _eastern_offset_h(utc: dt.datetime) -> int:
    """US Eastern UTC offset without tz data (Windows Python ships none):
    EDT (-4) from 2nd Sunday of March 07:00 UTC to 1st Sunday of Nov 06:00 UTC."""
    y = utc.year
    mar1, nov1 = dt.datetime(y, 3, 1), dt.datetime(y, 11, 1)
    start = mar1 + dt.timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=7)
    end = nov1 + dt.timedelta(days=(6 - nov1.weekday()) % 7, hours=6)
    return -4 if start <= utc.replace(tzinfo=None) < end else -5


def kalshi_game_date(start: dt.datetime | str) -> str:
    """Game start (aware datetime or ISO string, e.g. a vendor's UTC kickoff)
    -> the date Kalshi files it under. Kalshi dates games in US Eastern time:
    KXMLBHR-26OCT282000MILTB is Oct 28 8:00 PM EDT although it's Oct 29 in UTC.
    Vendor adapters must set MarketKey.game_date with this, not the UTC date."""
    if isinstance(start, str):
        start = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
    if start.tzinfo is None:
        raise ValueError("start time must be timezone-aware (e.g. ...Z or +00:00)")
    utc = start.astimezone(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return utc.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:   # no tz database (stock Windows Python): fixed US DST rule
        return (utc + dt.timedelta(hours=_eastern_offset_h(utc))).date().isoformat()


@dataclass
class MarketKey:
    """Canonical identity of a line, independent of platform naming."""
    market_type: str      # "mlb_hr" | "corners"
    entity: str           # normalized player name | "home_vs_away" teams string
    line: float | None    # corners line (e.g. 9.5); None for HR yes/no
    game_date: str        # YYYY-MM-DD, US Eastern (see kalshi_game_date)
    event_id: str = field(default="", compare=False)  # game id; not part of key()

    def key(self) -> str:
        ln = f"|{self.line}" if self.line is not None else ""
        return f"{self.market_type}|{norm_entity(self.entity)}{ln}|{self.game_date}"

    def event(self) -> str:
        """The game this line belongs to (for events/day). Falls back to
        type|matchup|date, which is right for matchups but not player props."""
        return self.event_id or f"{self.market_type}|{norm_entity(self.entity)}|{self.game_date}"


@dataclass
class Mapping:
    kalshi_ticker: str
    vendor_id: str
    score: float          # 1.0 = exact/override
    status: str           # "exact" | "fuzzy" | "override" | "unmatched"
    note: str = ""        # why it's unmatched, when we can tell


def load_overrides(path: str = "") -> dict[str, str]:
    """overrides.csv: kalshi_ticker,vendor_id  — manual truth, always wins."""
    path = path or config.OVERRIDES_CSV
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {r["kalshi_ticker"]: r["vendor_id"] for r in csv.DictReader(f)}


def _shift(date: str, days: int) -> str:
    return (dt.date.fromisoformat(date) + dt.timedelta(days=days)).isoformat()


def match_markets(
    kalshi: dict[str, MarketKey],       # ticker -> key
    vendor: dict[str, MarketKey],       # vendor_id -> key
    overrides: dict[str, str] | None = None,
    threshold: float = config.FUZZY_THRESHOLD,
) -> list[Mapping]:
    overrides = overrides if overrides is not None else load_overrides()
    vendor_by_key: dict[str, list[str]] = defaultdict(list)
    for vid, mk in vendor.items():
        vendor_by_key[mk.key()].append(vid)
    kalshi_key_count: dict[str, int] = defaultdict(int)
    for kmk in kalshi.values():
        kalshi_key_count[kmk.key()] += 1

    out = []
    for ticker, kmk in kalshi.items():
        if ticker in overrides:
            out.append(Mapping(ticker, overrides[ticker], 1.0, "override"))
            continue
        key = kmk.key()
        # Two markets that normalize identically (doubleheader, duplicate
        # listing) can't be told apart: guessing could map the wrong game.
        vids = vendor_by_key.get(key, [])
        if len(vids) > 1 or (vids and kalshi_key_count[key] > 1):
            out.append(Mapping(ticker, "", 0.0, "unmatched",
                               f"ambiguous: {kalshi_key_count[key]} Kalshi / {len(vids)} "
                               f"vendor markets share this key (doubleheader?)"))
            continue
        if vids:
            out.append(Mapping(ticker, vids[0], 1.0, "exact"))
            continue
        # fuzzy: same type/date/line, best entity similarity
        kent = norm_entity(kmk.entity)
        best_vid, best = "", 0.0
        for vid, vmk in vendor.items():
            if (vmk.market_type, vmk.game_date, vmk.line) != (kmk.market_type, kmk.game_date, kmk.line):
                continue
            r = SequenceMatcher(None, kent, norm_entity(vmk.entity)).ratio()
            if r > best:
                best, best_vid = r, vid
        if best >= threshold:
            out.append(Mapping(ticker, best_vid, best, "fuzzy"))
            continue
        # Diagnose the classic date bug: same line one day off -> vendor in UTC?
        note = ""
        for days in (1, -1):
            shifted = MarketKey(kmk.market_type, kmk.entity, kmk.line,
                                _shift(kmk.game_date, days)).key()
            if shifted in vendor_by_key:
                note = (f"vendor has this on {_shift(kmk.game_date, days)}: vendor dates "
                        f"in UTC? set game_date with mapping.kalshi_game_date()")
                break
        out.append(Mapping(ticker, best_vid, best, "unmatched", note))
    return out


def report(mappings: list[Mapping], max_listed: int = 25) -> str:
    by = lambda s: [m for m in mappings if m.status == s]
    lines = [f"mapped: {len(by('exact'))} exact, {len(by('override'))} override, "
             f"{len(by('fuzzy'))} fuzzy (REVIEW), {len(by('unmatched'))} unmatched (NO TRADE)"]
    for m in by("fuzzy"):
        lines.append(f"  REVIEW  {m.kalshi_ticker} -> {m.vendor_id}  ({m.score:.2f})")
    unmatched = by("unmatched")
    noted = sum(1 for m in unmatched if m.note)
    # Show diagnosed ones first: they're the actionable ones.
    for m in sorted(unmatched, key=lambda m: not m.note)[:max_listed]:
        hint = m.note or "add to overrides.csv if it's real"
        lines.append(f"  UNMATCHED  {m.kalshi_ticker}  (best {m.vendor_id or '-'} "
                     f"{m.score:.2f})  -> {hint}")
    if len(unmatched) > max_listed:
        lines.append(f"  ... and {len(unmatched) - max_listed} more unmatched"
                     + (f" ({noted} with a diagnosis)" if noted else ""))
    return "\n".join(lines)
