#!/usr/bin/env python3
"""Create RPM spec and build scripts"""

import os

base = r'C:\Users\Administrator\Desktop\ai\rpm_package'

# Create spec file
spec = '''Summary: Free LLM proxy service for car AI assistant
Name: benben-ai
Version: 1.0.0
Release: 1%{?dist}
License: MIT
Group: Applications/Internet
URL: https://github.com/benben-ai
BuildArch: noarch

Source0: %{name}-%{version}.tar.gz

Requires: python3 >= 3.6
Requires: python3-pip

%description
Free LLM proxy service for car AI assistant. Uses keyword matching to replace real LLM API calls,
supporting 52 vehicle control tools and 200+ voice commands.

%prep
%setup -q

%build
# No compilation needed

%install
mkdir -p %{buildroot}/usr/local/benben-ai
cp -a usr/local/benben-ai/* %{buildroot}/usr/local/benben-ai/

mkdir -p %{buildroot}/etc/systemd/system
cp -a etc/systemd/benben-ai.service %{buildroot}/etc/systemd/system/

mkdir -p %{buildroot}/etc/benben-ai
cp -a etc/benben-ai/config.json %{buildroot}/etc/benben-ai/

mkdir -p %{buildroot}/var/lib/benben-ai
mkdir -p %{buildroot}/var/log/benben-ai

%files
%config(noreplace) /etc/benben-ai/config.json
/usr/local/benben-ai
/etc/systemd/system/benben-ai.service
%dir /var/log/benben-ai
%dir /var/lib/benben-ai

%changelog
* Mon Aug 10 2026 BenBen Team - 1.0.0-1
- Initial release
'''

spec_path = os.path.join(base, 'benben-ai.spec')
with open(spec_path, 'w', encoding='utf-8') as f:
    f.write(spec)
print(f'Created: {spec_path}')

# Create build script
build = '''#!/bin/bash
# BenBen AI Assistant - RPM Build Script
# Run: sudo bash build_rpm.sh

set -e

BUILD_ROOT="$(pwd)/RPMBUILD"
SPEC_FILE="$(pwd)/benben-ai.spec"
TARBALL="$(pwd)/benben-ai-1.0.0.tar.gz"

echo "=== BenBen AI Assistant RPM Builder ==="
echo ""

# Create build directories
mkdir -p "$BUILD_ROOT"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

# Copy spec file
cp "$SPEC_FILE" "$BUILD_ROOT/SPECS/"

# Copy tarball
cp "$TARBALL" "$BUILD_ROOT/SOURCES/"

# Build RPM
echo "Building RPM..."
rpm -ba --define "_topdir $BUILD_ROOT" "$BUILD_ROOT/SPECS/benben-ai.spec"

# Show result
echo ""
echo "=== Build Complete ==="
echo "RPM files:"
find "$BUILD_ROOT/RPMS" -name "*.rpm" -exec ls -lh {} \\;
echo ""
echo "Installation:"
echo "  rpm -ivh $BUILD_ROOT/RPMS/noarch/benben-ai-1.0.0-1.noarch.rpm"
echo ""
echo "Service management:"
echo "  systemctl daemon-reload"
echo "  systemctl start benben-ai"
echo "  systemctl enable benben-ai"
echo "  systemctl status benben-ai"
'''

build_path = os.path.join(base, 'build_rpm.sh')
with open(build_path, 'w', encoding='utf-8') as f:
    f.write(build)
print(f'Created: {build_path}')

# Create INSTALL.md
install = '''# BenBen AI Assistant - CentOS 7 RPM Package

## Build RPM

```bash
# On CentOS 7 (as root)
cd /path/to/rpm_package
bash build_rpm.sh
```

## Install RPM

```bash
# Install the package
rpm -ivh benben-ai-1.0.0-1.noarch.rpm

# Or upgrade if already installed
rpm -Uvh benben-ai-1.0.0-1.noarch.rpm
```

## Start Service

```bash
# Reload systemd
systemctl daemon-reload

# Enable and start service
systemctl enable benben-ai
systemctl start benben-ai

# Check status
systemctl status benben-ai
```

## Configure

Edit config file:
```bash
vi /etc/benben-ai/config.json
```

## Service Commands

```bash
# Start
systemctl start benben-ai

# Stop
systemctl stop benben-ai

# Restart
systemctl restart benben-ai

# Enable at boot
systemctl enable benben-ai

# Check status
systemctl status benben-ai

# View logs
journalctl -u benben-ai -f
tail -f /var/log/benben-ai/service.log
```

## Configuration

| Item | Value |
|---|---|
| Service URL | http://<IP>:9998/compatible-mode/v1 |
| Admin Panel | http://<IP>:9998/admin |
| Config File | /etc/benben-ai/config.json |
| Log File | /var/log/benben-ai/service.log |
| Default Password | admin123456 |

## Uninstall

```bash
# Stop and disable service
systemctl stop benben-ai
systemctl disable benben-ai

# Remove package
rpm -e benben-ai

# Clean up data (optional)
rm -rf /var/lib/benben-ai /var/log/benben-ai /etc/benben-ai
```

## Requirements

- CentOS 7 / RHEL 7 / AlmaLinux 8+
- Python 3.6+
- systemd

install_path = os.path.join(base, 'INSTALL.md')
with open(install_path, 'w', encoding='utf-8') as f:
    f.write(install)
print(f'Created: {install_path}')

print()
print('All files created successfully!')
print()
print('Next steps:')
print('  1. Copy rpm_package/ to CentOS 7 server')
print('  2. Run: sudo bash build_rpm.sh')
print('  3. Install: sudo rpm -ivh RPMBUILD/RPMS/noarch/benben-ai-1.0.0-1.noarch.rpm')
print('  4. Start: sudo systemctl start benben-ai')
print('  5. Enable: sudo systemctl enable benben-ai')
