#!/usr/bin/env python3
"""
Unified Research Engine — Polymarket Intelligence Hub
Bridges Obsidian vault research ↔ live bot data ↔ daily reports.

What it does:
  1. Reads vault research (Prediction Markets/) for context
  2. Loads live opportunities.json (written by bot.py + tail_end_scanner.py)
  3. Loads paper_trades.json for P&L status
  4. Generates a structured daily Obsidian note:
       TheBrain/Prediction Markets/Daily Reports/YYYY-MM-DD Research Brief.md
  5. Logs key metrics to research_engine.log

Run: once per day, or on-demand.

On droplet: /root/research_engine.py
Local vault: C:/TheBrain/Prediction Markets/Daily Reports/
"""
import json, os, time, logging, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

import sys as _sys

from config import (
    GAMMA_API, OPPORTUNITIES_F, STATE_FILE, RESEARCH_LOG,
    MIN_EDGE, MIN_TAIL_EDGE, MIN_BRACKET_NO_PRICE,
)

# Use a local log path when running outside the droplet (Windows)
_log_path = RESEARCH_LOG if RESEARCH_LOG.startswith("/root") and Path("/root").exists() \
            else str(Path(__file__).parent / "research_engine.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RESEARCH] %(message)s",
    handlers=[logging.FileHandler(_log_path), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# Obsidian vault path — update if vault location changes
VAULT_ROOT   = Path("/mnt/c/TheBrain")          # on droplet via WSL mount
VAULT_LOCAL  = Path("C:/TheBrain")               # native Windows path (local runs)
PRED_MARKETS = "Prediction Markets"
DAILY_REPORTS = "Daily Reports"

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────
# Data loaders
# ─────────────────────────────────────────────────────────

def load_opportunities():
    try:
        with open(OPPORTUNITIES_F) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"directional": [], "bracket_leg1": [], "tail_end": []}


def load_paper_trades():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def fetch_polymarket_stats():
    """Quick summary of current weather market landscape."""
    try:
        total, with_volume = 0, 0
        r = requests.get(
            f"{GAMMA_API}/markets",
            params={"active": "true", "closed": "false", "limit": 100},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        weather_kw = ["temperature", "weather", "degrees", "fahrenheit", "celsius"]
        for m in data:
            q = (m.get("question") or "").lower()
            if any(k in q for k in weather_kw):
                total += 1
                if float(m.get("volume") or 0) > 100:
                    with_volume += 1
        return {"sampled_weather_markets": total, "with_volume_over_100": with_volume}
    except:
        return {}


# ─────────────────────────────────────────────────────────
# Report sections
# ─────────────────────────────────────────────────────────

def _pnl_section(pt):
    if not pt:
        return "_Paper trades file not found — run paper_trader.py first._\n"
    s  = pt["stats"]
    total = s["wins"] + s["losses"]
    win_rate = (s["wins"] / total * 100) if total else 0
    roi = (s["total_pnl"] / s["total_wagered"] * 100) if s["total_wagered"] else 0
    ret = (pt["bankroll"] - pt["starting_bankroll"]) / pt["starting_bankroll"] * 100

    lines = [
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Bankroll | ${pt['bankroll']:.2f} (started ${pt['starting_bankroll']:.2f}) |",
        f"| Total Return | {ret:+.1f}% |",
        f"| Closed Trades | {total} ({s['wins']}W / {s['losses']}L) |",
        f"| Win Rate | {win_rate:.1f}% |",
        f"| P&L | ${s['total_pnl']:+.2f} |",
        f"| ROI on wagered | {roi:+.1f}% |",
        f"| Open Positions | {len(pt['open_trades'])} |",
    ]
    return "\n".join(lines) + "\n"


def _open_positions_section(pt):
    if not pt or not pt["open_trades"]:
        return "_No open positions._\n"
    rows = ["| Market | Side | Entry | Stake | Edge at Entry | Opened |",
            "|--------|------|-------|-------|---------------|--------|"]
    for t in sorted(pt["open_trades"], key=lambda x: x.get("opened_at", ""), reverse=True)[:10]:
        rows.append(
            f"| {t['question'][:55]}… | {t['side']} | "
            f"{t['entry_price']:.3f} | ${t['stake']:.2f} | "
            f"{t['edge_at_entry']*100:+.1f}% | {t['opened_at'][:10]} |"
        )
    return "\n".join(rows) + "\n"


def _directional_opps_section(opps):
    if not opps:
        return "_No directional opportunities above threshold._\n"
    rows = ["| Market | City | Edge | Trade | Model% | Market% | Sources | Vol |",
            "|--------|------|------|-------|--------|---------|---------|-----|"]
    for o in sorted(opps, key=lambda x: abs(x["edge"]), reverse=True)[:15]:
        rows.append(
            f"| {o['question'][:50]}… | {o['city'].title()} | "
            f"{o['edge']*100:+.1f}% | {o['trade']} | "
            f"{o['model_prob']*100:.1f}% | {o['yes_price']*100:.1f}% | "
            f"{o.get('n_sources',1)} | ${o['volume']:,.0f} |"
        )
    return "\n".join(rows) + "\n"


def _bracket_opps_section(opps):
    if not opps:
        return "_No Leg-1 bracket opportunities found._\n"
    rows = ["| Market | Range | NO Price | Model YES% | Edge | Vol |",
            "|--------|-------|----------|------------|------|-----|"]
    for o in sorted(opps, key=lambda x: x["edge"], reverse=True)[:10]:
        rows.append(
            f"| {o['question'][:50]}… | "
            f"{o['bracket_lo_f']:.1f}–{o['bracket_hi_f']:.1f}°F | "
            f"{o['no_price']*100:.1f}¢ | {o['model_prob_yes']*100:.1f}% | "
            f"{o['edge']*100:+.1f}% | ${o['volume']:,.0f} |"
        )
    return "\n".join(rows) + "\n"


def _tail_end_opps_section(opps):
    if not opps:
        return "_No tail-end opportunities (scanner may be outside active window or all markets near 99¢)._\n"
    rows = ["| Market | City | Observed | Threshold | BUY | Entry | Edge | Return | Vol |",
            "|--------|------|----------|-----------|-----|-------|------|--------|-----|"]
    for o in sorted(opps, key=lambda x: x["tail_edge"], reverse=True)[:10]:
        rows.append(
            f"| {o['question'][:45]}… | {o['city'].title()} | "
            f"{o['current_temp_f']:.1f}°F | {o['threshold_f']:.1f}°F {o['direction']} | "
            f"{o['winning_side']} | {o['entry_price']*100:.1f}¢ | "
            f"{o['tail_edge']*100:.1f}¢ | {o['return_pct']:.1f}% | "
            f"${o['volume']:,.0f} |"
        )
    return "\n".join(rows) + "\n"


def _strategy_notes_section(opps_data, pt):
    """Auto-generated tactical notes based on today's data."""
    notes = []
    d = opps_data.get("directional", [])
    b = opps_data.get("bracket_leg1", [])
    t = opps_data.get("tail_end", [])

    if d:
        top = max(d, key=lambda x: abs(x["edge"]))
        notes.append(
            f"- **Top directional edge:** {top['edge']*100:+.1f}% on *{top['question'][:70]}* — "
            f"using {top.get('n_sources', '?')} weather sources"
        )
    if b:
        notes.append(
            f"- **Bracket Leg-1:** {len(b)} NO trades available above {MIN_BRACKET_NO_PRICE*100:.0f}¢ threshold"
        )
    if t:
        best = max(t, key=lambda x: x["tail_edge"])
        notes.append(
            f"- **Best tail-end trade:** BUY {best['winning_side']} @ {best['entry_price']*100:.1f}¢ "
            f"on *{best['question'][:60]}* — {best['return_pct']:.1f}% return, near risk-free"
        )
    if pt and pt["stats"]["total_wagered"] > 0:
        roi = pt["stats"]["total_pnl"] / pt["stats"]["total_wagered"] * 100
        if roi > 0:
            notes.append(f"- **Model validation:** ROI {roi:+.1f}% on paper — edge is real")
        elif roi < -10:
            notes.append(f"- ⚠️ **Paper ROI is {roi:+.1f}%** — review edge calculation logic")

    return "\n".join(notes) if notes else "_No automated notes today._"


# ─────────────────────────────────────────────────────────
# Report writer
# ─────────────────────────────────────────────────────────

def generate_report(vault_root=None):
    log.info(f"Generating daily research brief for {TODAY}")

    opps_data  = load_opportunities()
    pt         = load_paper_trades()
    pm_stats   = fetch_polymarket_stats()

    scan_time  = opps_data.get("scan_time", "unknown")
    tail_time  = opps_data.get("tail_scan_time", "not run yet")
    d_opps     = opps_data.get("directional", [])
    b_opps     = opps_data.get("bracket_leg1", [])
    t_opps     = opps_data.get("tail_end", [])
    total_opps = len(d_opps) + len(b_opps) + len(t_opps)

    report = f"""---
type: daily-brief
domain: polymarket
tags: [polymarket, weather-bot, research, daily-brief]
status: active
created: {TODAY}
updated: {TODAY}
---

# Polymarket Research Brief — {TODAY}

> Auto-generated by `research_engine.py` | Vault: `Prediction Markets/Daily Reports/`

---

## Summary

| Item | Value |
|------|-------|
| Total opportunities today | **{total_opps}** ({len(d_opps)} directional, {len(b_opps)} bracket Leg-1, {len(t_opps)} tail-end) |
| Last bot scan | {scan_time[:19] if scan_time != 'unknown' else 'not yet'} |
| Last tail-end scan | {tail_time[:19] if tail_time != 'not run yet' else 'not yet'} |
| Weather markets sampled | {pm_stats.get('sampled_weather_markets', '?')} (>{pm_stats.get('with_volume_over_100', '?')} with $100+ volume) |

---

## P&L Status (Paper Trading)

{_pnl_section(pt)}

### Open Positions

{_open_positions_section(pt)}

---

## Today's Opportunities

### Directional Markets (Probability Edge)
Edge ≥ {MIN_EDGE*100:.0f}% required | Three-source ensemble: Open-Meteo + Tomorrow.io + NWS

{_directional_opps_section(d_opps)}

### Bracket Markets — Leg 1 (NO at High Price)
NO price ≥ {MIN_BRACKET_NO_PRICE*100:.0f}¢ + model says bracket is unlikely (p < 7%)

{_bracket_opps_section(b_opps)}

### Tail-End Trades (Near Risk-Free, Afternoon Window)
Outcome confirmed by live station data | Edge ≥ {MIN_TAIL_EDGE*100:.0f}¢/share

{_tail_end_opps_section(t_opps)}

---

## Automated Strategy Notes

{_strategy_notes_section(opps_data, pt)}

---

## Strategy Reference

| Strategy | Phase | Status | Edge Source |
|----------|-------|--------|-------------|
| Directional weather (above/below) | Live | ✅ Running | Forecast model vs market |
| Bracket Leg-1 (NO > 93¢) | 2 | ✅ Running | Model says bracket unlikely |
| Tail-end (live station feed) | 1 | ✅ Running | Observed temp vs market |
| Bracket Leg-2 (YES < 2¢) | 3 | 🔜 After sigma recal | Point forecast precision |

**Research notes:** [[ColdMath - Extreme Bracket Strategy]] | [[Tail-End Trading - Late Settlement Arbitrage]] | [[Weather Trading Strategy Brief - March 13 2026]]

---

## Infrastructure

| Component | Status |
|-----------|--------|
| Droplet | 164.92.133.253 (Frankfurt) |
| bot.py | polymarket-bot.service |
| paper_trader.py | paper-trader.service |
| tail_end_scanner.py | tail-end.service |
| research_engine.py | run daily / on-demand |
| Weather sources | Open-Meteo + Tomorrow.io + NWS |

---

*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*
"""

    # Determine output path
    if vault_root:
        out_dir = Path(vault_root) / PRED_MARKETS / DAILY_REPORTS
    else:
        # Try local vault first
        local = Path("C:/TheBrain") / PRED_MARKETS / DAILY_REPORTS
        droplet = Path("/root/reports")
        out_dir = local if local.parent.exists() else droplet

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{TODAY} Research Brief.md"

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(report)

    log.info(f"Report written: {out_file}")
    log.info(
        f"Summary: {total_opps} opps | "
        f"P&L: {'${:+.2f}'.format(pt['stats']['total_pnl']) if pt else 'N/A'}"
    )
    return str(out_file)


# ─────────────────────────────────────────────────────────
# Vault intelligence reader (reads your research notes)
# ─────────────────────────────────────────────────────────

def read_vault_context(vault_root="C:/TheBrain"):
    """
    Scans Prediction Markets/ for research notes and extracts
    key insights for the engine.  Logged, not used programmatically yet —
    foundation for future Phase 3 model tuning from vault notes.
    """
    pred_path = Path(vault_root) / PRED_MARKETS
    if not pred_path.exists():
        log.warning(f"Vault path not found: {pred_path}")
        return {}

    context = {"notes_found": [], "strategies_active": []}
    for f in pred_path.glob("*.md"):
        context["notes_found"].append(f.name)
        text = f.read_text(encoding="utf-8", errors="ignore").lower()
        if "coldmath" in text or "bracket" in text:
            context["strategies_active"].append("bracket")
        if "tail-end" in text or "tail end" in text:
            context["strategies_active"].append("tail_end")
        if "copy trad" in text:
            context["strategies_active"].append("copy_trading")

    context["strategies_active"] = list(set(context["strategies_active"]))
    log.info(
        f"Vault context: {len(context['notes_found'])} notes, "
        f"strategies: {context['strategies_active']}"
    )
    return context


if __name__ == "__main__":
    import sys
    vault = sys.argv[1] if len(sys.argv) > 1 else "C:/TheBrain"
    log.info(f"Research Engine starting | vault={vault}")
    read_vault_context(vault)
    report_path = generate_report(vault_root=vault)
    print(f"\nReport: {report_path}")
