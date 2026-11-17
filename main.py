"""Kalshi one-way MM PoC — dry-run demo.

Run:  python main.py            (no keys needed, nothing is placed anywhere)
Demo orders:  set DRY_RUN=False and fill in KEY_ID / PRIVATE_KEY_PATH below
              (uses Kalshi's demo environment, not real money).
"""
from kalshi_client import KalshiClient
from mock_feed import CORNERS_LADDER, HR_PROPS
from pricing import consensus_fair, ladder_fair, sharp_spread
from quoting import Config, compute_quote, size_ladder

DRY_RUN = True
KEY_ID = ""            # your Kalshi API key id
PRIVATE_KEY_PATH = ""  # path to RSA private key .pem

cfg = Config(bankroll=50_000)
client = KalshiClient(KEY_ID, PRIVATE_KEY_PATH, dry_run=DRY_RUN)

print(f"Bankroll ${cfg.bankroll:,.0f} | deployable ${cfg.deployable:,.0f} | "
      f"event budget ${cfg.event_budget:,.0f} | max {cfg.max_concurrent_events} concurrent events\n")

# ---------- MLB HR props: one-way YES bids ----------
print("=" * 70)
print("MLB PLAYER HOME RUNS  (one-way: bidding YES)")
print("=" * 70)
line_budget = cfg.event_budget / cfg.lines_per_event_hr
for ticker, m in HR_PROPS.items():
    fair = consensus_fair(m["sharps"])
    q = compute_quote(fair, m["dk_fd"], side="yes", line_budget=line_budget, cfg=cfg,
                      sharp_spread=sharp_spread(m["sharps"]))
    print(f"\n{m['desc']}  [{ticker}]")
    print(f"  sharp consensus fair P(HR) = {fair:.3f}")
    print(f"  {q.reason}")
    if q.contracts > 0:
        print(f"  worst case loss ${q.contracts * q.price_cents / 100:,.0f} "
              f"(line budget ${line_budget:,.0f})")
        client.place_limit_order(ticker, q.side, q.price_cents, q.contracts)

# ---------- Corners ladder: one-way YES (over) bids, single risk bucket ----------
print("\n" + "=" * 70)
print(f"SOCCER CORNERS LADDER  ({CORNERS_LADDER['event']})  (one-way: bidding OVERs)")
print("=" * 70)
rungs = [(line, r["sharps"]) for line, r in CORNERS_LADDER["rungs"].items()]
fairs, flagged = ladder_fair(rungs)
if flagged:
    print("  ⚠ ladder had crossed/stale rungs — smoothed with isotonic regression")
print("  smoothed fair P(over):", "  ".join(f"{l}:{p:.3f}" for l, p in fairs))

dk_fd_by_line = {line: r["dk_fd"] for line, r in CORNERS_LADDER["rungs"].items()}
total_risk = 0.0
spreads = {line: sharp_spread(r["sharps"]) for line, r in CORNERS_LADDER["rungs"].items()}
for line, q in size_ladder(fairs, dk_fd_by_line, CORNERS_LADDER["side"], cfg, spreads):
    print(f"\n  Over {line}: {q.reason if q else 'no data'}")
    if q and q.contracts > 0:
        risk = q.contracts * q.price_cents / 100
        total_risk += risk
        client.place_limit_order(CORNERS_LADDER["ticker_fmt"].format(line=line),
                                 q.side, q.price_cents, q.contracts)
print(f"\n  Ladder bucket worst-case (all rungs lose together): ${total_risk:,.0f} "
      f"vs event budget ${cfg.event_budget:,.0f} "
      f"{'OK' if total_risk <= cfg.event_budget else 'BREACH — sizer bug!'}")
