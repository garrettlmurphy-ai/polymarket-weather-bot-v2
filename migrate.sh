#!/usr/bin/env bash
# Migrate polymarket bot from old droplet to new droplet.
# Run from the directory containing the bot Python files.
#
# Usage:
#   NEW_IP=157.245.73.141 bash migrate.sh
#
# You will be prompted for SSH passwords for each connection.

set -euo pipefail

OLD_IP="164.92.133.253"
NEW_IP="${NEW_IP:-157.245.73.141}"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"

# Detect script directory so we can find the bot files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== [1/5] Stopping bot on OLD droplet ($OLD_IP) ==="
ssh $SSH_OPTS root@"$OLD_IP" "
  systemctl stop polymarket-bot polymarket-tail 2>/dev/null || true
  pkill -f bot.py 2>/dev/null || true
  pkill -f tail_end_scanner.py 2>/dev/null || true
  echo 'Old droplet: bot stopped.'
" || echo "WARNING: Could not connect to old droplet — skipping stop step."

echo "=== [2/5] Backing up state file from OLD droplet ==="
scp $SSH_OPTS root@"$OLD_IP":/root/paper_trades.json "$SCRIPT_DIR/paper_trades_backup.json" 2>/dev/null \
  && echo "State file saved to paper_trades_backup.json" \
  || echo "WARNING: no paper_trades.json on old droplet — will start fresh."

echo "=== [3/5] Copying bot files to new droplet ($NEW_IP) ==="
scp $SSH_OPTS \
  "$SCRIPT_DIR/bot.py" \
  "$SCRIPT_DIR/tail_end_scanner.py" \
  "$SCRIPT_DIR/paper_trader.py" \
  "$SCRIPT_DIR/research_engine.py" \
  "$SCRIPT_DIR/config.py" \
  "$SCRIPT_DIR/daily_summary.py" \
  "$SCRIPT_DIR/requirements.txt" \
  root@"$NEW_IP":/root/

echo "=== [4/5] Installing deps and setting up services on new droplet ==="
ssh $SSH_OPTS root@"$NEW_IP" bash <<'REMOTE'
set -e

echo "-- Installing Python --"
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv -qq

echo "-- Installing Python packages --"
cd /root
python3 -m venv /root/.botenv
/root/.botenv/bin/pip install --upgrade pip -q
/root/.botenv/bin/pip install requests scipy -q

echo "-- Writing systemd services --"
cat > /etc/systemd/system/polymarket-bot.service <<EOF
[Unit]
Description=Polymarket Weather Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
ExecStart=/root/.botenv/bin/python3 /root/bot.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/polymarket-tail.service <<EOF
[Unit]
Description=Polymarket Tail-End Scanner
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
ExecStart=/root/.botenv/bin/python3 /root/tail_end_scanner.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/polymarket-daily.service <<EOF
[Unit]
Description=Polymarket Daily Summary

[Service]
Type=oneshot
User=root
WorkingDirectory=/root
ExecStart=/root/.botenv/bin/python3 /root/daily_summary.py
EOF

cat > /etc/systemd/system/polymarket-daily.timer <<EOF
[Unit]
Description=Polymarket Daily Summary Timer

[Timer]
OnCalendar=*-*-* 23:55:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable polymarket-bot polymarket-tail polymarket-daily.timer
systemctl start polymarket-bot polymarket-tail polymarket-daily.timer
echo "-- Services started --"
REMOTE

echo "=== [5/5] Transferring state file ==="
if [ -f "$SCRIPT_DIR/paper_trades_backup.json" ]; then
  scp $SSH_OPTS "$SCRIPT_DIR/paper_trades_backup.json" root@"$NEW_IP":/root/paper_trades.json
  echo "State file transferred."
fi

echo ""
echo "=== Verifying ==="
ssh $SSH_OPTS root@"$NEW_IP" "
  systemctl is-active polymarket-bot && echo 'polymarket-bot: RUNNING' || echo 'polymarket-bot: FAILED'
  systemctl is-active polymarket-tail && echo 'polymarket-tail: RUNNING' || echo 'polymarket-tail: FAILED'
"

echo ""
echo "=== Migration complete! ==="
echo "Monitor: ssh root@$NEW_IP 'journalctl -u polymarket-bot -f'"
