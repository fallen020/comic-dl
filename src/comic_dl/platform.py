"""Thin platform seam shared by the CLI and the packaging pipeline.

Keep this module deliberately small: most per-OS behavior already lives in
``platformdirs`` (via :mod:`comic_dl.config`) and the stdlib. This file
centralizes the handful of conventions that previously had to be spelled out
``os.name``/``platform.machine()``-style at each call site, so packaging
targets (Debian/Fedora/Arch/Windows, amd64 + arm64) and CI smoke checks can
reference one canonical spelling.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_WINDOWS_DOWNLOADS_GUID = "{374DE290-123F-4565-9164-39C4925E467B}"


def system() -> str:
    """Canonical OS name: ``"windows"``, ``"macos"``, ``"linux"``, else ``"other"``."""
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def _windows_known_folder(guid: str) -> Path | None:
    """Resolve a Windows Shell Folders GUID to a path, or None off-Windows.

    ``winreg`` is only importable on Windows; the dynamic import keeps the
    type-checker and other platforms happy.
    """
    if os.name != "nt":
        return None
    try:
        import importlib

        winreg = importlib.import_module("winreg")
    except ImportError:
        return None
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, guid)
    except OSError:
        return None
    if not isinstance(value, str) or not value:
        return None
    return Path(value)


def downloads_dir() -> Path:
    """The user's Downloads directory.

    Honors ``$XDG_DOWNLOAD_DIR`` when set; on Windows reads the real Shell
    Folders value (so OneDrive-redirected homes work) instead of assuming
    ``$HOME\\Downloads``; elsewhere falls back to ``~/Downloads``.
    """
    env = os.environ.get("XDG_DOWNLOAD_DIR")
    if env and env.strip():
        return Path(env.strip()).expanduser()
    if os.name == "nt":
        folder = _windows_known_folder(_WINDOWS_DOWNLOADS_GUID)
        if folder is not None:
            return folder
    return Path.home() / "Downloads"


def default_editor() -> str:
    """Fallback text editor when ``$VISUAL`` and ``$EDITOR`` are both unset."""
    return "notepad" if os.name == "nt" else "vi"
