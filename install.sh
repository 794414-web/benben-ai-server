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

# 自动清理旧版本（如果存在）
if systemctl list-unit-files benben-ai &>/dev/null; then
    echo "[Cleanup] Removing old installation..."
    systemctl stop benben-ai 2>/dev/null || true
    systemctl disable benben-ai 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    rm -f /etc/systemd/system/benben-ai.service
    rm -rf /usr/local/benben-ai
    rm -rf /etc/benben-ai
    rm -rf /var/lib/benben-ai
    echo "[Cleanup] Old version removed"
fi

echo "[1/5] Installing dependencies..."
yum install -y epel-release
yum install -y python3 python3-pip tar gzip

echo "[2/5] Downloading..."
FILE_NAME="benben-ai-1.0.0.tar.gz"
MIRRORS=(
    "https://raw.githubusercontent.com/794414-web/benben-ai-server/main/$FILE_NAME"
    "https://cdn.jsdelivr.net/gh/794414-web/benben-ai-server@main/$FILE_NAME"
    "https://raw.kkgithub.com/794414-web/benben-ai-server/main/$FILE_NAME"
)
cd /opt
rm -rf benben-install
mkdir benben-install
cd benben-install

download_ok=0
for mirror in "${MIRRORS[@]}"; do
    echo "Trying: $mirror"
    http_code=$(curl -sSL -w "%{http_code}" --connect-timeout 10 --max-time 30 "$mirror" -o src.tar.gz 2>/dev/null)
    if [ "$http_code" = "200" ] && [ -s src.tar.gz ] && gzip -t src.tar.gz 2>/dev/null; then
        echo "  OK! Downloaded successfully (HTTP $http_code)"
        download_ok=1
        break
    fi
    echo "  Failed (HTTP ${http_code:-000}), trying next..."
done

if [ $download_ok -eq 0 ]; then
    echo ""
    echo "ERROR: All download mirrors failed!"
    echo "Please manually download $FILE_NAME from one of:"
    for mirror in "${MIRRORS[@]}"; do
        echo "  $mirror"
    done
    echo "Then run:"
    echo "  cd /opt/benben-install && tar xzf $FILE_NAME && cd benben-ai-1.0.0"
    exit 1
fi

echo "[3/5] Extracting and installing..."
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

echo "[4/5] Installing Python dependencies..."
cd /usr/local/benben-ai
pip3 install --upgrade pip --quiet 2>/dev/null || true
pip3 install -r requirements.txt || {
    echo ""
    echo "ERROR: Failed to install Python dependencies!"
    echo "Try: pip3 install fastapi uvicorn[standard] pydantic"
    exit 1
}
echo "Python dependencies installed OK"

echo "[5/5] Starting service..."
systemctl daemon-reload
systemctl enable benben-ai
systemctl start benben-ai

sleep 2
if systemctl is-active --quiet benben-ai; then
    echo "Service started successfully!"
else
    echo ""
    echo "WARNING: Service may have failed to start. Check logs:"
    echo "  journalctl -u benben-ai -n 30"
fi

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
