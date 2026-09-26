#!/usr/bin/env bash
# Build an arch-specific RPM for comic-dl inside a Fedora container.
# Run under the arch you target: amd64 container -> x86_64 RPM, arm64 (QEMU)
# container -> aarch64 RPM.
#
#   packaging/rpm/build.sh      # full build -> $OUT_DIR/*.rpm
#
# Env:
#   VERSION             package version (default from pyproject.toml; leading
#                       'v' stripped)
#   CURL_CFFI_VERSION   pin for the vendored abi3 wheel (default from uv.lock)
#   PYWEBVIEW_VERSION   pin for the vendored pywebview wheel (default from
#                       uv.lock)
#   PROXY_TOOLS_VERSION pin for the vendored proxy-tools wheel (default from
#                       uv.lock)
#   REPO_DIR            read-only repo mount (default /src)
#   OUT_DIR             .rpm output dir (default /out)
set -euo pipefail

VERSION="${VERSION:-$(awk -F'"' '/^version = / { print $2; exit }' "${REPO_DIR:-/src}/pyproject.toml")}"
VERSION="${VERSION#v}"
REPO_DIR="${REPO_DIR:-/src}"
OUT_DIR="${OUT_DIR:-/out}"
WORK_DIR="${WORK_DIR:-/build}"

# Default the vendored wheels to the versions uv.lock resolves, so packaged
# binaries never drift from the declared dependency set. pywebview and
# proxy-tools ride along because Fedora packages neither.
_lock_version() {
  awk -v pkg="$1" '
    $0 == "name = \"" pkg "\"" { f=1 }
    f && /^version =/ { gsub(/"/, "", $3); print $3; exit }
  ' "$REPO_DIR/uv.lock"
}
CURL_CFFI_VERSION="${CURL_CFFI_VERSION:-$(_lock_version curl-cffi)}"
PYWEBVIEW_VERSION="${PYWEBVIEW_VERSION:-$(_lock_version pywebview)}"
PROXY_TOOLS_VERSION="${PROXY_TOOLS_VERSION:-$(_lock_version proxy-tools)}"

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

# Stamp version + wheel pins into a copy of the spec (repo is read-only).
spec="$WORK_DIR/rpmbuild/SPECS/comic-dl.spec"
sed -e "s/^Version: .*/Version: $VERSION/" \
    -e "s/^%global curl_cffi_version .*/%global curl_cffi_version $CURL_CFFI_VERSION/" \
    -e "s/^%global pywebview_version .*/%global pywebview_version $PYWEBVIEW_VERSION/" \
    -e "s/^%global proxy_tools_version .*/%global proxy_tools_version $PROXY_TOOLS_VERSION/" \
    "$REPO_DIR/packaging/rpm/comic-dl.spec" \
    > "$spec"

# Prepend a changelog entry for this build. The spec's own %changelog is the
# hand-maintained history; the version comes from pyproject.toml, so nothing
# else would ever keep the two in step and `rpm -q --changelog` would report
# whichever release was written into the spec by hand.
release="$(awk '/^Release:/ { sub(/%\{.*/, "", $2); print $2; exit }' "$spec")"
awk -v date="$(date -u +'%a %b %d %Y')" -v nvr="$VERSION-$release" -v ver="$VERSION" '
    /^%changelog$/ && !stamped {
        print
        printf "* %s Comic Downloader contributors <maintainers@users.noreply.github.com> - %s\n",
               date, nvr
        printf "- Release %s.\n", ver
        stamped = 1
        next
    }
    { print }
' "$spec" > "$spec.tmp"
mv "$spec.tmp" "$spec"

echo "Building RPM v$VERSION..."
rpmbuild --define "_topdir $WORK_DIR/rpmbuild" \
    -ba "$spec"

mkdir -p "$OUT_DIR"
cp "$WORK_DIR"/rpmbuild/RPMS/*/comic-dl-*.rpm "$OUT_DIR"/
echo
echo "Package(s) written to $OUT_DIR:"
ls -l "$OUT_DIR"