#!/usr/bin/env bash
# Build an Arch Linux package for comic-dl inside an archlinux container.
#
#   packaging/arch/build.sh      # full build -> $OUT_DIR/*.pkg.tar.zst
#
# makepkg refuses to run as root, so the build runs as a dedicated `builder`
# user. The repo is copied (writable) into the makepkg source dir first and the
# tag version is stamped into both PKGBUILD and pyproject.toml.
#
# Env:
#   VERSION             package version (default 0.1.0; leading 'v' stripped)
#   CURL_CFFI_VERSION   pin for the vendored abi3 wheel (default from uv.lock)
#   REPO_DIR            read-only repo mount (default /src)
#   OUT_DIR             output dir (default /out)
set -euo pipefail

VERSION="${VERSION#v}"
VERSION="${VERSION:-0.0.1}"
REPO_DIR="${REPO_DIR:-/src}"
OUT_DIR="${OUT_DIR:-/out}"
BUILD_DIR="${BUILD_DIR:-/build}"

# Default the vendored curl-cffi wheel to the version uv.lock resolves, so the
# packaged binary never drifts from the declared dependency set.
CURL_CFFI_VERSION="${CURL_CFFI_VERSION:-$(awk '
  /^name = "curl-cffi"$/ { f=1 }
  f && /^version =/ { gsub(/"/, "", $3); print $3; exit }
' "$REPO_DIR/uv.lock")}"

echo "Installing build dependencies..."
pacman -Syu --noconfirm --needed >/dev/null
pacman -S --noconfirm --needed base-devel python python-pip unzip >/dev/null
# makepkg refuses to run when the PKGBUILD runtime deps are missing, so
# install the same set PKGBUILD declares in depends=().
pacman -S --noconfirm --needed \
  python-beautifulsoup4 python-lxml python-rich python-platformdirs \
  python-defusedxml python-certifi python-cffi >/dev/null

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# makepkg's $srcdir defaults to <builddir>/src; pre-populate it with the repo.
cp -a "$REPO_DIR/." "$BUILD_DIR/src"
sed -i "s/^version = .*/version = \"$VERSION\"/" "$BUILD_DIR/src/pyproject.toml"

sed "s/^pkgver=.*/pkgver=$VERSION/" "$REPO_DIR/packaging/arch/PKGBUILD" \
    > "$BUILD_DIR/PKGBUILD"

useradd -m builder 2>/dev/null || true
chown -R builder:builder "$BUILD_DIR" "$OUT_DIR"

echo "Building Arch package v$VERSION..."
su builder -c \
    "cd '$BUILD_DIR' && CURL_CFFI_VERSION='$CURL_CFFI_VERSION' makepkg -f"

mkdir -p "$OUT_DIR"
cp "$BUILD_DIR"/comic-dl-*.pkg.tar.zst "$OUT_DIR"/
echo
echo "Package(s) written to $OUT_DIR:"
ls -l "$OUT_DIR"