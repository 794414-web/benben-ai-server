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
echo "Upgrading pip..."
pip3 install --upgrade pip --quiet 2>/dev/null || true
echo "Installing Python dependencies..."
pip3 install -r requirements.txt --quiet 2>/dev/null || pip3 install -r requirements.txt 2>/dev/null || echo "NOTE: Python deps install skipped"
echo "Verifying service..."
systemctl is-active benben-ai && echo "Service is running!" || echo "NOTE: service may need manual start"

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
