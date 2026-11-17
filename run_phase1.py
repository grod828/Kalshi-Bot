"""Phase 1 entry point: map markets, audit coverage + band frequency.

  python run_phase1.py            one pass (mock data, no keys needed)
  python run_phase1.py --loop 300 pass every 300s (leave running for days)
  python run_phase1.py --report   print gate report from accumulated DB

With real keys: set KALSHI_ENV/KALSHI_KEY_ID/... env vars, implement your
vendor adapter, and set ODDS_VENDOR — this file doesn't change.
"""
from __future__ import annotations

import sys
import time

import config
from audit import gate_report, open_db, run_audit_pass
from mapping import match_markets, report
from quoting import Config as StratConfig
from vendor_adapter import MockKalshiMarkets, get_vendor_adapter


def kalshi_markets():
    """Swap MockKalshiMarkets for a real GET /markets parser once keys exist."""
    if config.KALSHI_KEY_ID:
        raise NotImplementedError(
            "Real Kalshi market discovery: parse GET /markets for series in "
            "config.KALSHI_SERIES into MarketKey objects (see MockKalshiMarkets).")
    return MockKalshiMarkets().fetch()


def one_pass(con) -> None:
    kalshi = kalshi_markets()
    vendor_markets, vendor_quotes = get_vendor_adapter(config.VENDOR).fetch()
    mappings = match_markets(kalshi, vendor_markets)
    print(report(mappings))
    stats = run_audit_pass(mappings, kalshi, vendor_quotes,
                           StratConfig(bankroll=50_000), con)
    for mtype, st in stats.items():
        print(f"  {mtype}: {st['mapped']}/{st['kalshi_lines']} mapped, "
              f"{st['sharp']} with sharp quotes, {st['retail']} with DK/FD quotes")


if __name__ == "__main__":
    con = open_db()
    if "--report" in sys.argv:
        print(gate_report(con))
    elif "--loop" in sys.argv:
        interval = int(sys.argv[sys.argv.index("--loop") + 1])
        while True:
            one_pass(con)
            print(f"-- sleeping {interval}s --\n")
            time.sleep(interval)
    else:
        one_pass(con)
        print()
        print(gate_report(con))
