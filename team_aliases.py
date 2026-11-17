"""Team-name aliases: every spelling of a team -> one canonical name.

Applied to each SIDE of a matchup after mapping.norm_team() has lowercased,
stripped accents/punctuation, dropped generic club tokens (fc, afc, sc, ac,
calcio, ...) and dropped pure numbers (Mainz 05, Stade Brest 29, Como 1907).
So keys and values here are already in that normalized form.

Kalshi's own names (Nov 2026 event titles) are the reason most entries exist:
it uses short forms like "Bilbao", "Vallecano", "Atletico", "Inter",
"M´gladbach", "Stade Rennais", "Bodoe/Glimt". Canonical names are mostly
Kalshi's spelling. Add a line whenever a real mismatch shows up in the
UNMATCHED / REVIEW lists; never alias two different clubs to one name.
"""

TEAM_ALIASES = {
    # ---- MLB (used when an entity is a single team string) ----
    "ny yankees": "yankees", "new york yankees": "yankees",
    "ny mets": "mets", "new york mets": "mets",
    "la dodgers": "dodgers", "los angeles dodgers": "dodgers",
    "sf giants": "giants", "san francisco giants": "giants",

    # ---- EPL / English ----
    "man city": "manchester city",
    "man united": "manchester united", "man utd": "manchester united",
    "manchester utd": "manchester united",
    "spurs": "tottenham", "tottenham hotspur": "tottenham",
    "newcastle united": "newcastle", "newcastle utd": "newcastle",
    "brighton hove albion": "brighton", "brighton and hove albion": "brighton",
    "nottm forest": "nottingham forest", "nottingham": "nottingham forest",
    "leeds": "leeds united",
    "ipswich": "ipswich town",
    "hull": "hull city",
    "coventry city": "coventry",

    # ---- La Liga ----
    "athletic club": "bilbao", "athletic bilbao": "bilbao",
    "athletic club bilbao": "bilbao",
    "rayo vallecano": "vallecano", "rayo": "vallecano",
    "atletico madrid": "atletico", "atletico de madrid": "atletico",
    "atl madrid": "atletico",
    "deportivo alaves": "alaves",
    "deportivo la coruna": "deportivo de la coruna", "deportivo": "deportivo de la coruna",
    "real betis balompie": "real betis", "betis": "real betis",
    "racing santander": "santander", "racing": "santander",
    "celta": "celta vigo", "celta de vigo": "celta vigo",

    # ---- Bundesliga ----
    "bayern": "bayern munich", "bayern munchen": "bayern munich",
    "cologne": "koln",
    "m gladbach": "monchengladbach", "mgladbach": "monchengladbach",
    "gladbach": "monchengladbach", "borussia monchengladbach": "monchengladbach",
    "borussia dortmund": "dortmund", "bvb": "dortmund",
    "bayer leverkusen": "leverkusen",
    "rasenballsport leipzig": "leipzig",
    "eintracht frankfurt": "frankfurt",
    "werder bremen": "bremen",
    "hamburger": "hamburg",
    "union": "union berlin",

    # ---- Serie A ----
    "inter milan": "inter", "internazionale": "inter",
    "atalanta bc": "atalanta",
    "hellas verona": "verona",

    # ---- Ligue 1 ----
    "paris saint germain": "psg", "paris st germain": "psg", "paris sg": "psg",
    "olympique marseille": "marseille", "olympique de marseille": "marseille",
    "olympique lyonnais": "lyon", "olympique lyon": "lyon",
    "stade rennais": "rennes",
    "stade brest": "brest", "stade brestois": "brest",
    "strasbourg alsace": "strasbourg",
    "estac troyes": "troyes", "es troyes": "troyes",

    # ---- UEFA others ----
    "psv eindhoven": "eindhoven", "psv": "eindhoven",
    "bodoe glimt": "bodo glimt", "bod glimt": "bodo glimt",   # Bodø drops to "bod"
    "sporting lisbon": "sporting cp", "sporting": "sporting cp",
    "shakhtar donetsk": "shakhtar",
    "slavia praha": "slavia prague",
    "lask linz": "lask",
    "feyenoord rotterdam": "feyenoord",
    "club brugge kv": "club brugge",
}
