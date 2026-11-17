"""NFL yardage ladders: model fit, discovery, mapping, and audit pricing.
Run: python -m unittest -v"""
import math
import sqlite3
import unittest
from statistics import NormalDist

import audit
import config
from kalshi_markets import event_to_keys
from ladders import fit_ladder
from mapping import MarketKey, match_markets
from pricing import BookQuote, consensus_fair
from quoting import Config as StratConfig

N = NormalDist()
D = "2026-11-19"


def lognormal_points(median, s, lines):
    return [(L, 1 - N.cdf((math.log(L) - math.log(median)) / s)) for L in lines]


class FitTests(unittest.TestCase):
    def test_one_line_uses_assumed_spread(self):
        f = fit_ladder([(62.5, 0.5)], assumed_sigma=0.6)
        self.assertEqual((f.s, f.anchors), (0.6, (62.5,)))
        self.assertAlmostEqual(f.p_over(62.5), 0.5)
        self.assertIn("assumed", f.method)

    def test_several_lines_recover_the_distribution(self):
        f = fit_ladder(lognormal_points(55, 0.55, [44.5, 62.5, 74.5]), assumed_sigma=0.9)
        self.assertAlmostEqual(math.exp(f.m), 55, places=6)
        self.assertAlmostEqual(f.s, 0.55, places=6)
        self.assertIn("fit 3", f.method)

    def test_rungs_decrease_with_line(self):
        f = fit_ladder([(62.5, 0.5)], 0.6)
        ps = [f.p_over(L) for L in (9.5, 24.5, 39.5, 59.5, 79.5, 99.5)]
        self.assertEqual(ps, sorted(ps, reverse=True))

    def test_inconsistent_lines_fall_back_to_one_anchor(self):
        # higher line priced MORE likely: impossible -> negative slope rejected
        f = fit_ladder([(40.5, 0.4), (70.5, 0.6)], 0.6)
        self.assertIn("rejected", f.method)
        self.assertEqual(len(f.anchors), 1)

    def test_distance_in_model_sds(self):
        f = fit_ladder([(62.5, 0.5)], 0.6)
        self.assertEqual(f.distance_sd(62.5), 0)
        self.assertAlmostEqual(f.distance_sd(24.5), abs(math.log(24.5 / 62.5)) / 0.6)

    def test_unusable_points(self):
        self.assertIsNone(fit_ladder([], 0.6))
        self.assertIsNone(fit_ladder([(62.5, 0.999)], 0.6))


class OneSidedQuoteTests(unittest.TestCase):
    def test_consensus_skips_one_sided(self):
        qs = [BookQuote("pinnacle", -110, -110), BookQuote("circa", -500, None)]
        self.assertAlmostEqual(consensus_fair(qs), 0.5)

    def test_cannot_devig_one_sided(self):
        with self.assertRaises(ValueError):
            BookQuote("pinnacle", -500, None).fair_yes()


class DiscoveryTests(unittest.TestCase):
    def test_parses_real_yardage_market(self):
        event = {"event_ticker": "KXNFLRECYDS-26NOV19INDHOU",
                 "title": "Indianapolis vs Houston: Receiving Yards",
                 "markets": [{"ticker": "KXNFLRECYDS-26NOV19INDHOU-HOUNCOLLINS12-30",
                              "status": "active", "strike_type": "greater",
                              "floor_strike": 29.5, "title": "Nico Collins: 30+ receiving yards"}]}
        k = event_to_keys("nfl_rec_yds", event)["KXNFLRECYDS-26NOV19INDHOU-HOUNCOLLINS12-30"]
        self.assertEqual((k.entity, k.line, k.game_date, k.event_id),
                         ("Nico Collins", 29.5, D, "KXNFLRECYDS-26NOV19INDHOU"))


def rush(line, player="Jonathan Taylor", eid=""):
    return MarketKey("nfl_rush_yds", player, line, D, event_id=eid)


class LadderMappingTests(unittest.TestCase):
    vendor = {"v62": rush(62.5), "v40": rush(39.5)}

    def test_missing_line_maps_to_players_ladder(self):
        m, = match_markets({"K50": rush(49.5)}, self.vendor, overrides={})
        self.assertEqual((m.status, m.vendor_id), ("model", "v40"))   # nearest line: 10 vs 13 yds

    def test_exact_line_still_exact(self):
        m, = match_markets({"K40": rush(39.5)}, self.vendor, overrides={})
        self.assertEqual((m.status, m.vendor_id), ("exact", "v40"))

    def test_fuzzy_player_ignores_line(self):
        m, = match_markets({"K50": rush(49.5, "Jonathon Taylor")}, self.vendor, overrides={})
        self.assertEqual(m.status, "fuzzy")
        self.assertEqual(m.vendor_id, "v40")

    def test_other_player_unmatched(self):
        m, = match_markets({"K": rush(49.5, "Joe Mixon")}, self.vendor, overrides={})
        self.assertEqual(m.status, "unmatched")


class LadderAuditTests(unittest.TestCase):
    """Sharps quote 62.5 only; DK/FD sell yes-only milestones."""

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(audit.SCHEMA)
        self.vendor = {f"v{l}": rush(l) for l in (24.5, 39.5, 62.5)}
        self.quotes = {
            "v62.5": {"pinnacle": BookQuote("pinnacle", -110, -110),
                      "circa": BookQuote("circa", -110, -110)},
            "v39.5": {"draftkings": BookQuote("draftkings", -500, None)},
            "v24.5": {"draftkings": BookQuote("draftkings", -2000, None)},
        }
        self.kalshi = {f"K{l}": rush(l, eid="KXNFLRSHYDS-26NOV19INDHOU")
                       for l in (24.5, 39.5, 49.5, 62.5)}

    def run_pass(self):
        maps = match_markets(self.kalshi, self.vendor, overrides={})
        st = audit.run_audit_pass(maps, self.kalshi, self.quotes, StratConfig(), self.con,
                                  vendor_markets=self.vendor)
        rows = {t: (fair, q, reason) for t, fair, q, reason in self.con.execute(
            "SELECT kalshi_ticker, fair, quotable, reason FROM band_log")}
        return st["nfl_rush_yds"], rows

    def test_rung_priced_from_model_and_bid_on_no_side(self):
        st, rows = self.run_pass()
        fair, quotable, reason = rows["K39.5"]
        self.assertAlmostEqual(fair, 1 - N.cdf(math.log(39.5 / 62.5) / config.LADDER_SIGMA["nfl_rush_yds"]))
        self.assertEqual(quotable, 1)
        self.assertIn("quote NO", reason)

    def test_model_rung_needs_extra_edge(self):
        _, rows = self.run_pass()
        edge = float(rows["K39.5"][2].split("edge ")[1].split()[0])
        self.assertGreaterEqual(edge, StratConfig().min_edge + config.LADDER_MODEL_EXTRA_EDGE)

    def test_far_rung_skipped(self):
        st, rows = self.run_pass()
        self.assertNotIn("K24.5", rows)
        self.assertTrue(any("SD from nearest sharp line" in k for k in st["skipped"]))

    def test_rung_without_retail_not_priced(self):
        _, rows = self.run_pass()
        self.assertNotIn("K49.5", rows)       # sharp fair exists, but no DK/FD price there
        self.assertNotIn("K62.5", rows)

    def test_counts_modeled(self):
        st, _ = self.run_pass()
        self.assertEqual(st["mapped"], 4)
        self.assertEqual(st["modeled"], 2)    # 39.5 and 49.5 (62.5 is a sharp line)


if __name__ == "__main__":
    unittest.main()
