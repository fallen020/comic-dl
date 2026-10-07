"""``python -m comic_dl`` entry point: exit codes and friendly import errors."""

from __future__ import annotations

import builtins
import subprocess
import sys

import comic_dl
from comic_dl import __main__ as entry_mod
from comic_dl.errors import EXIT_ERROR, EXIT_OK, EXIT_USAGE, ComicError


class TestEntry:
    def test_returns_cli_exit_code(self, monkeypatch):
        async def fake_main():
            return EXIT_OK

        monkeypatch.setattr("comic_dl.cli.main", fake_main)
        assert entry_mod.entry() == EXIT_OK

    def test_import_failure_reports_error_instead_of_traceback(self, monkeypatch, capsys):
        """A scraper that fails at import must surface as the friendly
        report_error path, not a raw traceback from the import machinery."""
        real_import = builtins.__import__

        def _boom(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "cli" and level == 1:
                raise ComicError("site plugin failed to load")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _boom)
        assert entry_mod.entry() == EXIT_ERROR
        err = capsys.readouterr().err
        assert "site plugin failed to load" in err
        assert "Traceback" not in err


def test_module_invocation_prints_version():
    result = subprocess.run(
        [sys.executable, "-m", "comic_dl", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert f"comic-dl {comic_dl.__version__}" in result.stdout


def test_module_invocation_propagates_exit_code():
    """``sys.exit(entry())`` must carry the CLI's usage code out of the
    ``python -m`` shim (unknown command returns, not raises)."""
    result = subprocess.run(
        [sys.executable, "-m", "comic_dl", "--no-config", "lis"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == EXIT_USAGE
