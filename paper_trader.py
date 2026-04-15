#!/usr/bin/env python3
"""
Paper Trader — reads opportunities from bot.py, places virtual trades,
resolves them, tracks P&L.

Deploy to: /root/paper_trader.py on DigitalOcean droplet 164.92.133.253
"""
import json, os, time, logging, requests, sys
from datetime import datetime, timezone

from config import (
    GAMMA_API, STATE_FILE, PAPER_LOG,
    KELLY_FRACTION, MAX_POSITION_PCT, MAX_STAKE_DOLLARS, MAX_OPEN_POSITIONS,
    MIN_EDGE, SCAN_INTERVAL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(PAPER_LOG), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

STARTING_BANKROLL = 1000.0


# ─────────────────────────────────────────────────────────
# State management
# ─────────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "bankroll": STARTING_BANKROLL,
        "starting_bankroll": STARTING_BANKROLL,
        "open_trades": [],
        "closed_trades": [],
        "stats": {"wins": 0, "losses": 0, "total_pnl": 0.0, "total_wagered": 0.0},
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ─────────────────────────────────────────────────────────
# Market resolution
# ─────────────────────────────────────────────────────────

def get_market_resolution(market_id):
    try:
        r = requests.get(f"{GAMMA_API}/markets/{market_id}", timeout=10)
        r.raise_for_status()
        m = r.json()
        if m.get("closed") and m.get("resolutionSource"):
            outcomes = json.loads(m.get("outcomes", '["Yes","No"]'))
            prices   = json.loads(m.get("outcomePrices", "[0,0]"))
            for i, p in enumerate(prices):
                if float(p) >= 0.99:
                    return outcomes[i]
        return None
    except Exception as e:
        log.debug(f"Resolution check error for {market_id}: {e}")
        return None


# ─────────────────────────────────────────────────────────
# Trade placement
# ─────────────────────────────────────────────────────────

def already_have_position(state, market_id, side):
    """Block any second trade on the same market (either side).
    Holding YES+NO simultaneously guarantees one loss."""
    return any(t["market_id"] == market_id for t in state["open_trades"])


def place_paper_trade(state, opportunity):
    bankroll = state["bankroll"]
    is_yes   = opportunity["trade"] in ("BUY YES",)
    prob     = opportunity.get("model_prob", 0.5) if is_yes else 1 - opportunity.get("model_prob", 0.5)
    price    = opportunity["yes_price"] if is_yes else opportunity["no_price"]
    side     = "Yes" if is_yes else "No"

    if price <= 0 or price >= 1:
        return None

    b     = (1 - price) / price
    kelly = max(0, (prob * b - (1 - prob)) / b)
    stake = min(
        bankroll * kelly * KELLY_FRACTION,
        bankroll * MAX_POSITION_PCT,
        MAX_STAKE_DOLLARS,          # hard cap: max $15 per trade
    )
    stake = round(max(stake, 0.50), 2)
    if stake > bankroll:
        return None

    shares = stake / price
    trade = {
        "id":             f"PT{int(time.time())}",
        "market_id":      opportunity["id"],
        "question":       opportunity["question"],
        "city":           opportunity.get("city", ""),
        "market_type":    opportunity.get("market_type", "directional"),
        "side":           side,
        "entry_price":    price,
        "stake":          stake,
        "shares":         shares,
        "edge_at_entry":  opportunity.get("edge", 0),
        "model_prob":     opportunity.get("model_prob", prob),
        "kelly_pct":      kelly * 100,
        "end_date":       opportunity.get("end_date"),
        "opened_at":      datetime.now(timezone.utc).isoformat(),
        "status":         "open",
        "sources":        opportunity.get("sources", ""),
        "n_sources":      opportunity.get("n_sources", 1),
    }
    state["bankroll"] -= stake
    state["open_trades"].append(trade)
    save_state(state)
    log.info(
        f"📝 {trade['id']} [{trade['market_type']}] BUY {side} @ {price:.3f} | "
        f"Stake: ${stake:.2f} | Edge: {opportunity.get('edge',0)*100:+.1f}% | "
        f"Sources: {trade['n_sources']}"
    )
    return trade


# ─────────────────────────────────────────────────────────
# Resolution check
# ─────────────────────────────────────────────────────────

def check_resolutions(state):
    resolved = []
    for trade in state["open_trades"]:
        result = get_market_resolution(trade["market_id"])
        if result:
            won = result.lower() == trade["side"].lower()
            pnl = round(
                (trade["shares"] * (1.0 - trade["entry_price"])) if won else -trade["stake"],
                4,
            )
            trade.update({
                "status":    "won" if won else "lost",
                "result":    result,
                "pnl":       pnl,
                "closed_at": datetime.now(timezone.utc).isoformat(),
            })
            state["bankroll"] += (trade["stake"] + pnl) if won else 0
            state["stats"]["wins"]          += 1 if won else 0
            state["stats"]["losses"]        += 0 if won else 1
            state["stats"]["total_pnl"]     += pnl
            state["stats"]["total_wagered"] += trade["stake"]
            resolved.append(trade)
            log.info(
                f"{'✅ WON' if won else '❌ LOST'}: {trade['question'][:60]} | "
                f"P&L: ${pnl:+.2f} | Type: {trade.get('market_type','?')}"
            )
    if resolved:
        state["open_trades"]   = [t for t in state["open_trades"] if t["status"] == "open"]
        state["closed_trades"].extend(resolved)
        save_state(state)


def print_report(state):
    s     = state["stats"]
    total = s["wins"] + s["losses"]
    roi   = (s["total_pnl"] / s["total_wagered"] * 100) if s["total_wagered"] else 0
    ret   = (state["bankroll"] - state["starting_bankroll"]) / state["starting_bankroll"] * 100
    log.info("\n" + "=" * 60)
    log.info("📊 PAPER TRADING REPORT")
    log.info(f"  Bankroll:    ${state['bankroll']:.2f} (started ${state['starting_bankroll']:.2f})")
    log.info(f"  Total Return:{ret:+.1f}%")
    log.info(f"  Closed:      {total} trades ({s['wins']}W / {s['losses']}L)")
    log.info(f"  Win Rate:    {(s['wins']/total*100) if total else 0:.1f}%")
    log.info(f"  P&L:         ${s['total_pnl']:+.2f} | ROI: {roi:+.1f}%")
    log.info(f"  Open:        {len(state['open_trades'])} positions")
    log.info("=" * 60)


# ─────────────────────────────────────────────────────────
# Scan + trade loop
# ─────────────────────────────────────────────────────────

sys.path.insert(0, "/root")
from bot import get_weather_markets, parse_market, classify_market, analyze_directional, analyze_bracket_leg1
from tail_end_scanner import run_tail_scan


def run_scan_and_trade(state):
    log.info(f"Scan: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Pause new positions when at cap
    if len(state["open_trades"]) >= MAX_OPEN_POSITIONS:
        log.info(f"At position cap ({MAX_OPEN_POSITIONS}) — skipping new trades, checking resolutions")
        check_resolutions(state)
        print_report(state)
        return

    markets = get_weather_markets()
    placed = 0

    for m in markets:
        try:
            parsed = parse_market(m)
            if not parsed:
                continue

            mtype, info = classify_market(parsed["question"])

            if mtype == "directional":
                opp = analyze_directional(m)
            elif mtype == "bracket":
                opp = analyze_bracket_leg1(m)
            else:
                continue

            if not opp:
                continue

            is_yes = opp["trade"] == "BUY YES"
            side   = "Yes" if is_yes else "No"
            if already_have_position(state, parsed["id"], side):
                continue

            if len(state["open_trades"]) >= MAX_OPEN_POSITIONS:
                log.info(f"Hit position cap ({MAX_OPEN_POSITIONS}) mid-scan — stopping")
                break
            if place_paper_trade(state, opp):
                placed += 1
        except Exception as e:
            log.debug(f"Trade error: {e}")
        time.sleep(0.1)

    # Also place tail-end trades
    tail_opps = run_tail_scan()
    for opp in tail_opps:
        if len(state["open_trades"]) >= MAX_OPEN_POSITIONS:
            break
        side = opp.get("winning_side", "Yes")
        if already_have_position(state, opp["id"], side):
            continue
        opp["trade"] = f"BUY {side}"
        # For tail-end trades: probability is near-certain
        opp["model_prob"] = 0.97
        if place_paper_trade(state, opp):
            placed += 1

    log.info(f"Placed {placed} new paper trades")
    check_resolutions(state)
    print_report(state)


def main():
    log.info("Paper Trader v2 starting (directional + bracket Leg-1 + tail-end)...")
    state = load_state()
    while True:
        try:
            run_scan_and_trade(state)
        except Exception as e:
            log.error(f"Error: {e}", exc_info=True)
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
