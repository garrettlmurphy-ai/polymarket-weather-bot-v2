#!/usr/bin/env python3
"""
Calibration & performance analysis for the paper trader.

Answers the two questions that decide whether this bot can go live:
  1. Is the edge real?      → realized win-rate / ROI, net of the modeled costs.
  2. Is the model honest?   → a reliability curve + Brier score, and an
                              empirical best-fit multiplier for the SIGMA table.

Reads the closed_trades in paper_trades.json (STATE_FILE) and prints a report.
Run:  python calibrate.py            # uses STATE_FILE from config
      python calibrate.py trades.json

Nothing here places or modifies trades — it only reads history.
"""
import json, sys, math
from datetime import datetime
from collections import defaultdict

from scipy import stats

from config import STATE_FILE, SIGMA_BY_DAY


# ─────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────

def load_closed(path):
    with open(path) as f:
        state = json.load(f)
    return state, [t for t in state.get("closed_trades", []) if t.get("status") in ("won", "lost")]


def yes_happened(trade):
    """1 if the market resolved YES, 0 if NO, None if unknown."""
    result = (trade.get("result") or "").lower()
    if result in ("yes", "no"):
        return 1 if result == "yes" else 0
    # Fall back to side + won/lost
    side = (trade.get("side") or "").lower()
    won = trade.get("status") == "won"
    if side == "yes":
        return 1 if won else 0
    if side == "no":
        return 0 if won else 1
    return None


def side_prob(trade):
    """Model's probability for the SIDE actually traded (for reliability curve)."""
    return trade.get("model_prob")


def days_out(trade):
    try:
        opened = datetime.fromisoformat(trade["opened_at"].replace("Z", "+00:00")).date()
        end = datetime.fromisoformat((trade["end_date"] or "")[:10]).date()
        return max(0, (end - opened).days)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────
# Headline performance
# ─────────────────────────────────────────────────────────

def performance(state, closed):
    wins = sum(1 for t in closed if t["status"] == "won")
    losses = len(closed) - wins
    pnl = sum(t.get("pnl", 0) for t in closed)
    wagered = sum(t.get("stake", 0) for t in closed)
    print("=" * 60)
    print("PERFORMANCE (net of modeled costs)")
    print("=" * 60)
    print(f"  Closed trades : {len(closed)}  ({wins}W / {losses}L)")
    if closed:
        print(f"  Win rate      : {wins/len(closed)*100:.1f}%")
    print(f"  Total P&L     : ${pnl:+.2f}")
    if wagered:
        print(f"  ROI on wagered: {pnl/wagered*100:+.1f}%")
    # Per-type breakdown — the strategies have very different risk profiles
    by_type = defaultdict(lambda: [0, 0, 0.0])  # wins, n, pnl
    for t in closed:
        row = by_type[t.get("market_type", "?")]
        row[0] += 1 if t["status"] == "won" else 0
        row[1] += 1
        row[2] += t.get("pnl", 0)
    print("\n  By strategy:")
    for mtype, (w, n, p) in sorted(by_type.items()):
        print(f"    {mtype:16s}  {n:4d} trades  {w/n*100:5.1f}% win  ${p:+8.2f} P&L")


# ─────────────────────────────────────────────────────────
# Reliability curve + Brier score
# ─────────────────────────────────────────────────────────

def reliability(closed):
    print("\n" + "=" * 60)
    print("RELIABILITY  (does 70% predicted actually win ~70%?)")
    print("=" * 60)
    buckets = defaultdict(lambda: [0, 0])  # predicted-side won, n
    brier_sum, brier_n = 0.0, 0
    for t in closed:
        p = side_prob(t)
        if p is None:
            continue
        won = 1 if t["status"] == "won" else 0
        b = int(min(p, 0.999) * 10)  # 0.0-0.1 -> 0, ... 0.9-1.0 -> 9
        buckets[b][0] += won
        buckets[b][1] += 1
        brier_sum += (p - won) ** 2
        brier_n += 1
    if not brier_n:
        print("  Not enough trades with a stored model_prob yet.")
        return
    print(f"  {'predicted':>12} {'actual':>8} {'n':>6}")
    for b in range(10):
        w, n = buckets[b]
        if n == 0:
            continue
        lo, hi = b * 10, b * 10 + 10
        print(f"  {lo:3d}-{hi:<3d}%    {w/n*100:6.1f}%  {n:6d}")
    print(f"\n  Brier score   : {brier_sum/brier_n:.4f}  (0=perfect, 0.25=coin-flip)")


# ─────────────────────────────────────────────────────────
# Empirical sigma multiplier — the core calibration
# ─────────────────────────────────────────────────────────

def _ensemble_p_yes(mus, weights, threshold, direction, sigma):
    tw = sum(weights) or 1.0
    def one(mu):
        z = (threshold - mu) / sigma
        return (1 - stats.norm.cdf(z)) if direction == "above" else stats.norm.cdf(z)
    return sum(wi / tw * one(mu) for mu, wi in zip(mus, weights))


def fit_sigma_multiplier(closed):
    """
    Find the multiplier k on SIGMA_BY_DAY that best fits realized outcomes
    (minimizes log-loss). k>1 means real forecasts are MORE uncertain than the
    table assumes (current edges are overstated); k<1 means less.
    """
    print("\n" + "=" * 60)
    print("SIGMA CALIBRATION  (directional trades with stored forecasts)")
    print("=" * 60)
    usable = []
    for t in closed:
        if t.get("market_type") != "directional":
            continue
        mus = t.get("forecast_mus")
        thr = t.get("threshold_f")
        direction = t.get("direction")
        d = days_out(t)
        y = yes_happened(t)
        if not mus or thr is None or direction not in ("above", "below") or d is None or y is None:
            continue
        weights = t.get("forecast_weights") or [1.0] * len(mus)
        usable.append((mus, weights, thr, direction, d, y))

    if len(usable) < 20:
        print(f"  Only {len(usable)} usable directional trades with forecast data.")
        print("  Need ~20+ (the enriched trade fields are new — this fills in over time).")
        return

    best_k, best_loss = None, float("inf")
    for k in [round(0.5 + 0.05 * i, 2) for i in range(0, 41)]:  # 0.50 .. 2.50
        loss = 0.0
        for mus, weights, thr, direction, d, y in usable:
            sigma = SIGMA_BY_DAY.get(d, 12.0) * k
            p = min(max(_ensemble_p_yes(mus, weights, thr, direction, sigma), 1e-6), 1 - 1e-6)
            loss += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        loss /= len(usable)
        if loss < best_loss:
            best_loss, best_k = loss, k

    print(f"  Usable trades      : {len(usable)}")
    print(f"  Best sigma × factor: {best_k}   (log-loss {best_loss:.4f})")
    if best_k > 1.15:
        print("  → Forecasts are noisier than the table assumes. Current edges are")
        print(f"    OVERSTATED; multiply SIGMA_BY_DAY by ~{best_k} before trusting them.")
    elif best_k < 0.85:
        print("  → Forecasts are sharper than the table assumes; edges are understated.")
    else:
        print("  → SIGMA_BY_DAY is roughly well-calibrated.")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else STATE_FILE
    try:
        state, closed = load_closed(path)
    except FileNotFoundError:
        print(f"No state file at {path}. Run the paper trader first.")
        return
    if not closed:
        print("No closed trades yet — nothing to calibrate.")
        return
    performance(state, closed)
    reliability(closed)
    fit_sigma_multiplier(closed)


if __name__ == "__main__":
    main()
