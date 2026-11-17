"""Phase 1 audit: coverage stats + band-frequency study, persisted to SQLite.

Run this on a cron/loop for 3-4 days. It answers the Phase 1 gate:
  - how many Kalshi lines exist and how many map cleanly to the vendor?
  - on mapped lines, how often is the quote band non-empty (i.e. tradeable)?
No orders are placed. Would-be quotes are logged so you can later check them
against settlement (the poor man's markout).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import sqlite3
import time
from collections import defaultdict

import config
from ladders import fit_ladder
from mapping import Mapping, MarketKey, ladder_group
from pricing import BookQuote, consensus_fair, sharp_spread
from quoting import Config as StratConfig
from quoting import compute_quote

SCHEMA = """
CREATE TABLE IF NOT EXISTS coverage (
  ts REAL, market_type TEXT, kalshi_lines INT, mapped INT, fuzzy INT,
  unmatched INT, with_sharp_quotes INT, with_retail_quotes INT
);
CREATE TABLE IF NOT EXISTS event_log (
  ts REAL, market_type TEXT, event_id TEXT, game_date TEXT,
  kalshi_lines INT, mapped INT, quotable INT
);
CREATE TABLE IF NOT EXISTS band_log (
  ts REAL, kalshi_ticker TEXT, vendor_id TEXT, market_type TEXT,
  fair REAL, band_lo INT, band_hi INT, quotable INT,
  would_quote_price INT, would_quote_contracts INT, reason TEXT
);
"""


def open_db(path: str = "") -> sqlite3.Connection:
    con = sqlite3.connect(path or config.AUDIT_DB)
    con.executescript(SCHEMA)
    return con


def _split_books(books: dict[str, BookQuote]) -> tuple[list[BookQuote], list[BookQuote]]:
    return ([q for b, q in books.items() if b in config.SHARP_BOOKS],
            [q for b, q in books.items() if b in config.RETAIL_BOOKS])


def _ladder_inputs(kmk: MarketKey, vendor_id: str, vendor_markets: dict,
                   vendor_quotes: dict, cache: dict):
    """Fair value for one ladder rung from the player's whole sharp ladder.
    Returns (fair, sharps_for_spread_check, retail_at_rung, extra_edge, skip_reason)."""
    vmk = vendor_markets.get(vendor_id)
    if vmk is None:
        return None, [], [], 0.0, "vendor market missing"
    grp = ladder_group(vmk)
    if grp not in cache:
        points, sharps_at, retail_at = [], {}, {}
        for vid, mk in vendor_markets.items():
            if mk.market_type != vmk.market_type or ladder_group(mk) != grp:
                continue
            sharps, retail = _split_books(vendor_quotes.get(vid, {}))
            p = consensus_fair(sharps, max_age_s=config.MAX_QUOTE_AGE_S) if sharps else None
            if p is not None:
                points.append((mk.line, p))
                sharps_at[mk.line] = sharps
            if retail:
                retail_at[mk.line] = retail
        fit = fit_ladder(points, config.LADDER_SIGMA.get(vmk.market_type, 0.7))
        cache[grp] = (fit, sharps_at, retail_at)
    fit, sharps_at, retail_at = cache[grp]
    retail = retail_at.get(kmk.line, [])
    if kmk.line in sharps_at:                       # sharps quote this exact rung
        sh = sharps_at[kmk.line]
        return consensus_fair(sh, max_age_s=config.MAX_QUOTE_AGE_S), sh, retail, 0.0, ""
    if fit is None:
        return None, [], retail, 0.0, "no fresh sharp line for this player"
    dist = fit.distance_sd(kmk.line)
    if dist > config.LADDER_MAX_MODEL_SD:
        return None, [], retail, 0.0, (f"rung {dist:.1f} SD from nearest sharp line "
                                       f"(max {config.LADDER_MAX_MODEL_SD})")
    nearest = min(fit.anchors, key=lambda a: abs(a - kmk.line))
    return (fit.p_over(kmk.line), sharps_at.get(nearest, []), retail,
            config.LADDER_MODEL_EXTRA_EDGE, "")


def run_audit_pass(
    mappings: list[Mapping],
    kalshi_markets: dict,                       # ticker -> MarketKey
    vendor_quotes: dict[str, dict[str, BookQuote]],
    strat: StratConfig,
    con: sqlite3.Connection,
    side: str = "yes",
    fees_by_ticker: dict | None = None,         # ticker -> fees.FeeSchedule
    vendor_markets: dict | None = None,         # vendor_id -> MarketKey (ladders)
) -> dict:
    """fees_by_ticker: real Kalshi fee schedules (live runs). Tickers without
    one use strat.fees, the worst-case assumption.
    vendor_markets: needed to price ladder rungs (config.LADDER_TYPES) from
    the player's whole sharp ladder; without it ladders price line-by-line.
    side: default; config.QUOTE_SIDE overrides it per market type."""
    fees_by_ticker = fees_by_ticker or {}
    ladder_cache: dict = {}
    ts = time.time()
    by_type: dict[str, dict] = {}
    events: dict[str, dict] = {}     # game -> per-pass counts, for events/day
    line_budget = strat.event_budget / strat.lines_per_event_hr

    for m in mappings:
        kmk = kalshi_markets[m.kalshi_ticker]
        mtype = kmk.market_type
        st = by_type.setdefault(mtype, dict(kalshi_lines=0, mapped=0, fuzzy=0,
                                            unmatched=0, sharp=0, retail=0,
                                            modeled=0, skipped={}))
        ev = events.setdefault(kmk.event(), dict(mtype=mtype, date=kmk.game_date,
                                                 lines=0, mapped=0, quotable=0))
        st["kalshi_lines"] += 1
        ev["lines"] += 1
        if m.status == "unmatched":
            st["unmatched"] += 1
            continue
        st["mapped"] += 1
        ev["mapped"] += 1
        if m.status == "fuzzy":
            st["fuzzy"] += 1

        if mtype in config.LADDER_TYPES and vendor_markets is not None:
            fair, sharps, retail, extra, skip = _ladder_inputs(
                kmk, m.vendor_id, vendor_markets, vendor_quotes, ladder_cache)
            if skip:
                st["skipped"][skip.split(" (")[0]] = st["skipped"].get(skip.split(" (")[0], 0) + 1
            if fair is not None:
                st["sharp"] += 1
                st["modeled"] += int(extra > 0)
        else:
            sharps, retail = _split_books(vendor_quotes.get(m.vendor_id, {}))
            fair = consensus_fair(sharps, max_age_s=config.MAX_QUOTE_AGE_S) if sharps else None
            extra = 0.0
            st["sharp"] += int(bool(sharps))
        if retail:
            st["retail"] += 1
        if fair is None or not retail:
            continue

        line_cfg = dataclasses.replace(strat, min_edge=strat.min_edge + extra) if extra else strat
        q = compute_quote(fair, retail, config.QUOTE_SIDE.get(mtype, side), line_budget, line_cfg,
                          sharp_spread=sharp_spread(sharps, max_age_s=config.MAX_QUOTE_AGE_S),
                          fees=fees_by_ticker.get(m.kalshi_ticker))
        quotable = int(q is not None and q.contracts > 0)
        ev["quotable"] += quotable
        con.execute(
            "INSERT INTO band_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts, m.kalshi_ticker, m.vendor_id, mtype, fair,
             q.price_cents if q else 0, 0, quotable,
             q.price_cents if quotable else 0,
             q.contracts if quotable else 0,
             q.reason if q else "no retail/sharp data"))

    for eid, ev in events.items():
        con.execute("INSERT INTO event_log VALUES (?,?,?,?,?,?,?)",
                    (ts, ev["mtype"], eid, ev["date"], ev["lines"], ev["mapped"],
                     ev["quotable"]))
    for mtype, st in by_type.items():
        con.execute("INSERT INTO coverage VALUES (?,?,?,?,?,?,?,?)",
                    (ts, mtype, st["kalshi_lines"], st["mapped"], st["fuzzy"],
                     st["unmatched"], st["sharp"], st["retail"]))
    con.commit()
    return by_type


def period_start(game_date: str, per: str) -> str:
    """Bucket a game date: itself ('day') or the Tuesday starting its week
    ('week', Tue-Mon: one NFL week Thu-Mon, one soccer round Fri-Mon)."""
    d = dt.date.fromisoformat(game_date)
    if per == "week":
        d -= dt.timedelta(days=(d.weekday() - 1) % 7)
    return d.isoformat()


def game_id(sport: str, event_id: str) -> str:
    """Same game across series: KXNFLRSHYDS-26NOV19INDHOU and
    KXNFLRECYDS-26NOV19INDHOU are one game -> 'nfl|26NOV19INDHOU'."""
    if event_id.startswith("KX") and "-" in event_id:
        event_id = event_id.split("-", 1)[1]
    return f"{sport}|{event_id}"


def gate_report(con: sqlite3.Connection, max_periods: int = 6) -> str:
    """Phase 1 gate, judged PER SPORT (config.SPORT_OF / GATE_EVENTS); a sport
    is ready only if both parts pass:
      1. avg distinct games per day/week with >= 1 mapped line >= threshold
         (each game counted once per period, however many passes/series saw it)
      2. band non-empty on >= GATE_MIN_QUOTABLE_FRAC of priced lines
    Periods at the edges of the collection window can be partial, so run the
    audit across several days / a full week."""
    sport_of = lambda mt: config.SPORT_OF.get(mt, mt)
    cov = con.execute(
        "SELECT market_type, AVG(kalshi_lines), AVG(mapped), AVG(unmatched) "
        "FROM coverage GROUP BY market_type").fetchall()
    band = con.execute(
        "SELECT market_type, COUNT(*), SUM(quotable) FROM band_log "
        "GROUP BY market_type").fetchall()
    ev_rows = con.execute(
        "SELECT market_type, event_id, game_date, MAX(mapped > 0), MAX(quotable > 0) "
        "FROM event_log GROUP BY market_type, event_id, game_date").fetchall()

    lines = ["--- PHASE 1 GATE REPORT (per sport) ---"]
    for mt, n, mapped, unm in cov:
        lines.append(f"{mt}: avg {n:.0f} Kalshi lines/pass, {mapped:.0f} mapped, {unm:.0f} unmatched")

    # period -> game -> (mapped, quotable), per sport
    games: dict[str, dict[str, dict[str, list[int]]]] = defaultdict(lambda: defaultdict(dict))
    for mt, eid, gdate, mapped, quot in ev_rows:
        sp = sport_of(mt)
        per = config.GATE_EVENTS.get(sp, ("day", 0))[0]
        g = games[sp][period_start(gdate, per)].setdefault(game_id(sp, eid), [0, 0])
        g[0], g[1] = max(g[0], mapped), max(g[1], quot)
    priced: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for mt, n, quot in band:
        priced[sport_of(mt)][0] += n
        priced[sport_of(mt)][1] += quot or 0

    verdicts = {}
    for sp in sorted(set(games) | set(priced)):
        per, min_ev = config.GATE_EVENTS.get(sp, ("day", 0))
        lines.append(f"[{sp}]")
        periods = games.get(sp, {})
        if periods and any(g[0] for p in periods.values() for g in p.values()):
            lines.append(f"  games by {per} (listed / mappable / quotable):")
            for start in sorted(periods)[-max_periods:]:
                gs = periods[start].values()
                lines.append(f"    {start}: {len(gs)} / {sum(g[0] for g in gs)} / "
                             f"{sum(g[1] for g in gs)}")
            avg = sum(sum(g[0] for g in p.values()) for p in periods.values()) / len(periods)
            g1 = "PASS" if avg >= min_ev else "FAIL"
            lines.append(f"  part 1 games/{per}: avg {avg:.1f} mappable over {len(periods)} "
                         f"{per}s ({g1} vs {min_ev})")
        else:
            g1 = None
            listed = sum(len(p) for p in periods.values())
            lines.append(f"  part 1 games/{per}: no data — {listed} games listed, "
                         f"none mapped yet (mock odds vendor?)")
        n, quot = priced.get(sp, (0, 0))
        if n:
            pct = quot / n
            g2 = "PASS" if pct >= config.GATE_MIN_QUOTABLE_FRAC else "FAIL"
            lines.append(f"  part 2 quotable: {quot}/{n} = {pct:.0%} of priced lines "
                         f"({g2} vs {config.GATE_MIN_QUOTABLE_FRAC:.0%})")
        else:
            g2 = None
            lines.append("  part 2 quotable: no data — no line has a sharp fair value "
                         "and a DK/FD price yet")
        verdicts[sp] = ("FAIL" if "FAIL" in (g1, g2) else
                        "PASS" if g1 == g2 == "PASS" else "no verdict")
        lines.append(f"  {sp.upper()}: {verdicts[sp]}")

    ready = [sp for sp, v in verdicts.items() if v == "PASS"]
    lines.append("OVERALL: " + (f"ready to trade: {', '.join(ready)}" if ready
                                else "no sport has passed yet")
                 + "".join(f"; {sp} {v}" for sp, v in verdicts.items() if v != "PASS"))
    return "\n".join(lines)
