"""Live Kalshi market discovery -> MarketKey objects (read-only, no API key).

Kalshi's market data endpoints are public, so this needs no account and places
nothing. It pulls open events (with their markets nested) for every series in
config.KALSHI_SERIES and converts them to the same MarketKey shape the mock
uses, so mapping/audit code doesn't change.

Kalshi conventions handled here (verified against the live API, Nov 2026):
  corners  "9+ total corners", strike_type=greater_or_equal, floor_strike=9
           -> line 8.5, i.e. the sportsbook "Over 8.5" equivalent
  mlb_hr   "Sal Frelick: 1+ home runs?", strike_type=greater, floor_strike=0.5
           -> line None (the yes/no HR prop). 2+ HR markets are skipped:
              the strategy only quotes the 1+ prop.
  yards    "Jonathan Taylor: 25+ rushing yards", strike_type=greater,
           floor_strike=24.5 -> line 24.5 (player = entity; ladders.py prices it)
  1st TD   "Nico Collins: 1st Touchdown" -> entity "Nico Collins", one market
           per outcome incl. "HOU Texans D/ST" and "No Touchdown". Kalshi lists
           only likely scorers: an unlisted scorer resolves every market No.
  date     taken from the event ticker (KXEPLCORNERS-26NOV23BREEVE -> 2026-11-23),
           which is the "originally scheduled" date in Kalshi's rules text.

Pure stdlib (urllib) to keep the zero-install promise.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import config
from fees import WORST_CASE, FeeSchedule, schedule_from_api
from mapping import MarketKey

BASES = {
    "prod": "https://api.elections.kalshi.com/trade-api/v2",
    "demo": "https://demo-api.kalshi.co/trade-api/v2",
}
MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
EVENT_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
TRADEABLE_STATUSES = {"active", "open"}


# ---------------------------------------------------------------- parsing
def parse_event_date(event_ticker: str) -> str | None:
    """'KXMLBHR-26OCT282000MILTB' -> '2026-10-28'. None if no date code."""
    m = EVENT_DATE.search(event_ticker)
    if not m or m.group(2) not in MONTHS:
        return None
    yy, mon, dd = m.groups()
    return f"20{yy}-{MONTHS[mon]:02d}-{dd}"


def corners_line(market: dict) -> float | None:
    """Kalshi 'N+' (>= N) is the same bet as 'Over N-0.5'."""
    strike, kind = market.get("floor_strike"), market.get("strike_type")
    if strike is None:
        return None
    if kind == "greater_or_equal":
        return float(strike) - 0.5
    if kind == "greater":
        return float(strike)
    return None


def event_to_keys(market_type: str, event: dict) -> dict[str, MarketKey]:
    """One Kalshi event (with nested markets) -> {ticker: MarketKey}."""
    date = parse_event_date(event.get("event_ticker", ""))
    if not date:
        return {}
    out = {}
    for mkt in event.get("markets", []):
        if mkt.get("status") not in TRADEABLE_STATUSES:
            continue
        if market_type == "corners":
            line = corners_line(mkt)
            matchup = event.get("title", "").split(":")[0].strip()
            if line is None or not matchup:
                continue
            out[mkt["ticker"]] = MarketKey("corners", matchup, line, date,
                                           event_id=event.get("event_ticker", ""))
        elif market_type in config.LADDER_TYPES:
            # "Jonathan Taylor: 25+ rushing yards", strike_type greater, 24.5
            if mkt.get("strike_type") != "greater" or mkt.get("floor_strike") is None:
                continue
            player = mkt.get("title", "").split(":")[0].strip()
            if player:
                out[mkt["ticker"]] = MarketKey(market_type, player, float(mkt["floor_strike"]),
                                               date, event_id=event.get("event_ticker", ""))
        elif market_type in config.FIELD_TYPES:
            # "Nico Collins: 1st Touchdown", "HOU Texans D/ST: 1st Touchdown",
            # "No Touchdown: 1st Touchdown" (strike_type "structured")
            outcome = mkt.get("title", "").split(":")[0].strip()
            if outcome:
                out[mkt["ticker"]] = MarketKey(market_type, outcome, None, date,
                                               event_id=event.get("event_ticker", ""))
        elif market_type == "mlb_hr":
            # 1+ HR only: floor_strike 0.5 with strike_type "greater"
            if (mkt.get("strike_type"), mkt.get("floor_strike")) != ("greater", 0.5):
                continue
            player = mkt.get("title", "").split(":")[0].strip()
            if player:
                out[mkt["ticker"]] = MarketKey("mlb_hr", player, None, date,
                                               event_id=event.get("event_ticker", ""))
    return out


# ---------------------------------------------------------------- HTTP
def _get(url: str, retries: int = 4, timeout: float = 30,
         headers: dict | None = None) -> dict:
    """GET JSON; back off on 429/5xx (Kalshi rate-limits bursts of reads).
    Shared by the odds vendor adapter, which passes its API-key header."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
            wait = float(e.headers.get("Retry-After") or 2 ** attempt)
        except urllib.error.URLError:
            if attempt == retries:
                raise
            wait = 2 ** attempt
        time.sleep(wait)
    raise RuntimeError("unreachable")


class LiveKalshiMarkets:
    """Drop-in for MockKalshiMarkets: .fetch() -> {ticker: MarketKey}.
    After fetch(), .fees maps each ticker to its Kalshi FeeSchedule
    (series fee_type/fee_multiplier, with event overrides applied)."""

    def __init__(self, env: str = "", series: dict[str, list[str]] | None = None,
                 pause_s: float = 0.25, verbose: bool = True):
        self.base = BASES[env or config.KALSHI_DATA_ENV]
        self.series = series or config.KALSHI_SERIES
        self.pause_s, self.verbose = pause_s, verbose
        self.fees: dict[str, FeeSchedule] = {}

    def series_info(self, series_ticker: str) -> dict:
        """GET /series/{t}; {} on failure (fees then fall back to worst case)."""
        try:
            return _get(f"{self.base}/series/{series_ticker}").get("series", {})
        except (urllib.error.URLError, ValueError) as e:
            if self.verbose:
                print(f"  kalshi {series_ticker}: series lookup failed ({e}); "
                      f"assuming worst-case fees")
            return {}

    def events(self, series_ticker: str):
        cursor = ""
        while True:
            q = {"series_ticker": series_ticker, "status": "open",
                 "with_nested_markets": "true", "limit": 200}
            if cursor:
                q["cursor"] = cursor
            data = _get(f"{self.base}/events?{urllib.parse.urlencode(q)}")
            yield from data.get("events", [])
            cursor = data.get("cursor") or ""
            time.sleep(self.pause_s)
            if not cursor:
                return

    def fetch(self) -> dict[str, MarketKey]:
        out: dict[str, MarketKey] = {}
        for mtype, tickers in self.series.items():
            for st in tickers:
                series = self.series_info(st)
                time.sleep(self.pause_s)
                n_events, before = 0, len(out)
                for ev in self.events(st):
                    n_events += 1
                    keys = event_to_keys(mtype, ev)
                    sched = schedule_from_api(series, ev) if series else WORST_CASE
                    out.update(keys)
                    self.fees.update({t: sched for t in keys})
                if self.verbose:
                    fee = (f"fees {series.get('fee_type')} x{series.get('fee_multiplier')}"
                           if series else "fees unknown")
                    print(f"  kalshi {st}: {n_events} open events, "
                          f"{len(out) - before} {mtype} lines, {fee}")
        return out


if __name__ == "__main__":
    # Quick look at what's live right now: python kalshi_markets.py
    mk = LiveKalshiMarkets().fetch()
    print(f"\n{len(mk)} lines total")
    for t, k in list(mk.items())[:15]:
        print(f"  {t:45s} {k.key()}")
