"""Vendor adapter interface + mock implementation.

Contract: an adapter returns
    markets: dict[vendor_id, MarketKey]
    quotes:  dict[vendor_id, dict[book_name, BookQuote]]   (sharps + DK/FD together)

MarketKey.game_date MUST be the US Eastern date Kalshi uses: build it with
mapping.kalshi_game_date(start_time_utc), never the UTC date (a 7pm PT first
pitch is already tomorrow in UTC). Team names can be the vendor's own; the
mapper normalizes them (team_aliases.py), and matchup order doesn't matter.

To go live, implement UnabatedAdapter (or whichever vendor survives the trial)
with the same two methods. Nothing else in the codebase changes.
"""
from __future__ import annotations

import datetime as dt

from mapping import MarketKey
from pricing import BookQuote

TODAY = dt.date.today().isoformat()

# First TD scorer, one game. Vendor spellings differ from Kalshi's on purpose.
# (outcome, sharp American odds, DK American odds); DK carries more vig.
# The last few are players Kalshi doesn't list: they still count in the devig.
FIRST_TD_FIELD = [
    ("Jonathan Taylor", 600, 450), ("Nico Collins", 650, 550),
    ("Emari Demercado", 750, 600), ("Dalton Schultz", 1000, 800),
    ("Kayshon Boutte", 1300, 1000), ("Tank Dell", 1400, 1200),
    ("CJ Stroud", 2000, 1600), ("Tyler Warren", 1800, 1400),
    ("Houston Texans Defense", 2500, 1800), ("Indianapolis Colts Defense", 3000, 2200),
    ("No TD Scorer", 6000, 4000),
    ("Deion Burks", 2500, 2000), ("Anthony Gould", 3000, 2500),
    ("Foster Moreau", 2500, 2000), ("Seth McGowan", 2200, 1800),
    ("Laquon Treadwell", 3000, 2500), ("Dare Ogunbowale", 3000, 2500),
    ("Marlin Klein", 4000, 3000), ("Cade Stover", 4000, 3000),
    ("Ashton Dulin", 5000, 4000), ("Daniel Jones", 4000, 3000),
    ("Drew Ogletree", 5000, 4000), ("Jelani Woods", 5000, 4000),
]


class MockVendorAdapter:
    """Deterministic fixture data exercising every mapping path:
    exact match, fuzzy (accent/suffix), unmatched, plus corners ladder."""

    def fetch(self):
        markets = {
            # exact-match candidates
            "v-hr-judge":  MarketKey("mlb_hr", "Aaron Judge", None, TODAY),
            "v-hr-ohtani": MarketKey("mlb_hr", "Shohei Ohtani", None, TODAY),
            # fuzzy: vendor uses accent + Jr.
            "v-hr-acuna":  MarketKey("mlb_hr", "Ronald Acuña Jr.", None, TODAY),
            # corners ladder
            **{f"v-corn-{l}": MarketKey("corners", "Arsenal vs Chelsea", l, TODAY)
               for l in (7.5, 8.5, 9.5, 10.5, 11.5)},
            # NFL rushing yards: sharps quote ONE main line (62.5); DK/FD sell
            # yes-only milestones ("40+ yards") at Kalshi-style rungs.
            **{f"v-rush-jtaylor-{l}": MarketKey("nfl_rush_yds", "Jonathan Taylor", l, TODAY)
               for l in (24.5, 39.5, 59.5, 62.5, 79.5, 99.5)},
            # first TD scorer: event_id groups the field (the vendor's game id)
            **{f"v-ftd-{i}": MarketKey("nfl_first_td", name, None, TODAY, event_id="v-g-indhou")
               for i, (name, _, _) in enumerate(FIRST_TD_FIELD)},
        }
        q = lambda y, n: {  # sharps + retail for one line
            "pinnacle": BookQuote("pinnacle", y, n),
            "circa": BookQuote("circa", y + 5, n - 5),
            "bookmaker": BookQuote("bookmaker", y - 5, n + 5),
            "draftkings": BookQuote("draftkings", y - 20, n - 15),
            "fanduel": BookQuote("fanduel", y - 15, n - 20),
        }
        quotes = {
            "v-hr-judge": q(205, -245),
            "v-hr-ohtani": q(240, -290),
            "v-hr-acuna": q(320, -380),
            "v-corn-7.5": q(-280, 230),
            "v-corn-8.5": q(-160, 135),
            "v-corn-9.5": q(105, -125),
            "v-corn-10.5": q(175, -210),
            "v-corn-11.5": q(270, -330),
            # main line ~50/50 at the sharps
            "v-rush-jtaylor-62.5": {
                "pinnacle": BookQuote("pinnacle", -112, -108),
                "circa": BookQuote("circa", -110, -110),
                "bookmaker": BookQuote("bookmaker", -108, -112),
            },
            # retail milestones: yes side only
            **{f"v-rush-jtaylor-{l}": {
                "draftkings": BookQuote("draftkings", dk, None),
                "fanduel": BookQuote("fanduel", dk + 10, None),
            } for l, dk in ((24.5, -2000), (39.5, -500), (59.5, -130),
                            (79.5, +150), (99.5, +300))},
            # first TD: every book is yes-only
            **{f"v-ftd-{i}": {
                "pinnacle": BookQuote("pinnacle", sharp, None),
                "circa": BookQuote("circa", sharp + 50, None),
                "draftkings": BookQuote("draftkings", dk, None),
                "fanduel": BookQuote("fanduel", dk - 50, None),
            } for i, (_, sharp, dk) in enumerate(FIRST_TD_FIELD)},
        }
        return markets, quotes


class MockKalshiMarkets:
    """Stand-in for GET /markets until you have demo keys.
    Kalshi-side naming quirks are deliberate (no accents, different order)."""

    def fetch(self):
        return {
            "KXMLBHR-26JUL16-JUDGE":  MarketKey("mlb_hr", "Aaron Judge", None, TODAY,
                                                event_id="KXMLBHR-26JUL16NYYBOS"),
            "KXMLBHR-26JUL16-OHTANI": MarketKey("mlb_hr", "Shohei Ohtani", None, TODAY,
                                                event_id="KXMLBHR-26JUL16LADSF"),
            "KXMLBHR-26JUL16-ACUNA":  MarketKey("mlb_hr", "Ronald Acuna", None, TODAY,
                                                event_id="KXMLBHR-26JUL16ATLMIA"),
            # a Kalshi market the vendor doesn't carry -> must land UNMATCHED
            "KXMLBHR-26JUL16-DELACRUZ": MarketKey("mlb_hr", "Elly De La Cruz", None, TODAY,
                                                  event_id="KXMLBHR-26JUL16CINSTL"),
            # rushing ladder: 49.5 has no DK/FD milestone; 24.5 is too far
            # from the sharp main line for the model to be trusted
            **{f"KXNFLRSHYDS-26JUL16INDHOU-JTAYLOR-{int(l + .5)}":
               MarketKey("nfl_rush_yds", "Jonathan Taylor", l, TODAY,
                         event_id="KXNFLRSHYDS-26JUL16INDHOU")
               for l in (24.5, 39.5, 49.5, 59.5, 79.5, 99.5)},
            # first TD scorer: Kalshi lists likely scorers + both D/STs + No TD
            **{f"KXNFLFIRSTTD-26JUL16INDHOU-{suffix}":
               MarketKey("nfl_first_td", name, None, TODAY,
                         event_id="KXNFLFIRSTTD-26JUL16INDHOU")
               for suffix, name in (
                   ("INDJTAYLOR28", "Jonathan Taylor"), ("HOUNCOLLINS12", "Nico Collins"),
                   ("HOUEDEMERCADO31", "Emari Demercado"), ("HOUDSCHULTZ86", "Dalton Schultz"),
                   ("HOUTDELL1", "Tank Dell"), ("HOUCSTROUD7", "C.J. Stroud"),
                   ("INDLTREADWELL13", "Laquon Treadwell"), ("HOUHOUDST", "HOU Texans D/ST"),
                   ("INDINDDST", "IND Colts D/ST"), ("NONE", "No Touchdown"))},
            **{f"KXEPLCORN-26JUL16-ARSCHE-{l}":
               MarketKey("corners", "Arsenal vs Chelsea", l, TODAY,
                         event_id="KXEPLCORNERS-26JUL16ARSCHE")
               for l in (7.5, 8.5, 9.5, 10.5, 11.5)},
        }


def get_vendor_adapter(name: str):
    if name == "mock":
        return MockVendorAdapter()
    raise NotImplementedError(
        f"Vendor '{name}' not implemented yet. Write an adapter class with a "
        f".fetch() -> (markets, quotes) matching MockVendorAdapter's contract.")
