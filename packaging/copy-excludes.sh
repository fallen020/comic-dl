# One exclude list for all distro staging copies. Nothing any build reads
# lives under these paths (versions come from pyproject.toml/uv.lock).
COPY_EXCLUDES=(
  --exclude=.git
  --exclude=.venv
  --exclude=__pycache__
  --exclude='*.pyc'
  --exclude='*.egg-info'
  --exclude=dist
  --exclude=build
  --exclude=site
  --exclude=node_modules
  --exclude=debian/tmp
  --exclude=debian/stage
  --exclude=packaging/deb/debian
)
