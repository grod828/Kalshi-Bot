"""SportsGameOdds (SGO) adapter: GET /v2/events -> the vendor_adapter contract.

    markets: {vendor_id: MarketKey}
    quotes:  {vendor_id: {book: BookQuote}}

Docs: https://sportsgameodds.com/docs (full text: /docs/llms-full.txt).
Each event's `odds` is keyed by oddID = {statID}-{statEntityID}-{periodID}-
{betTypeID}-{sideID}; per-book prices live in odds[oddID].byBookmaker[book]
as American-odds STRINGS, with `overUnder` for O/U lines and `altLines` for
alternates (only with includeAltLines=true). SGO bills per EVENT returned,
not per market, so we ask for every market we need in one request per league.

Coverage per SGO's own market table (Nov 2026), sharps = Pinnacle/Circa/BookMaker:
  MLB HR        batting_homeRuns yn: Circa + Pinnacle; DK/FD
  NFL yardage   rushing/receiving_yards ou: Pinnacle, Circa, BookMaker; DK/FD
  NFL first TD  firstTouchdown yn: Circa only; DK/FD. No D/ST or "no TD"
                outcome is offered, so Kalshi's D/ST and No Touchdown markets
                stay unmatched and the devigged field slightly overstates players.
  Corners       cornerKicks-all ou: NO sharp book in any league -> corners
                can't get a fair value from SGO (they map, but never price).

Freshness: BookQuote.ts is set to the fetch time. SGO's per-book
`lastUpdatedAt` may mean "price last changed" rather than "last confirmed";
check against live data before using it for the staleness filter.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse

import config
from kalshi_markets import _get
from mapping import MarketKey, kalshi_game_date
from pricing import BookQuote

BASE = "https://api.sportsgameodds.com/v2"
# SGO bookmakerID -> the book names used in config.SHARP_BOOKS / RETAIL_BOOKS
BOOK_MAP = {"bookmakereu": "bookmaker"}
SGO_BOOK = {v: k for k, v in BOOK_MAP.items()}

# market type -> (SGO leagues, oddIDs to request; opposing sides come along)
MARKETS = {
    "mlb_hr": (["MLB"], ["batting_homeRuns-PLAYER_ID-game-yn-yes"]),
    "nfl_rush_yds": (["NFL"], ["rushing_yards-PLAYER_ID-game-ou-over"]),
    "nfl_rec_yds": (["NFL"], ["receiving_yards-PLAYER_ID-game-ou-over"]),
    "nfl_first_td": (["NFL"], ["firstTouchdown-PLAYER_ID-game-yn-yes"]),
    "corners": (["EPL", "LA_LIGA", "IT_SERIE_A", "BUNDESLIGA", "FR_LIGUE_1",
                 "UEFA_CHAMPIONS_LEAGUE"], ["cornerKicks-all-game-ou-over"]),
}
# (statID, betTypeID) -> market type; first TD only uses the yes side
STAT_TYPE = {
    ("batting_homeRuns", "yn"): "mlb_hr",
    ("rushing_yards", "ou"): "nfl_rush_yds",
    ("receiving_yards", "ou"): "nfl_rec_yds",
    ("firstTouchdown", "yn"): "nfl_first_td",
    ("cornerKicks", "ou"): "corners",
}
YES_SIDES, NO_SIDES = {"yes", "over"}, {"no", "under"}


def _american(s) -> int | None:
    try:
        return int(str(s).replace("+", "").strip())
    except (TypeError, ValueError):
        return None   # "even", "", missing: skip rather than guess


def _line(entry: dict) -> float | None:
    try:
        return float(entry["overUnder"]) if entry.get("overUnder") not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_events(events: list[dict], fetched_at: float | None = None,
                 books: set[str] | None = None) -> tuple[dict, dict]:
    """SGO event objects -> (markets, quotes). Pure: no network."""
    fetched_at = fetched_at if fetched_at is not None else time.time()
    books = books or set(config.SHARP_BOOKS) | set(config.RETAIL_BOOKS)
    markets: dict[str, MarketKey] = {}
    sides: dict[str, dict[str, dict[str, int]]] = {}   # vid -> book -> {yes/no: odds}

    for ev in events:
        status = ev.get("status") or {}
        if status.get("started") or status.get("cancelled") or not status.get("startsAt"):
            continue                                    # pre-game only
        date = kalshi_game_date(status["startsAt"])
        eid = ev.get("eventID", "")
        teams = ev.get("teams") or {}
        name_of = lambda side: ((teams.get(side) or {}).get("names") or {}).get("long", "")
        matchup = f"{name_of('home')} vs {name_of('away')}"
        players = ev.get("players") or {}

        for odd in (ev.get("odds") or {}).values():
            mtype = STAT_TYPE.get((odd.get("statID"), odd.get("betTypeID")))
            side = odd.get("sideID")
            if not mtype or odd.get("periodID") != "game" or odd.get("started"):
                continue
            if mtype == "nfl_first_td" and side != "yes":
                continue
            ent = odd.get("statEntityID", "")
            if mtype == "corners":
                if ent != "all":
                    continue                            # team corners: not ours
                entity = matchup
            else:
                entity = (players.get(ent) or {}).get("name") or ""
                if not entity:
                    continue
            ladder = odd.get("betTypeID") == "ou"
            for sgo_book, entry in (odd.get("byBookmaker") or {}).items():
                book = BOOK_MAP.get(sgo_book, sgo_book)
                if book not in books:
                    continue
                for e in [entry] + list(entry.get("altLines") or []):
                    if not e.get("available", True):
                        continue
                    odds = _american(e.get("odds"))
                    line = _line(e) if ladder else None
                    if odds is None or (ladder and line is None):
                        continue
                    vid = f"sgo:{eid}:{mtype}:{ent}:{line}"
                    markets.setdefault(vid, MarketKey(mtype, entity, line, date, event_id=eid))
                    key = "yes" if side in YES_SIDES else "no" if side in NO_SIDES else None
                    if key:
                        sides.setdefault(vid, {}).setdefault(book, {})[key] = odds

    quotes = {vid: {book: BookQuote(book, s.get("yes"), s.get("no"), ts=fetched_at)
                    for book, s in by_book.items() if "yes" in s or "no" in s}
              for vid, by_book in sides.items()}
    return markets, quotes


class SportsGameOddsAdapter:
    """.fetch() -> (markets, quotes) for config.KALSHI_SERIES' market types.

    API key: env ODDS_VENDOR_KEY, sent as x-api-key. If it's empty no header is
    sent, which works when the session's network proxy injects the key."""

    def __init__(self, api_key: str = "", market_types=None, limit: int = 50,
                 verbose: bool = True):
        self.api_key = api_key or config.VENDOR_API_KEY
        self.market_types = list(market_types or config.KALSHI_SERIES)
        self.limit, self.verbose = limit, verbose
        self.events_fetched = 0

    def _leagues(self) -> dict[str, list[str]]:
        """league -> oddIDs, so each league is one paginated request."""
        out: dict[str, list[str]] = {}
        for mt in self.market_types:
            leagues, odd_ids = MARKETS.get(mt, ([], []))
            for lg in leagues:
                out.setdefault(lg, []).extend(o for o in odd_ids if o not in out.get(lg, []))
        return out

    def events(self, league: str, odd_ids: list[str]) -> list[dict]:
        wanted = sorted({SGO_BOOK.get(b, b) for b in config.SHARP_BOOKS + config.RETAIL_BOOKS})
        params = {"leagueID": league, "oddsAvailable": "true", "started": "false",
                  "oddIDs": ",".join(odd_ids), "includeOpposingOddIDs": "true",
                  "bookmakerID": ",".join(wanted), "limit": self.limit}
        if any(o.split("-")[3] == "ou" for o in odd_ids):
            params["includeAltLines"] = "true"          # ladders + corners need alternates
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        out, cursor = [], ""
        while True:
            q = dict(params, **({"cursor": cursor} if cursor else {}))
            try:
                data = _get(f"{BASE}/events?{urllib.parse.urlencode(q)}", headers=headers)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise RuntimeError(
                        f"SportsGameOdds rejected the request ({e.code}): set ODDS_VENDOR_KEY "
                        f"to your API key (sportsgameodds.com/pricing)") from e
                raise
            page = data.get("data") or []
            out.extend(page)
            cursor = data.get("nextCursor") or ""
            if not cursor or not page:
                return out

    def fetch(self) -> tuple[dict, dict]:
        events = []
        for league, odd_ids in self._leagues().items():
            got = self.events(league, odd_ids)
            events.extend(got)
            if self.verbose:
                print(f"  sgo {league}: {len(got)} upcoming events")
        self.events_fetched = len(events)
        return parse_events(events)
