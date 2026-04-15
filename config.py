#!/usr/bin/env python3
"""
Unified configuration for all Polymarket bots.
Keys loaded here; imported everywhere else.
"""

# ─────────────────────────────────────────────
# API Keys
# ─────────────────────────────────────────────
TOMORROW_IO_KEY = "U4WLBud8PUpOgna48rLh1QFvi0BAalsA"
BINANCE_KEY     = "7BnK7TcxNXaySXvdccvQKYLsN6vVdlRyMdPojIjPF91H7w8QnnD9iOXmrMT2i1Rf"
OKX_KEY         = "dd0f40f8-15af-4e9a-969b-c7fb75ae06ee"
OKX_SECRET      = "46A7DD81480E659901A63CBD4C3AA175"

# ─────────────────────────────────────────────
# API Endpoints
# ─────────────────────────────────────────────
GAMMA_API          = "https://gamma-api.polymarket.com"
OPEN_METEO_API     = "https://api.open-meteo.com/v1/forecast"
NWS_API            = "https://api.weather.gov"
TOMORROW_FORECAST  = "https://api.tomorrow.io/v4/weather/forecast"
TOMORROW_REALTIME  = "https://api.tomorrow.io/v4/weather/realtime"
NOAA_OBS           = "https://api.weather.gov/stations/{station_id}/observations/latest"

# ─────────────────────────────────────────────
# Strategy Parameters
# ─────────────────────────────────────────────
MIN_EDGE             = 0.10   # minimum probability edge
MIN_BRACKET_NO_PRICE = 0.93   # Leg 1: only buy NO above this price
MIN_TAIL_EDGE        = 0.03   # known-outcome: minimum profit per share (≥3¢)
KNOWN_SAFETY_MARGIN_F = 2.0   # °F buffer before declaring outcome known
KELLY_FRACTION       = 0.25
MAX_POSITION_PCT     = 0.02   # max 2% of bankroll per trade
MAX_STAKE_DOLLARS    = 10.0   # hard cap per trade
MAX_OPEN_POSITIONS   = 15
MIN_VOLUME           = 1000.0 # skip markets with less than $1,000 volume
MIN_LIQUIDITY        = 50.0   # skip markets with <$50 liquidity (0.5/0.5 default price trap)
YES_PRICE_FLOOR      = 0.15   # never buy YES below 15¢ — model unreliable at extremes
NO_PRICE_FLOOR       = 0.10   # never buy NO below 10¢ (i.e. YES above 90¢)
SPREAD_COST          = 0.02   # ~2% spread cost deducted from edge
MAX_DAYS_OUT         = 1      # main scanner: today + tomorrow only
KNOWN_OUTCOME_DAYS   = 0      # known-outcome scanner: TODAY only (resolving in <24h)
SOURCE_DISAGREE_MAX_F = 8.0   # skip trade if sources disagree by more than this
SCAN_INTERVAL        = 600    # 10 min between scans

# Tomorrow.io weight in ensemble (higher = more trusted)
# Open-Meteo: 1.0, NWS: 0.8, Tomorrow.io: 1.1 (premium global model)
TOMORROW_WEIGHT = 1.1

# Forecast uncertainty (σ, °F) by days out
SIGMA_BY_DAY = {
    0: 1.5,  # same-day: temp is largely determined, small remaining uncertainty
    1: 2.5, 2: 3.5, 3: 4.5, 4: 5.5, 5: 6.5,
    6: 7.5, 7: 8.0, 8: 8.5, 9: 9.0, 10: 9.5,
    11: 10.0, 12: 10.5, 13: 11.0, 14: 11.5,
}

# Recalibrated sigmas for bracket prediction (Phase 3 — tighter)
SIGMA_BRACKET = {
    0: 1.0,  # same-day
    1: 1.5, 2: 2.0, 3: 2.5, 4: 3.5, 5: 4.5,
    6: 5.5, 7: 6.0, 8: 6.5, 9: 7.0, 10: 7.5,
    11: 8.0, 12: 8.5, 13: 9.0, 14: 9.5,
}

# ─────────────────────────────────────────────
# Cities — coords, NOAA station, Tomorrow.io flag, peak temp hour (UTC)
# noaa_station: None for international (uses Tomorrow.io realtime instead)
# peak_hour_utc: approximate hour after which daily high is locked in
# ─────────────────────────────────────────────
CITIES = {
    # North America (NOAA coverage)
    "new york":     {"coords": (40.7128, -74.0060),  "noaa_station": "KNYC",  "peak_hour_utc": 20},
    "nyc":          {"coords": (40.7128, -74.0060),  "noaa_station": "KNYC",  "peak_hour_utc": 20},
    "los angeles":  {"coords": (34.0522, -118.2437), "noaa_station": "KLAX",  "peak_hour_utc": 23},
    "chicago":      {"coords": (41.8781, -87.6298),  "noaa_station": "KORD",  "peak_hour_utc": 21},
    "houston":      {"coords": (29.7604, -95.3698),  "noaa_station": "KHOU",  "peak_hour_utc": 22},
    "phoenix":      {"coords": (33.4484, -112.0740), "noaa_station": "KPHX",  "peak_hour_utc": 23},
    "philadelphia": {"coords": (39.9526, -75.1652),  "noaa_station": "KPHL",  "peak_hour_utc": 20},
    "san antonio":  {"coords": (29.4241, -98.4936),  "noaa_station": "KSAT",  "peak_hour_utc": 22},
    "san diego":    {"coords": (32.7157, -117.1611), "noaa_station": "KSAN",  "peak_hour_utc": 23},
    "dallas":       {"coords": (32.7767, -96.7970),  "noaa_station": "KDFW",  "peak_hour_utc": 22},
    "miami":        {"coords": (25.7617, -80.1918),  "noaa_station": "KMIA",  "peak_hour_utc": 21},
    "atlanta":      {"coords": (33.7490, -84.3880),  "noaa_station": "KATL",  "peak_hour_utc": 21},
    "seattle":      {"coords": (47.6062, -122.3321), "noaa_station": "KSEA",  "peak_hour_utc": 23},
    "denver":       {"coords": (39.7392, -104.9903), "noaa_station": "KDEN",  "peak_hour_utc": 22},
    "boston":       {"coords": (42.3601, -71.0589),  "noaa_station": "KBOS",  "peak_hour_utc": 20},
    "las vegas":    {"coords": (36.1699, -115.1398), "noaa_station": "KLAS",  "peak_hour_utc": 23},
    "portland":     {"coords": (45.5231, -122.6765), "noaa_station": "KPDX",  "peak_hour_utc": 23},
    "minneapolis":  {"coords": (44.9778, -93.2650),  "noaa_station": "KMSP",  "peak_hour_utc": 21},
    "detroit":      {"coords": (42.3314, -83.0458),  "noaa_station": "KDTW",  "peak_hour_utc": 21},
    "nashville":    {"coords": (36.1627, -86.7816),  "noaa_station": "KBNA",  "peak_hour_utc": 21},
    "charlotte":    {"coords": (35.2271, -80.8431),  "noaa_station": "KCLT",  "peak_hour_utc": 21},
    "austin":       {"coords": (30.2672, -97.7431),  "noaa_station": "KAUS",  "peak_hour_utc": 22},
    # International (Tomorrow.io realtime — no NOAA coverage)
    "london":       {"coords": (51.5074, -0.1278),   "noaa_station": None,    "peak_hour_utc": 15},
    "paris":        {"coords": (48.8566, 2.3522),    "noaa_station": None,    "peak_hour_utc": 14},
    "tokyo":        {"coords": (35.6762, 139.6503),  "noaa_station": None,    "peak_hour_utc":  5},
    "sydney":       {"coords": (-33.8688, 151.2093), "noaa_station": None,    "peak_hour_utc":  4},
    "toronto":      {"coords": (43.6532, -79.3832),  "noaa_station": "CYYZ",  "peak_hour_utc": 21},
    "wellington":   {"coords": (-41.2865, 174.7762), "noaa_station": None,    "peak_hour_utc":  3},
    "lucknow":      {"coords": (26.8467, 80.9462),   "noaa_station": None,    "peak_hour_utc":  9},
}

# ─────────────────────────────────────────────
# Shared keyword list for weather market detection
# ─────────────────────────────────────────────
WEATHER_KEYWORDS = [
    "temperature", "weather", "degrees", "fahrenheit", "celsius",
    "high temp", "low temp", "above", "below", "highest temp", "lowest temp",
]

# ─────────────────────────────────────────────
# File paths (droplet)
# ─────────────────────────────────────────────
STATE_FILE       = "/root/paper_trades.json"
BOT_LOG          = "/root/bot.log"
PAPER_LOG        = "/root/paper_trader.log"
TAIL_LOG         = "/root/tail_end.log"
RESEARCH_LOG     = "/root/research_engine.log"
OPPORTUNITIES_F  = "/root/opportunities.json"   # live feed between scanners
