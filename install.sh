#!/bin/bash
# BenBen AI Assistant - One-Click Install
# Supports CentOS 7 (Python 3.6 default) - auto-installs Python 3.9
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

# 自动清理旧版本
if systemctl list-unit-files benben-ai &>/dev/null; then
    echo "[Cleanup] Removing old installation..."
    systemctl stop benben-ai 2>/dev/null || true
    systemctl disable benben-ai 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    rm -f /etc/systemd/system/benben-ai.service
    rm -rf /usr/local/benben-ai
    rm -rf /etc/benben-ai
    rm -rf /var/lib/benben-ai
    echo "[Cleanup] Done"
fi

echo "[1/6] Installing base packages..."
yum install -y epel-release 2>/dev/null || true
yum install -y curl tar gzip gcc openssl-devel bzip2-devel libffi-devel zlib-devel make 2>/dev/null || true

# 安装 Python 3.9
PYTHON_BIN=""
if [ -x /usr/local/bin/python3.9 ]; then
    PYTHON_BIN="/usr/local/bin/python3.9"
    echo "[Python] Python 3.9 already installed"
elif [ -x /opt/rh/rh-python39/root/usr/bin/python3 ]; then
    PYTHON_BIN="/opt/rh/rh-python39/root/usr/bin/python3"
    echo "[Python] Using SCL Python 3.9"
else
    echo "[Python] Installing Python 3.9..."
    # 尝试 SCL 方式
    if yum install -y centos-release-scl-rh 2>/dev/null && yum install -y rh-python39 2>/dev/null; then
        ln -sf /opt/rh/rh-python39/root/usr/bin/python3 /usr/local/bin/python3.9
        ln -sf /opt/rh/rh-python39/root/usr/bin/pip3 /usr/local/bin/pip3.9
        PYTHON_BIN="/usr/local/bin/python3.9"
        echo "[Python] Python 3.9 installed via SCL"
    else
        # 编译安装
        echo "[Python] Compiling Python 3.9 from source..."
        cd /tmp
        curl -sSL "https://www.python.org/ftp/python/3.9.19/Python-3.9.19.tgz" -o Python-3.9.19.tgz
        tar xzf Python-3.9.19.tgz
        cd Python-3.9.19
        ./configure --prefix=/usr/local --enable-optimizations 2>/dev/null || ./configure --prefix=/usr/local
        make -j$(nproc) 2>/dev/null || make
        make altinstall 2>/dev/null || make install
        cd /tmp && rm -rf Python-3.9.19 Python-3.9.19.tgz
        PYTHON_BIN="/usr/local/bin/python3.9"
        echo "[Python] Python 3.9 compiled and installed"
    fi
fi

echo "[Python] Python path: $PYTHON_BIN"
$PYTHON_BIN --version

echo "[2/6] Downloading application..."
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
    for mirror in "${MIRRORS[@]}"; do
        echo "  $mirror"
    done
    exit 1
fi

echo "[3/6] Extracting files..."
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

# 用实际 Python 路径替换 service 文件中的占位符
sed -i "s|__PYTHON_BIN__|$PYTHON_BIN|g" /etc/systemd/system/benben-ai.service
chmod +x /usr/local/benben-ai/fake_llm_server.py

echo "[4/6] Installing Python dependencies..."
cd /usr/local/benben-ai
$PYTHON_BIN -m pip install --upgrade pip --quiet 2>/dev/null || true
$PYTHON_BIN -m pip install -r requirements.txt || {
    echo ""
    echo "ERROR: Failed to install Python dependencies!"
    exit 1
}
echo "Python dependencies installed OK"

echo "[5/6] Starting service..."
systemctl daemon-reload
systemctl enable benben-ai
systemctl start benben-ai

sleep 3
if systemctl is-active --quiet benben-ai; then
    echo "Service started successfully!"
else
    echo ""
    echo "========== ERROR LOGS =========="
    journalctl -u benben-ai -n 20 --no-pager 2>/dev/null
    echo "================================="
    echo ""
    echo "Manual test:"
    cd /usr/local/benben-ai
    timeout 3 $PYTHON_BIN fake_llm_server.py 2>&1 || true
    echo ""
fi

echo "[6/6] Cleaning up..."
rm -rf /opt/benben-install

echo ""
echo "=== Installation Complete ==="
echo ""
IP=$(hostname -I 2>/dev/null | cut -d" " -f1 || hostname -I | awk '{print $1}')
echo "Service URL: http://$IP:9998/compatible-mode/v1"
echo "Admin Panel: http://$IP:9998/admin"
echo "Default Password: admin123456"
echo ""
echo "Commands:"
echo "  systemctl status benben-ai"
echo "  journalctl -u benben-ai -f"
echo "  tail -f /var/log/benben-ai/service.log"
