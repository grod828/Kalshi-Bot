"""NFL first TD scorer: field devig, discovery, within-game matching, audit.
Run: python -m unittest -v"""
import sqlite3
import unittest

import audit
import config
from kalshi_markets import event_to_keys
from mapping import MarketKey, match_markets, norm_entity
from pricing import BookQuote, american_to_prob, devig_field, field_consensus
from quoting import Config as StratConfig

D = "2026-11-19"
FIELD = {"fav": 0.20, "mid": 0.15, "s1": 0.10, "s2": 0.10, "s3": 0.10,
         "l1": 0.08, "l2": 0.08, "l3": 0.08, "l4": 0.15, "notd": 0.26}   # sums to 1.30


class DevigFieldTests(unittest.TestCase):
    def test_both_methods_sum_to_one(self):
        for method in ("multiplicative", "power"):
            self.assertAlmostEqual(sum(devig_field(FIELD, method).values()), 1.0, places=9)

    def test_power_takes_more_vig_off_longshots(self):
        mult, power = devig_field(FIELD, "multiplicative"), devig_field(FIELD, "power")
        self.assertLess(power["l1"], mult["l1"])
        self.assertGreater(power["fav"], mult["fav"])

    def test_field_under_one_is_just_normalized(self):
        out = devig_field({"a": 0.3, "b": 0.3}, "power")
        self.assertAlmostEqual(out["a"], 0.5)

    def test_empty(self):
        self.assertEqual(devig_field({}), {})


def yes(book, odds, ts=None):
    q = BookQuote(book, odds, None)
    if ts is not None:
        q.ts = ts
    return q


class FieldConsensusTests(unittest.TestCase):
    now = 1_000_000.0

    def field(self, books):
        """books: {book: {outcome: american}} -> outcome -> book -> quote"""
        out = {}
        for book, odds in books.items():
            for o, a in odds.items():
                out.setdefault(o, {})[book] = yes(book, a, self.now)
        return out

    def test_two_books_average_and_spread(self):
        f = self.field({"pinnacle": {"a": 150, "b": 150, "c": 300},
                        "circa": {"a": 120, "b": 180, "c": 300}})
        out = field_consensus(f, now=self.now, method="multiplicative")
        self.assertAlmostEqual(sum(fair for fair, _ in out.values()), 1.0, places=9)
        self.assertGreater(out["a"][1], 0)          # books disagree on a
        self.assertIsNotNone(out["c"][1])

    def test_incomplete_book_skipped(self):
        # circa lists only two longshots: sums to ~0.2, would inflate them 5x
        f = self.field({"pinnacle": {"a": 150, "b": 150, "c": 300},
                        "circa": {"b": 900, "c": 900}})
        out = field_consensus(f, now=self.now)
        self.assertIsNone(out["b"][1])              # only pinnacle counted

    def test_stale_and_retail_ignored(self):
        f = self.field({"pinnacle": {"a": 150, "b": 150, "c": 300},
                        "draftkings": {"a": 100, "b": 100, "c": 200}})
        f["a"]["circa"] = yes("circa", 100, self.now - 999)
        out = field_consensus(f, now=self.now)
        self.assertTrue(all(spread is None for _, spread in out.values()))


class DiscoveryAndNamesTests(unittest.TestCase):
    def test_parses_first_td_event(self):
        ev = {"event_ticker": "KXNFLFIRSTTD-26NOV19INDHOU",
              "title": "Indianapolis vs Houston: 1st Touchdown",
              "markets": [{"ticker": t, "status": "active", "strike_type": "structured",
                           "title": f"{name}: 1st Touchdown"}
                          for t, name in (("A", "Nico Collins"), ("B", "HOU Texans D/ST"),
                                          ("C", "No Touchdown"))]}
        keys = event_to_keys("nfl_first_td", ev)
        self.assertEqual({k.entity for k in keys.values()},
                         {"Nico Collins", "HOU Texans D/ST", "No Touchdown"})
        self.assertTrue(all(k.line is None and k.game_date == D for k in keys.values()))

    def test_outcome_names(self):
        same = [("HOU Texans D/ST", "Houston Texans Defense"),
                ("IND Colts D/ST", "Indianapolis Colts Defense/Special Teams"),
                ("No Touchdown", "No TD Scorer"), ("C.J. Stroud", "CJ Stroud")]
        for a, b in same:
            self.assertEqual(norm_entity(a), norm_entity(b), (a, b))
        self.assertNotEqual(norm_entity("HOU Texans D/ST"), norm_entity("IND Colts D/ST"))


def td(name, eid):
    return MarketKey("nfl_first_td", name, None, D, event_id=eid)


class WithinGameMatchTests(unittest.TestCase):
    """Two games on one date: 'No Touchdown' is the same key in both."""
    kalshi = {"K1-COLLINS": td("Nico Collins", "KG1"), "K1-NONE": td("No Touchdown", "KG1"),
              "K2-ALLEN": td("Josh Allen", "KG2"), "K2-NONE": td("No Touchdown", "KG2")}
    vendor = {"v1": td("Nico Collins", "VG1"), "v1n": td("No TD Scorer", "VG1"),
              "v2": td("Josh Allen", "VG2"), "v2n": td("No TD Scorer", "VG2")}

    def test_no_td_matched_inside_its_game(self):
        m = {x.kalshi_ticker: x for x in match_markets(self.kalshi, self.vendor, overrides={})}
        self.assertEqual((m["K1-NONE"].status, m["K1-NONE"].vendor_id), ("exact", "v1n"))
        self.assertEqual(m["K2-NONE"].vendor_id, "v2n")
        self.assertIn("within its game", m["K1-NONE"].note)

    def test_without_sibling_matches_it_stays_unmatched(self):
        kalshi = {"K1-NONE": td("No Touchdown", "KG1"), "K2-NONE": td("No Touchdown", "KG2")}
        out = match_markets(kalshi, self.vendor, overrides={})
        self.assertTrue(all(m.status == "unmatched" for m in out))

    def test_siblings_pointing_at_two_vendor_games_blocks_it(self):
        kalshi = {"K1-COLLINS": td("Nico Collins", "KG1"), "K1-ALLEN": td("Josh Allen", "KG1"),
                  "K1-NONE": td("No Touchdown", "KG1"), "K2-NONE": td("No Touchdown", "KG2")}
        m = {x.kalshi_ticker: x for x in match_markets(kalshi, self.vendor, overrides={})}
        self.assertEqual(m["K1-NONE"].status, "unmatched")
        self.assertIn("2 vendor games", m["K1-NONE"].note)


class FirstTdAuditTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(audit.SCHEMA)
        names = {"fav": (450, 350), "mid": (600, 500), "l1": (1500, 1000), "l2": (1500, 1000),
                 "l3": (1500, 1000), "notd": (3000, 2500), "x1": (200, 150), "x2": (250, 200)}   # sharps sum ~1.16
        self.vendor = {f"v-{n}": td(n, "VG") for n in names}
        self.quotes = {f"v-{n}": {"pinnacle": yes("pinnacle", s), "circa": yes("circa", s),
                                  "draftkings": yes("draftkings", dk)}
                       for n, (s, dk) in names.items()}
        self.kalshi = {f"K-{n}": td(n, "KXNFLFIRSTTD-26NOV19INDHOU")
                       for n in ("fav", "mid", "l1", "notd")}

    def run_pass(self):
        maps = match_markets(self.kalshi, self.vendor, overrides={})
        st = audit.run_audit_pass(maps, self.kalshi, self.quotes, StratConfig(), self.con,
                                  vendor_markets=self.vendor)
        rows = {t: (fair, n, reason) for t, fair, n, reason in self.con.execute(
            "SELECT kalshi_ticker, fair, would_quote_contracts, reason FROM band_log")}
        return st["nfl_first_td"], rows

    def test_fair_comes_from_the_whole_devigged_field(self):
        _, rows = self.run_pass()
        implied = {n: american_to_prob(s) for n, s in
                   (("fav", 450), ("mid", 600), ("l1", 1500), ("l2", 1500), ("l3", 1500),
                    ("notd", 3000), ("x1", 200), ("x2", 250))}
        want = devig_field(implied, config.FIELD_DEVIG)["fav"]
        self.assertAlmostEqual(rows["K-fav"][0], want)

    def test_bids_no_and_worst_case_fits_outcome_budget(self):
        _, rows = self.run_pass()
        fair, contracts, reason = rows["K-fav"]
        self.assertIn("quote NO", reason)
        price = int(reason.split("NO ")[1].split("c")[0]) / 100
        budget = StratConfig().event_budget * config.FIELD_OUTCOME_BUDGET_FRAC
        self.assertLessEqual(contracts * price, budget)
        self.assertGreater(contracts * price, budget * 0.95)

    def test_incomplete_sharp_field_not_priced(self):
        for vid in list(self.quotes):           # sharps list only 2 outcomes
            if vid not in ("v-l1", "v-l2"):
                self.quotes[vid] = {"draftkings": self.quotes[vid]["draftkings"]}
        st, rows = self.run_pass()
        self.assertEqual(rows, {})
        self.assertTrue(any("no sharp field price" in k for k in st["skipped"]))


if __name__ == "__main__":
    unittest.main()
