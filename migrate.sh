#!/usr/bin/env bash
# Migration script: stop old droplet, transfer state, start new droplet.
# Run from your LOCAL machine (not from either droplet).
#
# Usage:
#   NEW_IP=<new-droplet-ip> bash migrate.sh
#
# Requirements: ssh key access to both droplets as root.

set -euo pipefail

OLD_IP="164.92.133.253"
NEW_IP="${NEW_IP:?Set NEW_IP to the new droplet IP, e.g.: NEW_IP=1.2.3.4 bash migrate.sh}"
SSH_OPTS="-o StrictHostKeyChecking=accept-new"

echo "=== [1/5] Stopping bot on OLD droplet ($OLD_IP) ==="
ssh $SSH_OPTS root@"$OLD_IP" "
  systemctl stop polymarket-bot polymarket-tail 2>/dev/null || true
  pkill -f bot.py 2>/dev/null || true
  pkill -f tail_end_scanner.py 2>/dev/null || true
  echo 'Old droplet: bot stopped.'
"

echo "=== [2/5] Backing up state file from OLD droplet ==="
scp $SSH_OPTS root@"$OLD_IP":/root/paper_trades.json ./paper_trades_backup.json 2>/dev/null \
  && echo "State file saved to ./paper_trades_backup.json" \
  || echo "WARNING: no paper_trades.json found on old droplet — starting fresh on new droplet."

echo "=== [3/5] Setting up new droplet ($NEW_IP) ==="
scp $SSH_OPTS setup_droplet.sh root@"$NEW_IP":/root/setup_droplet.sh
ssh $SSH_OPTS root@"$NEW_IP" "bash /root/setup_droplet.sh"

echo "=== [4/5] Transferring state file to NEW droplet ==="
if [ -f ./paper_trades_backup.json ]; then
  scp $SSH_OPTS ./paper_trades_backup.json root@"$NEW_IP":/root/paper_trades.json
  echo "State file transferred."
else
  echo "No backup found — new droplet will start with empty state."
fi

echo "=== [5/5] Verifying services on new droplet ==="
ssh $SSH_OPTS root@"$NEW_IP" "
  systemctl is-active polymarket-bot && echo 'polymarket-bot: RUNNING' || echo 'polymarket-bot: NOT running'
  systemctl is-active polymarket-tail && echo 'polymarket-tail: RUNNING' || echo 'polymarket-tail: NOT running'
"

echo ""
echo "=== Migration complete! ==="
echo "Old droplet ($OLD_IP): bot is STOPPED."
echo "New droplet ($NEW_IP): bot is RUNNING."
echo ""
echo "Monitor with:  ssh root@$NEW_IP 'journalctl -u polymarket-bot -f'"
echo "Daily summary: ssh root@$NEW_IP 'journalctl -u polymarket-tail -f'"
