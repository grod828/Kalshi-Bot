"""Phase 1 entry point: map markets, audit coverage + band frequency.

  python run_phase1.py            one pass (mock data, no keys needed)
  python run_phase1.py --live     same, but real Kalshi markets (public API, no key)
  python run_phase1.py --loop 300 pass every 300s (leave running for days)
  python run_phase1.py --report   print gate report from accumulated DB

--live is the same as KALSHI_MARKETS=live and writes to phase1_audit_live.sqlite
(add --live to --report/--loop too). The odds side stays on the mock vendor
until you implement an adapter and set ODDS_VENDOR — this file doesn't change.
"""
from __future__ import annotations

import os
import sys
import time

if "--live" in sys.argv:          # must happen before config is imported
    os.environ["KALSHI_MARKETS"] = "live"

import config
from audit import gate_report, open_db, run_audit_pass
from mapping import match_markets, report
from quoting import Config as StratConfig
from vendor_adapter import MockKalshiMarkets, get_vendor_adapter


def kalshi_markets():
    """Kalshi side of the mapping: fixture, or live public market data.
    Returns (markets, fees_by_ticker); mock markets get no fee info, so the
    audit assumes worst-case fees for them."""
    if config.KALSHI_MARKETS == "live":
        from kalshi_markets import LiveKalshiMarkets
        print(f"Kalshi markets: LIVE ({config.KALSHI_DATA_ENV}), "
              f"odds vendor: {config.VENDOR}  -> {config.AUDIT_DB}")
        live = LiveKalshiMarkets()
        return live.fetch(), live.fees
    return MockKalshiMarkets().fetch(), {}


def one_pass(con) -> None:
    kalshi, fees = kalshi_markets()
    vendor_markets, vendor_quotes = get_vendor_adapter(config.VENDOR).fetch()
    mappings = match_markets(kalshi, vendor_markets)
    print(report(mappings))
    stats = run_audit_pass(mappings, kalshi, vendor_quotes,
                           StratConfig(bankroll=50_000), con, fees_by_ticker=fees)
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
