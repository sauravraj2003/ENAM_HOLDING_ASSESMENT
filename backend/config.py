"""FINN configuration: the demo portfolio, default watch rules and runtime knobs."""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("FINN_CACHE_DIR", os.path.join(BASE_DIR, ".cache"))


def _load_dotenv():
    """Tiny .env loader so we don't need python-dotenv."""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# Mock portfolio: 18 NSE names across sectors and sizes (large caps, mid caps,
# a lender, a PSU defence name, a new-age platform, EMS / cables capex plays).
PORTFOLIO = [
    {"symbol": "RELIANCE", "sector": "Conglomerate"},
    {"symbol": "HDFCBANK", "sector": "Bank"},
    {"symbol": "ICICIBANK", "sector": "Bank"},
    {"symbol": "BAJFINANCE", "sector": "NBFC"},
    {"symbol": "CDSL", "sector": "Capital markets"},
    {"symbol": "TCS", "sector": "IT services"},
    {"symbol": "PERSISTENT", "sector": "IT services"},
    {"symbol": "BHARTIARTL", "sector": "Telecom"},
    {"symbol": "ITC", "sector": "FMCG"},
    {"symbol": "TITAN", "sector": "Consumer"},
    {"symbol": "TRENT", "sector": "Retail"},
    {"symbol": "LT", "sector": "Engineering"},
    {"symbol": "HAL", "sector": "Defence PSU"},
    {"symbol": "SUNPHARMA", "sector": "Pharma"},
    {"symbol": "MARUTI", "sector": "Auto"},
    {"symbol": "DIXON", "sector": "Electronics mfg"},
    {"symbol": "POLYCAB", "sector": "Cables & wires"},
    {"symbol": "ETERNAL", "sector": "Internet platform"},
]

# Ad-hoc research demo set (outside the portfolio). The engine works on any
# NSE symbol screener.in covers; these are just the suggested chips.
RESEARCH_DEMO = ["ASIANPAINT", "PIDILITIND", "IRCTC", "KPITTECH", "DMART"]

# Default watch rules. The investor edits these in the Rules tab.
DEFAULT_RULES = {
    "window_hours": 24,
    "volume_multiple": 2.0,      # alert if EOD volume > N x trailing average
    "volume_lookback": 20,       # trading days in that average
    "price_move_pct": 4.0,       # alert if |1-day move| > X%
    "material_types": ["results", "mgmt", "mna", "fundraise", "rating", "legal", "orders", "capex"],
    "poll_minutes": 15,          # background watcher cadence
}

PORT = int(os.environ.get("PORT", os.environ.get("FINN_PORT", "8000")))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
