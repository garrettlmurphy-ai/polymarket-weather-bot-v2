#!/usr/bin/env python3
import json, os, requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = "8400301663:AAFwI_BsN0CeeLvEqGuFHz-UAw2-k7ahOSM"
CHAT_ID = "633297295"
STATE_FILE = "/root/paper_trades.json"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)

def build_summary():
    if not os.path.exists(STATE_FILE):
        return "No paper trading data found yet."

    with open(STATE_FILE) as f:
        s = json.load(f)

    st    = s["stats"]
    total = st["wins"] + st["losses"]
    wr    = (st["wins"] / total * 100) if total else 0
    roi   = (st["total_pnl"] / st["total_wagered"] * 100) if st["total_wagered"] else 0
    ret   = (s["bankroll"] - s["starting_bankroll"]) / s["starting_bankroll"] * 100

    # Trades closed today
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    todays_closed = [t for t in s["closed_trades"] if t.get("closed_at","").startswith(today)]
    today_pnl = sum(t.get("pnl", 0) for t in todays_closed)

    lines = [
        f"📊 *Polymarket Weather Bot — Daily Summary*",
        f"📅 {datetime.now(timezone.utc).strftime('%A %d %B %Y')}",
        f"",
        f"💰 *Bankroll:* ${s['bankroll']:.2f} (started ${s['starting_bankroll']:.2f})",
        f"📈 *Total Return:* {ret:+.1f}%",
        f"",
        f"*All-Time Stats*",
        f"• Closed trades: {total} ({st['wins']}W / {st['losses']}L)",
        f"• Win rate: {wr:.1f}%",
        f"• Total P&L: ${st['total_pnl']:+.2f}",
        f"• ROI on wagered: {roi:+.1f}%",
        f"",
        f"*Today ({today})*",
        f"• Trades resolved: {len(todays_closed)}",
        f"• Today's P&L: ${today_pnl:+.2f}",
        f"",
        f"📂 *Open Positions:* {len(s['open_trades'])}",
    ]

    # Show open positions
    if s["open_trades"]:
        lines.append("")
        lines.append("*Open Trades:*")
        for t in s["open_trades"][:10]:
            lines.append(f"  • {t['question'][:55]}...")
            lines.append(f"    BUY {t['side']} @ {t['entry_price']:.3f} | Stake: ${t['stake']:.2f} | Edge: {t['edge_at_entry']*100:+.1f}%")

    # Show today's resolved trades
    if todays_closed:
        lines.append("")
        lines.append("*Resolved Today:*")
        for t in todays_closed:
            emoji = "✅" if t["status"] == "won" else "❌"
            lines.append(f"  {emoji} {t['question'][:50]}...")
            lines.append(f"    P&L: ${t.get('pnl',0):+.2f}")

    return "\n".join(lines)

if __name__ == "__main__":
    msg = build_summary()
    send_telegram(msg)
    print("Summary sent to Telegram.")
    print(msg)
