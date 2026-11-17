"""Matching fixes: per-team aliases, ET game dates, ambiguity, events/day gate.
Run: python -m unittest -v"""
import datetime as dt
import sqlite3
import unittest

import audit
from mapping import (MarketKey, _eastern_offset_h, kalshi_game_date, match_markets,
                     norm_entity, norm_team, report)

# Kalshi titles (Nov 2026) vs typical sportsbook spellings of the same game
SAME_GAME = [
    ("Manchester City vs Fulham", "Fulham @ Man City FC"),
    ("Bilbao vs Espanyol", "Athletic Club v RCD Espanyol"),
    ("Vallecano vs Valencia", "Rayo Vallecano vs Valencia CF"),
    ("M´gladbach vs Dortmund", "Borussia Mönchengladbach vs Borussia Dortmund"),
    ("Bayern Munich vs FC Köln", "FC Bayern München vs 1. FC Köln"),
    ("Bodoe/Glimt vs LASK", "FK Bodø/Glimt vs LASK Linz"),
    ("Parma Calcio vs Roma", "Parma vs AS Roma"),
    ("Stade Brest 29 vs Stade Rennais", "Stade Brestois 29 vs Rennes"),
    ("Nice vs PSG", "OGC Nice vs Paris Saint-Germain"),
    ("Alaves vs Deportivo De La Coruna", "Deportivo Alavés vs Deportivo La Coruña"),
    ("Arsenal vs Dortmund", "Arsenal FC vs BV Borussia 09 Dortmund"),
]
DIFFERENT_CLUBS = [
    ("Real Madrid", "Real Sociedad"), ("Manchester City", "Manchester United"),
    ("Inter", "Milan"), ("Paris FC", "PSG"), ("Lens", "Lille"),
]


class NormalizationTests(unittest.TestCase):
    def test_real_kalshi_names_match_sportsbook_names(self):
        for kalshi, vendor in SAME_GAME:
            with self.subTest(kalshi=kalshi):
                self.assertEqual(norm_entity(kalshi), norm_entity(vendor))

    def test_different_clubs_stay_different(self):
        for a, b in DIFFERENT_CLUBS:
            with self.subTest(a=a, b=b):
                self.assertNotEqual(norm_team(a), norm_team(b))

    def test_home_away_order_ignored(self):
        self.assertEqual(norm_entity("Fulham vs Manchester City"),
                         norm_entity("Manchester City vs Fulham"))

    def test_player_names_unchanged(self):
        self.assertEqual(norm_entity("Ronald Acuña Jr."), "ronald acuna")

    def test_name_made_only_of_club_tokens_is_kept(self):
        self.assertEqual(norm_team("LOSC"), "losc")


class GameDateTests(unittest.TestCase):
    def test_late_game_files_under_eastern_date(self):
        # KXMLBHR-26OCT282000MILTB: 8pm EDT Oct 28 == 00:00 UTC Oct 29
        self.assertEqual(kalshi_game_date("2026-10-29T00:00:00Z"), "2026-10-28")
        self.assertEqual(kalshi_game_date("2026-10-28T20:00:00-04:00"), "2026-10-28")

    def test_afternoon_kickoff_unchanged(self):
        self.assertEqual(kalshi_game_date("2026-11-21T15:00:00Z"), "2026-11-21")

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            kalshi_game_date("2026-10-29T00:00:00")

    def test_fallback_dst_boundaries_2026(self):
        utc = lambda s: dt.datetime.fromisoformat(s)
        self.assertEqual(_eastern_offset_h(utc("2026-03-08T06:59")), -5)
        self.assertEqual(_eastern_offset_h(utc("2026-03-08T07:00")), -4)
        self.assertEqual(_eastern_offset_h(utc("2026-11-01T05:59")), -4)
        self.assertEqual(_eastern_offset_h(utc("2026-11-01T06:00")), -5)

    def test_fallback_agrees_with_tz_database(self):
        try:
            from zoneinfo import ZoneInfo
            ny = ZoneInfo("America/New_York")
        except Exception:
            self.skipTest("no tz database")
        t = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        while t.year == 2026:
            want = t.astimezone(ny).utcoffset() // dt.timedelta(hours=1)
            self.assertEqual(_eastern_offset_h(t), want, t)
            t += dt.timedelta(hours=1)


def corners(entity, line=8.5, date="2026-11-21", eid=""):
    return MarketKey("corners", entity, line, date, event_id=eid)


class MatchTests(unittest.TestCase):
    def test_vendor_spelling_maps_exactly(self):
        m, = match_markets({"K1": corners("Manchester City vs Fulham")},
                           {"v1": corners("Fulham @ Man City FC")}, overrides={})
        self.assertEqual((m.status, m.vendor_id), ("exact", "v1"))

    def test_doubleheader_on_vendor_side_is_ambiguous(self):
        hr = lambda d="2026-07-16": MarketKey("mlb_hr", "Aaron Judge", None, d)
        m, = match_markets({"K1": hr()}, {"g1": hr(), "g2": hr()}, overrides={})
        self.assertEqual(m.status, "unmatched")
        self.assertIn("ambiguous", m.note)

    def test_duplicate_kalshi_keys_are_ambiguous(self):
        hr = MarketKey("mlb_hr", "Aaron Judge", None, "2026-07-16")
        out = match_markets({"K1": hr, "K2": hr}, {"v1": hr}, overrides={})
        self.assertTrue(all(m.status == "unmatched" for m in out))

    def test_override_beats_ambiguity(self):
        hr = MarketKey("mlb_hr", "Aaron Judge", None, "2026-07-16")
        m, = match_markets({"K1": hr}, {"g1": hr, "g2": hr}, overrides={"K1": "g2"})
        self.assertEqual((m.status, m.vendor_id), ("override", "g2"))

    def test_off_by_one_date_is_diagnosed_not_matched(self):
        m, = match_markets({"K1": corners("Arsenal vs Chelsea", date="2026-11-21")},
                           {"v1": corners("Arsenal vs Chelsea", date="2026-11-22")},
                           overrides={})
        self.assertEqual(m.status, "unmatched")
        self.assertIn("UTC", m.note)
        self.assertIn("UTC", report([m]))

    def test_event_id(self):
        self.assertEqual(corners("A vs B", eid="EV1").event(), "EV1")
        self.assertEqual(corners("B vs A").event(), corners("A vs B").event())


class EventsPerDayGateTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(audit.SCHEMA)

    def log(self, n_events, date="2026-11-21", mapped=1, ts=1.0):
        for i in range(n_events):
            self.con.execute("INSERT INTO event_log VALUES (?,?,?,?,?,?,?)",
                             (ts, "corners", f"EV{i}", date, 5, mapped, 0))

    def test_passes_at_threshold(self):
        self.log(20)
        self.log(20, ts=2.0)          # same games seen again: still 20, not 40
        self.assertIn("GATE 1 events/day: avg 20.0", audit.gate_report(self.con))
        self.assertIn("(PASS vs 20)", audit.gate_report(self.con))

    def test_fails_below_threshold(self):
        self.log(12)
        self.assertIn("(FAIL vs 20)", audit.gate_report(self.con))
        self.assertIn("OVERALL: FAIL", audit.gate_report(self.con))

    def test_nothing_mapped_is_no_data_not_fail(self):
        self.log(30, mapped=0)
        r = audit.gate_report(self.con)
        self.assertIn("GATE 1 events/day: no data", r)
        self.assertIn("OVERALL: no verdict", r)


if __name__ == "__main__":
    unittest.main()
