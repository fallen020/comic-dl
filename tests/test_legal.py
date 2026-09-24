"""First-run legal notice: one-time display, suppressions, forced re-show."""

from __future__ import annotations

import pytest

from comic_dl import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("COMIC_DL_NO_LEGAL_NOTICE", raising=False)


def _marker():
    return config.config_dir() / "legal-notice"


def _seed_marker():
    _marker().parent.mkdir(parents=True, exist_ok=True)
    _marker().write_text("1", encoding="utf-8")


def _run(monkeypatch, *argv):
    async def fake_run_urls(urls, args):
        return 0

    monkeypatch.setattr("comic_dl.cli._run_urls", fake_run_urls)
    monkeypatch.setattr("sys.argv", ["prog", *argv])
    from comic_dl.cli import main

    return main()


class TestScanGlobalFlags:
    def test_show_legal_notice_flag_stripped_for_subcommands(self):
        from comic_dl.cli import _scan_global_flags

        flags = _scan_global_flags(["--show-legal-notice", "help"])
        assert flags.argv == ["help"]

    def test_quiet_stripped_and_kept_for_update(self):
        from comic_dl.cli import _scan_global_flags

        assert _scan_global_flags(["-q", "https://e-hentai.org/g/1/2/"]).argv == [
            "https://e-hentai.org/g/1/2/"
        ]
        assert _scan_global_flags(["-q", "update", "x"]).argv == ["update", "x"]


class TestFirstRun:
    """The notice prints on the first download run and writes the marker."""

    @pytest.mark.asyncio
    async def test_first_run_prints_and_writes_marker(self, monkeypatch, capsys):
        assert await _run(monkeypatch, "--no-banner", "https://e-hentai.org/g/1/2/") == 0
        err = capsys.readouterr().err.replace("\n", "")
        assert "Legal notice" in err
        assert "not responsible for user actions" in err
        assert _marker().read_text(encoding="utf-8").strip() == "1"

    @pytest.mark.asyncio
    async def test_second_run_silent(self, monkeypatch, capsys):
        assert await _run(monkeypatch, "--no-banner", "https://e-hentai.org/g/1/2/") == 0
        capsys.readouterr()
        assert await _run(monkeypatch, "--no-banner", "https://e-hentai.org/g/1/2/") == 0
        assert "Legal notice" not in capsys.readouterr().err.replace("\n", "")

    @pytest.mark.asyncio
    async def test_preexisting_marker_skips_notice(self, monkeypatch, capsys):
        _seed_marker()
        assert await _run(monkeypatch, "--no-banner", "https://e-hentai.org/g/1/2/") == 0
        assert "Legal notice" not in capsys.readouterr().err.replace("\n", "")


class TestSuppression:
    """Scripted runs stay silent and never write the marker."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "argv",
        [
            ("--json", "https://e-hentai.org/g/1/2/"),
            ("--quiet", "https://e-hentai.org/g/1/2/"),
            ("--no-config", "https://e-hentai.org/g/1/2/"),
        ],
        ids=["json", "quiet", "no-config"],
    )
    async def test_flags_suppress(self, monkeypatch, capsys, argv):
        assert await _run(monkeypatch, *argv) == 0
        assert "Legal notice" not in capsys.readouterr().err.replace("\n", "")
        assert not _marker().exists()

    @pytest.mark.asyncio
    async def test_env_var_suppresses(self, monkeypatch, capsys):
        monkeypatch.setenv("COMIC_DL_NO_LEGAL_NOTICE", "1")
        assert await _run(monkeypatch, "--no-banner", "https://e-hentai.org/g/1/2/") == 0
        assert "Legal notice" not in capsys.readouterr().err.replace("\n", "")
        assert not _marker().exists()


class TestForceReshow:
    """--show-legal-notice re-shows an acknowledged notice and rewrites the marker."""

    @pytest.mark.asyncio
    async def test_forced_after_acknowledgement(self, monkeypatch, capsys):
        _seed_marker()
        assert (
            await _run(
                monkeypatch, "--no-banner", "--show-legal-notice", "https://e-hentai.org/g/1/2/"
            )
            == 0
        )
        err = capsys.readouterr().err.replace("\n", "")
        assert "Legal notice" in err
        assert _marker().read_text(encoding="utf-8").strip() == "1"

    @pytest.mark.asyncio
    async def test_forced_beats_quiet(self, monkeypatch, capsys):
        assert (
            await _run(monkeypatch, "--quiet", "--show-legal-notice", "https://e-hentai.org/g/1/2/")
            == 0
        )
        err = capsys.readouterr().err.replace("\n", "")
        assert "Legal notice" in err
        assert _marker().exists()

    @pytest.mark.asyncio
    async def test_forced_beats_no_config(self, monkeypatch, capsys):
        assert (
            await _run(
                monkeypatch, "--no-config", "--show-legal-notice", "https://e-hentai.org/g/1/2/"
            )
            == 0
        )
        assert "Legal notice" in capsys.readouterr().err.replace("\n", "")


class TestNonDownloadCommands:
    """Dispatched commands never print or acknowledge the notice."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("argv", "code"),
        [
            (("help",), 0),
            (("config",), 0),
            (("cookie",), 2),
            (("cache",), 2),
            (("update",), 2),
        ],
    )
    async def test_no_notice(self, monkeypatch, capsys, argv, code):
        assert await _run(monkeypatch, *argv) == code
        assert "Legal notice" not in capsys.readouterr().err.replace("\n", "")
        assert not _marker().exists()


class TestUnwritableConfigDir:
    """An unwritable config dir never fails the run; the notice still prints."""

    @pytest.mark.asyncio
    async def test_print_survives_marker_write_failure(self, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise OSError

        monkeypatch.setattr("tempfile.mkstemp", boom)
        from argparse import Namespace

        from comic_dl.cli import _maybe_show_legal_notice

        args = Namespace(json=False, quiet=False, no_config=False)
        assert _maybe_show_legal_notice(args) is True
        assert "Legal notice" in capsys.readouterr().err.replace("\n", "")
