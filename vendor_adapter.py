"""Vendor adapter interface + mock implementation.

Contract: an adapter returns
    markets: dict[vendor_id, MarketKey]
    quotes:  dict[vendor_id, dict[book_name, BookQuote]]   (sharps + DK/FD together)

To go live, implement UnabatedAdapter (or whichever vendor survives the trial)
with the same two methods. Nothing else in the codebase changes.
"""
from __future__ import annotations

import datetime as dt

from mapping import MarketKey
from pricing import BookQuote

TODAY = dt.date.today().isoformat()


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
        }
        return markets, quotes


class MockKalshiMarkets:
    """Stand-in for GET /markets until you have demo keys.
    Kalshi-side naming quirks are deliberate (no accents, different order)."""

    def fetch(self):
        return {
            "KXMLBHR-26JUL16-JUDGE":  MarketKey("mlb_hr", "Aaron Judge", None, TODAY),
            "KXMLBHR-26JUL16-OHTANI": MarketKey("mlb_hr", "Shohei Ohtani", None, TODAY),
            "KXMLBHR-26JUL16-ACUNA":  MarketKey("mlb_hr", "Ronald Acuna", None, TODAY),
            # a Kalshi market the vendor doesn't carry -> must land UNMATCHED
            "KXMLBHR-26JUL16-DELACRUZ": MarketKey("mlb_hr", "Elly De La Cruz", None, TODAY),
            **{f"KXEPLCORN-26JUL16-ARSCHE-{l}":
               MarketKey("corners", "Arsenal vs Chelsea", l, TODAY)
               for l in (7.5, 8.5, 9.5, 10.5, 11.5)},
        }


def get_vendor_adapter(name: str):
    if name == "mock":
        return MockVendorAdapter()
    raise NotImplementedError(
        f"Vendor '{name}' not implemented yet. Write an adapter class with a "
        f".fetch() -> (markets, quotes) matching MockVendorAdapter's contract.")
