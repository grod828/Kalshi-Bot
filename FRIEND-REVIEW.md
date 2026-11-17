# Review Packet — Kalshi One-Way MM

*For review by people already running a profitable version of this. Everything runs
with `python main.py` and `python run_phase1.py` — zero keys, zero installs.*

## What this is

One-way maker on Kalshi sports lines (MLB player HRs, soccer corners ladders),
**pre-game only** — all quotes pulled at game start, nothing in-play.
Fair value = weighted no-vig consensus of Pinnacle/Circa/BookMaker. Quote only when
the counterparty beats DK/FD by ≥1¢ AND we keep ≥2¢ vs fair. $50k bankroll,
$1,000 worst-case per event, corners ladder = one correlated risk bucket.
Quoting window in `config.py`: open 12h before start, hard-pull at start, +1¢
min_edge during the lineup-risk window (last 4h).

## File map (read in this order)

| File | What it does | Key decisions to critique |
|---|---|---|
| `pricing.py` | Devig (mult + power), consensus, isotonic ladder smoothing | Book weights 50/30/20; 120s staleness; multiplicative default |
| `quoting.py` | Quote band + sizing | min_edge 2¢, improvement 1¢, quote at bottom of band, 2%/event |
| `mapping.py` | Kalshi↔vendor ticker matching | 0.82 fuzzy threshold; unmatched = never trade |
| `audit.py` + `run_phase1.py` | Coverage + band-frequency study, SQLite | 30% quotable gate; ≥20 events/day gate |
| `kalshi_client.py` | RSA-PSS signing, demo env, dry-run | — |
| `vendor_adapter.py` | Vendor interface + mock | The contract a real adapter must satisfy |
| `main.py` | End-to-end demo | — |

## Specific questions for you

**Data / vendor**
1. Which odds feed do you use, and what's its real-world latency on prop/corners
   moves? Is Unabated's API good enough, or did you need OpticOdds-class speed?
2. Do you get Circa and BookMaker from the same feed, or approximate with fewer books?
3. What staleness cutoff do you run? Is 120s naive for props?

**Pricing / quoting**
4. Multiplicative vs power devig on HR props (15–35% range) — does it matter in practice?
5. Our band: [1 − DK/FD_price + 1¢, fair − 2¢], quote at the bottom. Do you get
   filled at max-edge prices, or did you have to quote closer to fair to see flow?
6. Is "beat DK/FD by 1¢" the right attractiveness anchor, or does Kalshi flow
   not actually price-shop against sportsbooks?
7. Ladder: we isotonic-smooth crossed rungs and size the whole ladder as one
   bucket weighted toward the 50% rung. How do you handle rung correlation?

**Execution (pre-game only)**
8. What rate tier do you run, and does Basic (10 writes/s) survive the pull-everything
   moment when a full slate starts within the same hour?
9. Requote trigger: we plan pull-on-1-tick-drift. Pre-game, is that too twitchy?
10. Pre-game adverse selection = mostly lineup/injury news. MLB lineups drop 2–4h
    before first pitch — do you pause HR quotes until lineups confirm, widen edge
    in that window (we default +1¢), or just eat it?
11. How early do you start quoting? We default 12h pre-start — is early resting
    liquidity picked off overnight, or is it free flow?
12. Maker fees per series — which series were quietly unprofitable after fees?

**Risk / sizing**
13. 2% of bankroll worst-case per event, 40 concurrent — sane or aggressive?
    (Pre-game-only means capital locks up from fill until settlement — did
    settlement lag ever constrain your deployable bankroll?)
14. What's your kill-switch list? Ours: feed silence >60s, reconciliation
    mismatch, error streak, 15% bankroll drawdown, and hard-pull at game start.
15. Anything that blew up on you that isn't on this list?

**If they'll share:** fill logs or markout stats — even a week's worth recalibrates
every parameter above better than months of our own trial and error.

## How to plug in their feedback

Every number they'd want to change is in exactly one place:
`quoting.Config` (edges, sizing), `config.py` (books, weights via
`pricing.DEFAULT_WEIGHTS`, staleness, fuzzy threshold, series tickers).
Vendor swap = one new class in `vendor_adapter.py`. No strategy code changes.
