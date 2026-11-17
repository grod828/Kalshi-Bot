"""Phase 1 audit: coverage stats + band-frequency study, persisted to SQLite.

Run this on a cron/loop for 3-4 days. It answers the Phase 1 gate:
  - how many Kalshi lines exist and how many map cleanly to the vendor?
  - on mapped lines, how often is the quote band non-empty (i.e. tradeable)?
No orders are placed. Would-be quotes are logged so you can later check them
against settlement (the poor man's markout).
"""
from __future__ import annotations

import sqlite3
import time

import config
from mapping import Mapping
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


def run_audit_pass(
    mappings: list[Mapping],
    kalshi_markets: dict,                       # ticker -> MarketKey
    vendor_quotes: dict[str, dict[str, BookQuote]],
    strat: StratConfig,
    con: sqlite3.Connection,
    side: str = "yes",
    fees_by_ticker: dict | None = None,         # ticker -> fees.FeeSchedule
) -> dict:
    """fees_by_ticker: real Kalshi fee schedules (live runs). Tickers without
    one use strat.fees, the worst-case assumption."""
    fees_by_ticker = fees_by_ticker or {}
    ts = time.time()
    by_type: dict[str, dict] = {}
    events: dict[str, dict] = {}     # game -> per-pass counts, for events/day
    line_budget = strat.event_budget / strat.lines_per_event_hr

    for m in mappings:
        kmk = kalshi_markets[m.kalshi_ticker]
        mtype = kmk.market_type
        st = by_type.setdefault(mtype, dict(kalshi_lines=0, mapped=0, fuzzy=0,
                                            unmatched=0, sharp=0, retail=0))
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

        books = vendor_quotes.get(m.vendor_id, {})
        sharps = [q for b, q in books.items() if b in config.SHARP_BOOKS]
        retail = [q for b, q in books.items() if b in config.RETAIL_BOOKS]
        if sharps:
            st["sharp"] += 1
        if retail:
            st["retail"] += 1
        if not (sharps and retail):
            continue

        fair = consensus_fair(sharps, max_age_s=config.MAX_QUOTE_AGE_S)
        if fair is None:
            continue
        q = compute_quote(fair, retail, side, line_budget, strat,
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


def gate_report(con: sqlite3.Connection, max_dates: int = 10) -> str:
    """Phase 1 gate, both must pass:
      1. >= GATE_MIN_EVENTS_PER_DAY distinct games per game date with at least
         one mapped line (averaged over the game dates seen)
      2. band non-empty on >= GATE_MIN_QUOTABLE_FRAC of priced lines
    Game dates at the edges of the collection window can be partial (listings
    not open yet / already closed), so run the audit for several days."""
    cov = con.execute(
        "SELECT market_type, AVG(kalshi_lines), AVG(mapped), AVG(unmatched) "
        "FROM coverage GROUP BY market_type").fetchall()
    band = con.execute(
        "SELECT market_type, COUNT(*), SUM(quotable) FROM band_log "
        "GROUP BY market_type").fetchall()
    days = con.execute(
        "SELECT game_date, COUNT(DISTINCT event_id), "
        "COUNT(DISTINCT CASE WHEN mapped > 0 THEN event_id END), "
        "COUNT(DISTINCT CASE WHEN quotable > 0 THEN event_id END) "
        "FROM event_log GROUP BY game_date ORDER BY game_date").fetchall()

    lines = ["--- PHASE 1 GATE REPORT ---"]
    for mt, n, mapped, unm in cov:
        lines.append(f"{mt}: avg {n:.0f} Kalshi lines/pass, {mapped:.0f} mapped, {unm:.0f} unmatched")

    # Gate 1: mappable events per day
    min_ev = config.GATE_MIN_EVENTS_PER_DAY
    if days and not any(r[2] for r in days):
        g1 = None
        lines.append(f"GATE 1 events/day: no data — {sum(r[1] for r in days)} games listed "
                     f"over {len(days)} dates but none mapped yet (mock odds vendor?)")
    elif days:
        lines.append("events by game date (listed / mappable / quotable):")
        for d, listed, mappable, quot in days[-max_dates:]:
            lines.append(f"  {d}: {listed} / {mappable} / {quot}")
        if len(days) > max_dates:
            lines.append(f"  ... {len(days) - max_dates} earlier dates not shown")
        avg_ev = sum(r[2] for r in days) / len(days)
        g1 = "PASS" if avg_ev >= min_ev else "FAIL"
        lines.append(f"GATE 1 events/day: avg {avg_ev:.1f} mappable over {len(days)} "
                     f"game dates ({g1} vs {min_ev})")
    else:
        g1 = None
        lines.append("GATE 1 events/day: no data yet")

    # Gate 2: quotable share of priced lines
    min_q = config.GATE_MIN_QUOTABLE_FRAC
    total_n = total_q = 0
    for mt, n, quot in band:
        quot = quot or 0
        total_n, total_q = total_n + n, total_q + quot
        lines.append(f"{mt}: band non-empty {quot}/{n} = {quot/n:.0%}")
    if total_n:
        pct = total_q / total_n
        g2 = "PASS" if pct >= min_q else "FAIL"
        lines.append(f"GATE 2 quotable: {pct:.0%} of priced lines ({g2} vs {min_q:.0%})")
    else:
        g2 = None
        lines.append("GATE 2 quotable: no data — no line has both a mapping and "
                     "sharp + DK/FD quotes yet (mock odds vendor vs live Kalshi?)")

    if "FAIL" in (g1, g2):
        lines.append("OVERALL: FAIL")
    elif g1 == g2 == "PASS":
        lines.append("OVERALL: PASS")
    else:
        lines.append("OVERALL: no verdict until both gates have data")
    return "\n".join(lines)
