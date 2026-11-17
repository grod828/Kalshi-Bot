"""Single place for keys, endpoints, and strategy knobs.
Everything your friends might want to tweak lives here or in quoting.Config."""
import os

# ---- Kalshi ----
KALSHI_ENV = os.getenv("KALSHI_ENV", "demo")            # "demo" | "prod"
KALSHI_KEY_ID = os.getenv("KALSHI_KEY_ID", "")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PK_PATH", "kalshi_private_key.pem")

# Where Phase 1 gets its Kalshi market list: "mock" (fixture, offline) or
# "live" (public GET /events — read-only, no key, places nothing).
KALSHI_MARKETS = os.getenv("KALSHI_MARKETS", "mock")
# Which exchange to read market data from. Separate from KALSHI_ENV (orders):
# coverage only means something against prod's real listings.
KALSHI_DATA_ENV = os.getenv("KALSHI_DATA_ENV", "prod")   # "prod" | "demo"

# Series tickers to scan (verified against GET /series, Nov 2026).
# Total-corners series only; Kalshi's *TCORNERS series are team corners.
KALSHI_SERIES = {
    "mlb_hr": ["KXMLBHR"],            # "Pro Baseball Home Runs" (empty in offseason)
    "corners": ["KXEPLCORNERS", "KXUCLCORNERS", "KXLALIGACORNERS",
                "KXSERIEACORNERS", "KXBUNDESLIGACORNERS", "KXLIGUE1CORNERS"],
    "nfl_rush_yds": ["KXNFLRSHYDS"],  # "Jonathan Taylor: 25+ rushing yards" ladders
    "nfl_rec_yds": ["KXNFLRECYDS"],   # receiving yards ladders
    "nfl_first_td": ["KXNFLFIRSTTD"], # first TD scorer: one market per player/D-ST/No TD
}

# Which side we bid, per market type (one-way). Yardage: we sell the
# milestone overs retail likes (bid NO), like holding "under 40 yards".
QUOTE_SIDE = {"mlb_hr": "yes", "corners": "yes",
              "nfl_rush_yds": "no", "nfl_rec_yds": "no", "nfl_first_td": "no"}

# ---- Field markets: exactly one outcome wins (first TD scorer) ----
FIELD_TYPES = {"nfl_first_td"}
FIELD_DEVIG = "power"            # "power" (more vig off longshots) | "multiplicative"
FIELD_MIN_OVERROUND = 1.05       # skip a book whose field sums lower: it's missing outcomes
# We bid NO on each outcome; only the first scorer's NO can lose, so a game's
# worst case is its single largest NO position. Each outcome may use this
# share of the event budget; < 1 leaves room for inactive players settling at
# Kalshi's "fair market price" instead of 0/1.
FIELD_OUTCOME_BUDGET_FRAC = 0.5

# ---- Player yardage ladders (ladders.py) ----
# Rungs are priced from a lognormal fitted to the player's sharp lines.
LADDER_TYPES = {"nfl_rush_yds", "nfl_rec_yds"}
# Spread of ln(yards) used when sharps quote only ONE line for a player.
# PLACEHOLDERS: calibrate from settled results / multi-line sharp fits.
LADDER_SIGMA = {"nfl_rush_yds": 0.60, "nfl_rec_yds": 0.70}
LADDER_MAX_MODEL_SD = 1.0        # don't quote rungs > 1 model SD from a sharp line
LADDER_MODEL_EXTRA_EDGE = 0.01   # +1c min_edge on rungs priced by the model

# ---- Odds vendor ----
VENDOR = os.getenv("ODDS_VENDOR", "mock")               # "mock" | "sportsgameodds"
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

# ---- Phase 1 gate: judged PER SPORT (both parts must pass for a sport) ----
SPORT_OF = {"mlb_hr": "mlb", "corners": "soccer",
            "nfl_rush_yds": "nfl", "nfl_rec_yds": "nfl", "nfl_first_td": "nfl"}
# Part 1: avg distinct games with >= 1 mapped line, per day or per week.
# Weeks run Tuesday-Monday (one NFL week Thu-Mon; one soccer round Fri-Mon).
# PLACEHOLDER thresholds; set them from what each sport can actually supply:
#   MLB ~15 games/day in season; NFL ~14-16 games/week; soccer EPL + La Liga
#   ~20 games/round (Kalshi corners listings, Nov 2026).
GATE_EVENTS = {"mlb": ("day", 10), "soccer": ("week", 20), "nfl": ("week", 10)}
GATE_MIN_QUOTABLE_FRAC = 0.30    # part 2: share of priced lines with a non-empty band

# ---- Audit ----
# Live and mock runs get separate DBs so fixture passes never pollute real stats.
AUDIT_DB = os.getenv("AUDIT_DB", "phase1_audit_live.sqlite" if KALSHI_MARKETS == "live"
                     else "phase1_audit.sqlite")
