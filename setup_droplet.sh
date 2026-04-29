#!/usr/bin/env bash
# Run this script ON the new droplet as root.
# It installs dependencies and deploys all bot files.
# Usage: bash setup_droplet.sh

set -euo pipefail

BOT_DIR="/root"
REPO="https://github.com/garrettlmurphy-ai/polymarket-weather-bot-v2.git"
BRANCH="main"

echo "=== Installing system packages ==="
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv git

echo "=== Cloning repo ==="
cd "$BOT_DIR"
rm -rf polymarket-weather-bot-v2
git clone --branch "$BRANCH" "$REPO" polymarket-weather-bot-v2

echo "=== Installing Python dependencies ==="
cd polymarket-weather-bot-v2
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install requests scipy -q

echo "=== Copying bot files to /root ==="
cp bot.py tail_end_scanner.py paper_trader.py research_engine.py config.py daily_summary.py /root/

echo "=== Writing systemd service files ==="

cat > /etc/systemd/system/polymarket-bot.service <<'EOF'
[Unit]
Description=Polymarket Weather Bot (main scanner)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
ExecStart=/root/polymarket-weather-bot-v2/.venv/bin/python3 /root/bot.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/polymarket-tail.service <<'EOF'
[Unit]
Description=Polymarket Tail-End Scanner
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
ExecStart=/root/polymarket-weather-bot-v2/.venv/bin/python3 /root/tail_end_scanner.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/polymarket-daily.timer <<'EOF'
[Unit]
Description=Polymarket Daily Summary Timer

[Timer]
OnCalendar=*-*-* 23:55:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/polymarket-daily.service <<'EOF'
[Unit]
Description=Polymarket Daily Summary

[Service]
Type=oneshot
User=root
WorkingDirectory=/root
ExecStart=/root/polymarket-weather-bot-v2/.venv/bin/python3 /root/daily_summary.py
EOF

echo "=== Enabling and starting services ==="
systemctl daemon-reload
systemctl enable polymarket-bot polymarket-tail polymarket-daily.timer
systemctl start polymarket-bot polymarket-tail polymarket-daily.timer

echo ""
echo "=== Done! Check status with: ==="
echo "  systemctl status polymarket-bot"
echo "  systemctl status polymarket-tail"
echo "  journalctl -u polymarket-bot -f"
