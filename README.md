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
- With the default mock odds vendor every live line shows UNMATCHED. Use the real
  feed for a meaningful run: `ODDS_VENDOR=sportsgameodds python run_phase1.py --live`.

### Odds vendor: SportsGameOdds (`sgo_adapter.py`)

```
export ODDS_VENDOR=sportsgameodds
export ODDS_VENDOR_KEY=...        # from sportsgameodds.com (free plan available)
python run_phase1.py --live
```

- One `GET /v2/events` per league asks for every market we need at once. SGO bills
  **per event returned**, not per market or book, so bundling is free.
- Odd IDs used: `batting_homeRuns-PLAYER_ID-game-yn`, `rushing_yards-` /
  `receiving_yards-PLAYER_ID-game-ou` (with alternate lines),
  `firstTouchdown-PLAYER_ID-game-yn`, and `cornerKicks-all-game-ou`.
- SGO's `bookmakereu` is our `bookmaker`. Retail alternate lines with only an over
  price become yes-only milestones. Start times are converted to Kalshi's Eastern date.
- **Coverage, per SGO's market table (Nov 2026):**

| Market | Sharp books at SGO | DK/FD | Can be priced? |
|---|---|---|---|
| NFL rushing/receiving yards | Pinnacle, Circa, BookMaker | yes | yes |
| MLB player HR | Circa, Pinnacle | yes | yes |
| NFL first TD | Circa only | yes | yes, one sharp book (no D/ST or "no TD" outcomes) |
| Soccer total corners | **none** | yes | **no**: needs another sharp source |

- Quote timestamps are the fetch time. Whether SGO's `lastUpdatedAt` means "price
  changed" or "price confirmed" still needs checking on live data before the 120s
  staleness filter relies on it.

### Phase 1 gate (per sport)

Each sport (`config.SPORT_OF`) is judged on its own, and is ready to trade only if
both parts pass. Thresholds are in `config.py`, and the defaults are **placeholders**:

| Sport | Part 1: mappable games | Why that period |
|---|---|---|
| MLB | ≥ 10 per **day** | ~15 games a day in season |
| NFL | ≥ 10 per **week** | ~14–16 games a week, almost all on Sunday |
| Soccer | ≥ 20 per **week** | EPL + La Liga ≈ 20 games a round |

Part 2, for every sport: the quote band is non-empty on ≥ 30% of priced lines.

- Weeks run Tuesday–Monday (one NFL week, one soccer round).
- A game counts once per period, however many passes or series saw it. The NFL
  rushing and receiving markets for the same game are one game.
- The mock fixture is tiny, so `python run_phase1.py` reads FAIL for every sport by design.

### NFL yardage ladders (`ladders.py`)

Kalshi lists about 10 rungs per player (`KXNFLRSHYDS`, `KXNFLRECYDS`: "Jonathan Taylor:
40+ rushing yards", from 10+ to 80+). Sharp books usually quote one main O/U line.

- **Pricing:** every rung is read off a lognormal fitted to the player's sharp lines.
  With 2+ lines both parameters are fitted; with one, the spread is an assumed value
  per stat (`LADDER_SIGMA`: **placeholders to calibrate**).
- **Guards:** model-priced rungs need +1¢ edge (`LADDER_MODEL_EXTRA_EDGE`), and rungs
  more than 1 model SD from a sharp line aren't quoted (`LADDER_MAX_MODEL_SD`).
  Lognormal has no mass at 0 yards, so very low rungs are the least trustworthy.
- **Side:** we bid **NO**, selling the milestone overs retail buys at DK/FD
  (`QUOTE_SIDE`). A rung is priced only if DK/FD offer that milestone, and their
  yes-only quotes are fine (`BookQuote(..., no_american=None)`).
- **Matching:** a Kalshi rung with no vendor market at that exact line maps to the
  player's ladder (status `model`).
- **Fees:** both series are plain `quadratic`, so resting orders pay nothing.

### NFL first TD scorer (`KXNFLFIRSTTD`)

One Kalshi market per outcome: about 20 players, both D/STs ("HOU Texans D/ST"), and
"No Touchdown". Kalshi lists only likely scorers; if an unlisted player scores first,
every listed market resolves No.

- **Pricing:** books quote first TD yes-only, as a list. Each sharp book's whole field
  for the game is devigged (`pricing.devig_field`: power method by default, which
  takes more vig off longshots), then averaged across books. A book whose field sums
  to less than 1.05 is skipped, since it's missing outcomes. The vendor must group a
  game's outcomes with `MarketKey.event_id` and should include players Kalshi doesn't list.
- **Side and risk:** we bid **NO** on each outcome (selling the longshots retail buys
  at DK/FD). Only the first scorer's NO can lose, so a game's worst case is its single
  largest NO position. Each outcome may use `FIELD_OUTCOME_BUDGET_FRAC` (50%) of the
  event budget, which leaves room for inactive players settling at Kalshi's "fair
  market price".
- **Matching:** D/STs normalize to the team nickname ("Houston Texans Defense" →
  `dst texans`), initials match ("C.J." = "CJ"), and "No Touchdown" (present in every
  game) is matched inside its game via the game's other mapped outcomes.
- **Fees:** this series **charges maker fees** (`quadratic_with_maker_fees`). At typical
  NO prices of 85–95¢ that's about 0.1–0.25¢ a contract, already netted out.

### Matching (`mapping.py`, `team_aliases.py`)

- Matchups are split into teams. Each side is normalized (accents, punctuation, club
  tokens like FC/AS/Calcio, and numbers like "05" are dropped) and then aliased, and
  home/away order is ignored. So Kalshi's "Bilbao vs Espanyol" matches "Athletic Club
  v RCD Espanyol". Add aliases to `team_aliases.py` when real mismatches show up.
- **Dates:** Kalshi files games under their **US Eastern** date. A vendor adapter must
  set `game_date = mapping.kalshi_game_date(start_utc)`, not the UTC date. If it
  doesn't, the UNMATCHED list says "vendor dates in UTC?".
- **Ambiguity:** if two markets normalize to the same key (an MLB doubleheader), both
  stay UNMATCHED rather than risk the wrong game. Resolve them with `overrides.csv`.

Reviewing this for someone? Start with `FRIEND-REVIEW.md`.

What it does: devigs Pinnacle/Circa/BookMaker (mock feed), builds weighted consensus fair value, computes the one-way quote band (counterparty must beat DK/FD by ≥1¢ AND we keep ≥2¢ edge vs fair), sizes from the $50k bankroll ($1,000/event, corners ladder treated as one correlated bucket), and prints dry-run orders.

## Files

- `pricing.py` — devig (multiplicative + power), consensus, isotonic ladder smoothing
- `ladders.py` — NFL yardage ladders: lognormal fitted to the sharp lines prices every Kalshi rung
- `pricing.py` also devigs one-sided multi-outcome fields (`devig_field`, `field_consensus`) for first TD scorer
- `fees.py` — Kalshi fee math; edges and sizing are measured **after** fees (see below)
- `quoting.py` — quote band + bankroll sizing + outlier guards: no quote if edge > 8¢ (`max_edge`) or the sharps disagree by > 4¢ (`max_sharp_spread`); `Config` holds all knobs
- `mapping.py` — Kalshi↔vendor ticker matching: normalize → exact → fuzzy → overrides.csv; unmatched and ambiguous lines never trade
- `team_aliases.py` — team-name aliases (Kalshi's short names ↔ common sportsbook names)
- `vendor_adapter.py` — vendor interface + mock; `ODDS_VENDOR` picks the adapter
- `sgo_adapter.py` — SportsGameOdds adapter (real odds feed)
- `audit.py` / `run_phase1.py` — coverage + band-frequency study into SQLite, with a two-part pass/fail gate report (events/day + quotable)
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
  case: maker fees on, ×1. That's why the mock reads 62% quotable, not 88%. 2 of 3 mock HR
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

Kalshi WebSocket book/fill tracking, requote/pull loop, position reconciliation, kill switch, persistence, and a check that a quote won't cross Kalshi's book (which would make it a taker and pay the taker fee).
