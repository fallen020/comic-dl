from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from comic_dl import platform
from comic_dl.platform import default_editor, downloads_dir, system


class TestSystemSeam:
    def test_system_is_classified(self):
        assert system() in {"windows", "macos", "linux", "other"}


class TestDownloadsDir:
    def test_xdg_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(tmp_path / "dls"))
        assert downloads_dir() == tmp_path / "dls"

    def test_falls_back_to_home(self, monkeypatch):
        monkeypatch.delenv("XDG_DOWNLOAD_DIR", raising=False)
        monkeypatch.setattr(Path, "home", lambda: Path("/home/tester"))
        assert downloads_dir() == Path("/home/tester/Downloads")

    def test_known_folder_off_windows_returns_none(self, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        assert platform._windows_known_folder("{}") is None

    # On Python < 3.12, Path() dispatches on the *current* os.name, so the
    # mocked 'nt' makes even the expected value unconstructible on POSIX
    # (NotImplementedError) — and crashes pytest's own failure renderer
    # the same way. 3.12+ tolerates it; the winreg parsing itself is
    # version-independent.
    @pytest.mark.skipif(
        sys.version_info < (3, 12),
        reason="pathlib honors mocked os.name below 3.12",
    )
    def test_windows_known_folder_reads_shell_folders(self, monkeypatch):
        class _Key:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _FakeWinreg:
            HKEY_CURRENT_USER = object()

            @staticmethod
            def OpenKey(_root, _path):
                return _Key()

            @staticmethod
            def QueryValueEx(_key, guid):
                assert guid == "{}"
                return r"C:\Users\Tester\Downloads", 3

        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg)
        assert platform._windows_known_folder("{}") == Path(r"C:\Users\Tester\Downloads")


class TestDefaultEditor:
    def test_posix_default_editor(self):
        assert default_editor() == "vi"

    def test_windows_default_editor(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        assert default_editor() == "notepad"
