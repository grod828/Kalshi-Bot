# Kalshi One-Way MM — PoC + Phase 1 toolkit

Run with zero setup (pure stdlib, nothing placed anywhere):

```
python main.py           # pricing/sizing/quoting demo with dry-run orders
python run_phase1.py     # ticker mapping + coverage/band audit (one pass + gate report)
python run_phase1.py --loop 300   # audit pass every 5 min; leave running for days
python run_phase1.py --report     # gate report from accumulated SQLite data
python run_phase1.py --live       # same audit against REAL Kalshi markets (public API, no key)
python kalshi_markets.py          # just list what Kalshi has open right now
python -m unittest -v             # all tests (offline): live-market parser + quoting guards
```

### Live Kalshi markets (`--live`)

Reads open events for the series in `config.KALSHI_SERIES` from Kalshi's public
market-data API (prod by default, `KALSHI_DATA_ENV=demo` to switch). No key, no
orders. Results go to `phase1_audit_live.sqlite`, kept apart from mock runs; pass
`--live` to `--report` / `--loop` too.

- Corners: Kalshi lists "9+ total corners", which is stored as line 8.5 so it
  maps to a sportsbook "Over 8.5".
- MLB HR: only the 1+ HR prop is kept (2+ markets skipped). `KXMLBHR` has no
  open events in the offseason.
- The odds side is still the mock vendor, so every live line shows UNMATCHED
  and the gate report gives no verdict until a real vendor adapter exists.

Reviewing this for someone? Start with `FRIEND-REVIEW.md`.

What it does: devigs Pinnacle/Circa/BookMaker (mock feed), builds weighted consensus fair value, computes the one-way quote band (counterparty must beat DK/FD by ≥1¢ AND we keep ≥2¢ edge vs fair), sizes from the $50k bankroll ($1,000/event, corners ladder treated as one correlated bucket), and prints dry-run orders.

## Files

- `pricing.py` — devig (multiplicative + power), consensus, isotonic ladder smoothing
- `fees.py` — Kalshi fee math; edges and sizing are measured **after** fees (see below)
- `quoting.py` — quote band + bankroll sizing + outlier guards: no quote if edge > 8¢ (`max_edge`) or the sharps disagree by > 4¢ (`max_sharp_spread`); `Config` holds all knobs
- `mapping.py` — Kalshi↔vendor ticker matching: normalize → exact → fuzzy → overrides.csv; unmatched lines never trade
- `vendor_adapter.py` — vendor interface + mock; a real (Unabated/OpticOdds) adapter is one class with the same `.fetch()` contract
- `audit.py` / `run_phase1.py` — coverage + band-frequency study into SQLite, with pass/fail gate report
- `config.py` — keys, series tickers, books, thresholds (env-var overridable)
- `mock_feed.py` — mock sharp + DK/FD odds for `main.py`
- `kalshi_client.py` — RSA-PSS signed Kalshi client, demo env by default
- `kalshi_markets.py` — live Kalshi market discovery (public, read-only) → `MarketKey`s
- `main.py` — the demo
- `FRIEND-REVIEW.md` — review packet: decisions to critique + questions for experienced operators

## Fees

Kalshi charges `rate × multiplier × P × (1 − P)` per contract, rounded up to the cent
per order. Takers pay 0.07; makers pay 0.0175, but **only on series whose fee type is
`quadratic_with_maker_fees`**. Plain `quadratic` series charge resting orders nothing.

- `min_edge` (2¢) is enforced after the fee, and per-contract worst-case loss = price + fee.
- `--live` reads each series' `fee_type` / `fee_multiplier` and applies event overrides
  (seen live: a `KXMLBHR` event overrode the series' ×0.5 to ×1).
- Without real fee data (the mock, `main.py`, a failed lookup) the bot assumes the worst
  case: maker fees on, ×1. That's why the mock gate reads 62%, not 88%. 2 of 3 mock HR
  props don't clear 2¢ once a maker fee is charged.
- As of Nov 2026 every series in `config.KALSHI_SERIES` is plain `quadratic`, so resting
  quotes pay no fee. Rates are from public fee guides; Kalshi's fee schedule is the
  source of truth.

## Going from dry-run to Kalshi demo orders

1. Create a demo account + API key at Kalshi's demo environment; download the RSA private key
2. `pip install cryptography requests`
3. In `main.py`: `DRY_RUN = False`, set `KEY_ID`, `PRIVATE_KEY_PATH`
4. Replace mock tickers with real ones from `client.get_markets(series_ticker=...)`

## Not in this PoC (phase 2)

Live odds vendor adapter, Kalshi WebSocket book/fill tracking, requote/pull loop, position reconciliation, kill switch, persistence, and a check that a quote won't cross Kalshi's book (which would make it a taker and pay the taker fee).
