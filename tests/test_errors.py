from __future__ import annotations

import pytest

from comic_dl.cli import _unknown_command, parse_urls
from comic_dl.cli.library import run_library_command
from comic_dl.errors import (
    EXIT_ERROR,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USAGE,
    ComicError,
    ExitCode,
    ScrapeError,
    ValidationError,
)
from comic_dl.ui import (
    ComicArgumentParser,
    print_error,
    print_success,
    print_warning,
    report_error,
    set_verbosity,
)


@pytest.fixture(autouse=True)
def _reset_verbosity():
    yield
    set_verbosity(0)


class TestExitCodeConstants:
    def test_spec_table(self):
        assert EXIT_OK == 0
        assert EXIT_ERROR == 1
        assert EXIT_USAGE == 2
        assert EXIT_INTERRUPTED == 130

    def test_exit_codes_are_int_enums(self):
        """IntEnum members stay int-compatible for sys.exit and comparisons."""
        assert isinstance(EXIT_ERROR, int)
        assert ExitCode.OK == 0
        assert EXIT_INTERRUPTED is ExitCode.INTERRUPTED

    def test_comic_error_maps_to_exit_code(self):
        assert ValidationError().exit_code == EXIT_USAGE


class TestTimeoutMessages:
    def test_scrape_timeout_message_names_url(self):
        from comic_dl.errors import ScrapeTimeout

        err = ScrapeTimeout("https://a/", 30.0)
        assert str(err) == "Request timed out after 30s fetching https://a/"
        assert err.url == "https://a/"
        assert err.timeout == 30.0

    def test_download_timeout_message_names_file(self):
        from comic_dl.errors import DownloadTimeout

        err = DownloadTimeout("p1.jpg", 30.0)
        assert str(err) == "Download timed out after 30s (p1.jpg)"
        assert err.filename == "p1.jpg"


class TestErrorKindTaxonomy:
    """Every ComicError subclass exposes a stable machine-readable kind."""

    def test_subclass_kinds(self):
        from comic_dl.errors import (
            DownloadError,
            DownloadTimeout,
            LibraryError,
            ScrapeError,
            ScrapeTimeout,
        )

        assert ValidationError().kind == "usage"
        assert ScrapeError("x").kind == "scrape"
        assert DownloadError().kind == "download"
        assert ScrapeTimeout("https://a/", 30.0).kind == "timeout"
        assert DownloadTimeout("p1.jpg", 30.0).kind == "timeout"
        assert LibraryError().kind == "library"

    def test_error_kind_helper_mirrors_classify(self):
        from comic_dl.errors import ScrapeError
        from comic_dl.ui import error_kind

        assert error_kind(ScrapeError("x")) == "scrape"
        assert error_kind(ConnectionError("boom")) == "network"
        assert error_kind(OSError("disk")) == "os"
        assert error_kind(RuntimeError("?")) == "internal"


class TestParseUrlsExitCodes:
    def test_invalid_scheme_is_usage(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv", ["prog", "--url", "/tmp/not/a/url"],
        )
        with pytest.raises(SystemExit) as exc_info:
            parse_urls()
        assert exc_info.value.code == EXIT_USAGE

    def test_missing_file_is_usage(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv", ["prog", "--file", "/nonexistent/urls.txt"],
        )
        with pytest.raises(SystemExit) as exc_info:
            parse_urls()
        assert exc_info.value.code == EXIT_USAGE

    def test_unknown_flag_exits_usage_and_suggests(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["prog", "--list-sourec"])
        with pytest.raises(SystemExit) as exc_info:
            parse_urls()
        assert exc_info.value.code == EXIT_USAGE
        err = capsys.readouterr().err
        assert "Did you mean: --list-sources" in err

    def test_unknown_flag_shows_no_traceback(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["prog", "--bogus-flag"])
        with pytest.raises(SystemExit):
            parse_urls()
        assert "Traceback" not in capsys.readouterr().err


class TestUnknownCommand:
    def test_returns_usage(self, capsys):
        assert _unknown_command("lis") == EXIT_USAGE

    def test_suggests_close_command(self, capsys):
        _unknown_command("lis")
        err = capsys.readouterr().err
        assert "unknown command 'lis'" in err
        assert "Did you mean: list?" in err

    def test_known_command_not_suggested(self, capsys):
        _unknown_command("update")
        err = capsys.readouterr().err
        assert "Did you mean" not in err


class TestLibraryExitCodes:
    def test_unknown_command_is_usage(self):
        assert run_library_command("bogus", []) == EXIT_USAGE

    def test_missing_db_is_runtime_error(self, tmp_path):
        root = tmp_path / "dl"
        root.mkdir()
        db = root / ".comic-dl" / "library.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"this is not a sqlite database")
        assert run_library_command("list", ["-o", str(root)]) == EXIT_ERROR

    def test_invalid_days_is_usage(self, tmp_path):
        root = tmp_path / "dl"
        root.mkdir()
        assert run_library_command(
            "latest", ["-o", str(root), "--days", "0"],
        ) == EXIT_USAGE


class TestReportError:
    def test_normal_mode_hides_traceback(self, capsys):
        set_verbosity(0)
        code = report_error(ValueError("boom"))
        err = capsys.readouterr().err
        assert code == EXIT_ERROR
        assert "Unexpected internal error." in err
        assert "Traceback" not in err

    def test_diagnostic_mode_hides_traceback(self, capsys):
        set_verbosity(2)
        code = report_error(ValueError("boom"))
        err = capsys.readouterr().err
        assert code == EXIT_ERROR
        assert "Unexpected internal error." in err
        assert "Traceback" not in err

    def test_trace_mode_shows_traceback(self, capsys):
        set_verbosity(3)
        try:
            raise ValueError("boom")
        except ValueError as exc:
            code = report_error(exc)
        err = capsys.readouterr().err
        assert code == EXIT_ERROR
        assert "Traceback" in err
        assert "ValueError: boom" in err

    def test_comic_error_message_and_code(self, capsys):
        code = report_error(ValidationError("bad chapters"))
        assert code == EXIT_USAGE
        assert "bad chapters" in capsys.readouterr().err

    def test_context_and_hint(self, capsys):
        code = report_error(
            ValueError("boom"), context="Failed: https://x", hint="Try again.",
        )
        err = capsys.readouterr().err
        assert code == EXIT_ERROR
        assert "Failed: https://x" in err
        assert "Try again." in err


class TestScrapeError:
    def test_is_a_valueerror_for_cli_catch_sites(self):
        # The CLI's per-chapter handler catches ValueError; scrapers raising
        # ScrapeError must keep flowing through it unchanged.
        assert issubclass(ScrapeError, ValueError)

    def test_is_a_comic_error_for_top_level_reporting(self):
        err = ScrapeError("No images found on this page.", hint="Try another URL.")
        assert isinstance(err, ComicError)
        assert err.exit_code == EXIT_ERROR
        assert str(err) == "No images found on this page."
        assert err.hint == "Try another URL."

    def test_hint_defaults_empty(self):
        assert ScrapeError("bad page").hint == ""


class TestStreamSeparation:
    def test_error_goes_to_stderr(self, capsys):
        print_error("boom")
        captured = capsys.readouterr()
        assert "boom" in captured.err
        assert "boom" not in captured.out

    def test_warning_goes_to_stderr(self, capsys):
        print_warning("careful")
        captured = capsys.readouterr()
        assert "careful" in captured.err
        assert "careful" not in captured.out

    def test_success_stays_stdout(self, capsys):
        print_success("ok")
        captured = capsys.readouterr()
        assert "ok" in captured.out
        assert "ok" not in captured.err


class TestParserSuggestion:
    def test_suggests_known_flag(self, capsys):
        parser = ComicArgumentParser(prog="prog")
        parser.add_argument("--list-sources", action="store_true")
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--list-sourec"])
        assert exc_info.value.code == EXIT_USAGE
        assert "Did you mean: --list-sources" in capsys.readouterr().err

    def test_no_suggestion_for_far_typo(self, capsys):
        parser = ComicArgumentParser(prog="prog")
        parser.add_argument("--list-sources", action="store_true")
        with pytest.raises(SystemExit):
            parser.parse_args(["--zzzz"])
        assert "Did you mean" not in capsys.readouterr().err


class TestMainExitCodes:
    @pytest.mark.asyncio
    async def test_unknown_command_via_main(self, monkeypatch, capsys):
        from comic_dl.cli import main

        monkeypatch.setattr("sys.argv", ["prog", "lis"])
        assert await main() == EXIT_USAGE
        assert "Did you mean: list?" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_main_usage_error_exit_code(self, monkeypatch, capsys):
        from comic_dl.cli import main

        monkeypatch.setattr(
            "sys.argv", ["prog", "--url", "/tmp/not/a/url"],
        )
        with pytest.raises(SystemExit) as exc_info:
            await main()
        assert exc_info.value.code == EXIT_USAGE

    @pytest.mark.asyncio
    async def test_main_accepts_verbosity_on_library_command(self, monkeypatch, tmp_path):
        from comic_dl.cli import main

        # -vv is global verbosity, stripped before the library parser runs.
        # list on an empty dir returns EXIT_OK ("Library is empty"); the
        # point is that the flag is not rejected as unrecognized (exit 2).
        monkeypatch.setattr(
            "sys.argv", ["prog", "list", "-o", str(tmp_path), "-vv"],
        )
        assert await main() == EXIT_OK
