#!/bin/bash
# BenBen AI Assistant - RPM Build Script
set -e

BUILD_ROOT="$(pwd)/RPMBUILD"
SPEC_FILE="$(pwd)/benben-ai.spec"
TARBALL="$(pwd)/benben-ai-1.0.0.tar.gz"

echo "=== BenBen AI RPM Builder ==="
mkdir -p "$BUILD_ROOT"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

cp "$SPEC_FILE" "$BUILD_ROOT/SPECS/"
cp "$TARBALL" "$BUILD_ROOT/SOURCES/"

echo "Building RPM..."
rpm -ba --define "_topdir $BUILD_ROOT" "$BUILD_ROOT/SPECS/benben-ai.spec"

echo "=== Build Complete ==="
find "$BUILD_ROOT/RPMS" -name "*.rpm" -exec ls -lh {} \;
echo "Install: rpm -ivh $BUILD_ROOT/RPMS/noarch/benben-ai-1.0.0-1.noarch.rpm"
