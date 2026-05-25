#!/bin/bash
# Oracle Cloud Free Tier — One-time setup script
# VM: VM.Standard.A1.Flex (Ubuntu 22.04 ARM, 4 OCPUs, 24GB RAM — always free)
#
# Run this once after creating the VM:
#   chmod +x oracle_setup.sh && ./oracle_setup.sh

set -e

echo "=== India Auto-Trader — Oracle Cloud Setup ==="

# 1. System timezone
sudo timedatectl set-timezone Asia/Kolkata
echo "[OK] Timezone set to Asia/Kolkata"

# 2. System packages
sudo apt-get update -qq
sudo apt-get install -y python3.11 python3-pip git cron

# 3. Clone repo
REPO="https://github.com/Harsh02738/india-auto-trader.git"
DEST="/home/ubuntu/india-auto-trader"

if [ -d "$DEST" ]; then
    echo "[INFO] Repo already exists — pulling latest"
    cd "$DEST" && git pull origin master
else
    git clone "$REPO" "$DEST"
fi
echo "[OK] Repository ready at $DEST"

# 4. Python dependencies
cd "$DEST"
pip3 install -r requirements.txt --quiet
echo "[OK] Python dependencies installed"

# 5. .env file — must be copied manually
if [ ! -f "$DEST/.env" ]; then
    echo "[WARN] .env file not found!"
    echo "       Copy it from your laptop:"
    echo "       scp .env ubuntu@<VM_IP>:$DEST/.env"
fi

# 6. Data directories
mkdir -p "$DEST/data/charts" "$DEST/data/backtest" "$DEST/data/claude_trader"
mkdir -p "$DEST/data/market" "$DEST/data/realtime" "$DEST/data/portfolio"
mkdir -p "$DEST/data/signals" "$DEST/logs"
echo "[OK] Data directories created"

# 7. Install cron schedule
crontab -l 2>/dev/null > /tmp/current_cron || true
cat "$DEST/deploy/auto_trader.cron" >> /tmp/current_cron
crontab /tmp/current_cron
echo "[OK] Cron schedule installed"

# 8. Log rotation
sudo tee /etc/logrotate.d/claude_trader > /dev/null << 'EOF'
/home/ubuntu/india-auto-trader/logs/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
}
EOF
echo "[OK] Log rotation configured"

echo ""
echo "=== Setup complete ==="
echo "Test with: cd $DEST && python3 auto_trader.py --task scan"
echo "Logs at:   $DEST/logs/auto_trader.log"
