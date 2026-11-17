"""Offline tests for live Kalshi parsing. Fixtures are trimmed copies of real
GET /events responses (Nov 2026). Run: python -m unittest -v"""
import unittest

from kalshi_markets import corners_line, event_to_keys, parse_event_date
from mapping import MarketKey, match_markets

CORNERS_EVENT = {
    "event_ticker": "KXEPLCORNERS-26NOV21MCIFUL",
    "title": "Manchester City vs Fulham: Total Corners",
    "markets": [
        {"ticker": "KXEPLCORNERS-26NOV21MCIFUL-9", "status": "active",
         "floor_strike": 9, "strike_type": "greater_or_equal", "title": "9+ total corners"},
        {"ticker": "KXEPLCORNERS-26NOV21MCIFUL-12", "status": "active",
         "floor_strike": 12, "strike_type": "greater_or_equal", "title": "12+ total corners"},
        {"ticker": "KXEPLCORNERS-26NOV21MCIFUL-7", "status": "closed",
         "floor_strike": 7, "strike_type": "greater_or_equal", "title": "7+ total corners"},
    ],
}

HR_EVENT = {
    "event_ticker": "KXMLBHR-26OCT282000MILTB",
    "title": "Milwaukee vs Tampa Bay: Home Runs",
    "markets": [
        {"ticker": "KXMLBHR-26OCT282000MILTB-MILSFRELICK10-1", "status": "active",
         "floor_strike": 0.5, "strike_type": "greater", "title": "Sal Frelick: 1+ home runs?"},
        {"ticker": "KXMLBHR-26OCT282000MILTB-TBRVILADE26-2", "status": "active",
         "floor_strike": 1.5, "strike_type": "greater", "title": "Ryan Vilade: 2+ home runs?"},
    ],
}


class ParseTests(unittest.TestCase):
    def test_event_dates(self):
        self.assertEqual(parse_event_date("KXEPLCORNERS-26NOV23BREEVE"), "2026-11-23")
        self.assertEqual(parse_event_date("KXMLBHR-26OCT282000MILTB"), "2026-10-28")
        self.assertIsNone(parse_event_date("KXMLBHR"))

    def test_corners_plus_n_is_over_n_minus_half(self):
        self.assertEqual(corners_line({"floor_strike": 9, "strike_type": "greater_or_equal"}), 8.5)
        self.assertEqual(corners_line({"floor_strike": 8.5, "strike_type": "greater"}), 8.5)
        self.assertIsNone(corners_line({"floor_strike": 9, "strike_type": "less"}))

    def test_corners_event(self):
        keys = event_to_keys("corners", CORNERS_EVENT)
        self.assertEqual(set(keys), {"KXEPLCORNERS-26NOV21MCIFUL-9",
                                     "KXEPLCORNERS-26NOV21MCIFUL-12"})  # closed rung dropped
        self.assertEqual(keys["KXEPLCORNERS-26NOV21MCIFUL-9"],
                         MarketKey("corners", "Manchester City vs Fulham", 8.5, "2026-11-21"))

    def test_hr_event_keeps_only_1plus(self):
        keys = event_to_keys("mlb_hr", HR_EVENT)
        self.assertEqual(keys, {"KXMLBHR-26OCT282000MILTB-MILSFRELICK10-1":
                                MarketKey("mlb_hr", "Sal Frelick", None, "2026-10-28")})

    def test_live_keys_match_vendor_over_line(self):
        """A vendor 'Over 8.5' on the same game must map exactly to Kalshi '9+'."""
        kalshi = event_to_keys("corners", CORNERS_EVENT)
        vendor = {"v1": MarketKey("corners", "Manchester City vs Fulham", 8.5, "2026-11-21")}
        m = {x.kalshi_ticker: x for x in match_markets(kalshi, vendor, overrides={})}
        self.assertEqual(m["KXEPLCORNERS-26NOV21MCIFUL-9"].status, "exact")
        self.assertEqual(m["KXEPLCORNERS-26NOV21MCIFUL-12"].status, "unmatched")


if __name__ == "__main__":
    unittest.main()
