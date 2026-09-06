"""Download size estimation, caps, and disk-space checks."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from ..ui import err_console, format_bytes, print_warning
from ..utils import parse_size_string

# Minimum free space required when the download size is unknown. A large
# fixed floor would reject small downloads (a 20 MB chapter under 512 MB
# free), so keep it modest; when an estimate exists, the estimate + margin
# drives the check instead.
MIN_FREE_DISK_BYTES = 64 * 1024 * 1024

# Per-page byte guess for the --max-size pre-gate when neither the site nor
# a probe yields a size. Comic pages average well under this, and the
# runtime total-size cap still enforces --max-size byte-exactly during the
# download, so the guess only needs to be sane — not worst-case.
UNKNOWN_PAGE_BYTES_GUESS = 5 * 1024 * 1024


def _parse_size(raw: str) -> int:
    try:
        return parse_size_string(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid size: {raw!r} (e.g. 100MB, 2GB, 512KB, 104857600)"
        ) from None


def format_option_size(value: int | None) -> str:
    """Render a size option for display: ``0``/``None`` mean unlimited, and
    byte counts become human units (``104857600`` → ``100 MB``)."""
    if value is None or value <= 0:
        return "unlimited"
    return format_bytes(value)


def _estimate_download_bytes(known_size: int = 0) -> int:
    """Return the site-provided download size when known, otherwise 0.

    A return of 0 means "unknown" — callers must not hard-fail on the
    estimate; the fixed free-space floor in `_check_disk_space` guards instead.
    """
    return known_size if known_size > 0 else 0


def _check_disk_space(path: Path, estimate: int) -> bool:
    if estimate > 0:
        safety_margin = int(estimate * 0.1)
        required = max(estimate + safety_margin, MIN_FREE_DISK_BYTES)
    else:
        required = MIN_FREE_DISK_BYTES
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return True

    if usage.free >= required:
        return True

    print_warning("Insufficient disk space for download:")
    if estimate > 0:
        err_console.print(f"    Estimated download:     {format_bytes(estimate)}")
        err_console.print(f"    Safety margin:          {format_bytes(safety_margin)}")
        err_console.print(f"    Recommended free space: {format_bytes(required)}")
    else:
        err_console.print(f"    Minimum free space:     {format_bytes(required)}")
    err_console.print(f"    Available on {path}:    {format_bytes(usage.free)}")
    return False
