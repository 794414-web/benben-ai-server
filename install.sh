#!/bin/bash
# BenBen AI Assistant - One-Click Install
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
yum install -y python3 python3-pip rpm-build tar gzip

echo "[2/4] Downloading..."
REPO_URL="https://raw.githubusercontent.com/794414-web/benben-ai-server/main/rpm_package"
cd /opt
rm -rf benben-build
mkdir benben-build
cd benben-build
curl -sSL "$REPO_URL/benben-ai-1.0.0.tar.gz" -o src.tar.gz
curl -sSL "$REPO_URL/benben-ai.spec" -o benben-ai.spec

echo "[3/4] Building RPM..."
mkdir -p /tmp/rpmbuild/{BUILD,RPMS,SOURCES,SPECS}
cp benben-ai.spec /tmp/rpmbuild/SPECS/
cp src.tar.gz /tmp/rpmbuild/SOURCES/
rpm -ba --define "_topdir /tmp/rpmbuild" /tmp/rpmbuild/SPECS/benben-ai.spec
RPM=/tmp/rpmbuild/RPMS/noarch/benben-ai-1.0.0-1.noarch.rpm

if [ ! -f "$RPM" ]; then
    echo "ERROR: Build failed"
    exit 1
fi

echo "[4/4] Installing..."
rpm -ivh "$RPM"

systemctl daemon-reload
systemctl enable benben-ai
systemctl start benben-ai

rm -rf /opt/benben-build /tmp/rpmbuild

echo "=== Installation Complete ==="
IP=$(hostname -I | awk '{print $1}')
echo "Service URL: http://$IP:9998/compatible-mode/v1"
echo "Admin Panel: http://$IP:9998/admin"
echo "Default Password: admin123456"
