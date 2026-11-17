"""SportsGameOdds adapter: parsing (docs-format fixture), HTTP, end to end.
Run: python -m unittest -v"""
import io
import sqlite3
import unittest
import urllib.error
from unittest import mock

import audit
import sgo_adapter
from mapping import MarketKey, match_markets
from quoting import Config as StratConfig
from sgo_adapter import SportsGameOddsAdapter, parse_events

TAYLOR, COLLINS = "JONATHAN_TAYLOR_1_NFL", "NICO_COLLINS_1_NFL"


def odd(stat, ent, side, by_book, period="game", bet="ou"):
    return {f"{stat}-{ent}-{period}-{bet}-{side}": {
        "oddID": f"{stat}-{ent}-{period}-{bet}-{side}", "statID": stat, "statEntityID": ent,
        "periodID": period, "betTypeID": bet, "sideID": side, "started": False,
        "byBookmaker": by_book}}


def nfl_event(**overrides):
    ev = {
        "eventID": "EV1", "leagueID": "NFL",
        "teams": {"home": {"names": {"long": "Houston Texans"}},
                  "away": {"names": {"long": "Indianapolis Colts"}}},
        # Thursday 8:15 PM ET Nov 19 == 01:15 UTC Nov 20
        "status": {"started": False, "cancelled": False, "startsAt": "2026-11-20T01:15:00.000Z"},
        "players": {TAYLOR: {"name": "Jonathan Taylor"}, COLLINS: {"name": "Nico Collins"}},
        "odds": {
            **odd("rushing_yards", TAYLOR, "over", {
                "pinnacle": {"odds": "-110", "overUnder": "62.5", "available": True},
                "bookmakereu": {"odds": "-112", "overUnder": "62.5", "available": True},
                "draftkings": {"odds": "-115", "overUnder": "62.5", "available": True,
                               "altLines": [{"odds": "-500", "overUnder": "39.5", "available": True},
                                            {"odds": "+150", "overUnder": "79.5", "available": True}]},
                "fanduel": {"odds": "-120", "overUnder": "62.5", "available": False},
                "betmgm": {"odds": "-110", "overUnder": "62.5", "available": True}}),
            **odd("rushing_yards", TAYLOR, "under", {
                "pinnacle": {"odds": "-110", "overUnder": "62.5", "available": True},
                "bookmakereu": {"odds": "-108", "overUnder": "62.5", "available": True},
                "draftkings": {"odds": "-105", "overUnder": "62.5", "available": True}}),
            **odd("rushing_yards", TAYLOR, "over", {
                "pinnacle": {"odds": "-110", "overUnder": "30.5", "available": True}}, period="1h"),
            **odd("firstTouchdown", TAYLOR, "yes", {
                "circa": {"odds": "+600", "available": True},
                "draftkings": {"odds": "+450", "available": True}}, bet="yn"),
            **odd("firstTouchdown", TAYLOR, "no", {"circa": {"odds": "-1000", "available": True}},
                  bet="yn"),
            **odd("firstTouchdown", COLLINS, "yes", {
                "circa": {"odds": "even", "available": True}}, bet="yn"),
            **odd("passing_yards", TAYLOR, "over", {
                "pinnacle": {"odds": "-110", "overUnder": "0.5", "available": True}}),
        },
    }
    ev.update(overrides)
    return ev


SOCCER = {
    "eventID": "EV9", "leagueID": "EPL",
    "teams": {"home": {"names": {"long": "Manchester City"}}, "away": {"names": {"long": "Fulham"}}},
    "status": {"started": False, "startsAt": "2026-11-21T15:00:00.000Z"},
    "odds": {**odd("cornerKicks", "all", "over", {
        "draftkings": {"odds": "-110", "overUnder": "9.5", "available": True,
                       "altLines": [{"odds": "+130", "overUnder": "10.5", "available": True}]}}),
             **odd("cornerKicks", "HOME_TEAM_ID", "over", {
                 "draftkings": {"odds": "-110", "overUnder": "4.5", "available": True}})},
}


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.markets, self.quotes = parse_events(
            [nfl_event(), nfl_event(eventID="EV2", status={"started": True,
                                                            "startsAt": "2026-11-19T18:00:00Z"}),
             SOCCER], fetched_at=123.0)

    def by(self, mtype, line=None, entity=None):
        return {vid: mk for vid, mk in self.markets.items() if mk.market_type == mtype
                and (line is None or mk.line == line) and (entity is None or mk.entity == entity)}

    def test_dates_are_kalshi_eastern(self):
        self.assertTrue(all(mk.game_date == "2026-11-19" for mk in self.by("nfl_rush_yds").values()))

    def test_main_line_two_sided_and_bookmaker_renamed(self):
        (vid, mk), = self.by("nfl_rush_yds", 62.5).items()
        self.assertEqual((mk.entity, mk.event_id), ("Jonathan Taylor", "EV1"))
        q = self.quotes[vid]
        self.assertEqual((q["pinnacle"].yes_american, q["pinnacle"].no_american), (-110, -110))
        self.assertEqual((q["bookmaker"].yes_american, q["bookmaker"].no_american), (-112, -108))
        self.assertEqual(q["pinnacle"].ts, 123.0)
        self.assertNotIn("fanduel", q)          # available: false
        self.assertNotIn("betmgm", q)           # not a book we use

    def test_retail_alt_lines_are_yes_only_milestones(self):
        (vid, _), = self.by("nfl_rush_yds", 39.5).items()
        dk = self.quotes[vid]["draftkings"]
        self.assertEqual((dk.yes_american, dk.no_american), (-500, None))

    def test_other_periods_and_stats_ignored(self):
        self.assertFalse(self.by("nfl_rush_yds", 30.5))
        self.assertEqual({mk.market_type for mk in self.markets.values()},
                         {"nfl_rush_yds", "nfl_first_td", "corners"})

    def test_started_games_skipped(self):
        self.assertFalse(any(mk.event_id == "EV2" for mk in self.markets.values()))

    def test_first_td_yes_only_and_bad_odds_skipped(self):
        (vid, mk), = self.by("nfl_first_td", entity="Jonathan Taylor").items()
        self.assertEqual(mk.event_id, "EV1")
        self.assertEqual(set(self.quotes[vid]), {"circa", "draftkings"})
        self.assertIsNone(self.quotes[vid]["circa"].no_american)   # "no" side ignored
        self.assertFalse(self.by("nfl_first_td", entity="Nico Collins"))   # "even" unparsed

    def test_corners_total_only_with_matchup(self):
        lines = sorted(mk.line for mk in self.by("corners").values())
        self.assertEqual(lines, [9.5, 10.5])
        self.assertTrue(all(mk.entity == "Manchester City vs Fulham"
                            for mk in self.by("corners").values()))


class HttpTests(unittest.TestCase):
    def test_paginates_and_sends_key(self):
        pages = [{"data": [nfl_event()], "nextCursor": "c1"}, {"data": [SOCCER]}]
        calls = []

        def fake_get(url, headers=None, **_):
            calls.append((url, headers))
            return pages[len(calls) - 1]
        with mock.patch.object(sgo_adapter, "_get", fake_get):
            got = SportsGameOddsAdapter(api_key="KEY", verbose=False).events("NFL", ["x-PLAYER_ID-game-ou-over"])
        self.assertEqual(len(got), 2)
        self.assertNotIn("cursor=", calls[0][0])
        self.assertIn("cursor=c1", calls[1][0])
        self.assertEqual(calls[0][1], {"x-api-key": "KEY"})
        self.assertIn("includeAltLines=true", calls[0][0])
        self.assertIn("bookmakereu", calls[0][0])

    def test_no_key_sends_no_header(self):
        with mock.patch.object(sgo_adapter, "_get", return_value={"data": []}) as g, \
                mock.patch.object(sgo_adapter.config, "VENDOR_API_KEY", ""):
            SportsGameOddsAdapter(verbose=False).events("NFL", ["a-b-game-yn-yes"])
        self.assertEqual(g.call_args.kwargs["headers"], {})

    def test_bad_key_explained(self):
        err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b""))
        with mock.patch.object(sgo_adapter, "_get", side_effect=err):
            with self.assertRaisesRegex(RuntimeError, "ODDS_VENDOR_KEY"):
                SportsGameOddsAdapter(api_key="bad", verbose=False).events("NFL", ["a-b-game-yn-yes"])

    def test_one_request_per_league(self):
        a = SportsGameOddsAdapter(market_types=["nfl_rush_yds", "nfl_first_td", "mlb_hr"])
        self.assertEqual(sorted(a._leagues()), ["MLB", "NFL"])
        self.assertEqual(len(a._leagues()["NFL"]), 2)


class EndToEndTests(unittest.TestCase):
    def test_kalshi_rung_priced_from_sgo_ladder(self):
        markets, quotes = parse_events([nfl_event()])
        kalshi = {"KXNFLRSHYDS-26NOV19INDHOU-INDJTAYLOR28-40":
                  MarketKey("nfl_rush_yds", "Jonathan Taylor", 39.5, "2026-11-19",
                            event_id="KXNFLRSHYDS-26NOV19INDHOU")}
        maps = match_markets(kalshi, markets, overrides={})
        self.assertEqual(maps[0].status, "exact")    # DK milestone exists at 39.5
        con = sqlite3.connect(":memory:")
        con.executescript(audit.SCHEMA)
        st = audit.run_audit_pass(maps, kalshi, quotes, StratConfig(), con,
                                  vendor_markets=markets)["nfl_rush_yds"]
        self.assertEqual((st["sharp"], st["modeled"], st["retail"]), (1, 1, 1))
        reason, = con.execute("SELECT reason FROM band_log").fetchone()
        self.assertIn("quote NO", reason)


if __name__ == "__main__":
    unittest.main()
