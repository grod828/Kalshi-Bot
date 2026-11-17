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
from pricing import BookQuote, consensus_fair
from quoting import Config as StratConfig
from quoting import compute_quote

SCHEMA = """
CREATE TABLE IF NOT EXISTS coverage (
  ts REAL, market_type TEXT, kalshi_lines INT, mapped INT, fuzzy INT,
  unmatched INT, with_sharp_quotes INT, with_retail_quotes INT
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
) -> dict:
    ts = time.time()
    by_type: dict[str, dict] = {}
    line_budget = strat.event_budget / strat.lines_per_event_hr

    for m in mappings:
        mtype = kalshi_markets[m.kalshi_ticker].market_type
        st = by_type.setdefault(mtype, dict(kalshi_lines=0, mapped=0, fuzzy=0,
                                            unmatched=0, sharp=0, retail=0))
        st["kalshi_lines"] += 1
        if m.status == "unmatched":
            st["unmatched"] += 1
            continue
        st["mapped"] += 1
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
        q = compute_quote(fair, retail, side, line_budget, strat)
        quotable = int(q is not None and q.contracts > 0)
        con.execute(
            "INSERT INTO band_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts, m.kalshi_ticker, m.vendor_id, mtype, fair,
             q.price_cents if q else 0, 0, quotable,
             q.price_cents if quotable else 0,
             q.contracts if quotable else 0,
             q.reason if q else "no retail/sharp data"))

    for mtype, st in by_type.items():
        con.execute("INSERT INTO coverage VALUES (?,?,?,?,?,?,?,?)",
                    (ts, mtype, st["kalshi_lines"], st["mapped"], st["fuzzy"],
                     st["unmatched"], st["sharp"], st["retail"]))
    con.commit()
    return by_type


def gate_report(con: sqlite3.Connection) -> str:
    """Phase 1 gate: >= 20 mappable events/day, band non-empty on >= 30% of lines."""
    cov = con.execute(
        "SELECT market_type, AVG(kalshi_lines), AVG(mapped), AVG(unmatched) "
        "FROM coverage GROUP BY market_type").fetchall()
    band = con.execute(
        "SELECT market_type, COUNT(*), SUM(quotable) FROM band_log "
        "GROUP BY market_type").fetchall()
    lines = ["--- PHASE 1 GATE REPORT ---"]
    for mt, n, mapped, unm in cov:
        lines.append(f"{mt}: avg {n:.0f} Kalshi lines/pass, {mapped:.0f} mapped, {unm:.0f} unmatched")
    total_n = total_q = 0
    for mt, n, quot in band:
        quot = quot or 0
        total_n, total_q = total_n + n, total_q + quot
        lines.append(f"{mt}: band non-empty {quot}/{n} = {quot/n:.0%}")
    if total_n:
        pct = total_q / total_n
        lines.append(f"OVERALL: {pct:.0%} quotable "
                     f"({'PASS' if pct >= 0.30 else 'FAIL'} vs 30% gate)")
    else:
        lines.append("OVERALL: no verdict — no line has both a mapping and "
                     "sharp + DK/FD quotes yet (mock odds vendor vs live Kalshi?)")
    return "\n".join(lines)
