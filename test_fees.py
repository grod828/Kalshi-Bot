"""Fee math + fee-aware quoting. Run: python -m unittest -v"""
import unittest

from fees import WORST_CASE, FeeSchedule, schedule_from_api
from pricing import BookQuote
from quoting import Config, compute_quote

NO_MAKER_FEES = FeeSchedule("quadratic", 1.0, "series")
DK_FD = [BookQuote("draftkings", +215, -300), BookQuote("fanduel", +220, -310)]  # Ohtani


class FeeScheduleTests(unittest.TestCase):
    def test_taker_peaks_at_50c(self):
        self.assertAlmostEqual(NO_MAKER_FEES.per_contract(0.50, "taker"), 0.0175)
        self.assertLess(NO_MAKER_FEES.per_contract(0.20, "taker"), 0.0175)

    def test_makers_free_on_plain_quadratic(self):
        self.assertEqual(NO_MAKER_FEES.per_contract(0.50, "maker"), 0.0)

    def test_makers_pay_on_maker_fee_series(self):
        s = FeeSchedule("quadratic_with_maker_fees", 1.0)
        self.assertAlmostEqual(s.per_contract(0.50, "maker"), 0.004375)

    def test_multiplier_scales(self):
        self.assertAlmostEqual(FeeSchedule("quadratic", 0.5).per_contract(0.5, "taker"), 0.00875)

    def test_unknown_fee_type_charges_makers(self):
        self.assertGreater(FeeSchedule("something_new", 1.0).per_contract(0.5, "maker"), 0)

    def test_bad_role_rejected(self):
        with self.assertRaises(ValueError):
            NO_MAKER_FEES.rate("both")

    def test_order_fee_rounds_up_to_cent(self):
        self.assertEqual(NO_MAKER_FEES.order_fee(0.50, 1, "taker"), 0.02)     # 1.75c -> 2c
        self.assertEqual(NO_MAKER_FEES.order_fee(0.50, 100, "taker"), 1.75)   # exact
        self.assertEqual(NO_MAKER_FEES.order_fee(0.50, 1000, "maker"), 0.0)


class FromApiTests(unittest.TestCase):
    def test_series_values(self):
        s = schedule_from_api({"fee_type": "quadratic", "fee_multiplier": 0.5})
        self.assertEqual((s.fee_type, s.multiplier, s.source), ("quadratic", 0.5, "series"))

    def test_event_override_wins(self):
        # Seen live: KXMLBHR series is x0.5 but an Oct 2026 event overrode it to x1
        s = schedule_from_api({"fee_type": "quadratic", "fee_multiplier": 0.5},
                              {"fee_type_override": "quadratic", "fee_multiplier_override": 1})
        self.assertEqual((s.multiplier, s.source), (1.0, "event override"))

    def test_missing_falls_back_to_worst_case(self):
        self.assertIs(schedule_from_api({}), WORST_CASE)


class FeeAwareQuoteTests(unittest.TestCase):
    cfg = Config(bankroll=50_000)

    def test_no_maker_fee_series_unchanged(self):
        q = compute_quote(0.283, DK_FD, "yes", 250, self.cfg, fees=NO_MAKER_FEES)
        self.assertEqual((q.price_cents, q.fee), (26, 0.0))
        self.assertGreater(q.contracts, 0)

    def test_fee_can_close_the_band(self):
        # 2.3c gross edge; worst-case maker fee ~0.34c leaves < 2c -> no quote
        q = compute_quote(0.283, DK_FD, "yes", 250, self.cfg)   # cfg.fees = worst case
        self.assertEqual(q.contracts, 0)
        self.assertIn("after fees", q.reason)

    def test_edge_reported_net_and_size_includes_fee(self):
        q = compute_quote(0.32, DK_FD, "yes", 250, self.cfg)
        self.assertGreater(q.fee, 0)
        self.assertAlmostEqual(q.edge, 0.32 - q.price_cents / 100 - q.fee)
        self.assertGreaterEqual(q.edge, self.cfg.min_edge)
        self.assertLessEqual(q.contracts * (q.price_cents / 100 + q.fee), 250)

    def test_taker_role_costs_more(self):
        maker = compute_quote(0.32, DK_FD, "yes", 250, self.cfg)
        taker = compute_quote(0.32, DK_FD, "yes", 250, Config(fee_role="taker"))
        self.assertGreater(taker.fee, maker.fee)


if __name__ == "__main__":
    unittest.main()
