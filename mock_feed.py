"""Mock odds feed in the normalized format a real vendor adapter (Unabated,
OpticOdds, ...) must produce. Swap this module out; strategy code is untouched.

Numbers are realistic shapes, not live odds.
"""
from pricing import BookQuote

# ---- MLB player HR props: "Player hits a HR tonight" (yes = HR) ----
HR_PROPS = {
    "KXMLB-JUDGE-HR": {
        "desc": "Aaron Judge HR (NYY vs BOS)",
        "sharps": [
            BookQuote("pinnacle",  +205, -245),
            BookQuote("circa",     +210, -250),
            BookQuote("bookmaker", +200, -240),
        ],
        "dk_fd": [
            BookQuote("draftkings", +185, -260),
            BookQuote("fanduel",    +190, -265),
        ],
    },
    "KXMLB-OHTANI-HR": {
        "desc": "Shohei Ohtani HR (LAD vs SF)",
        "sharps": [
            BookQuote("pinnacle",  +240, -290),
            BookQuote("circa",     +245, -295),
            BookQuote("bookmaker", +235, -285),
        ],
        "dk_fd": [
            BookQuote("draftkings", +215, -300),
            BookQuote("fanduel",    +220, -310),
        ],
    },
    "KXMLB-SOTO-HR": {  # tight line: expect NO QUOTE (band empty)
        "desc": "Juan Soto HR (NYM vs PHI)",
        "sharps": [
            BookQuote("pinnacle",  +290, -340),
            BookQuote("circa",     +285, -335),
            BookQuote("bookmaker", +295, -345),
        ],
        "dk_fd": [
            BookQuote("draftkings", +290, -350),  # DK unusually sharp here
            BookQuote("fanduel",    +285, -345),
        ],
    },
}

# ---- Soccer total corners ladder (yes = OVER line) ----
CORNERS_LADDER = {
    "event": "ARS vs CHE total corners",
    "ticker_fmt": "KXEPL-ARSCHE-CORN-{line}",
    "side": "yes",  # we one-way bid the OVERs
    "rungs": {
        7.5: {
            "sharps": [
                BookQuote("pinnacle",  -280, +230),
                BookQuote("circa",     -270, +225),
                BookQuote("bookmaker", -285, +235),
            ],
            "dk_fd": [BookQuote("draftkings", -250, +195),
                      BookQuote("fanduel",    -255, +200)],
        },
        8.5: {
            "sharps": [
                BookQuote("pinnacle",  -160, +135),
                BookQuote("circa",     -155, +130),
                # bookmaker stale/crossed on purpose -> isotonic smoothing kicks in
                BookQuote("bookmaker", -190, +160),
            ],
            "dk_fd": [BookQuote("draftkings", -140, +110),
                      BookQuote("fanduel",    -145, +115)],
        },
        9.5: {
            "sharps": [
                BookQuote("pinnacle",  +105, -125),
                BookQuote("circa",     +110, -130),
                BookQuote("bookmaker", +100, -120),
            ],
            "dk_fd": [BookQuote("draftkings", +120, -145),
                      BookQuote("fanduel",    +118, -142)],
        },
        10.5: {
            "sharps": [
                BookQuote("pinnacle",  +175, -210),
                BookQuote("circa",     +180, -215),
                BookQuote("bookmaker", +170, -205),
            ],
            "dk_fd": [BookQuote("draftkings", +195, -240),
                      BookQuote("fanduel",    +200, -245)],
        },
        11.5: {
            "sharps": [
                BookQuote("pinnacle",  +270, -330),
                BookQuote("circa",     +280, -340),
                BookQuote("bookmaker", +265, -325),
            ],
            "dk_fd": [BookQuote("draftkings", +290, -360),
                      BookQuote("fanduel",    +295, -365)],
        },
    },
}
