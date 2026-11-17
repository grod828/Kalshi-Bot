# Kalshi One-Way MM — PoC + Phase 1 toolkit

Run with zero setup (pure stdlib, nothing placed anywhere):

```
python main.py           # pricing/sizing/quoting demo with dry-run orders
python run_phase1.py     # ticker mapping + coverage/band audit (one pass + gate report)
python run_phase1.py --loop 300   # audit pass every 5 min; leave running for days
python run_phase1.py --report     # gate report from accumulated SQLite data
```

Reviewing this for someone? Start with `FRIEND-REVIEW.md`.

What it does: devigs Pinnacle/Circa/BookMaker (mock feed), builds weighted consensus fair value, computes the one-way quote band (counterparty must beat DK/FD by ≥1¢ AND we keep ≥2¢ edge vs fair), sizes from the $50k bankroll ($1,000/event, corners ladder treated as one correlated bucket), and prints dry-run orders.

## Files

- `pricing.py` — devig (multiplicative + power), consensus, isotonic ladder smoothing
- `quoting.py` — quote band + bankroll sizing (`Config` holds all knobs)
- `mapping.py` — Kalshi↔vendor ticker matching: normalize → exact → fuzzy → overrides.csv; unmatched lines never trade
- `vendor_adapter.py` — vendor interface + mock; a real (Unabated/OpticOdds) adapter is one class with the same `.fetch()` contract
- `audit.py` / `run_phase1.py` — coverage + band-frequency study into SQLite, with pass/fail gate report
- `config.py` — keys, series tickers, books, thresholds (env-var overridable)
- `mock_feed.py` — mock sharp + DK/FD odds for `main.py`
- `kalshi_client.py` — RSA-PSS signed Kalshi client, demo env by default
- `main.py` — the demo
- `FRIEND-REVIEW.md` — review packet: decisions to critique + questions for experienced operators

## Going from dry-run to Kalshi demo orders

1. Create a demo account + API key at Kalshi's demo environment; download the RSA private key
2. `pip install cryptography requests`
3. In `main.py`: `DRY_RUN = False`, set `KEY_ID`, `PRIVATE_KEY_PATH`
4. Replace mock tickers with real ones from `client.get_markets(series_ticker=...)`

## Not in this PoC (phase 2)

Live odds vendor adapter, Kalshi WebSocket book/fill tracking, requote/pull loop, position reconciliation, kill switch, persistence, fee-aware edge floor.
