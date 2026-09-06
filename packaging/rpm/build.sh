#!/usr/bin/env bash
# Build an arch-specific RPM for comic-dl inside a Fedora container.
# Run under the arch you target: amd64 container -> x86_64 RPM, arm64 (QEMU)
# container -> aarch64 RPM.
#
#   packaging/rpm/build.sh      # full build -> $OUT_DIR/*.rpm
#
# Env:
#   VERSION             package version (default 0.1.0; leading 'v' stripped)
#   CURL_CFFI_VERSION   pin for the vendored abi3 wheel (default from uv.lock)
#   REPO_DIR            read-only repo mount (default /src)
#   OUT_DIR             .rpm output dir (default /out)
set -euo pipefail

VERSION="${VERSION#v}"
VERSION="${VERSION:-0.0.1}"
REPO_DIR="${REPO_DIR:-/src}"
OUT_DIR="${OUT_DIR:-/out}"
WORK_DIR="${WORK_DIR:-/build}"

# Default the vendored curl-cffi wheel to the version uv.lock resolves, so the
# packaged binary never drifts from the declared dependency set.
CURL_CFFI_VERSION="${CURL_CFFI_VERSION:-$(awk '
  /^name = "curl-cffi"$/ { f=1 }
  f && /^version =/ { gsub(/"/, "", $3); print $3; exit }
' "$REPO_DIR/uv.lock")}"

echo "Installing build dependencies..."
dnf install -y --quiet rpm-build python3 python3-pip unzip

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR/rpmbuild/SOURCES" "$WORK_DIR/rpmbuild/SPECS"

# Stage a writable copy of the repo and stamp the tag version into pyproject.
mkdir -p "$WORK_DIR/tarsrc"
cp -a "$REPO_DIR/." "$WORK_DIR/tarsrc/src"
sed -i "s/^version = .*/version = \"$VERSION\"/" "$WORK_DIR/tarsrc/src/pyproject.toml"

# rpmbuild needs a Source tarball with a single top-level directory.
tar -C "$WORK_DIR/tarsrc" \
    --exclude=.git --exclude=.venv --exclude='__pycache__' --exclude='*.pyc' \
    --exclude=dist --exclude=build \
    -czf "$WORK_DIR/rpmbuild/SOURCES/comic-dl-$VERSION.tar.gz" src

# Stamp version + curl_cffi pin into a copy of the spec (repo is read-only).
sed "s/^Version: .*/Version: $VERSION/" "$REPO_DIR/packaging/rpm/comic-dl.spec" \
    | sed "s/^%global curl_cffi_version .*/%global curl_cffi_version $CURL_CFFI_VERSION/" \
    > "$WORK_DIR/rpmbuild/SPECS/comic-dl.spec"

echo "Building RPM v$VERSION..."
rpmbuild --define "_topdir $WORK_DIR/rpmbuild" \
    -ba "$WORK_DIR/rpmbuild/SPECS/comic-dl.spec"

mkdir -p "$OUT_DIR"
cp "$WORK_DIR"/rpmbuild/RPMS/*/comic-dl-*.rpm "$OUT_DIR"/
echo
echo "Package(s) written to $OUT_DIR:"
ls -l "$OUT_DIR"