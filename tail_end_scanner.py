#!/usr/bin/env python3
"""
Known-Outcome Scanner — "find trades where we already know the answer"

Strategy:
  Scan markets resolving TODAY where the temperature outcome is already
  physically determined — observed temp is past the threshold with a safety
  margin — but the market is still trading at a discount.

  This is pure certainty arbitrage:
    1. Get the CURRENT observed temperature from NOAA (official resolution source)
       or Tomorrow.io realtime for international cities.
    2. If observed temp is already X°F clear of the threshold in the winning direction
       → outcome is known with near-certainty.
    3. If market price hasn't caught up (≥3¢ edge remaining) → flag the trade.
    4. No time-of-day heuristic. Uses the DATA to decide, not the clock.

Why this beats the forecast scanner:
  - Forecast scanner: we think the temp WILL be X (probabilistic)
  - Known-outcome scanner: the temp IS already X (near-certain)
  - Win rate should be 95%+, not 70–80%

Also catches "impatient seller" discount:
  Winning-side holders sometimes sell at 94–98¢ to free up capital for
  the next trade rather than waiting for UMA settlement. We buy those shares.

Run: continuously, every SCAN_INTERVAL seconds, all day.
Deploy: /root/tail_end_scanner.py
"""
import json, time, logging, requests, re, concurrent.futures
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import (
    GAMMA_API, NOAA_OBS, TOMORROW_REALTIME, TOMORROW_IO_KEY,
    KNOWN_SAFETY_MARGIN_F, MIN_TAIL_EDGE, KNOWN_OUTCOME_DAYS,
    MIN_VOLUME, SCAN_INTERVAL, TAIL_LOG, OPPORTUNITIES_F, CITIES,
)

# Local log path fallback for running outside droplet
_log_path = TAIL_LOG if Path("/root").exists() else str(Path(__file__).parent / "tail_end.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [KNOWN] %(message)s",
    handlers=[logging.FileHandler(_log_path), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Live temperature reading
# ─────────────────────────────────────────────────────────

def get_noaa_current(station_id):
    """
    Current observed temperature in °F from NOAA station.
    This is the SAME data source the UMA oracle will use to resolve the market.
    """
    try:
        url = NOAA_OBS.format(station_id=station_id)
        r = requests.get(url, timeout=10, headers={"User-Agent": "PolymarketBot/2.0"})
        if r.status_code != 200:
            return None, None
        props = r.json().get("properties", {})
        temp_c = props.get("temperature", {}).get("value")
        if temp_c is None:
            return None, None
        obs_time = props.get("timestamp", "")
        return temp_c * 9/5 + 32, obs_time
    except Exception as e:
        log.debug(f"NOAA {station_id} error: {e}")
        return None, None


def get_tomorrow_realtime(lat, lon):
    """Current temperature in °F via Tomorrow.io realtime (international cities)."""
    try:
        r = requests.get(
            TOMORROW_REALTIME,
            params={
                "location": f"{lat},{lon}",
                "units": "imperial",
                "apikey": TOMORROW_IO_KEY,
            },
            timeout=10,
        )
        if r.status_code == 429:
            log.warning("Tomorrow.io realtime rate limit")
            return None, None
        r.raise_for_status()
        vals = r.json().get("data", {}).get("values", {})
        return vals.get("temperature"), "tomorrow.io-realtime"
    except Exception as e:
        log.debug(f"Tomorrow.io realtime error: {e}")
        return None, None


# Cache realtime readings per city for this scan cycle — avoid repeat calls
_realtime_cache: dict = {}

def _fetch_one_city(city_name):
    """Fetch temp for one city — called in thread pool."""
    city_data = CITIES.get(city_name)
    if not city_data:
        return city_name, None, None
    station = city_data.get("noaa_station")
    if station:
        t, _ = get_noaa_current(station)
        if t is not None:
            return city_name, t, f"NOAA:{station}"
    lat, lon = city_data["coords"]
    t, label = get_tomorrow_realtime(lat, lon)
    return city_name, t, label


def prefetch_cities(city_names):
    """
    Fetch temperatures for a list of cities in parallel (thread pool).
    Populates _realtime_cache. Max 8 concurrent threads, 15s total timeout.
    """
    needed = [c for c in city_names if c not in _realtime_cache]
    if not needed:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch_one_city, c): c for c in needed}
        done, _ = concurrent.futures.wait(futs, timeout=15)
        for fut in done:
            city, temp, label = fut.result()
            if temp is not None:
                _realtime_cache[city] = (temp, label)


def get_current_temp_f(city_name):
    """Returns (temp_f, source_label) from cache (populated by prefetch_cities)."""
    return _realtime_cache.get(city_name, (None, None))


# ─────────────────────────────────────────────────────────
# Market discovery — today only
# ─────────────────────────────────────────────────────────

WEATHER_KEYWORDS = [
    "temperature", "weather", "degrees", "fahrenheit", "celsius",
    "high temp", "low temp", "above", "below", "highest temp", "lowest temp",
]

def get_todays_markets():
    """
    Fetch weather markets resolving within KNOWN_OUTCOME_DAYS (today only).
    These are the only markets where we can have a known outcome.
    """
    markets, offset = [], 0
    today = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=KNOWN_OUTCOME_DAYS)

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
            for m in data:
                q = (m.get("question") or "").lower()
                if not any(kw in q for kw in WEATHER_KEYWORDS):
                    continue
                if float(m.get("volume") or 0) < MIN_VOLUME:
                    continue
                end_raw = m.get("endDate") or ""
                if end_raw:
                    try:
                        end_date = datetime.fromisoformat(
                            end_raw.replace("Z", "+00:00")
                        ).date()
                        if end_date > cutoff:
                            continue
                    except ValueError:
                        pass
                markets.append(m)
            if len(data) < 100:
                break
            offset += 100
            time.sleep(0.3)
        except Exception as e:
            log.error(f"Market fetch error: {e}")
            break

    log.info(f"Today's markets: {len(markets)} eligible (resolving today, ≥${MIN_VOLUME:,.0f} vol)")
    return markets


# ─────────────────────────────────────────────────────────
# Market parsing
# ─────────────────────────────────────────────────────────

def parse_directional_market(m):
    """
    Extract: city, threshold_f, direction, yes_price, no_price.
    Returns None if not a parseable directional market.
    """
    try:
        prices   = json.loads(m.get("outcomePrices", "[0.5,0.5]"))
        outcomes = json.loads(m.get("outcomes", '["Yes","No"]'))
        yes_idx  = 0 if outcomes[0].lower() == "yes" else 1
        yes_price = float(prices[yes_idx])
    except:
        return None

    q = (m.get("question") or "").lower()

    # Identify city
    city, city_data = None, None
    for name, data in CITIES.items():
        if name in q:
            city, city_data = name, data
            break
    if not city:
        return None

    # Direction
    if any(w in q for w in ["above", "exceed", "over", "reach", "at least"]):
        direction = "above"
    elif any(w in q for w in ["below", "under", "less than", "or below", "or lower"]):
        direction = "below"
    else:
        return None

    # Threshold
    tm = (
        re.search(r'(\d+(?:\.\d+)?)\s*[°]?\s*([fc])\b', q) or
        re.search(r'(\d+(?:\.\d+)?)\s*degrees?\s*([fc])\b', q)
    )
    if tm:
        val  = float(tm.group(1))
        unit = tm.group(2).lower()
        threshold_f = val if unit == "f" else val * 9/5 + 32
    else:
        tm2 = re.search(r'\b([6-9]\d|1[0-1]\d)\b', q)
        threshold_f = float(tm2.group(1)) if tm2 else None
    if threshold_f is None:
        return None

    # "highest temp" → use high; "lowest temp" → use low
    temp_type = "high" if any(w in q for w in ["highest", "high temp", "max"]) else \
                "low"  if any(w in q for w in ["lowest", "low temp", "min"])  else "high"

    return {
        "id":          m.get("id"),
        "question":    m.get("question", ""),
        "city":        city,
        "city_data":   city_data,
        "threshold_f": threshold_f,
        "direction":   direction,
        "temp_type":   temp_type,
        "yes_price":   yes_price,
        "no_price":    1 - yes_price,
        "volume":      float(m.get("volume") or 0),
        "liquidity":   float(m.get("liquidity") or 0),
        "end_date":    m.get("endDate"),
    }


# ─────────────────────────────────────────────────────────
# Known-outcome detection — THE CORE LOGIC
# ─────────────────────────────────────────────────────────

def check_known_outcome(market):
    """
    Data-driven certainty check.

    For "above X°F" markets:
      - If observed temp is already >= threshold + safety_margin → YES will win
        (temperature can't un-happen; once you hit X, the market resolves YES)

    For "below X°F" markets (daily HIGH):
      - If observed temp is already > threshold + safety_margin → NO will win
        (the high has already exceeded the threshold — market will NOT resolve YES)
      - If observed temp currently <= threshold - safety_margin AND we are past
        the time window where further rise is very unlikely (past local peak hour
        with a large margin) — then YES will win. We check this conservatively.

    Returns opportunity dict or None.
    """
    city      = market["city"]
    direction = market["direction"]
    threshold = market["threshold_f"]

    current_temp, temp_source = get_current_temp_f(city)
    if current_temp is None:
        return None

    winning_side = None
    certainty_note = ""

    if direction == "above":
        # "Will temp be ABOVE X?" → YES wins if temp already exceeded X by margin
        if current_temp >= threshold + KNOWN_SAFETY_MARGIN_F:
            winning_side  = "Yes"
            certainty_note = (
                f"Observed {current_temp:.1f}°F ≥ threshold {threshold:.1f}°F "
                f"+ {KNOWN_SAFETY_MARGIN_F}°F margin → YES confirmed"
            )
        # → NO wins if temp is so far below threshold it cannot plausibly recover
        # (conservative: only if >15°F below and within 2h of market close — skip for now)

    else:  # direction == "below"
        # "Will temp be BELOW X?" → NO wins if temp already exceeded X by margin
        # (daily high already broke the threshold — question resolves NO)
        if current_temp >= threshold + KNOWN_SAFETY_MARGIN_F:
            winning_side  = "No"
            certainty_note = (
                f"Observed {current_temp:.1f}°F ≥ threshold {threshold:.1f}°F "
                f"+ {KNOWN_SAFETY_MARGIN_F}°F margin → YES impossible, NO wins"
            )
        # YES wins if temp is well below threshold and we're past peak hour
        elif current_temp <= threshold - KNOWN_SAFETY_MARGIN_F:
            city_data = market["city_data"]
            peak_utc  = city_data.get("peak_hour_utc", 20)
            now_utc   = datetime.now(timezone.utc).hour
            # Only call YES confirmed if we're at least 1h past peak
            if now_utc >= peak_utc + 1:
                winning_side  = "Yes"
                certainty_note = (
                    f"Observed {current_temp:.1f}°F ≤ threshold {threshold:.1f}°F "
                    f"- {KNOWN_SAFETY_MARGIN_F}°F margin, past peak hour ({peak_utc}UTC) "
                    f"→ daily high locked in, YES wins"
                )

    if not winning_side:
        return None

    entry_price = market["yes_price"] if winning_side == "Yes" else market["no_price"]
    edge        = 1.0 - entry_price  # profit per share if we're right

    if edge < MIN_TAIL_EDGE:
        log.debug(
            f"SKIP (edge {edge*100:.1f}¢ < {MIN_TAIL_EDGE*100:.0f}¢): "
            f"{market['question'][:60]}"
        )
        return None

    return {
        **market,
        "market_type":    "known_outcome",
        "winning_side":   winning_side,
        "entry_price":    entry_price,
        "edge":           edge,
        "trade":          f"BUY {winning_side}",
        "return_pct":     edge / entry_price * 100,
        "current_temp_f": current_temp,
        "temp_source":    temp_source,
        "certainty_note": certainty_note,
        "model_prob":     0.97,   # used by paper_trader for Kelly sizing
    }


# ─────────────────────────────────────────────────────────
# Main scan
# ─────────────────────────────────────────────────────────

def run_known_outcome_scan():
    _realtime_cache.clear()
    log.info(
        f"Known-outcome scan {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')} "
        f"| safety_margin={KNOWN_SAFETY_MARGIN_F}°F | min_edge={MIN_TAIL_EDGE*100:.0f}¢"
    )

    markets = get_todays_markets()

    # Pre-parse to find which cities appear in today's markets, then batch-fetch temps
    cities_needed = set()
    parsed_markets = []
    for m in markets:
        p = parse_directional_market(m)
        if p:
            cities_needed.add(p["city"])
            parsed_markets.append(p)

    log.info(f"Fetching live temps for {len(cities_needed)} cities in parallel...")
    prefetch_cities(list(cities_needed))
    log.info(f"Temp fetch done. Checking {len(parsed_markets)} directional markets...")

    opps = []

    for parsed in parsed_markets:
        try:
            result = check_known_outcome(parsed)
            if result:
                opps.append(result)
        except Exception as e:
            log.debug(f"Market error: {e}")

    log.info(f"Known outcomes found: {len(opps)}")

    for o in sorted(opps, key=lambda x: x["edge"], reverse=True):
        log.info(
            f"\n[KNOWN OUTCOME] {o['question']}\n"
            f"  {o['certainty_note']}\n"
            f"  Source: {o['temp_source']}\n"
            f"  BUY {o['winning_side']} @ {o['entry_price']*100:.1f}¢ → pays $1.00\n"
            f"  Edge: {o['edge']*100:.1f}¢/share | Return: {o['return_pct']:.1f}% | "
            f"Vol: ${o['volume']:,.0f} | Liq: ${o['liquidity']:,.0f}"
        )

    _merge_opportunities(opps)
    return opps


def _merge_opportunities(known_opps):
    try:
        try:
            with open(OPPORTUNITIES_F) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"directional": [], "bracket_leg1": []}
        data["known_outcome"]      = known_opps
        data["known_scan_time"]    = datetime.now(timezone.utc).isoformat()
        with open(OPPORTUNITIES_F, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        log.error(f"Failed to update opportunities.json: {e}")


# Backward compat alias used by paper_trader.py
def run_tail_scan():
    return run_known_outcome_scan()


if __name__ == "__main__":
    log.info(
        f"Known-Outcome Scanner | "
        f"safety_margin={KNOWN_SAFETY_MARGIN_F}°F | "
        f"min_edge={MIN_TAIL_EDGE*100:.0f}¢ | "
        f"resolving=today-only"
    )
    while True:
        try:
            run_known_outcome_scan()
        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
        time.sleep(SCAN_INTERVAL)
