"""Single place for keys, endpoints, and strategy knobs.
Everything your friends might want to tweak lives here or in quoting.Config."""
import os

# ---- Kalshi ----
KALSHI_ENV = os.getenv("KALSHI_ENV", "demo")            # "demo" | "prod"
KALSHI_KEY_ID = os.getenv("KALSHI_KEY_ID", "")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PK_PATH", "kalshi_private_key.pem")

# Series tickers to scan. Verify against GET /series — these move.
KALSHI_SERIES = {
    "mlb_hr": ["KXMLBHR"],            # player home-run series (verify exact ticker)
    "corners": ["KXEPLCORN", "KXUCLCORN"],  # soccer corners series (verify)
}

# ---- Odds vendor ----
VENDOR = os.getenv("ODDS_VENDOR", "mock")               # "mock" | "unabated" | ...
VENDOR_API_KEY = os.getenv("ODDS_VENDOR_KEY", "")
SHARP_BOOKS = ["pinnacle", "circa", "bookmaker"]
RETAIL_BOOKS = ["draftkings", "fanduel"]
MAX_QUOTE_AGE_S = 120

# ---- Quoting window (PRE-GAME ONLY — no in-play quoting) ----
QUOTE_OPEN_HOURS_BEFORE_START = 12   # start resting quotes this early
QUOTE_PULL_MIN_BEFORE_START = 0      # pull ALL quotes at game start, hard rule
# Lineup-risk window: MLB lineups drop ~2-4h pre-first-pitch; HR props are most
# exposed to informed flow right then. Options: widen min_edge, or pause/requote
# on lineup confirmation. Ask the friends which they do (see FRIEND-REVIEW.md).
LINEUP_RISK_WINDOW_H = 4
LINEUP_WINDOW_EXTRA_EDGE = 0.01      # +1c min_edge inside the risk window

# ---- Mapping ----
FUZZY_THRESHOLD = 0.82       # difflib ratio below this -> needs manual review
OVERRIDES_CSV = "overrides.csv"

# ---- Audit ----
AUDIT_DB = "phase1_audit.sqlite"
