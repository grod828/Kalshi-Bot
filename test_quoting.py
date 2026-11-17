"""Tests for the outlier guards (max edge, sharp disagreement).
Run: python -m unittest -v"""
import unittest

from pricing import BookQuote, consensus_fair, sharp_spread
from quoting import Config, compute_quote, size_ladder

CFG = Config(bankroll=50_000)

# Retail NO at -260 (~72.2c) -> YES bid must be >= 29c to beat DK/FD by 1c
DK_FD = [BookQuote("draftkings", +185, -260), BookQuote("fanduel", +190, -265)]


class SharpSpreadTests(unittest.TestCase):
    def test_spread_of_fresh_sharps(self):
        qs = [BookQuote("pinnacle", +205, -245), BookQuote("circa", +150, -170)]
        expected = qs[1].fair_yes() - qs[0].fair_yes()
        self.assertAlmostEqual(sharp_spread(qs), expected)

    def test_ignores_stale_and_non_sharp_books(self):
        now = 1_000_000.0
        qs = [BookQuote("pinnacle", +205, -245, ts=now),
              BookQuote("circa", +150, -170, ts=now - 500),   # stale
              BookQuote("draftkings", +100, -120, ts=now),    # not a sharp
              BookQuote("bookmaker", +200, -240, ts=now)]
        spread = sharp_spread(qs, max_age_s=120, now=now)
        self.assertAlmostEqual(spread, abs(qs[0].fair_yes() - qs[3].fair_yes()))

    def test_needs_two_books(self):
        self.assertIsNone(sharp_spread([BookQuote("pinnacle", +205, -245)]))


class GuardTests(unittest.TestCase):
    def test_normal_edge_quotes(self):
        q = compute_quote(0.32, DK_FD, "yes", 250, CFG, sharp_spread=0.01)
        self.assertGreater(q.contracts, 0)
        self.assertLessEqual(q.edge + q.fee, CFG.max_edge)

    def test_outlier_edge_refused(self):
        # fair 45% but DK/FD lets us bid 29c -> ~16c "edge": too good to be true
        q = compute_quote(0.45, DK_FD, "yes", 250, CFG)
        self.assertEqual(q.contracts, 0)
        self.assertIn("OUTLIER", q.reason)
        self.assertGreater(q.edge + q.fee, CFG.max_edge)

    def test_edge_exactly_at_cap_still_quotes(self):
        q = compute_quote(0.37, DK_FD, "yes", 250, CFG)   # bid 29c, edge 8c
        self.assertAlmostEqual(q.edge + q.fee, 0.08)       # cap is on the pre-fee edge
        self.assertGreater(q.contracts, 0)

    def test_sharp_disagreement_refused(self):
        q = compute_quote(0.32, DK_FD, "yes", 250, CFG, sharp_spread=0.06)
        self.assertEqual(q.contracts, 0)
        self.assertIn("disagree", q.reason)

    def test_no_spread_info_does_not_block(self):
        q = compute_quote(0.32, DK_FD, "yes", 250, CFG, sharp_spread=None)
        self.assertGreater(q.contracts, 0)

    def test_ladder_passes_spread_per_rung(self):
        fairs = [(8.5, 0.32), (9.5, 0.32)]
        out = dict(size_ladder(fairs, {8.5: DK_FD, 9.5: DK_FD}, "yes", CFG,
                               {8.5: 0.01, 9.5: 0.10}))
        self.assertGreater(out[8.5].contracts, 0)
        self.assertEqual(out[9.5].contracts, 0)

    def test_consensus_unchanged(self):
        qs = [BookQuote("pinnacle", +205, -245), BookQuote("circa", +210, -250)]
        self.assertIsNotNone(consensus_fair(qs))


if __name__ == "__main__":
    unittest.main()
