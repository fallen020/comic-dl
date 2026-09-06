#!/usr/bin/env bash
# Debian package builder for comic-dl.
#
# All build work happens under /tmp. The project tree is never written to.
#
#   packaging/deb/build.sh stage      # called from debian/rules; stages the
#                                     # package contents into debian/tmp
#   packaging/deb/build.sh local      # full local build -> /tmp/comic-dl/*.deb
#   packaging/deb/build.sh docker     # build inside Docker -> /tmp/comic-dl/*.deb
#   packaging/deb/build.sh docker-build  # runs inside the container
#
# Env:
#   CURL_CFFI_VERSION   pin for the vendored abi3 wheel (default from uv.lock)
#   BUILD_DIR           staging root (default /tmp/comic-dl-build)
#   OUT_DIR             .deb output dir (default /tmp/comic-dl)
#   VERSION             package version (default 0.1.0; a leading 'v' is stripped)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Default the vendored curl-cffi wheel to the version uv.lock resolves, so the
# packaged binary never drifts from the declared dependency set.
CURL_CFFI_VERSION="${CURL_CFFI_VERSION:-$(awk '
  /^name = "curl-cffi"$/ { f=1 }
  f && /^version =/ { gsub(/"/, "", $3); print $3; exit }
' "$REPO_DIR/uv.lock")}"
BUILD_DIR="${BUILD_DIR:-/tmp/comic-dl-build}"
OUT_DIR="${OUT_DIR:-/tmp/comic-dl}"
VERSION="${VERSION#v}"
VERSION="${VERSION:-0.0.1}"

COPY_EXCLUDES=(
  --exclude=.git
  --exclude=.venv
  --exclude=__pycache__
  --exclude='*.pyc'
  --exclude='*.egg-info'
  --exclude=dist
  --exclude=build
  --exclude=site
  --exclude=debian/tmp
  --exclude=debian/stage
  --exclude=packaging/deb/debian
)

stage() {
  # Populate debian/tmp with the runtime layout. Run from the package build dir.
  local site="$PWD/debian/stage/usr/lib/comic-dl/site-packages"
  local bindir="$PWD/debian/stage/usr/bin"
  local wheeldir
  wheeldir="$(mktemp -d)"
  trap 'rm -rf "$wheeldir"' RETURN

  rm -rf "$site" "$bindir"
  mkdir -p "$site" "$bindir"

  python3 -m pip wheel --no-deps --wheel-dir "$wheeldir" .
  python3 -m pip wheel --no-deps --wheel-dir "$wheeldir" "curl-cffi==$CURL_CFFI_VERSION"

  (cd "$wheeldir" && unzip -qo comic_dl-*.whl -d "$site" && unzip -qo curl_cffi-*.whl -d "$site")

  cat > "$bindir/comic-dl" <<'EOF'
#!/bin/sh
PYTHONPATH=/usr/lib/comic-dl/site-packages${PYTHONPATH:+:$PYTHONPATH} \
    exec /usr/bin/python3 -m comic_dl "$@"
EOF
  chmod 0755 "$bindir/comic-dl"

  echo "staged: $site, $bindir"
}

copy_repo_to_build() {
  mkdir -p "$BUILD_DIR"
  tar -C "$REPO_DIR" "${COPY_EXCLUDES[@]}" -cf - . | tar -C "$BUILD_DIR" -xf -
}

stage_debian_dir() {
  # dpkg-buildpackage requires debian/ at the source root; it lives in the
  # repo under packaging/deb/debian so the project tree stays clean.
  rm -rf "$BUILD_DIR/debian"
  cp -a "$REPO_DIR/packaging/deb/debian" "$BUILD_DIR/debian"
}

prepare_build_tree() {
  # Stamp the package version into a build-tree copy so the wheel, the .deb
  # filename, and the changelog all agree. Never touches the repo.
  local dir="$1"
  sed -i "s/^version = .*/version = \"$VERSION\"/" "$dir/pyproject.toml"

  local top
  top="$(awk '/^[a-z0-9._-]+ \(/ { gsub(/[()]/, "", $2); print $2; exit }' "$dir/debian/changelog")"
  if [[ "$top" != "$VERSION" ]]; then
    local stamp
    stamp="$(date -u +'%a, %d %b %Y %H:%M:%S +0000')"
    {
      echo "comic-dl ($VERSION) unstable; urgency=medium"
      echo
      echo "  * Release $VERSION."
      echo
      echo " -- Comic Downloader contributors <maintainers@users.noreply.github.com>  $stamp"
      echo
      cat "$dir/debian/changelog"
    } > "$dir/debian/changelog.tmp"
    mv "$dir/debian/changelog.tmp" "$dir/debian/changelog"
  fi
}

local_build() {
  rm -rf "$BUILD_DIR" "$OUT_DIR"
  mkdir -p "$OUT_DIR"
  copy_repo_to_build
  stage_debian_dir
  prepare_build_tree "$BUILD_DIR"

  (cd "$BUILD_DIR" && dpkg-buildpackage -us -uc -b)

  cp "$BUILD_DIR"/../comic-dl_*.deb "$OUT_DIR"/
  echo
  echo "Package(s) written to $OUT_DIR:"
  ls -l "$OUT_DIR"
}

docker_build() {
  command -v docker >/dev/null 2>&1 || {
    echo "error: docker is not installed. Use 'packaging/deb/build.sh local' instead." >&2
    exit 1
  }
  rm -rf "$OUT_DIR"
  mkdir -p "$OUT_DIR"

  docker build -t comic-dl-deb -f "$REPO_DIR/packaging/deb/Dockerfile" "$REPO_DIR"
  docker run --rm \
    -v "$REPO_DIR:/src:ro" \
    -v "$OUT_DIR:/out" \
    -e CURL_CFFI_VERSION="$CURL_CFFI_VERSION" \
    comic-dl-deb \
    bash /src/packaging/deb/build.sh docker-build

  echo
  echo "Package(s) written to $OUT_DIR:"
  ls -l "$OUT_DIR"
}

docker_build_inside() {
  # Executed inside the container. /src is the repo (ro), /build is scratch.
  # Self-provisions like the rpm/arch scripts so raw distro images work.
  # build-essential is assumed present on buildds (never listed in
  # Build-Depends per policy) but absent from bare containers.
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends \
    build-essential debhelper python3 python3-pip unzip
  rm -rf /build
  mkdir -p /build
  tar -C /src "${COPY_EXCLUDES[@]}" -cf - . | tar -C /build -xf -
  cp -a /src/packaging/deb/debian /build/debian
  prepare_build_tree /build

  (cd /build && dpkg-buildpackage -us -uc -b)

  cp /build/../comic-dl_*.deb /out/
}

case "${1:-}" in
  stage) stage ;;
  local) local_build ;;
  docker) docker_build ;;
  docker-build) docker_build_inside ;;
  *)
    echo "usage: $0 {stage|local|docker}" >&2
    exit 2
    ;;
esac
