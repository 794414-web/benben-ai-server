#!/bin/bash
# BenBen AI Assistant - One-Click Install
# Direct installation (no RPM build required)
set -e

echo "=== BenBen AI Installer ==="

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Run as root"
    exit 1
fi

if [ ! -f /etc/redhat-release ]; then
    echo "ERROR: CentOS/RHEL only"
    exit 1
fi

echo "[1/4] Installing dependencies..."
yum install -y epel-release
yum install -y python3 python3-pip tar gzip

echo "[2/4] Downloading..."
REPO_URL="https://raw.githubusercontent.com/794414-web/benben-ai-server/main"
MIRROR_URL="https://ghp.ci/https://raw.githubusercontent.com/794414-web/benben-ai-server/main"
cd /opt
rm -rf benben-install
mkdir benben-install
cd benben-install
echo "Downloading package..."
curl -sSL --connect-timeout 15 "$REPO_URL/benben-ai-1.0.0.tar.gz" -o src.tar.gz
if [ ! -s src.tar.gz ] || ! gzip -t src.tar.gz 2>/dev/null; then
    echo "GitHub direct failed, trying CDN mirror..."
    curl -sSL --connect-timeout 15 "$MIRROR_URL/benben-ai-1.0.0.tar.gz" -o src.tar.gz
fi
if [ ! -s src.tar.gz ] || ! gzip -t src.tar.gz 2>/dev/null; then
    echo "ERROR: Failed to download benben-ai-1.0.0.tar.gz"
    echo "Please manually download from:"
    echo "  $REPO_URL/benben-ai-1.0.0.tar.gz"
    exit 1
fi

echo "[3/4] Extracting and installing..."
tar xzf src.tar.gz

if [ -d "benben-ai-1.0.0" ]; then
    cd benben-ai-1.0.0
fi

mkdir -p /usr/local/benben-ai
mkdir -p /etc/benben-ai
mkdir -p /var/log/benben-ai
mkdir -p /var/lib/benben-ai

cp -r usr/local/benben-ai/* /usr/local/benben-ai/
cp etc/benben-ai/config.json /etc/benben-ai/
cp etc/systemd/benben-ai.service /etc/systemd/system/

chmod +x /usr/local/benben-ai/fake_llm_server.py

echo "[4/4] Starting service..."
systemctl daemon-reload
systemctl enable benben-ai
systemctl start benben-ai

cd /usr/local/benben-ai
pip3 install -r requirements.txt || pip install -r requirements.txt || echo "NOTE: Python deps install skipped"

rm -rf /opt/benben-install

echo ""
echo "=== Installation Complete ==="
echo ""
IP=$(hostname -I | cut -d" " -f1)
echo "Service URL: http://$IP:9998/compatible-mode/v1"
echo "Admin Panel: http://$IP:9998/admin"
echo "Default Password: admin123456"
echo ""
echo "Commands:"
echo "  systemctl status benben-ai"
echo "  journalctl -u benben-ai -f"
echo "  tail -f /var/log/benben-ai/service.log"
