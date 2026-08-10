#!/bin/bash
# BenBen AI Assistant - One-Click Update
# 更新代码但保留 Python 环境和用户数据
set -e

echo "=== BenBen AI Updater ==="

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Run as root"
    exit 1
fi

echo "[1/3] Downloading latest version..."
FILE_NAME="benben-ai-1.0.0.tar.gz"
MIRRORS=(
    "https://raw.githubusercontent.com/794414-web/benben-ai-server/main/$FILE_NAME"
    "https://cdn.jsdelivr.net/gh/794414-web/benben-ai-server@main/$FILE_NAME"
    "https://raw.kkgithub.com/794414-web/benben-ai-server/main/$FILE_NAME"
)

cd /opt
rm -rf benben-update
mkdir benben-update
cd benben-update

download_ok=0
for mirror in "${MIRRORS[@]}"; do
    echo "Trying: $mirror"
    http_code=$(curl -sSL -w "%{http_code}" --connect-timeout 10 --max-time 30 "$mirror" -o src.tar.gz 2>/dev/null)
    if [ "$http_code" = "200" ] && [ -s src.tar.gz ] && gzip -t src.tar.gz 2>/dev/null; then
        echo "  OK! Downloaded successfully"
        download_ok=1
        break
    fi
    echo "  Failed (HTTP ${http_code:-000}), trying next..."
done

if [ $download_ok -eq 0 ]; then
    echo "ERROR: All download mirrors failed!"
    exit 1
fi

echo "[2/3] Updating files..."
tar xzf src.tar.gz
cd benben-ai-1.0.0

# 检测实际 Python 路径
PYTHON_BIN=""
if [ -x /usr/local/bin/python3.9 ]; then
    PYTHON_BIN="/usr/local/bin/python3.9"
elif [ -x /opt/benben-conda/bin/python3.11 ]; then
    PYTHON_BIN="/usr/local/bin/python3.9"
elif [ -x /opt/rh/rh-python39/root/usr/bin/python3 ]; then
    PYTHON_BIN="/usr/local/bin/python3.9"
else
    echo "WARNING: Python 3.9 not found at /usr/local/bin/python3.9"
    # 尝试找到 Python
    PYTHON_BIN=$(which python3.9 2>/dev/null || echo "")
    if [ -z "$PYTHON_BIN" ]; then
        PYTHON_BIN=$(which python3 2>/dev/null || echo "")
    fi
fi

echo "Using Python: $PYTHON_BIN"

# 保留用户数据（config.json 和 users.json 在 /usr/local/benben-ai/ 下）
# 只更新代码文件
cp -f usr/local/benben-ai/fake_llm_server.py /usr/local/benben-ai/
cp -f etc/systemd/benben-ai.service /etc/systemd/system/

# 用实际 Python 路径替换 service 文件中的占位符
if [ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ]; then
    sed -i "s|__PYTHON_BIN__|$PYTHON_BIN|g" /etc/systemd/system/benben-ai.service
    echo "Service file updated with Python path"
fi

# 确保 Python 依赖是最新的
if [ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ]; then
    cd /usr/local/benben-ai
    $PYTHON_BIN -m pip install --upgrade pip --quiet 2>/dev/null || true
    $PYTHON_BIN -m pip install -r requirements.txt --quiet 2>/dev/null || true
    echo "Dependencies updated"
else
    echo "WARNING: Skipping dependency update (Python not found)"
fi

echo "[3/3] Restarting service..."
systemctl daemon-reload

# 先尝试停止旧服务
systemctl stop benben-ai 2>/dev/null || true
sleep 1

# 启动服务
systemctl start benben-ai
sleep 3

if systemctl is-active --quiet benben-ai; then
    echo "Service restarted successfully!"
else
    echo "WARNING: Service may have issues. Check logs:"
    journalctl -u benben-ai -n 10 --no-pager
fi

# 清理
rm -rf /opt/benben-update

echo ""
echo "=== Update Complete ==="
echo "Service URL: http://$(hostname -I 2>/dev/null | cut -d' ' -f1 || echo 'SERVER_IP'):9998/admin"
