#!/usr/bin/env python3
"""
Polymarket Weather Arbitrage Bot — Enhanced
Three-source ensemble: Open-Meteo + NWS + Tomorrow.io
Scans directional markets AND narrow bracket markets (Leg 1).

Deploy to: /root/bot.py on DigitalOcean droplet 164.92.133.253
"""
import requests, json, math, time, logging, re
from datetime import datetime, timezone, timedelta
from scipy import stats

from config import (
    GAMMA_API, OPEN_METEO_API, NWS_API, TOMORROW_FORECAST,
    TOMORROW_IO_KEY, TOMORROW_WEIGHT,
    MIN_EDGE, MIN_BRACKET_NO_PRICE, SIGMA_BY_DAY,
    MIN_VOLUME, MIN_LIQUIDITY, YES_PRICE_FLOOR, NO_PRICE_FLOOR, SPREAD_COST,
    MAX_DAYS_OUT, SOURCE_DISAGREE_MAX_F,
    WEATHER_KEYWORDS,
    SCAN_INTERVAL, BOT_LOG, OPPORTUNITIES_F, CITIES,
)

# ─────────────────────────────────────────────────────────
# Per-scan forecast cache — one API call per city, not per market
# ─────────────────────────────────────────────────────────
_forecast_cache: dict = {}   # key: (city_name, target_date) → forecasts list

def _cache_key(city_name, target_date):
    return (city_name, target_date or "nearest")

def clear_forecast_cache():
    _forecast_cache.clear()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(BOT_LOG), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# City lookup helpers (shared with tail_end_scanner)
# ─────────────────────────────────────────────────────────

def find_city(question_lower):
    """Return (city_name, city_data) or (None, None)."""
    for name, data in CITIES.items():
        if name in question_lower:
            return name, data
    return None, None


# ─────────────────────────────────────────────────────────
# Market discovery
# ─────────────────────────────────────────────────────────

def get_weather_markets():
    """
    Fetch weather markets resolving within MAX_DAYS_OUT days only.
    Also filters by MIN_VOLUME to skip thin markets.
    Stops paginating once markets are too far out (Gamma API returns by end date asc).
    """
    markets, offset = [], 0
    today = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=MAX_DAYS_OUT)

    while True:
        try:
            r = requests.get(
                f"{GAMMA_API}/markets",
                params={"active": "true", "closed": "false", "limit": 100, "offset": offset},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            found_any = False
            for m in data:
                q = (m.get("question") or "").lower()
                if not any(kw in q for kw in WEATHER_KEYWORDS):
                    continue
                # Volume filter
                if float(m.get("volume") or 0) < MIN_VOLUME:
                    continue
                # Date filter — only today/tomorrow
                end_raw = m.get("endDate") or ""
                if end_raw:
                    try:
                        end_date = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).date()
                        if end_date > cutoff:
                            continue
                    except ValueError:
                        pass
                markets.append(m)
                found_any = True
            if len(data) < 100:
                break
            offset += 100
            time.sleep(0.5)
        except Exception as e:
            log.error(f"Market fetch error: {e}")
            break

    log.info(f"Found {len(markets)} snipe-eligible markets (≤{MAX_DAYS_OUT}d, ≥${MIN_VOLUME:,.0f} vol)")
    return markets


def parse_market(m):
    try:
        prices   = json.loads(m.get("outcomePrices", "[0.5,0.5]"))
        outcomes = json.loads(m.get("outcomes", '["Yes","No"]'))
        yes_idx  = 0 if outcomes[0].lower() == "yes" else 1
        yes_price = float(prices[yes_idx])
        return {
            "id":        m.get("id"),
            "question":  m.get("question", ""),
            "yes_price": yes_price,
            "no_price":  1 - yes_price,
            "end_date":  m.get("endDate"),
            "volume":    float(m.get("volume") or 0),
            "liquidity": float(m.get("liquidity") or 0),
        }
    except:
        return None


# ─────────────────────────────────────────────────────────
# Market classification
# ─────────────────────────────────────────────────────────

def classify_market(question):
    """
    Returns:
      ("directional", info_dict)  — e.g. "will Dallas high temp be above 75°F?"
      ("bracket", info_dict)      — e.g. "will Dallas high temp be 72–73°F?"
      (None, None)
    """
    q = question.lower()
    city, city_data = find_city(q)
    if not city:
        return None, None

    coords = city_data["coords"]

    # ── Bracket market: "72–73°F", "14–15°C", "exactly 14°C" ──
    bracket_m = re.search(
        r'(\d+(?:\.\d+)?)\s*[–\-–]\s*(\d+(?:\.\d+)?)\s*[°]?\s*([fc])\b', q
    ) or re.search(
        r'exactly\s+(\d+(?:\.\d+)?)\s*[°]?\s*([fc])\b', q
    )
    if bracket_m:
        groups = bracket_m.groups()
        if len(groups) == 3:  # range
            lo, hi, unit = float(groups[0]), float(groups[1]), groups[2].lower()
        else:  # exact
            lo = hi = float(groups[0]); unit = groups[1].lower()
        if unit == "c":
            lo = lo * 9/5 + 32
            hi = hi * 9/5 + 32
        date_m = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', question)
        return "bracket", {
            "city": city, "coords": coords, "bracket_lo_f": lo, "bracket_hi_f": hi,
            "bracket_mid_f": (lo + hi) / 2, "bracket_width_f": hi - lo,
            "end_date": date_m.group(1) if date_m else None,
        }

    # ── Directional market: above / below threshold ──
    direction = None
    if any(w in q for w in ["above", "exceed", "over", "reach", "at least"]):
        direction = "above"
    elif any(w in q for w in ["below", "under", "less than"]):
        direction = "below"
    if not direction:
        return None, None

    # Extract temperature threshold
    tm = re.search(r'(\d+(?:\.\d+)?)\s*[°]?\s*([fc])\b', q) or \
         re.search(r'(\d+(?:\.\d+)?)\s*degrees?\s*([fc])\b', q)
    if tm:
        val = float(tm.group(1))
        unit = tm.group(2).lower()
        threshold_f = val if unit == "f" else val * 9/5 + 32
    else:
        tm2 = re.search(r'\b([6-9]\d|1[0-1]\d)\b', q)
        if tm2:
            threshold_f = float(tm2.group(1))
        else:
            return None, None

    date_m = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', question)
    return "directional", {
        "city": city, "coords": coords, "threshold_f": threshold_f,
        "direction": direction, "end_date": date_m.group(1) if date_m else None,
    }


# ─────────────────────────────────────────────────────────
# Weather data sources
# ─────────────────────────────────────────────────────────

def get_open_meteo(lat, lon, target_date=None):
    try:
        r = requests.get(
            OPEN_METEO_API,
            params={
                "latitude": lat, "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit",
                "forecast_days": 14, "timezone": "auto",
            },
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        today = datetime.now(timezone.utc).date()
        for i, ds in enumerate(d["daily"]["time"]):
            date = datetime.strptime(ds, "%Y-%m-%d").date()
            days_out = (date - today).days
            if days_out < 0:
                continue
            sigma = SIGMA_BY_DAY.get(days_out, 12.0)
            entry = {
                "date": ds, "days_out": days_out,
                "high_f": d["daily"]["temperature_2m_max"][i],
                "low_f":  d["daily"]["temperature_2m_min"][i],
                "sigma":  sigma,
            }
            if target_date and ds == target_date:
                return entry
        return entry if not target_date else None
    except Exception as e:
        log.error(f"Open-Meteo error: {e}")
        return None


def get_nws(lat, lon):
    """Returns current-day high temperature forecast in °F (US only)."""
    try:
        pr = requests.get(
            f"{NWS_API}/points/{lat},{lon}", timeout=8,
            headers={"User-Agent": "PolymarketBot/2.0"},
        )
        if pr.status_code != 200:
            return None
        fr = requests.get(
            pr.json()["properties"]["forecast"], timeout=8,
            headers={"User-Agent": "PolymarketBot/2.0"},
        )
        fr.raise_for_status()
        for p in fr.json()["properties"]["periods"]:
            if p["isDaytime"]:
                t = p["temperature"]
                return t if p["temperatureUnit"] == "F" else t * 9/5 + 32
    except:
        return None


def get_tomorrow_io(lat, lon, target_date=None):
    """
    Fetch Tomorrow.io daily forecast.
    Returns dict with high_f, low_f, sigma for target_date (or nearest day).
    """
    try:
        r = requests.get(
            TOMORROW_FORECAST,
            params={
                "location": f"{lat},{lon}",
                "timesteps": "1d",
                "units": "imperial",
                "apikey": TOMORROW_IO_KEY,
            },
            timeout=12,
        )
        if r.status_code == 429:
            log.warning("Tomorrow.io rate limit hit — skipping")
            return None
        r.raise_for_status()
        data = r.json()
        today = datetime.now(timezone.utc).date()
        timelines = data.get("timelines", {}).get("daily", [])
        for day in timelines:
            ds = day["time"][:10]  # "2026-04-08T..."
            date = datetime.strptime(ds, "%Y-%m-%d").date()
            days_out = (date - today).days
            if days_out < 0:
                continue
            vals = day.get("values", {})
            high_f = vals.get("temperatureMax")
            low_f  = vals.get("temperatureMin")
            if high_f is None or low_f is None:
                continue
            sigma = SIGMA_BY_DAY.get(days_out, 12.0)
            entry = {"date": ds, "days_out": days_out, "high_f": high_f, "low_f": low_f, "sigma": sigma}
            if target_date and ds == target_date:
                return entry
        return entry if timelines and not target_date else None
    except Exception as e:
        log.error(f"Tomorrow.io forecast error: {e}")
        return None


# ─────────────────────────────────────────────────────────
# Ensemble probability calculation
# ─────────────────────────────────────────────────────────

def calc_prob(mu, sigma, threshold, direction):
    z = (threshold - mu) / sigma
    return 1 - stats.norm.cdf(z) if direction == "above" else stats.norm.cdf(z)


def build_ensemble(city_name, lat, lon, target_date, question_lower, sigma_table=None):
    """
    Pull all three sources, build weighted ensemble.
    CACHED per city+date — API called once per city per scan, not once per market.
    Returns list of forecast dicts.
    """
    if sigma_table is None:
        sigma_table = SIGMA_BY_DAY

    ckey = _cache_key(city_name, target_date)
    if ckey in _forecast_cache:
        raw = _forecast_cache[ckey]
    else:
        raw = {}
        om = get_open_meteo(lat, lon, target_date)
        if om:
            raw["open_meteo"] = om
        tm = get_tomorrow_io(lat, lon, target_date)
        if tm:
            raw["tomorrow_io"] = tm
        nws_t = get_nws(lat, lon)
        if nws_t is not None:
            raw["nws_temp"] = nws_t
        _forecast_cache[ckey] = raw
        time.sleep(0.3)   # brief pause after API calls — only happens once per city

    # Build forecast list using question context (high vs low vs avg)
    forecasts = []
    use_high = "high" in question_lower
    use_low  = "low"  in question_lower

    if "open_meteo" in raw:
        om = raw["open_meteo"]
        mu = om["high_f"] if use_high else om["low_f"] if use_low else (om["high_f"] + om["low_f"]) / 2
        forecasts.append({"mu": mu, "sigma": om["sigma"], "weight": 1.0, "src": "open-meteo"})

    if "tomorrow_io" in raw:
        tm = raw["tomorrow_io"]
        mu = tm["high_f"] if use_high else tm["low_f"] if use_low else (tm["high_f"] + tm["low_f"]) / 2
        forecasts.append({"mu": mu, "sigma": tm["sigma"], "weight": TOMORROW_WEIGHT, "src": "tomorrow.io"})

    if "nws_temp" in raw:
        sigma_nws = sigma_table.get(1, 4.5)
        forecasts.append({"mu": raw["nws_temp"], "sigma": sigma_nws, "weight": 0.8, "src": "nws"})

    return forecasts


def ensemble_prob(forecasts, threshold, direction):
    if not forecasts:
        return None
    tw = sum(f["weight"] for f in forecasts)
    return sum(
        f["weight"] / tw * calc_prob(f["mu"], f["sigma"], threshold, direction)
        for f in forecasts
    )


def sources_agree(forecasts):
    """
    Returns (True, None) if all forecast sources agree within SOURCE_DISAGREE_MAX_F.
    Returns (False, spread) if they disagree beyond the threshold.
    Single-source forecasts always pass (no disagreement possible).
    """
    if len(forecasts) < 2:
        return True, 0.0
    mus = [f["mu"] for f in forecasts]
    spread = max(mus) - min(mus)
    return spread <= SOURCE_DISAGREE_MAX_F, spread


# ─────────────────────────────────────────────────────────
# Opportunity analysis
# ─────────────────────────────────────────────────────────

def analyze_directional(market_raw):
    parsed = parse_market(market_raw)
    if not parsed:
        return None
    mtype, info = classify_market(parsed["question"])
    if mtype != "directional":
        return None

    # Liquidity filter — 0.5/0.5 default price is a price trap, not a real signal
    if parsed["liquidity"] < MIN_LIQUIDITY:
        return None

    lat, lon = info["coords"]
    # Use API endDate (YYYY-MM-DD) as target — question text never has ISO dates
    target_date = (parsed.get("end_date") or "")[:10] or None
    forecasts = build_ensemble(info["city"], lat, lon, target_date, parsed["question"].lower())
    if not forecasts:
        return None

    # Skip if sources disagree — conflicting signals mean uncertain edge
    agree, spread = sources_agree(forecasts)
    if not agree:
        log.debug(
            f"SKIP (sources disagree {spread:.1f}°F > {SOURCE_DISAGREE_MAX_F}°F): "
            f"{parsed['question'][:60]}"
        )
        return None

    model_prob = ensemble_prob(forecasts, info["threshold_f"], info["direction"])
    if model_prob is None:
        return None

    raw_edge = model_prob - parsed["yes_price"]
    edge = raw_edge - (SPREAD_COST if raw_edge > 0 else -SPREAD_COST)
    if abs(edge) < MIN_EDGE:
        return None

    trade = "BUY YES" if edge > 0 else "BUY NO"
    # Price floors — model is unreliable at extremes
    if trade == "BUY YES" and parsed["yes_price"] < YES_PRICE_FLOOR:
        return None
    if trade == "BUY NO" and parsed["no_price"] < NO_PRICE_FLOOR:
        return None

    tp = parsed["yes_price"] if trade == "BUY YES" else parsed["no_price"]
    mp = model_prob if trade == "BUY YES" else 1 - model_prob
    kelly = max(0, (mp * (1 - tp) - (1 - mp) * tp) / (1 - tp)) if 0 < tp < 1 else 0
    ev    = mp * (1 - tp) - (1 - mp) * tp
    src   = " + ".join(f"{f['src']}({f['mu']:.1f}°F)" for f in forecasts)

    return {
        **parsed, **info,
        "market_type": "directional",
        "model_prob": model_prob, "edge": edge, "trade": trade,
        "ev": ev, "kelly": kelly, "sources": src,
        "n_sources": len(forecasts),
    }


def analyze_bracket_leg1(market_raw):
    """
    Leg 1: flag bracket market NO when:
      - NO price > MIN_BRACKET_NO_PRICE (0.93)
      - Our ensemble says bracket is unlikely (model_prob_YES < 0.07)
    Returns opportunity dict or None.
    """
    parsed = parse_market(market_raw)
    if not parsed:
        return None
    mtype, info = classify_market(parsed["question"])
    if mtype != "bracket":
        return None

    # Only trade NO on brackets we can cheaply buy
    if parsed["no_price"] < MIN_BRACKET_NO_PRICE:
        return None

    lat, lon = info["coords"]
    # Use API endDate (YYYY-MM-DD) as target — question text never has ISO dates
    target_date = (parsed.get("end_date") or "")[:10] or None
    forecasts = build_ensemble(info["city"], lat, lon, target_date, parsed["question"].lower())
    if not forecasts:
        return None

    # For brackets: probability that temp lands in [lo, hi]
    tw = sum(f["weight"] for f in forecasts)
    model_prob_yes = sum(
        f["weight"] / tw * (
            calc_prob(f["mu"], f["sigma"], info["bracket_lo_f"], "above") -
            calc_prob(f["mu"], f["sigma"], info["bracket_hi_f"], "above")
        )
        for f in forecasts
    )
    model_prob_yes = max(0.0, model_prob_yes)

    # Leg 1: only buy NO when model says YES is unlikely
    if model_prob_yes > 0.07:
        return None

    edge = parsed["no_price"] - (1 - model_prob_yes)
    if edge < 0.01:
        return None

    src = " + ".join(f"{f['src']}({f['mu']:.1f}°F)" for f in forecasts)
    return {
        **parsed, **info,
        "market_type": "bracket_leg1",
        "model_prob_yes": model_prob_yes,
        "model_prob_no": 1 - model_prob_yes,
        "edge": edge,
        "trade": "BUY NO",
        "ev": (1 - model_prob_yes) * (1 - parsed["no_price"]) - model_prob_yes * parsed["no_price"],
        "sources": src,
        "n_sources": len(forecasts),
    }


# ─────────────────────────────────────────────────────────
# Main scan loop
# ─────────────────────────────────────────────────────────

def run_scan():
    log.info("=" * 60)
    log.info(f"Scan started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
             f"[daily-snipe: ≤{MAX_DAYS_OUT}d, ≥${MIN_VOLUME:,.0f} vol, edge≥{MIN_EDGE*100:.0f}%]")

    # Clear forecast cache at the start of each scan
    clear_forecast_cache()

    markets = get_weather_markets()
    directional_opps, bracket_opps = [], []

    for m in markets:
        q = (m.get("question") or "").lower()
        mtype, _ = classify_market(q) if q else (None, None)

        if mtype == "directional":
            r = analyze_directional(m)
            if r:
                directional_opps.append(r)
        elif mtype == "bracket":
            r = analyze_bracket_leg1(m)
            if r:
                bracket_opps.append(r)

    all_opps = directional_opps + bracket_opps
    log.info(
        f"Opportunities: {len(directional_opps)} directional, "
        f"{len(bracket_opps)} bracket Leg-1"
    )

    for o in sorted(all_opps, key=lambda x: abs(x["edge"]), reverse=True):
        if o["market_type"] == "directional":
            log.info(
                f"\n[DIRECTIONAL] {o['question']}\n"
                f"  City: {o['city']} | Threshold: {o['threshold_f']:.1f}°F {o['direction']}\n"
                f"  Market YES: {o['yes_price']*100:.1f}% | Model YES: {o['model_prob']*100:.1f}%\n"
                f"  Edge: {o['edge']*100:+.1f}% | Trade: {o['trade']} | Sources({o['n_sources']}): {o['sources']}\n"
                f"  EV: {o['ev']:+.4f} | Kelly: {o['kelly']*100:.1f}% | Vol: ${o['volume']:,.0f}"
            )
        else:
            log.info(
                f"\n[BRACKET LEG1] {o['question']}\n"
                f"  City: {o['city']} | Range: {o['bracket_lo_f']:.1f}–{o['bracket_hi_f']:.1f}°F\n"
                f"  NO price: {o['no_price']*100:.1f}¢ | Model YES prob: {o['model_prob_yes']*100:.1f}%\n"
                f"  Edge: {o['edge']*100:+.1f}% | Trade: BUY NO | Sources({o['n_sources']}): {o['sources']}\n"
                f"  Vol: ${o['volume']:,.0f}"
            )

    # Write opportunities to shared file for research_engine.py
    try:
        with open(OPPORTUNITIES_F, "w") as f:
            json.dump({
                "scan_time": datetime.now(timezone.utc).isoformat(),
                "directional": directional_opps,
                "bracket_leg1": bracket_opps,
            }, f, indent=2, default=str)
    except Exception as e:
        log.error(f"Failed to write opportunities.json: {e}")

    return all_opps


if __name__ == "__main__":
    log.info(
        f"Polymarket Weather Bot v2 | "
        f"min_edge={MIN_EDGE*100:.0f}% | interval={SCAN_INTERVAL}s | "
        f"sources=open-meteo+tomorrow.io+nws"
    )
    while True:
        try:
            run_scan()
        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
        log.info(f"Next scan in {SCAN_INTERVAL}s")
        time.sleep(SCAN_INTERVAL)
