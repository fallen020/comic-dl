from __future__ import annotations

import argparse
import asyncio
import io
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

import comic_dl.cli as cli
from comic_dl.cli import (
    ChapterSelection,
    _build_downloaded_index,
    _cbz_source_url,
    _check_disk_space,
    _cleanup_temp_dir,
    _estimate_download_bytes,
    _extract_domain,
    _handle_interrupt,
    _is_verbosity_token,
    _open_library,
    _parse_size,
    _read_urls_from_file,
    _resolve_archive_path,
    _run_with_network_retry,
    _scan_global_flags,
    _tmp_root,
    _with_spinner,
    format_option_size,
    parse_chapter_selection,
    parse_urls,
    process_url,
    request_stop,
    reset_stop,
    resume_command,
    stop_requested,
    validate_chapter_flag,
)
from comic_dl.errors import EXIT_ERROR, EXIT_OK, EXIT_USAGE, ScrapeTimeout
from comic_dl.library import Library, library_path
from comic_dl.models import ImageItem, PostMetadata
from comic_dl.ui import format_bytes
from comic_dl.utils import normalize_url


def _patch_chapter_scraper(monkeypatch, overrides):
    """Route ``domain -> instance`` for chapter scraping during a test.

    Intercepts the ``comic_dl.cli`` chapter lookup; all other domains fall
    through to the real registry.
    """
    from comic_dl.scrapers import registry

    real = registry.get_chapter_scraper

    def lookup(domain):
        if domain in overrides:
            return overrides[domain]
        return real(domain)

    monkeypatch.setattr("comic_dl.cli.get_chapter_scraper", lookup)


def _make_zip_cbz(path: Path, name: str, web: str = "") -> None:
    """Write a minimal valid .cbz carrying an optional source URL."""
    path.mkdir(parents=True, exist_ok=True)
    xml = f"<ComicInfo><Title>T</Title><Web>{web}</Web></ComicInfo>" if web \
        else "<ComicInfo><Title>T</Title></ComicInfo>"
    with zipfile.ZipFile(path / name, "w") as zf:
        zf.writestr("ComicInfo.xml", xml)


class TestNormalizeUrl:
    def test_normalizes(self):
        assert normalize_url("HTTP://EXAMPLE.COM") == "https://example.com/"

    def test_no_scheme(self):
        assert normalize_url("example.com").startswith("https://")

    def test_already_normalized(self):
        assert normalize_url("https://e-hentai.org/g/123/abc") == "https://e-hentai.org/g/123/abc"


class TestFormatBytes:
    def test_bytes(self):
        assert format_bytes(500) == "500 B"

    def test_kb(self):
        assert format_bytes(2048) == "2 KB"

    def test_mb(self):
        assert format_bytes(5 * 1024 * 1024) == "5 MB"

    def test_gb(self):
        assert format_bytes(3 * 1024 ** 3) == "3.0 GB"


class TestFormatOptionSize:
    def test_unlimited_when_zero(self):
        assert format_option_size(0) == "unlimited"

    def test_unlimited_when_none(self):
        assert format_option_size(None) == "unlimited"

    def test_human_units(self):
        assert format_option_size(100 * 1024 * 1024) == "100 MB"


class TestEstimateDownloadBytes:
    def test_unknown_size_returns_zero(self):
        assert _estimate_download_bytes() == 0

    def test_known_size_returned(self):
        known = 7 * 1024 ** 3
        est = _estimate_download_bytes(known_size=known)
        assert est == known

    def test_known_size_zero_ignored(self):
        assert _estimate_download_bytes(known_size=0) == 0


class TestCheckDiskSpace:
    def test_sufficient_space(self, tmp_path):
        assert _check_disk_space(tmp_path, 1) is True

    def test_insufficient_space(self, monkeypatch, tmp_path):
        class FakeUsage:
            free = 10

        monkeypatch.setattr(shutil, "disk_usage", lambda p: FakeUsage())
        assert _check_disk_space(tmp_path, 100) is False

    def test_oserror_returns_true(self, monkeypatch, tmp_path):
        monkeypatch.setattr(shutil, "disk_usage", lambda p: (_ for _ in ()).throw(OSError()))
        assert _check_disk_space(tmp_path, 100) is True

    def test_zero_estimate_enforces_floor(self, monkeypatch, tmp_path):
        class FakeUsage:
            free = 32 * 1024 * 1024  # below MIN_FREE_DISK_BYTES (64 MB)

        monkeypatch.setattr(shutil, "disk_usage", lambda p: FakeUsage())
        assert _check_disk_space(tmp_path, 0) is False

    def test_zero_estimate_passes_above_floor(self, monkeypatch, tmp_path):
        class FakeUsage:
            free = 1024 ** 3  # above MIN_FREE_DISK_BYTES

        monkeypatch.setattr(shutil, "disk_usage", lambda p: FakeUsage())
        assert _check_disk_space(tmp_path, 0) is True

    def test_small_download_passes_with_modest_free_space(self, monkeypatch, tmp_path):
        """A ~20 MB download must not fail with only 100 MB free (the old
        512 MB floor rejected it)."""
        class FakeUsage:
            free = 100 * 1024 * 1024

        monkeypatch.setattr(shutil, "disk_usage", lambda p: FakeUsage())
        assert _check_disk_space(tmp_path, 20 * 1024 * 1024) is True

    def test_known_size_never_below_floor(self, monkeypatch, tmp_path):
        """A tiny estimate still requires the minimum floor."""
        class FakeUsage:
            free = 48 * 1024 * 1024  # below the 64 MB floor

        monkeypatch.setattr(shutil, "disk_usage", lambda p: FakeUsage())
        assert _check_disk_space(tmp_path, 1 * 1024 * 1024) is False

    def test_known_size_gate_passes(self, monkeypatch, tmp_path):
        class FakeUsage:
            free = 10 * 1024 ** 3  # 10 GB free

        monkeypatch.setattr(shutil, "disk_usage", lambda p: FakeUsage())
        assert _check_disk_space(tmp_path, 7 * 1024 ** 3) is True

    def test_known_size_gate_fails(self, monkeypatch, tmp_path):
        class FakeUsage:
            free = 5 * 1024 ** 3  # 5 GB free, needs ~7.7 GB

        monkeypatch.setattr(shutil, "disk_usage", lambda p: FakeUsage())
        assert _check_disk_space(tmp_path, 7 * 1024 ** 3) is False


class TestCleanupTempDir:
    def test_cleans_existing_dir(self, monkeypatch):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "stale").mkdir()
        (tmp / "stale" / "file.txt").write_bytes(b"data")
        monkeypatch.setattr("comic_dl.cli._TMP_ROOT", tmp)
        assert tmp.exists()
        _cleanup_temp_dir()
        assert not tmp.exists()

    def test_no_error_for_nonexistent(self, monkeypatch):
        monkeypatch.setattr("comic_dl.cli._TMP_ROOT", None)
        _cleanup_temp_dir()

    def test_no_allocation_when_never_used(self, monkeypatch):
        monkeypatch.setattr("comic_dl.cli._TMP_ROOT", None)
        _cleanup_temp_dir()
        assert cli._TMP_ROOT is None


class TestTmpRootAllocation:
    def test_allocated_with_mkdtemp_semantics(self, monkeypatch):
        import stat
        monkeypatch.setattr("comic_dl.cli._TMP_ROOT", None)
        root = _tmp_root()
        try:
            # Random per-run name, not the predictable shared path.
            assert root.name.startswith("comic-dl-")
            assert root != Path(tempfile.gettempdir()) / "comic-dl"
            # Owner-only permissions (mkdtemp guarantees 0700).
            mode = stat.S_IMODE(root.stat().st_mode)
            assert mode == 0o700
        finally:
            shutil.rmtree(root, ignore_errors=True)
            monkeypatch.setattr("comic_dl.cli._TMP_ROOT", None)

    def test_reused_within_process(self, monkeypatch):
        monkeypatch.setattr("comic_dl.cli._TMP_ROOT", None)
        first = _tmp_root()
        second = _tmp_root()
        try:
            assert first == second
        finally:
            shutil.rmtree(first, ignore_errors=True)
            monkeypatch.setattr("comic_dl.cli._TMP_ROOT", None)

    def test_uses_configured_tmp_dir(self, monkeypatch, tmp_path):
        scratch = tmp_path / "scratch"
        monkeypatch.setattr("comic_dl.cli._TMP_ROOT", None)
        monkeypatch.setattr(
            "comic_dl.cli.download_setting",
            lambda name, default=None: str(scratch),
        )
        root = _tmp_root()
        try:
            assert root.parent == scratch
            assert root.name.startswith("comic-dl-")
        finally:
            shutil.rmtree(root, ignore_errors=True)
            monkeypatch.setattr("comic_dl.cli._TMP_ROOT", None)


class TestQuietFlagDispatch:
    """Regression: ``update -q`` must reach the update parser (F4)."""

    pytestmark = pytest.mark.asyncio

    async def test_update_receives_quiet_flag(self, monkeypatch):
        received: list[list[str]] = []

        async def fake_run_update(argv):
            received.append(list(argv))
            return 0

        monkeypatch.setattr("comic_dl.cli._run_update", fake_run_update)
        monkeypatch.setattr("sys.argv", ["prog", "update", "-q", "My Series"])
        from comic_dl.cli import main
        assert await main() == 0
        assert received and "-q" in received[0]

    async def test_long_form_quiet_also_survives(self, monkeypatch):
        received: list[list[str]] = []

        async def fake_run_update(argv):
            received.append(list(argv))
            return 0

        monkeypatch.setattr("comic_dl.cli._run_update", fake_run_update)
        monkeypatch.setattr("sys.argv", ["prog", "update", "--quiet", "s"])
        from comic_dl.cli import main
        assert await main() == 0
        assert received and "--quiet" in received[0]

    async def test_quiet_stripped_for_other_subcommands(self, monkeypatch):
        received: list[list[str]] = []

        def fake_run_cache(argv):
            received.append(list(argv))
            return 0

        monkeypatch.setattr("comic_dl.cli._run_cache", fake_run_cache)
        monkeypatch.setattr("sys.argv", ["prog", "cache", "-q", "status"])
        from comic_dl.cli import main
        assert await main() == 0
        # cache's parser does not declare -q; it must be stripped.
        assert "-q" not in received[0]


class TestCommandDispatchOrder:
    """Regression: flags before a command must not fall through to URL
    parsing (F5) — ``prog --impersonate X update Y`` used to treat
    'update'/'Y' as download URLs."""

    pytestmark = pytest.mark.asyncio

    async def test_flag_before_command_rejected_loudly(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--impersonate", "chrome131", "update", "My Series"],
        )
        from comic_dl.cli import main
        assert await main() == 2
        err = capsys.readouterr().err
        assert "before command 'update'" in err
        assert "comic-dl update My Series" in err

    async def test_command_first_still_dispatches(self, monkeypatch):
        received: list[list[str]] = []

        async def fake_run_update(argv):
            received.append(list(argv))
            return 0

        monkeypatch.setattr("comic_dl.cli._run_update", fake_run_update)
        monkeypatch.setattr("sys.argv", ["prog", "update", "-q", "s"])
        from comic_dl.cli import main
        assert await main() == 0
        assert received == [["-q", "s"]]


class TestWithSpinner:
    pytestmark = pytest.mark.asyncio
    async def test_quiet_mode_skips_spinner(self):
        result = await _with_spinner("test", True, _fake_coro("done"))
        assert result == "done"

    async def test_non_quiet_uses_spinner(self):
        result = await _with_spinner("test", False, _fake_coro("done"))
        assert result == "done"


async def _fake_coro(val):
    return val


class TestReadUrlsFromFile:
    def test_reads_nonempty_lines(self, tmp_path):
        f = tmp_path / "urls.txt"
        f.write_text("https://a.com/\nhttps://b.com/\n")
        assert _read_urls_from_file(f) == ["https://a.com/", "https://b.com/"]

    def test_skips_blank_lines(self, tmp_path):
        f = tmp_path / "urls.txt"
        f.write_text("https://a.com/\n\n\nhttps://b.com/\n")
        assert _read_urls_from_file(f) == ["https://a.com/", "https://b.com/"]

    def test_skips_comments(self, tmp_path):
        f = tmp_path / "urls.txt"
        f.write_text("# comment\nhttps://a.com/\n")
        assert _read_urls_from_file(f) == ["https://a.com/"]

    def test_strips_whitespace(self, tmp_path):
        f = tmp_path / "urls.txt"
        f.write_text("  https://a.com/  \n")
        assert _read_urls_from_file(f) == ["https://a.com/"]

    def test_dedupes_identical_urls_keeping_order(self, tmp_path):
        f = tmp_path / "urls.txt"
        f.write_text("https://a.com/\nhttps://a.com/\nhttps://b.com/\n")
        assert _read_urls_from_file(f) == ["https://a.com/", "https://b.com/"]

    def test_dedupes_normalized_variants(self, tmp_path):
        f = tmp_path / "urls.txt"
        f.write_text("https://a.com/\nhttp://a.com/\nhttps://A.com/\nhttps://b.com/\n")
        assert _read_urls_from_file(f) == ["https://a.com/", "https://b.com/"]

    def test_dedupes_keeps_first_spelling(self, tmp_path):
        f = tmp_path / "urls.txt"
        f.write_text("https://A.com/1\nhttps://a.com/1\n")
        assert _read_urls_from_file(f) == ["https://A.com/1"]

    def test_returns_none_for_directory(self, tmp_path):
        assert _read_urls_from_file(tmp_path) is None

    def test_skips_overlong_url(self, tmp_path, capsys):
        from comic_dl.cli import MAX_URL_LENGTH
        f = tmp_path / "urls.txt"
        long_url = "https://a.com/" + "x" * (MAX_URL_LENGTH + 10)
        f.write_text(f"{long_url}\nhttps://b.com/\n")
        assert _read_urls_from_file(f) == ["https://b.com/"]
        assert "longer than" in (capsys.readouterr().err)

    def test_stops_at_max_urls(self, tmp_path, capsys):
        from comic_dl.cli import MAX_URLS_PER_RUN, _read_urls_from_file_indexed
        f = tmp_path / "urls.txt"
        f.write_text(
            "".join(f"https://a.com/{i}\n" for i in range(MAX_URLS_PER_RUN + 5))
        )
        result = _read_urls_from_file_indexed(f)
        assert result is not None
        assert len(result) == MAX_URLS_PER_RUN
        assert "stopping at" in (capsys.readouterr().err)


class TestReadUrlsFromFileIndexed:
    def test_reports_line_numbers(self, tmp_path):
        from comic_dl.cli import _read_urls_from_file_indexed

        f = tmp_path / "urls.txt"
        f.write_text(
            "# comment\n\nhttps://a.com/\nhttps://b.com/\n"
        )
        assert _read_urls_from_file_indexed(f) == [
            ("https://a.com/", 3),
            ("https://b.com/", 4),
        ]

    def test_dedup_keeps_first_occurrence_line(self, tmp_path):
        from comic_dl.cli import _read_urls_from_file_indexed

        f = tmp_path / "urls.txt"
        f.write_text(
            "https://a.com/\nhttps://b.com/\nhttps://A.com/\n"
        )
        assert _read_urls_from_file_indexed(f) == [
            ("https://a.com/", 1),
            ("https://b.com/", 2),
        ]

    def test_strips_inline_comments(self, tmp_path):
        from comic_dl.cli import _read_urls_from_file_indexed

        f = tmp_path / "urls.txt"
        f.write_text(
            "https://a.com/ # chapter 1\nhttps://b.com/\t# second\n"
        )
        assert _read_urls_from_file_indexed(f) == [
            ("https://a.com/", 1),
            ("https://b.com/", 2),
        ]

    def test_preserves_url_fragments(self, tmp_path):
        from comic_dl.cli import _read_urls_from_file_indexed

        f = tmp_path / "urls.txt"
        f.write_text(
            "https://a.com/#page2\n"
        )
        assert _read_urls_from_file_indexed(f) == [
            ("https://a.com/#page2", 1),
        ]

    def test_skips_invalid_urls_with_warning(self, tmp_path, capsys):
        from comic_dl.cli import _read_urls_from_file_indexed

        f = tmp_path / "urls.txt"
        f.write_text(
            "https://a.com/\nnot a url\nftp://b.com/\nhttps://c.com/\n"
        )
        assert _read_urls_from_file_indexed(f) == [
            ("https://a.com/", 1),
            ("https://c.com/", 4),
        ]
        err = capsys.readouterr().err.replace("\n", "")
        assert f"{f}:2" in err
        assert "not a url" in err
        assert f"{f}:3" in err
        assert "ftp://b.com/" in err

    def test_comment_only_line_after_inline_comment(self, tmp_path):
        from comic_dl.cli import _read_urls_from_file_indexed

        f = tmp_path / "urls.txt"
        f.write_text(
            "   # full-line comment\nhttps://a.com/ # inline\n"
        )
        assert _read_urls_from_file_indexed(f) == [
            ("https://a.com/", 2),
        ]


class TestParseUrls:
    def test_url_argument(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv", ["prog", "--url", "https://e-hentai.org/g/123/abc/"],
        )
        urls, _args = parse_urls()
        assert len(urls) == 1
        assert urls[0] == "https://e-hentai.org/g/123/abc/"

    def test_file_argument(self, tmp_path, monkeypatch):
        f = tmp_path / "urls.txt"
        f.write_text("https://e-hentai.org/g/1/a/\nhttps://pawchive.pw/p/user/2/post/3/\n")
        monkeypatch.setattr("sys.argv", ["prog", "--file", str(f)])
        urls, _args = parse_urls()
        assert len(urls) == 2
        assert urls[0] == "https://e-hentai.org/g/1/a/"
        assert urls[1] == "https://pawchive.pw/p/user/2/post/3/"

    def test_file_with_comments(self, tmp_path, monkeypatch):
        f = tmp_path / "urls.txt"
        f.write_text("# this is a comment\nhttps://e-hentai.org/g/1/a/\n")
        monkeypatch.setattr("sys.argv", ["prog", "--file", str(f)])
        urls, _args = parse_urls()
        assert len(urls) == 1

    def test_file_prints_load_count(self, tmp_path, monkeypatch, capsys):
        f = tmp_path / "urls.txt"
        f.write_text("https://a.com/\nhttps://b.com/\nhttps://c.com/\n")
        monkeypatch.setattr("sys.argv", ["prog", "--file", str(f)])
        urls, _ = parse_urls()
        assert urls == ["https://a.com/", "https://b.com/", "https://c.com/"]
        err = capsys.readouterr().err.replace("\n", "")
        assert f"Loaded 3 URLs from {f}" in err

    def test_file_singular_load_count(self, tmp_path, monkeypatch, capsys):
        f = tmp_path / "urls.txt"
        f.write_text("https://a.com/\n")
        monkeypatch.setattr("sys.argv", ["prog", "--file", str(f)])
        parse_urls()
        assert f"Loaded 1 URL from {f}" in capsys.readouterr().err.replace("\n", "")

    def test_file_load_count_suppressed_when_quiet(self, tmp_path, monkeypatch, capsys):
        f = tmp_path / "urls.txt"
        f.write_text("https://a.com/\nhttps://b.com/\n")
        monkeypatch.setattr("sys.argv", ["prog", "--file", str(f), "--quiet"])
        parse_urls()
        assert "Loaded" not in capsys.readouterr().err

    def test_url_argument_prints_no_load_count(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv", ["prog", "--url", "https://a.com/"]
        )
        parse_urls()
        assert "Loaded" not in capsys.readouterr().err

    def test_all_invalid_file_errors(self, tmp_path, monkeypatch, capsys):
        f = tmp_path / "urls.txt"
        f.write_text("not a url\nftp://b.com/\n")
        monkeypatch.setattr("sys.argv", ["prog", "--file", str(f)])
        with pytest.raises(SystemExit) as excinfo:
            parse_urls()
        assert excinfo.value.code == 2
        assert "no valid URLs" in capsys.readouterr().err.replace("\n", "")

    def test_file_sets_url_origins(self, tmp_path, monkeypatch):
        f = tmp_path / "urls.txt"
        f.write_text(
            "# comment\n\nhttps://a.com/\nhttps://b.com/\nhttps://a.com/\n"
        )
        monkeypatch.setattr("sys.argv", ["prog", "--file", str(f)])
        urls, args = parse_urls()
        assert urls == ["https://a.com/", "https://b.com/"]
        assert args.url_origins == {
            "https://a.com/": f"{f}:3",
            "https://b.com/": f"{f}:4",
        }

    def test_url_argument_has_no_origins(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv", ["prog", "-u", "https://a.com/"]
        )
        _, args = parse_urls()
        assert args.url_origins is None

    def test_overlong_single_url_rejected(self, monkeypatch, capsys):
        from comic_dl.cli import MAX_URL_LENGTH
        from comic_dl.errors import EXIT_USAGE

        long_url = "https://a.com/" + "x" * (MAX_URL_LENGTH + 5)
        monkeypatch.setattr("sys.argv", ["prog", "-u", long_url])
        with pytest.raises(SystemExit) as excinfo:
            parse_urls()
        assert excinfo.value.code == EXIT_USAGE
        captured = capsys.readouterr()
        assert "maximum" in (captured.out + captured.err)

    def test_file_not_found(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent/urls.txt"])
        with pytest.raises(SystemExit):
            parse_urls()

    def test_default_output(self, monkeypatch, tmp_path):
        monkeypatch.setattr("comic_dl.cli.configured_output_dir", lambda: tmp_path / "dl")
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/"])
        _, args = parse_urls()
        assert args.output == tmp_path / "dl"

    def test_custom_output(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/", "--output", str(tmp_path / "mydl")])
        _, args = parse_urls()
        assert args.output == tmp_path / "mydl"

    def test_quiet_flag(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/", "--quiet"])
        _, args = parse_urls()
        assert args.quiet is True

    def test_no_banner_flag(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/", "--no-banner"]
        )
        _, args = parse_urls()
        assert args.no_banner is True

    def test_no_clobber_flag(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--no-clobber"],
        )
        _, args = parse_urls()
        assert args.no_clobber is True
        assert args.force is False

    def test_no_clobber_conflicts_with_force(self, monkeypatch, capsys):

        from comic_dl.errors import EXIT_USAGE

        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/",
             "--no-clobber", "--force"],
        )
        with pytest.raises(SystemExit) as excinfo:
            parse_urls()
        assert excinfo.value.code == EXIT_USAGE
        captured = capsys.readouterr()
        assert "conflicts with --force" in (captured.out + captured.err)

    def test_output_directory_displayed_at_startup(self, monkeypatch, tmp_path, capsys):
        outdir = tmp_path / "dl"
        monkeypatch.setattr("comic_dl.cli.configured_output_dir", lambda: outdir)
        monkeypatch.setattr(
            "sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/", "--no-banner"]
        )
        parse_urls()
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Output directory" in combined
        # Strip folding newlines: Rich wraps long paths at the console width,
        # which can split the path mid-token (joining with a space would not
        # restore it).
        assert str(outdir.resolve()) in combined.replace("\n", "")

    def test_output_directory_hidden_when_quiet(self, monkeypatch, tmp_path, capsys):
        outdir = tmp_path / "dl"
        monkeypatch.setattr("comic_dl.cli.configured_output_dir", lambda: outdir)
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--no-banner", "--quiet"],
        )
        parse_urls()
        captured = capsys.readouterr()
        assert "Output directory" not in (captured.out + captured.err)

    def test_debug_file_flag(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--debug-file", "/tmp/x.log"],
        )
        _, args = parse_urls()
        assert str(args.debug_file) == "/tmp/x.log"

    def test_verbose_counter(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/", "-vv"])
        _, args = parse_urls()
        assert args.verbose == 2

    def test_concurrency_default(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/"])
        _, args = parse_urls()
        assert args.concurrency == 5

    def test_concurrency_zero_rejected(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/", "-c", "0"])
        with pytest.raises(SystemExit):
            parse_urls()

    def test_concurrency_negative_rejected(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/", "-c", "-1"])
        with pytest.raises(SystemExit):
            parse_urls()

    def test_chapter_parallel_default(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/"])
        _, args = parse_urls()
        assert args.chapter_parallel == 1

    def test_chapter_parallel_value(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--chapter-parallel", "4"],
        )
        _, args = parse_urls()
        assert args.chapter_parallel == 4

    def test_chapter_parallel_zero_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--chapter-parallel", "0"],
        )
        with pytest.raises(SystemExit):
            parse_urls()

    def test_chapter_parallel_capped(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--chapter-parallel", "99"],
        )
        _, args = parse_urls()
        assert args.chapter_parallel == 8
        captured = capsys.readouterr()
        assert "--chapter-parallel capped to 8" in (captured.out + captured.err)

    def test_interactive_file_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.argv", ["prog"])
        monkeypatch.setattr(
            "comic_dl.cli._is_interactive_output", lambda: True,
        )
        f = tmp_path / "links.txt"
        f.write_text("https://e-hentai.org/g/1/a/\n")
        monkeypatch.setattr("comic_dl.cli.Prompt.ask", lambda msg: str(f))
        urls, _ = parse_urls()
        assert urls == ["https://e-hentai.org/g/1/a/"]

    def test_interactive_url_with_scheme(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog"])
        monkeypatch.setattr(
            "comic_dl.cli._is_interactive_output", lambda: True,
        )
        monkeypatch.setattr("comic_dl.cli.Prompt.ask", lambda msg: "https://e-hentai.org/g/1/a/")
        urls, _ = parse_urls()
        assert urls == ["https://e-hentai.org/g/1/a/"]

    def test_interactive_empty_input(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog"])
        monkeypatch.setattr(
            "comic_dl.cli._is_interactive_output", lambda: True,
        )
        monkeypatch.setattr("comic_dl.cli.Prompt.ask", lambda msg: "  ")
        with pytest.raises(SystemExit):
            parse_urls()

    def test_interactive_nonexistent_path_no_scheme(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog"])
        monkeypatch.setattr(
            "comic_dl.cli._is_interactive_output", lambda: True,
        )
        monkeypatch.setattr("comic_dl.cli.Prompt.ask", lambda msg: "nonexistent_file.txt")
        urls, _ = parse_urls()
        assert len(urls) == 1
        assert urls[0].startswith("https://")

    def test_interactive_eof(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog"])
        monkeypatch.setattr(
            "comic_dl.cli._is_interactive_output", lambda: True,
        )
        def _raise_eof(*args):
            raise EOFError()
        monkeypatch.setattr("comic_dl.cli.Prompt.ask", _raise_eof)
        with pytest.raises(SystemExit) as exc_info:
            parse_urls()
        assert exc_info.value.code == 130

    def test_noninteractive_no_args_exits_usage(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog"])
        monkeypatch.setattr(
            "comic_dl.cli._is_interactive_output", lambda: False,
        )
        with pytest.raises(SystemExit) as exc_info:
            parse_urls()
        assert exc_info.value.code == EXIT_USAGE

    def test_quiet_verbose_mutually_exclusive(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "-q", "-v"],
        )
        with pytest.raises(SystemExit) as exc_info:
            parse_urls()
        assert exc_info.value.code == EXIT_USAGE

    def test_quiet_verbose_count_mutually_exclusive(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "-q", "-vv"],
        )
        with pytest.raises(SystemExit) as exc_info:
            parse_urls()
        assert exc_info.value.code == EXIT_USAGE

    def test_verbose_count_without_quiet_allowed(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "-vvv"],
        )
        _, args = parse_urls()
        assert args.verbose == 3
        assert args.quiet is False


class TestScanGlobalFlags:
    def test_counts_single(self):
        flags = _scan_global_flags(["-v"])
        assert flags.verbosity == 1
        assert flags.argv == []

    def test_counts_double(self):
        flags = _scan_global_flags(["-vv"])
        assert flags.verbosity == 2

    def test_counts_triple(self):
        flags = _scan_global_flags(["-vvv"])
        assert flags.verbosity == 3

    def test_counts_verbose_long(self):
        flags = _scan_global_flags(["--verbose"])
        assert flags.verbosity == 1

    def test_counts_accumulate(self):
        flags = _scan_global_flags(["-v", "--verbose", "-v"])
        assert flags.verbosity == 3

    def test_ignores_unrelated_options(self):
        flags = _scan_global_flags(["--convoy", "-output", "url"])
        assert flags.verbosity == 0
        assert flags.argv == ["--convoy", "-output", "url"]

    def test_verbosity_token_exact_only(self):
        assert _is_verbosity_token("-vv") is True
        assert _is_verbosity_token("--verbose") is True
        assert _is_verbosity_token("--convoy") is False
        assert _is_verbosity_token("-o") is False
        assert _is_verbosity_token("url") is False

    def test_color_and_value_stripped(self):
        flags = _scan_global_flags(["--color", "always", "update"])
        assert flags.color_mode == "always"
        assert flags.argv == ["update"]

    def test_color_equals_form(self):
        flags = _scan_global_flags(["--color=never", "url"])
        assert flags.color_mode == "never"
        assert flags.argv == ["url"]

    def test_no_color_overrides_color(self):
        flags = _scan_global_flags(["--no-color", "--color", "always", "update"])
        assert flags.color_mode == "never"
        assert flags.argv == ["update"]

    def test_invalid_color_survives_for_validation(self):
        flags = _scan_global_flags(["--color=neon"])
        assert flags.color_mode == "neon"

    def test_debug_file_and_config_stripped(self):
        flags = _scan_global_flags(
            ["--debug-file", "dbg.log", "--config", "custom.toml", "update"]
        )
        assert flags.debug_file == "dbg.log"
        assert flags.config_path == "custom.toml"
        assert flags.argv == ["update"]

    def test_config_equals_form(self):
        flags = _scan_global_flags(["--config=custom.toml", "cache", "status"])
        assert flags.config_path == "custom.toml"
        assert flags.argv == ["cache", "status"]

    def test_json_detected_and_kept_for_subcommand(self):
        flags = _scan_global_flags(["library", "--json"])
        assert flags.json is True
        assert flags.argv == ["library", "--json"]

    def test_quiet_kept_for_update(self):
        flags = _scan_global_flags(["update", "-q"])
        assert flags.argv == ["update", "-q"]

    def test_quiet_stripped_for_other_subcommands(self):
        flags = _scan_global_flags(["library", "-q"])
        assert flags.argv == ["library"]

    def test_no_config_flag(self):
        flags = _scan_global_flags(["--no-config", "update"])
        assert flags.no_config is True
        assert flags.argv == ["update"]


class TestProcessUrl:
    pytestmark = pytest.mark.asyncio
    """Test process_url with mocked scraper/downloader."""

    class MockScraper:
        async def scrape(self, _url, _client):
            return PostMetadata(
                series_title="Test Series",
                chapter_title="Chapter 1",
                images=[
                    ImageItem(url="http://x.com/1", page_number=1, filename="page1.jpg"),
                    ImageItem(url="http://x.com/2", page_number=2, filename="page2.jpg"),
                ],
                total_pages=2,
            )

    async def _test_process_url(self, monkeypatch, url, **kwargs):
        """Run process_url with test defaults."""
        _patch_chapter_scraper(
            monkeypatch,
            {"e-hentai.org": self.MockScraper(), "pawchive.pw": self.MockScraper()},
        )

        with tempfile.TemporaryDirectory() as td:
            await process_url(
                url=url,
                output_dir=Path(td),
                concurrency=5,
                force=False,
                **kwargs,
            )

    async def test_unsupported_url(self):
        from comic_dl.config import set_runtime_download

        set_runtime_download(generic=False)
        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://example.com/bad",
                output_dir=Path(td),
                concurrency=5,

                force=False,
            )
            assert status == "failed"

    async def test_gedecomix_series_url_routes_to_series(self, monkeypatch, capsys):
        calls = []

        async def fake_process_series(**kwargs):
            calls.append(kwargs["url"])
            return True

        monkeypatch.setattr("comic_dl.cli._process_series", fake_process_series)

        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://gedecomix.com/porncomic/hell-village/",
                output_dir=Path(td),
                concurrency=1,
                force=False,
                quiet=True,
            )
        assert status == "downloaded"
        assert calls == ["https://gedecomix.com/porncomic/hell-village"]
        assert "Unsupported URL" not in capsys.readouterr().out

    async def test_asurascans_series_url_routes_to_series(self, monkeypatch, capsys):
        calls = []

        async def fake_process_series(**kwargs):
            calls.append(kwargs["url"])
            return True

        monkeypatch.setattr("comic_dl.cli._process_series", fake_process_series)

        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://asurascans.com/comics/murim-psychopath-00dcbf97/",
                output_dir=Path(td),
                concurrency=1,
                force=False,
                quiet=True,
            )
        assert status == "downloaded"
        assert calls == ["https://asurascans.com/comics/murim-psychopath-00dcbf97"]
        assert "Unsupported URL" not in capsys.readouterr().out

    async def test_gedecomix_chapter_url_still_routes_to_chapter(self, monkeypatch):
        class MockScraper:
            async def scrape(self, _url, _client):
                return PostMetadata(
                    series_title="Test Series",
                    chapter_title="Chapter 1",
                    images=[
                        ImageItem(url="http://x.com/1", page_number=1, filename="page1.jpg"),
                    ],
                    total_pages=1,
                )

        async def mock_download(images, dest_dir, *args, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b'\xff\xd8\xff')
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)
        mock = MockScraper()
        _patch_chapter_scraper(monkeypatch, {"gedecomix.com": mock})

        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://gedecomix.com/porncomic/series/5-ch-05/",
                output_dir=Path(td),
                concurrency=1,

                force=False,
                quiet=True,
            )
            assert status == "downloaded"

    async def test_pawchive_url_routes_correctly(self, monkeypatch):
        mock = self.MockScraper()
        _patch_chapter_scraper(monkeypatch, {"pawchive.pw": mock})

        async def mock_download(images, dest_dir, *args, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)

        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://pawchive.pw/patreon/user/1/post/2/",
                output_dir=Path(td),
                concurrency=1,

                force=False,
                quiet=True,
            )
            assert status == "downloaded"

    async def test_ehentai_url_routes_correctly(self, monkeypatch):
        mock = self.MockScraper()
        _patch_chapter_scraper(monkeypatch, {"e-hentai.org": mock})

        async def mock_download(images, dest_dir, *args, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)

        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://e-hentai.org/g/1/abc/",
                output_dir=Path(td),
                concurrency=1,

                force=False,
                quiet=True,
            )
            assert status == "downloaded"

    async def test_page_count_warning(self, capsys, monkeypatch):

        class MismatchScraper:
            async def scrape(self, _url, _client):
                return PostMetadata(
                    series_title="Test Series",
                    chapter_title="Chapter 1",
                    images=[
                        ImageItem(url="http://x.com/1", page_number=1, filename="page1.jpg"),
                    ],
                    total_pages=5,
                )

        mock = MismatchScraper()
        _patch_chapter_scraper(monkeypatch, {"e-hentai.org": mock})

        with tempfile.TemporaryDirectory() as td:
            await process_url(
                url="https://e-hentai.org/g/1/abc/",
                output_dir=Path(td),
                concurrency=1,

                force=False,
                quiet=False,
            )
            captured = capsys.readouterr()
            assert "Expected 5 page(s), extracted 1" in captured.err

    async def test_max_total_size_exceeded(self, monkeypatch):
        mock = self.MockScraper()
        _patch_chapter_scraper(monkeypatch, {"e-hentai.org": mock})

        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://e-hentai.org/g/1/abc/",
                output_dir=Path(td),
                concurrency=1,

                force=False,
                max_image_size=100 * 1024 * 1024,
                max_total_size=1,
            )
            assert status == "failed"

    async def test_max_size_uses_site_estimate_not_worst_case(self, monkeypatch):
        """A gallery with a small site estimate passes --max-size even when
        pages*max-image-size would blow the cap."""

        class SmallEstimateScraper:
            async def scrape(self, _url, _client):
                return PostMetadata(
                    series_title="Test Series",
                    chapter_title="Chapter 1",
                    images=[
                        ImageItem(url=f"http://x.com/{i}", page_number=i, filename=f"p{i}.jpg")
                        for i in range(1000)
                    ],
                    total_pages=1000,
                    estimated_size=5 * 1024 * 1024,
                )

        _patch_chapter_scraper(monkeypatch, {"e-hentai.org": SmallEstimateScraper()})

        async def mock_download(images, dest_dir, *args, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        async def no_probe(_images, _referer_url, _quiet, *, known_size=0):
            return None

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)
        monkeypatch.setattr("comic_dl.cli._probe_estimate_display", no_probe)
        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://e-hentai.org/g/1/abc/",
                output_dir=Path(td),
                concurrency=1,
                force=False,
                max_image_size=100 * 1024 * 1024,
                max_total_size=50 * 1024 * 1024,
            )
            assert status == "downloaded"

    async def test_max_size_unknown_estimate_uses_realistic_guess(self, monkeypatch):
        """Without a site estimate, the gate guesses from a realistic per-page
        size — not pages*max-image-size, which rejected modest runs (F6)."""

        class NoEstimateScraper:
            async def scrape(self, _url, _client):
                return PostMetadata(
                    series_title="Test Series",
                    chapter_title="Chapter 1",
                    images=[
                        ImageItem(
                            url=f"http://x.com/{i}",
                            page_number=i,
                            filename=f"p{i}.jpg",
                        )
                        for i in range(200)
                    ],
                    total_pages=200,
                    estimated_size=0,
                )

        _patch_chapter_scraper(monkeypatch, {"e-hentai.org": NoEstimateScraper()})

        async def mock_download(images, dest_dir, *args, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        async def no_probe(_images, _referer_url, _quiet, *, known_size=0):
            return None

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)
        monkeypatch.setattr("comic_dl.cli._probe_estimate_display", no_probe)
        with tempfile.TemporaryDirectory() as td:
            # Old fallback: 200p x 100MB = 20GB -> rejected. Realistic guess
            # (5 MB/page => 1 GB) passes a 2 GB cap.
            status, _label = await process_url(
                url="https://e-hentai.org/g/1/abc/",
                output_dir=Path(td),
                concurrency=1,
                force=False,
                max_image_size=100 * 1024 * 1024,
                max_total_size=2 * 1024 * 1024 * 1024,
            )
            assert status == "downloaded"

    async def test_max_size_unknown_estimate_still_enforced(self, monkeypatch):
        """The gate still rejects when even the realistic guess blows the cap."""

        class NoEstimateScraper:
            async def scrape(self, _url, _client):
                return PostMetadata(
                    series_title="Test Series",
                    chapter_title="Chapter 1",
                    images=[
                        ImageItem(
                            url=f"http://x.com/{i}",
                            page_number=i,
                            filename=f"p{i}.jpg",
                        )
                        for i in range(100)
                    ],
                    total_pages=100,
                    estimated_size=0,
                )

        _patch_chapter_scraper(monkeypatch, {"e-hentai.org": NoEstimateScraper()})

        async def no_download(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("download must not start past --max-size gate")

        async def no_probe(_images, _referer_url, _quiet, *, known_size=0):
            return None

        monkeypatch.setattr("comic_dl.downloader.download_httpx", no_download)
        monkeypatch.setattr("comic_dl.cli._probe_estimate_display", no_probe)
        with tempfile.TemporaryDirectory() as td:
            # Guess: 100p x 5MB = 500MB > 100MB cap -> rejected pre-download.
            status, _label = await process_url(
                url="https://e-hentai.org/g/1/abc/",
                output_dir=Path(td),
                concurrency=1,
                force=False,
                max_image_size=100 * 1024 * 1024,
                max_total_size=100 * 1024 * 1024,
            )
            assert status == "failed"

    async def test_existing_cbz_skips(self, monkeypatch):
        mock = self.MockScraper()
        _patch_chapter_scraper(monkeypatch, {"pawchive.pw": mock})

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            sdir = out / "Test Series"
            sdir.mkdir(parents=True)
            cbz = sdir / "Chapter 1.cbz"
            cbz.write_bytes(b"fake cbz")

            await process_url(
                url="https://pawchive.pw/patreon/user/1/post/2/",
                output_dir=out,
                concurrency=1,

                force=False,
                quiet=True,
            )
            assert cbz.read_bytes() == b"fake cbz"

            status, _label = await process_url(
                url="https://pawchive.pw/patreon/user/1/post/2/",
                output_dir=out,
                concurrency=1,

                force=False,
                quiet=True,
            )
            assert status == "skipped"

    async def test_force_overwrites(self, monkeypatch):

        async def mock_download(images, dest_dir, *args, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b'\xff\xd8\xff')
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)

        mock = self.MockScraper()
        _patch_chapter_scraper(monkeypatch, {"pawchive.pw": mock})

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            sdir = out / "Test Series"
            sdir.mkdir(parents=True)
            cbz = sdir / "Chapter 1.cbz"
            cbz.write_bytes(b"old")

            await process_url(
                url="https://pawchive.pw/patreon/user/1/post/2/",
                output_dir=out,
                concurrency=1,

                force=True,
                quiet=True,
            )
            assert cbz.read_bytes() != b"old"

            status, label = await process_url(
                url="https://pawchive.pw/patreon/user/1/post/2/",
                output_dir=out,
                concurrency=1,

                force=True,
                quiet=True,
            )
            assert status == "downloaded"
            assert label == "Chapter 1.cbz"

    async def test_scrape_timeout_retries_then_succeeds(self, monkeypatch):

        _real_sleep = asyncio.sleep

        async def no_sleep(duration):
            await _real_sleep(min(duration, 0.01))

        async def no_probe(_images, _referer, _quiet, *, known_size=0):
            return None

        async def mock_download(images, dest_dir, *args, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        calls = {"n": 0}

        class FlakyThenOk:
            async def scrape(self, _url, _client):
                calls["n"] += 1
                if calls["n"] <= 2:
                    raise ScrapeTimeout(_url, 30.0)
                return PostMetadata(
                    series_title="Test Series",
                    chapter_title="Chapter 1",
                    images=[
                        ImageItem(url="http://x.com/1", page_number=1, filename="page1.jpg"),
                    ],
                    total_pages=1,
                )

        monkeypatch.setattr("comic_dl.cli.asyncio.sleep", no_sleep)
        monkeypatch.setattr("comic_dl.cli._probe_estimate_display", no_probe)
        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)
        _patch_chapter_scraper(monkeypatch, {"e-hentai.org": FlakyThenOk()})

        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://e-hentai.org/g/1/abc/",
                output_dir=Path(td),
                concurrency=1,
                force=False,
                quiet=False,
            )
        assert status == "downloaded"
        assert calls["n"] == 3

    async def test_scrape_timeout_fails_after_retries(self, monkeypatch, capsys):

        _real_sleep = asyncio.sleep

        async def no_sleep(duration):
            await _real_sleep(min(duration, 0.01))

        async def no_probe(_images, _referer, _quiet, *, known_size=0):
            return None

        class AlwaysTimeout:
            async def scrape(self, _url, _client):
                raise ScrapeTimeout(_url, 30.0)

        monkeypatch.setattr("comic_dl.cli.asyncio.sleep", no_sleep)
        monkeypatch.setattr("comic_dl.cli._probe_estimate_display", no_probe)
        _patch_chapter_scraper(monkeypatch, {"e-hentai.org": AlwaysTimeout()})

        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://e-hentai.org/g/1/abc/",
                output_dir=Path(td),
                concurrency=1,
                force=False,
                quiet=False,
            )
        assert status == "failed"
        assert "timed out after 30s" in capsys.readouterr().err

    async def test_scrape_429_honors_retry_after(self, monkeypatch):
        from types import SimpleNamespace

        from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

        sleeps: list[float] = []
        _real_sleep = asyncio.sleep

        async def capture_sleep(duration):
            sleeps.append(float(duration))
            await _real_sleep(min(duration, 0.01))

        async def no_probe(_images, _referer, _quiet, *, known_size=0):
            return None

        async def mock_download(images, dest_dir, *args, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        calls = {"n": 0}

        class FlakyThenOk:
            async def scrape(self, _url, _client):
                calls["n"] += 1
                if calls["n"] <= 2:
                    raise CurlHTTPError(
                        "rate limited",
                        response=SimpleNamespace(
                            status_code=429, headers={"retry-after": "5"}
                        ),
                    )
                return PostMetadata(
                    series_title="Test Series",
                    chapter_title="Chapter 1",
                    images=[
                        ImageItem(url="http://x.com/1", page_number=1, filename="page1.jpg"),
                    ],
                    total_pages=1,
                )

        monkeypatch.setattr("comic_dl.cli.asyncio.sleep", capture_sleep)
        monkeypatch.setattr("comic_dl.cli._probe_estimate_display", no_probe)
        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)
        _patch_chapter_scraper(monkeypatch, {"e-hentai.org": FlakyThenOk()})

        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://e-hentai.org/g/1/abc/",
                output_dir=Path(td),
                concurrency=1,
                force=False,
                quiet=False,
            )
        assert status == "downloaded"
        assert calls["n"] == 3
        # Both retries waited the server-named Retry-After (5s), not the fixed
        # (1 + attempt) * 0.5 ramp. Other sleeps are the engine's own pacing.
        assert sleeps.count(5.0) == 2

    async def test_probe_skipped_when_site_reports_size(self, monkeypatch, capsys):
        from comic_dl import cli as cli_mod

        async def should_not_run(*args, **kwargs):
            raise AssertionError("probe must not run when the site reports a size")

        monkeypatch.setattr("comic_dl.cli.probe_download_size", should_not_run)
        cli_mod.set_verbosity(cli_mod.VERBOSE)
        try:
            await cli_mod._probe_estimate_display(
                [object()], "http://x/", quiet=False, known_size=5 * 1024 * 1024
            )
        finally:
            cli_mod.set_verbosity(cli_mod.NORMAL)
        assert "Estimated download size: ~5 MB" in capsys.readouterr().err


class TestChapterSelection:
    def test_empty_is_all(self):
        assert parse_chapter_selection("", 5) == ChapterSelection(kind="all")

    def test_a_is_all(self):
        assert parse_chapter_selection("a", 5) == ChapterSelection(kind="all")
        assert parse_chapter_selection("A", 5) == ChapterSelection(kind="all")

    def test_all_keyword(self):
        assert parse_chapter_selection("all", 5) == ChapterSelection(kind="all")

    def test_q_is_quit(self):
        assert parse_chapter_selection("q", 5) == ChapterSelection(kind="quit")

    def test_quit_keyword(self):
        assert parse_chapter_selection("quit", 5) == ChapterSelection(kind="quit")

    def test_single_index(self):
        sel = parse_chapter_selection("2", 5)
        assert sel.kind == "indices"
        assert sel.indices == frozenset({2})

    def test_range(self):
        sel = parse_chapter_selection("1-3", 5)
        assert sel.indices == frozenset({1, 2, 3})

    def test_mixed_list(self):
        sel = parse_chapter_selection("10-12,1", 12)
        assert sel.indices == frozenset({1, 10, 11, 12})

    def test_whitespace_tolerant(self):
        sel = parse_chapter_selection(" 2 , 4 ", 5)
        assert sel.indices == frozenset({2, 4})

    def test_out_of_bounds_raises(self):
        with pytest.raises(ValueError, match="out of range"):
            parse_chapter_selection("6", 5)

    def test_zero_raises(self):
        with pytest.raises(ValueError, match="1-based"):
            parse_chapter_selection("0", 5)

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="not a number or range"):
            parse_chapter_selection("-1", 5)

    def test_reversed_range_raises(self):
        with pytest.raises(ValueError, match="reversed"):
            parse_chapter_selection("5-2", 5)

    def test_mixing_keyword_raises(self):
        with pytest.raises(ValueError, match="cannot mix"):
            parse_chapter_selection("a,2", 5)

    def test_bad_token_raises(self):
        with pytest.raises(ValueError, match="not a number or range"):
            parse_chapter_selection("abc", 5)

    def test_boundary_in_bounds_ok(self):
        assert parse_chapter_selection("4", 4).indices == frozenset({4})

    def test_boundary_out_of_range(self):
        with pytest.raises(ValueError):
            parse_chapter_selection("5", 4)


class TestValidateChapterFlag:
    def test_valid_specs(self):
        for spec in ("", "a", "all", "1", "1-3", "1-3,5", "2,4"):
            validate_chapter_flag(spec)  # must not raise

    def test_whitespace_ok(self):
        validate_chapter_flag(" 2 , 4 ")

    def test_q_raises(self):
        with pytest.raises(ValueError, match="interactive chapter selector"):
            validate_chapter_flag("q")

    def test_quit_raises(self):
        with pytest.raises(ValueError, match="interactive chapter selector"):
            validate_chapter_flag("quit")

    def test_q_mixed_with_list_raises(self):
        with pytest.raises(ValueError, match="cannot mix"):
            validate_chapter_flag("1,q")

    def test_reversed_range_raises(self):
        with pytest.raises(ValueError, match="reversed"):
            validate_chapter_flag("5-2")

    def test_zero_raises(self):
        with pytest.raises(ValueError):
            validate_chapter_flag("0")

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            validate_chapter_flag("-1")

    def test_bad_token_raises(self):
        with pytest.raises(ValueError):
            validate_chapter_flag("abc")

    def test_mixing_keyword_raises(self):
        with pytest.raises(ValueError):
            validate_chapter_flag("a,2")

    def test_out_of_bounds_not_checked(self):
        # Bounds are validated per-series after scraping, not at flag parse.
        validate_chapter_flag("99")


class TestParseUrlsChaptersFlag:
    def test_flag_parsed(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/",
                         "--chapters", "1-3,7"],
        )
        _, args = parse_urls()
        assert args.chapters == "1-3,7"

    def test_default_none(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/"])
        _, args = parse_urls()
        assert args.chapters is None

    def test_invalid_exits_usage(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/",
                         "--chapters", "abc"],
        )
        with pytest.raises(SystemExit) as exc_info:
            parse_urls()
        assert exc_info.value.code == 2

    def test_reversed_range_exits_usage(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/",
                         "--chapters", "5-2"],
        )
        with pytest.raises(SystemExit) as exc_info:
            parse_urls()
        assert exc_info.value.code == 2


class TestParseSize:
    def test_plain_int(self):
        assert _parse_size("1048576") == 1048576

    def test_mb_suffix(self):
        assert _parse_size("100MB") == 100 * 1024 * 1024

    def test_gb_suffix(self):
        assert _parse_size("2GB") == 2 * 1024 ** 3

    def test_kb_suffix(self):
        assert _parse_size("512KB") == 512 * 1024

    def test_case_insensitive(self):
        assert _parse_size("50mb") == 50 * 1024 * 1024
        assert _parse_size("10Mb") == 10 * 1024 * 1024

    def test_float_value(self):
        assert _parse_size("1.5MB") == int(1.5 * 1024 * 1024)

    def test_negative_int_rejected(self):
        import argparse
        with pytest.raises(argparse.ArgumentTypeError):
            _parse_size("-5")

    def test_negative_suffixed_rejected(self):
        import argparse
        with pytest.raises(argparse.ArgumentTypeError):
            _parse_size("-5GB")

    def test_zero_allowed(self):
        assert _parse_size("0") == 0


class TestOpenLibrary:
    def test_creates_output_dir(self, tmp_path):
        target = tmp_path / "new" / "nested"
        lib = _open_library(target)
        assert lib is not None
        assert target.is_dir()

    def test_unwritable_output_returns_none_without_traceback(self, tmp_path, capsys):
        file = tmp_path / "blocked"
        file.write_text("x")
        lib = _open_library(file)
        assert lib is None
        captured = capsys.readouterr()
        assert "not writable" in captured.err
        assert "Traceback" not in captured.err


class TestExtractDomain:
    def test_webtoons_com(self):
        assert _extract_domain("https://www.webtoons.com/en/romance/series/episode") == "webtoons.com"

    def test_ehentai(self):
        assert _extract_domain("https://e-hentai.org/g/123/abc/") == "e-hentai.org"

    def test_pawchive(self):
        assert _extract_domain("https://pawchive.pw/patreon/user/1/post/2/") == "pawchive.pw"

    def test_bare_domain(self):
        assert _extract_domain("http://example.com") == "example.com"

    def test_empty_url(self):
        assert _extract_domain("") == ""


class TestRunWithNetworkRetry:
    pytestmark = pytest.mark.asyncio

    async def test_retries_transient_then_succeeds(self):
        """A flaky connection mid-stream must not fail the chapter."""
        attempts = [0]

        async def run_once():
            attempts[0] += 1
            if attempts[0] == 1:
                raise ConnectionError("flaky")
            return "ok"

        result = await _run_with_network_retry(run_once, quiet=True)
        assert result == "ok"
        assert attempts[0] == 2

    async def test_gives_up_after_budget(self):
        """A persistently failing stream raises after the retry budget."""
        attempts = [0]

        async def run_once():
            attempts[0] += 1
            raise ScrapeTimeout("https://e-hentai.org/g/1/ab/", 30)

        with pytest.raises(ScrapeTimeout):
            await _run_with_network_retry(run_once, quiet=True)
        assert attempts[0] == 3

    async def test_success_first_attempt(self):
        attempts = [0]

        async def run_once():
            attempts[0] += 1
            return "done"

        assert await _run_with_network_retry(run_once, quiet=True) == "done"
        assert attempts[0] == 1


class TestBatchErrorContinuation:
    pytestmark = pytest.mark.asyncio

    async def test_error_on_one_does_not_block_others(self, monkeypatch):
        call_count = [0]

        async def mock_process(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("first fails")
            return "downloaded", "ok.cbz"

        monkeypatch.setattr("comic_dl.cli.process_url", mock_process)
        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: {})
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent"])
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            ["https://a.com/", "https://b.com/"],
            argparse.Namespace(quiet=True, output=Path("/tmp"),
                               concurrency=5, parallel=5, force=False,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=False, url=None, dry_run=False),
        ))

        from comic_dl.cli import main
        assert await main() == 1
        assert call_count[0] == 2


class TestBatchVerdicts:
    pytestmark = pytest.mark.asyncio

    def _patch_run(self, monkeypatch, results):
        calls = []

        async def mock_process(url, **kwargs):
            calls.append(url)
            return results.pop(0)

        monkeypatch.setattr("comic_dl.cli.process_url", mock_process)
        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: {})
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent"])
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            ["https://a.com/", "https://b.com/"],
            argparse.Namespace(quiet=True, output=Path("/tmp"),
                               concurrency=5, parallel=5, force=False,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=False, url=None, dry_run=False),
        ))
        return calls

    async def test_all_skipped_exits_zero(self, monkeypatch, capsys):
        self._patch_run(monkeypatch, [("skipped", ""), ("skipped", "")])

        from comic_dl.cli import main
        assert await main() == 0
        captured = capsys.readouterr()
        assert "Skipped: https://a.com/" in captured.err
        assert "Skipped: https://b.com/" in captured.err
        assert "All 2 URLs completed successfully (2 skipped)." in captured.out

    async def test_skipped_counts_as_success_not_failure(self, monkeypatch, capsys):
        self._patch_run(monkeypatch, [("downloaded", "a.cbz"), ("skipped", "")])

        from comic_dl.cli import main
        assert await main() == 0
        captured = capsys.readouterr()
        # The pipeline itself prints the durable "Saved" line; _run_urls must
        # not add a redundant "Downloaded" line for the same artifact.
        assert "Downloaded: a.cbz" not in captured.out
        assert "Skipped: https://b.com/" in captured.err
        assert "All 2 URLs completed successfully (1 skipped)." in captured.out

    async def test_failure_still_exits_one(self, monkeypatch, capsys):
        self._patch_run(monkeypatch, [("downloaded", "a.cbz"), ("failed", "")])

        from comic_dl.cli import main
        assert await main() == 1
        captured = capsys.readouterr()
        assert "Failed: https://b.com/" in captured.err
        assert "Processed 2 URLs: 1 downloaded, 0 skipped, 1 failed" in captured.err

    async def test_exception_prints_failed_verdict(self, monkeypatch, capsys):
        async def mock_process(url, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr("comic_dl.cli.process_url", mock_process)
        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: {})
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent"])
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            ["https://a.com/"],
            argparse.Namespace(quiet=True, output=Path("/tmp"),
                               concurrency=5, parallel=5, force=False,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=False, url=None, dry_run=False),
        ))

        from comic_dl.cli import main
        assert await main() == 1
        captured = capsys.readouterr()
        assert "Failed: https://a.com/" in captured.err

    async def test_identical_failures_grouped_in_summary(self, monkeypatch, capsys):
        self._patch_run(monkeypatch, [("failed", ""), ("failed", "")])

        from comic_dl.cli import main
        assert await main() == 1
        captured = capsys.readouterr()
        # Both URLs share the fallback reason, so the recap groups them into
        # one reason line with a count instead of two repeated error lines.
        assert captured.err.count("Failed: https://a.com/") == 1
        assert captured.err.count("Failed: https://b.com/") == 1
        assert "x2" in captured.err
        assert "Download failed." in captured.err


class TestFirstRunHint:
    def _run_hint(self, args, monkeypatch, config_path_value):
        import comic_dl.cli as cli_mod
        from comic_dl.cli import _maybe_first_run_hint

        monkeypatch.setattr(cli_mod, "console", argparse.Namespace(is_terminal=True))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr(cli_mod, "config_path", lambda: config_path_value)
        _maybe_first_run_hint(args, 2)

    def test_hidden_when_quiet_or_json(self, monkeypatch, tmp_path, capsys):
        missing = tmp_path / "no-config.toml"
        self._run_hint(argparse.Namespace(quiet=True, json=False), monkeypatch, missing)
        self._run_hint(argparse.Namespace(quiet=False, json=True), monkeypatch, missing)
        assert capsys.readouterr().out == ""

    def test_hidden_when_config_exists(self, monkeypatch, tmp_path, capsys):
        cfg = tmp_path / "config.toml"
        cfg.write_text("[http]\n")
        self._run_hint(argparse.Namespace(quiet=False, json=False), monkeypatch, cfg)
        assert capsys.readouterr().out == ""

    def test_hidden_when_nothing_completed(self, monkeypatch, tmp_path, capsys):
        import comic_dl.cli as cli_mod
        from comic_dl.cli import _maybe_first_run_hint

        monkeypatch.setattr(cli_mod, "console", argparse.Namespace(is_terminal=True))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr(cli_mod, "config_path", lambda: tmp_path / "missing.toml")
        _maybe_first_run_hint(argparse.Namespace(quiet=False, json=False), 0)
        assert capsys.readouterr().out == ""

    def test_shown_without_config(self, monkeypatch, tmp_path, capsys):
        missing = tmp_path / "no-config.toml"
        self._run_hint(argparse.Namespace(quiet=False, json=False), monkeypatch, missing)
        assert "Tip:" in capsys.readouterr().err


class TestDownloadJson:
    pytestmark = pytest.mark.asyncio

    def _patch(self, monkeypatch, results, url="https://a.com/", urls=None):
        if urls is None:
            urls = ["https://a.com/"]

        async def mock_process(url, **kwargs):
            status, name = results.pop(0)
            stats = kwargs.get("stats")
            if stats is not None and status == "downloaded":
                stats.output_path = f"/out/{name}"
                stats.chapters_downloaded = 1
                stats.bytes = 7
            return status, name

        monkeypatch.setattr("comic_dl.cli.process_url", mock_process)
        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: {})
        argv = (
            ["prog", "-u", url, "--json"]
            if url is not None
            else ["prog", "--file", "/nonexistent", "--json"]
        )
        monkeypatch.setattr("sys.argv", argv)
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            urls,
            argparse.Namespace(quiet=True, output=Path("/tmp"),
                               concurrency=5, parallel=5, force=False,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=True, url=url, dry_run=False),
        ))

    async def test_single_url_flat_payload(self, monkeypatch, capsys):
        import json

        self._patch(monkeypatch, [("downloaded", "a.cbz")])

        from comic_dl.cli import main
        assert await main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 1
        assert payload["status"] == "success"
        assert payload["url"] == "https://a.com/"
        assert payload["output_path"] == "/out/a.cbz"
        assert payload["chapters_downloaded"] == 1
        assert payload["bytes"] == 7
        assert "duration_s" in payload
        assert "error" not in payload

    async def test_single_url_failure_exits_one(self, monkeypatch, capsys):
        import json

        async def mock_process(url, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr("comic_dl.cli.process_url", mock_process)
        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: {})
        monkeypatch.setattr(
            "sys.argv", ["prog", "-u", "https://a.com/", "--json"]
        )
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            ["https://a.com/"],
            argparse.Namespace(quiet=True, output=Path("/tmp"),
                               concurrency=5, parallel=5, force=False,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=True, url="https://a.com/", dry_run=False),
        ))

        from comic_dl.cli import main
        assert await main() == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 1
        assert payload["status"] == "failed"
        assert payload["error"] == 1
        assert payload["message"] == "Unexpected internal error."

    async def test_file_multiple_urls_nested_payload(self, monkeypatch, capsys):
        import json

        self._patch(
            monkeypatch,
            [("downloaded", "a.cbz"), ("failed", "")],
            url=None,
            urls=["https://a.com/", "https://b.com/"],
        )

        from comic_dl.cli import main
        assert await main() == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 1
        assert len(payload["urls"]) == 2
        assert payload["succeeded"] == 1
        assert payload["skipped"] == 0
        assert payload["failed"] == 1
        assert payload["urls"][0]["status"] == "success"
        assert payload["urls"][1]["status"] == "failed"
        assert payload["urls"][1]["output_path"] is None


class _FakeAsyncSession:
    def __init__(self, scrape_fn=None, **kwargs):
        self._scrape_fn = scrape_fn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestDryRun:
    pytestmark = pytest.mark.asyncio

    def _patch(self, monkeypatch, index, force=False, json=False):
        calls = []

        async def mock_process(url, **kwargs):
            calls.append(url)
            return ("downloaded", "ok.cbz")

        async def mock_preview(url, index, force):
            existing = index.get(normalize_url(url))
            if existing is not None and not force:
                return {
                    "url": url, "domain": "a.com", "kind": "chapter",
                    "title": "Chapter 1", "detail": "20 pages",
                    "action": "skip", "existing": existing.name,
                }
            if existing is not None:
                return {
                    "url": url, "domain": "a.com", "kind": "series",
                    "title": "Series A", "detail": "12 chapters",
                    "action": "redownload",
                }
            return {
                "url": url, "domain": "a.com", "kind": "chapter",
                "title": "Chapter 1", "detail": "20 pages",
                "action": "download",
            }

        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: index)
        monkeypatch.setattr("comic_dl.cli._preview_url", mock_preview)
        monkeypatch.setattr("comic_dl.cli.process_url", mock_process)
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent"])
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            ["https://a.com/", "https://b.com/"],
            argparse.Namespace(quiet=True, output=Path("/tmp"),
                               concurrency=5, parallel=5, force=force,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=json, url=None,
                               dry_run=True),
        ))
        return calls

    async def test_previews_skip_and_download(self, monkeypatch, capsys):
        index = {normalize_url("https://a.com/"): Path("a.cbz")}
        calls = self._patch(monkeypatch, index)

        from comic_dl.cli import main
        assert await main() == 0
        assert calls == []
        err = capsys.readouterr().err
        assert "would skip" in err and "https://a.com/" in err
        assert "already downloaded as a.cbz" in err
        assert "would download" in err and "https://b.com/" in err
        assert "1 already downloaded" in err
        assert "Nothing was written." in err

    async def test_previews_show_resolved_detail(self, monkeypatch, capsys):
        self._patch(monkeypatch, {})

        from comic_dl.cli import main
        assert await main() == 0
        err = capsys.readouterr().err
        assert "chapter 'Chapter 1' · 20 pages" in err

    async def test_force_previews_redownload(self, monkeypatch, capsys):
        index = {normalize_url("https://a.com/"): Path("a.cbz")}
        self._patch(monkeypatch, index, force=True)

        from comic_dl.cli import main
        assert await main() == 0
        err = capsys.readouterr().err
        assert "would redownload" in err and "https://a.com/" in err
        assert "series 'Series A' · 12 chapters" in err
        assert "1 would redownload" in err

    async def test_json_output(self, monkeypatch, capsys):
        import json

        index = {normalize_url("https://a.com/"): Path("a.cbz")}
        self._patch(monkeypatch, index, json=True)

        from comic_dl.cli import main
        assert await main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 1
        assert payload["urls"][0]["url"] == "https://a.com/"
        assert payload["urls"][0]["action"] == "skip"
        assert payload["urls"][0]["existing"] == "a.cbz"
        assert payload["urls"][0]["kind"] == "chapter"
        assert payload["urls"][1] == {
            "url": "https://b.com/",
            "domain": "a.com",
            "kind": "chapter",
            "title": "Chapter 1",
            "detail": "20 pages",
            "action": "download",
        }

    async def test_dry_run_json_on_stdout_human_on_stderr(self, monkeypatch, capsys):
        import json

        self._patch(monkeypatch, {}, json=True)

        from comic_dl.cli import main
        assert await main() == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["urls"]
        # JSON goes to stdout; the human preview is not printed at all here.
        assert "would download" not in captured.out
        assert "would download" not in captured.err

    async def test_dry_run_shows_destination_and_format(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import _report_dry_run

        out_dir = tmp_path / "out"
        args = argparse.Namespace(
            output=out_dir, format="zip", compress="deflate", force=False,
            parallel=5, concurrency=3, max_image_size=100 * 1024 * 1024,
            max_size=0, chapter_parallel=1, chapters=None, json=False,
            url=None, dry_run=True,
        )
        entries = [
            {
                "url": "https://a.com/1/", "domain": "a.com", "kind": "chapter",
                "title": "Chapter 1", "detail": "20 pages", "action": "download",
                "pages": 20, "series": "Series A", "post_id": "", "size": 10485760,
                "estimated_size": 0,
            },
            {
                "url": "https://a.com/2/", "domain": "a.com", "kind": "chapter",
                "title": "Chapter 2", "detail": "5 pages", "action": "download",
                "pages": 5, "series": "Series A", "post_id": "", "size": 0,
                "estimated_size": 0,
            },
        ]
        _report_dry_run(entries, [e["url"] for e in entries], args, {})
        # Rich wraps long lines at the console width; strip newlines so the
        # assertions are not sensitive to where a wrap boundary lands.
        err = capsys.readouterr().err.replace("\n", "")
        assert "-> Series A/Chapter 1.zip [deflate]" in err
        assert "[01/2]" in err and "[02/2]" in err
        assert "Concurrency: 5 URLs in parallel" in err
        assert "20 pages" in err and "~10 MB" in err
        assert "Estimated total: ~10 MB" in err
        assert "Nothing was written." in err

    async def test_dry_run_format_changes_extension(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import _report_dry_run

        base_args = dict(
            output=tmp_path / "out", compress="stored", force=False,
            parallel=5, concurrency=3, max_image_size=100 * 1024 * 1024,
            max_size=0, chapter_parallel=1, chapters=None, json=False,
            url=None, dry_run=True,
        )
        entry = {
            "url": "https://a.com/1/", "domain": "a.com", "kind": "chapter",
            "title": "Chapter 1", "detail": "20 pages", "action": "download",
            "pages": 20, "series": "Series A", "post_id": "", "size": 0,
            "estimated_size": 0,
        }
        for fmt, ext in (("cbz", ".cbz"), ("zip", ".zip"), ("cbt", ".cbt")):
            _report_dry_run(
                [entry], [entry["url"]],
                argparse.Namespace(format=fmt, **base_args), {},
            )
            err = capsys.readouterr().err.replace("\n", "")
            assert f"-> Series A/Chapter 1{ext}" in err

    async def test_error_entry_reported_without_crash(self, monkeypatch, capsys):
        async def fail_preview(url, index, force):
            return {
                "url": url, "domain": "x.com", "kind": "",
                "title": "", "detail": "",
                "action": "error", "error": "Unsupported URL.",
            }

        monkeypatch.setattr("comic_dl.cli._preview_url", fail_preview)
        monkeypatch.setattr("comic_dl.cli.process_url", lambda url, **k: None)
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent"])
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            ["https://x.com/"],
            argparse.Namespace(quiet=True, output=Path("/tmp"),
                               concurrency=5, parallel=5, force=False,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=False, url=None,
                               dry_run=True),
        ))

        from comic_dl.cli import main
        assert await main() == 0
        err = capsys.readouterr().err
        assert "would error" in err and "https://x.com/" in err
        assert "Unsupported URL." in err
        assert "1 errors." in err
        assert "Nothing was written." in err

    async def test_dry_run_content_error_shows_message(self, monkeypatch, capsys):
        class _RaisingScraper:
            async def scrape(self, url, client):
                raise ValueError("Gallery has no images")

        monkeypatch.setattr("comic_dl.cli._with_referer", lambda url: {})
        monkeypatch.setattr("comic_dl.cli.get_series_scraper", lambda domain: None)
        monkeypatch.setattr("comic_dl.cli.get_chapter_scraper", lambda domain: _RaisingScraper())
        monkeypatch.setattr(
            "comic_dl.cli.AsyncSession", _FakeAsyncSession
        )
        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: {})
        monkeypatch.setattr("comic_dl.cli.process_url", lambda url, **k: None)
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent"])
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            ["https://a.com/g/1/"],
            argparse.Namespace(quiet=True, output=Path("/tmp"),
                               concurrency=5, parallel=5, force=False,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=False, url=None,
                               dry_run=True),
        ))

        from comic_dl.cli import main
        assert await main() == 0
        err = capsys.readouterr().err
        assert "Gallery has no images" in err
        assert "would error" in err


class TestPreviewClassification:
    def test_series_always_download(self):
        from comic_dl.cli import _classify_preview_entry

        index = {normalize_url("https://a.com/s/"): Path("S.cbz")}
        entry = {
            "kind": "series", "url": "https://a.com/s/", "title": "S",
            "detail": "3 chapters", "action": "",
        }
        classified = _classify_preview_entry(entry, "https://a.com/s/", index, False)
        assert classified["action"] == "download"


class TestForceBatchWarning:
    pytestmark = pytest.mark.asyncio

    def _patch(self, monkeypatch, urls, force=True, quiet=True):
        calls = []

        async def mock_process(url, **kwargs):
            calls.append(url)
            return ("downloaded", "ok.cbz")

        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: {})
        monkeypatch.setattr("comic_dl.cli.process_url", mock_process)
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent"])
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            urls,
            argparse.Namespace(quiet=quiet, output=Path("/tmp"),
                               concurrency=5, parallel=5, force=force,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=False, url=None,
                               dry_run=False),
        ))
        return calls

    async def test_noninteractive_batch_force_fails_loud(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "comic_dl.cli._redownload_estimate", lambda urls, index: (3, 1024)
        )
        calls = self._patch(monkeypatch, ["https://a.com/", "https://b.com/"])

        from comic_dl.cli import main
        from comic_dl.errors import EXIT_INTERRUPTED
        # Aligned with the other unanswerable-confirmation refusals
        # (library remove --json without -y, EOF on Prompt.ask): 130.
        assert await main() == EXIT_INTERRUPTED
        assert calls == []
        captured = capsys.readouterr()
        assert "--force would redownload 3 existing chapters (~1 KB)." in captured.err
        assert "Pass --dry-run to preview this redownload." in captured.err

    async def test_single_url_force_needs_no_warning(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "comic_dl.cli._redownload_estimate", lambda urls, index: (3, 1024)
        )
        calls = self._patch(monkeypatch, ["https://a.com/"])

        from comic_dl.cli import main
        assert await main() == 0
        assert calls == ["https://a.com/"]
        assert "--force would redownload" not in capsys.readouterr().err

    async def test_declined_interactive_prompt_aborts(self, monkeypatch, capsys):
        import types

        monkeypatch.setattr(
            "comic_dl.cli._redownload_estimate", lambda urls, index: (3, 1024)
        )
        monkeypatch.setattr("comic_dl.cli.Prompt.ask", lambda *a, **k: "n")
        fake_stdin = types.SimpleNamespace(isatty=lambda: True)
        monkeypatch.setattr("comic_dl.cli.sys.stdin", fake_stdin)
        import comic_dl.cli as cli

        monkeypatch.setattr(
            type(cli.console), "is_terminal", property(lambda self: True)
        )
        calls = self._patch(monkeypatch, ["https://a.com/", "https://b.com/"], quiet=False)

        from comic_dl.cli import main
        assert await main() == 0
        assert calls == []
        assert "Cancelled." in capsys.readouterr().err


class TestRedownloadEstimate:
    def test_counts_chapters_and_bytes(self, tmp_path):
        from comic_dl.cli import _redownload_estimate

        series = tmp_path / "Series"
        series.mkdir()
        (series / "1.cbz").write_bytes(b"x" * 100)
        (series / "2.cbz").write_bytes(b"x" * 200)
        (series / "notes.md").write_bytes(b"x" * 50)
        index = {
            normalize_url("https://a.com/"): series / "1.cbz",
            normalize_url("https://b.com/"): Path("/nonexistent/x.cbz"),
        }
        chapters, size = _redownload_estimate(
            ["https://a.com/", "https://b.com/"], index
        )
        assert chapters == 3
        assert size == 350


class TestRunUrlsPreSkip:
    pytestmark = pytest.mark.asyncio

    def _patch(self, monkeypatch, index, quiet=True, force=False, urls=None):
        calls = []

        async def mock_process(url, **kwargs):
            calls.append(url)
            return ("downloaded", "ok.cbz")

        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: index)
        monkeypatch.setattr("comic_dl.cli.process_url", mock_process)
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent"])
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            urls if urls is not None else ["https://a.com/", "https://b.com/"],
            argparse.Namespace(quiet=quiet, output=Path("/tmp"),
                               concurrency=5, parallel=5, force=force,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=False, url=None, dry_run=False),
        ))
        return calls

    async def test_match_skips_before_scrape(self, monkeypatch, capsys):
        index = {normalize_url("https://a.com/"): Path("a.cbz")}
        calls = self._patch(monkeypatch, index, quiet=False)

        from comic_dl.cli import main
        assert await main() == 0
        assert calls == ["https://b.com/"]
        captured = capsys.readouterr()
        assert "a.cbz already exists. Skipping." in captured.err
        assert "Skipped: https://a.com/" not in captured.out
        assert "All 2 URLs completed successfully (1 skipped)." in captured.out

    async def test_quiet_suppresses_skip_message(self, monkeypatch, capsys):
        index = {normalize_url("https://a.com/"): Path("a.cbz")}
        calls = self._patch(monkeypatch, index, quiet=True)

        from comic_dl.cli import main
        assert await main() == 0
        assert calls == ["https://b.com/"]
        assert "a.cbz already exists. Skipping." not in capsys.readouterr().out

    async def test_force_bypasses_index(self, monkeypatch):
        index = {normalize_url("https://a.com/"): Path("a.cbz")}
        calls = self._patch(monkeypatch, index, quiet=True, force=True,
                            urls=["https://a.com/"])

        from comic_dl.cli import main
        assert await main() == 0
        assert calls == ["https://a.com/"]

    async def test_mismatch_still_reaches_process_url(self, monkeypatch):
        index = {normalize_url("https://a.com/"): Path("a.cbz")}
        calls = self._patch(monkeypatch, index, quiet=True,
                            urls=["https://c.com/", "https://a.com/"])

        from comic_dl.cli import main
        assert await main() == 0
        assert calls == ["https://c.com/"]

    async def test_series_url_not_in_index_routes_to_process_url(self, monkeypatch):
        index = {normalize_url("https://pawchive.pw/p/user/1/post/2/"): Path("a.cbz")}
        calls = self._patch(monkeypatch, index, quiet=True,
                            urls=["https://webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1"])

        from comic_dl.cli import main
        assert await main() == 0
        assert calls == ["https://webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1"]


class TestRunUrlsParallel:
    """Batch URLs run concurrently up to --parallel, JSON stays in URL order."""

    pytestmark = pytest.mark.asyncio

    def _patch(self, monkeypatch, urls, parallel, json=True):
        calls = []
        active = []
        max_active = [0]

        async def mock_process(url, **kwargs):
            active.append(url)
            max_active[0] = max(max_active[0], len(active))
            calls.append(url)
            await asyncio.sleep(0.05)
            active.remove(url)
            return ("downloaded", "ok.cbz")

        monkeypatch.setattr("comic_dl.cli.process_url", mock_process)
        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: {})
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent"])
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            urls,
            argparse.Namespace(quiet=True, output=Path("/tmp"),
                               concurrency=5, parallel=parallel, force=False,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=json, url=None, dry_run=False),
        ))
        return calls, max_active

    async def test_parallel_3_runs_concurrently(self, monkeypatch, capsys):
        urls = [f"https://s{i}.com/p/{i}" for i in range(6)]
        calls, max_active = self._patch(monkeypatch, urls, parallel=3)
        from comic_dl.cli import main
        assert await main() == 0
        assert sorted(calls) == sorted(urls)
        assert max_active[0] == 3

    async def test_json_output_preserves_input_order(self, monkeypatch, capsys):
        import json

        urls = [f"https://s{i}.com/p/{i}" for i in range(6)]
        self._patch(monkeypatch, urls, parallel=2)
        from comic_dl.cli import main
        assert await main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["succeeded"] == 6
        assert [u["url"] for u in payload["urls"]] == urls

    async def test_parallel_1_is_sequential(self, monkeypatch):
        urls = [f"https://s{i}.com/p/{i}" for i in range(4)]
        calls, max_active = self._patch(monkeypatch, urls, parallel=1)
        from comic_dl.cli import main
        assert await main() == 0
        assert max_active[0] == 1
        assert calls == urls


class TestSharedBatchActivity:
    """A non-quiet batch shares ONE Activity so the UI gate never serializes."""

    pytestmark = pytest.mark.asyncio

    def _patch(self, monkeypatch, urls, parallel):
        calls = []
        active = []
        max_active = [0]
        seen_activities = set()
        seen_row_keys = set()
        batch_activities = []

        async def mock_process(url, **kwargs):
            active.append(url)
            max_active[0] = max(max_active[0], len(active))
            calls.append(url)
            await asyncio.sleep(0.05)
            active.remove(url)
            activity = kwargs.get("activity")
            if activity is not None:
                seen_activities.add(id(activity))
                batch_activities.append(activity)
            seen_row_keys.add(kwargs.get("row_key"))
            return ("downloaded", "ok.cbz")

        monkeypatch.setattr("comic_dl.cli.process_url", mock_process)
        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: {})
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent"])
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            urls,
            argparse.Namespace(quiet=False, output=Path("/tmp"),
                               concurrency=5, parallel=parallel, force=False,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=False, url=None,
                               dry_run=False),
        ))
        return calls, max_active, seen_activities, seen_row_keys, batch_activities

    async def test_quiet_off_batch_uses_one_shared_activity(self, monkeypatch, capsys):
        urls = [f"https://s{i}.com/p/{i}" for i in range(5)]
        _, max_active, seen_activities, seen_row_keys, batch_acts = self._patch(
            monkeypatch, urls, parallel=3
        )
        from comic_dl.cli import main
        assert await main() == 0
        # Exactly one Activity instance serves the whole batch, and every URL
        # borrows a distinct row from it.
        assert len(seen_activities) == 1
        assert len(seen_row_keys) == len(urls)
        # The shared Activity is batch-aware: an Overall denominator was set up
        # before any row ran, and every URL row was retired as done.
        assert batch_acts
        act = batch_acts[0]
        assert act._batch_total == len(urls)
        assert sum(1 for st in act._rows.values() if st.status == "done") == len(urls)
        assert sum(1 for st in act._rows.values() if st.status == "queued") == 0
        # Still parallel: the shared Activity never hits the UI gate.
        assert max_active[0] == 3

    async def test_batch_header_printed_once(self, monkeypatch, capsys):
        urls = [f"https://s{i}.com/p/{i}" for i in range(3)]
        self._patch(monkeypatch, urls, parallel=3)
        from comic_dl.cli import main
        assert await main() == 0
        err = capsys.readouterr().err
        assert err.count("Processing 3 URLs") == 1


class TestUrlOriginMessages:
    pytestmark = pytest.mark.asyncio

    def _patch(self, monkeypatch, results):
        async def mock_process(url, **kwargs):
            return results.pop(0)

        monkeypatch.setattr("comic_dl.cli.process_url", mock_process)
        monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: {})
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent"])
        monkeypatch.setattr("comic_dl.cli.parse_urls", lambda: (
            ["https://a.com/", "https://b.com/"],
            argparse.Namespace(quiet=True, output=Path("/tmp"),
                               concurrency=5, parallel=5, force=False,
                               max_image_size=100 * 1024 * 1024, max_size=0,
                               chapter_parallel=1,
                               chapters=None, json=False, url=None,
                               dry_run=False,
                               url_origins={
                                   "https://a.com/": "urls.txt:3",
                                   "https://b.com/": "urls.txt:4",
                               }),
        ))

    async def test_failure_message_includes_file_line(self, monkeypatch, capsys):
        self._patch(monkeypatch, [("downloaded", "a.cbz"), ("failed", "")])

        from comic_dl.cli import main
        assert await main() == 1
        assert "Failed: https://b.com/ (urls.txt:4)" in capsys.readouterr().err

    async def test_skip_message_includes_file_line(self, monkeypatch, capsys):
        self._patch(monkeypatch, [("downloaded", "a.cbz"), ("skipped", "")])

        from comic_dl.cli import main
        assert await main() == 0
        assert "Skipped: https://b.com/ (urls.txt:4)" in capsys.readouterr().err


class TestBuildDownloadedIndex:
    def _make_cbz(self, path: Path, url: str) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "ComicInfo.xml",
                f"<ComicInfo><Title>T</Title><Web>{url}</Web></ComicInfo>",
            )

    def test_maps_cbz_by_normalized_url(self, tmp_path):
        sdir = tmp_path / "Series"
        sdir.mkdir()
        cbz = sdir / "Chapter 1.cbz"
        self._make_cbz(cbz, "https://x.com/c/1/")
        index = _build_downloaded_index(tmp_path)
        assert index[normalize_url("http://x.com/c/1")] == cbz

    def test_maps_nested_cbz(self, tmp_path):
        sdir = tmp_path / "S" / "Chapter 2"
        sdir.mkdir(parents=True)
        cbz = sdir / "page.cbz"
        self._make_cbz(cbz, "https://x.com/c/2")
        index = _build_downloaded_index(tmp_path)
        assert index[normalize_url("https://x.com/c/2/")] == cbz

    def test_ignores_missing_dir(self, tmp_path):
        assert _build_downloaded_index(tmp_path / "nope") == {}

    def test_ignores_bad_zip(self, tmp_path):
        sdir = tmp_path / "Series"
        sdir.mkdir()
        (sdir / "bad.cbz").write_bytes(b"not a zip")
        assert _build_downloaded_index(tmp_path) == {}

    def test_ignores_cbz_without_web_tag(self, tmp_path):
        sdir = tmp_path / "Series"
        sdir.mkdir()
        cbz = sdir / "no-url.cbz"
        with zipfile.ZipFile(cbz, "w") as zf:
            zf.writestr("ComicInfo.xml", "<ComicInfo><Title>T</Title></ComicInfo>")
        assert _build_downloaded_index(tmp_path) == {}

    def test_maps_marked_md(self, tmp_path):
        sdir = tmp_path / "Series"
        sdir.mkdir()
        md = sdir / "Chapter.md"
        md.write_text(
            "<!-- source: https://x.com/p/2/ -->\n# Title\n\ntext\n",
            encoding="utf-8",
        )
        index = _build_downloaded_index(tmp_path)
        assert index[normalize_url("https://x.com/p/2")] == md

    def test_ignores_unmarked_md(self, tmp_path):
        sdir = tmp_path / "Series"
        sdir.mkdir()
        (sdir / "notes.md").write_text("# Not a post\n", encoding="utf-8")
        assert _build_downloaded_index(tmp_path) == {}

    def test_ignores_malformed_marker(self, tmp_path):
        sdir = tmp_path / "Series"
        sdir.mkdir()
        (sdir / "x.md").write_text("<!-- source: not a url -->\n", encoding="utf-8")
        assert _build_downloaded_index(tmp_path) == {}

    def _seed_library_db(self, tmp_path):
        """Record a synthetic library (2 series x 3 chapters + downloads)."""
        lib = Library(library_path(tmp_path))
        lib.open()
        for sid in ("webtoons.com:a", "webtoons.com:b"):
            lib.upsert_series(
                sid, title=sid, source=f"https://webtoons.com/s/{sid}/list",
                source_site="webtoons.com", relative_path=sid,
            )
            for ep in range(3):
                _make_zip_cbz(
                    tmp_path / sid,
                    f"Ep {ep}.cbz",
                    f"https://webtoons.com/viewer?title_no={sid}&episode_no={ep}",
                )
                lib.upsert_chapter(
                    sid,
                    url=f"https://webtoons.com/viewer?title_no={sid}&episode_no={ep}",
                    cbz=f"Ep {ep}.cbz",
                    title=f"Ep. {ep}",
                )
        # One standalone cbz + one standalone text post, both recorded.
        _make_zip_cbz(tmp_path / "solo", "Solo.cbz", "https://pawchive.pw/u/1/post/9")
        lib.upsert_download(
            "https://pawchive.pw/u/1/post/9", "solo/Solo.cbz", "cbz"
        )
        (tmp_path / "solo" / "note.md").write_text(
            "<!-- source: https://pawchive.pw/u/1/post/10 -->\n# t\n",
            encoding="utf-8",
        )
        lib.upsert_download(
            "https://pawchive.pw/u/1/post/10", "solo/note.md", "md"
        )
        lib.close()

    def test_db_primary_avoids_zip_opens_for_recorded(self, tmp_path, monkeypatch):
        """A fully-recorded library resolves from the DB without reading a zip."""
        self._seed_library_db(tmp_path)
        real = _cbz_source_url
        calls = {"n": 0}

        def counting(path):
            calls["n"] += 1
            return real(path)

        monkeypatch.setattr("comic_dl.cli._cbz_source_url", counting)
        index = _build_downloaded_index(tmp_path)
        # Every .cbz is covered by a DB row, so no zip is ever opened.
        assert calls["n"] == 0
        assert len(index) == 8

    def test_fallback_scans_unrecorded_cbz(self, tmp_path, monkeypatch):
        """A .cbz absent from the DB still gets discovered by the scan."""
        self._seed_library_db(tmp_path)
        _make_zip_cbz(
            tmp_path / "legacy", "Old.cbz", "https://webtoons.com/legacy/epic/7"
        )
        real = _cbz_source_url
        calls = {"n": 0}

        def counting(path):
            calls["n"] += 1
            return real(path)

        monkeypatch.setattr("comic_dl.cli._cbz_source_url", counting)
        index = _build_downloaded_index(tmp_path)
        assert calls["n"] == 1
        assert normalize_url("https://webtoons.com/legacy/epic/7/") in index

    def test_skips_comic_dl_dir(self, tmp_path, monkeypatch):
        sdir = tmp_path / ".comic-dl" / "trash"
        sdir.mkdir(parents=True)
        _make_zip_cbz(sdir, "trash.cbz", "https://x.com/trash/1")
        calls = {"n": 0}

        def counting(path):
            calls["n"] += 1
            return path

        monkeypatch.setattr("comic_dl.cli._cbz_source_url", counting)
        assert _build_downloaded_index(tmp_path) == {}
        assert calls["n"] == 0


class TestCbzSourceUrl:
    def test_reads_web_url_from_comicinfo(self, tmp_path):
        cbz = tmp_path / "a.cbz"
        with zipfile.ZipFile(cbz, "w") as zf:
            zf.writestr(
                "ComicInfo.xml",
                '<ComicInfo><Title>T</Title><Web>https://fsicomics.com/x/</Web></ComicInfo>',
            )
        assert _cbz_source_url(cbz) == "https://fsicomics.com/x/"

    def test_missing_web_returns_empty(self, tmp_path):
        cbz = tmp_path / "a.cbz"
        with zipfile.ZipFile(cbz, "w") as zf:
            zf.writestr("ComicInfo.xml", "<ComicInfo><Title>T</Title></ComicInfo>")
        assert _cbz_source_url(cbz) == ""

    def test_not_a_zip_returns_empty(self, tmp_path):
        cbz = tmp_path / "a.cbz"
        cbz.write_bytes(b"not a zip")
        assert _cbz_source_url(cbz) == ""

    def test_missing_file_returns_empty(self, tmp_path):
        assert _cbz_source_url(tmp_path / "nope.cbz") == ""

    def test_oversized_comicinfo_returns_empty(self, tmp_path):
        cbz = tmp_path / "bomb.cbz"
        blob = b"a" * (1_048_576 + 1)
        with zipfile.ZipFile(cbz, "w") as zf:
            zf.writestr("ComicInfo.xml", blob)
        # Only an uncompressed-size guard matters — no huge string is inflated.
        assert _cbz_source_url(cbz) == ""

    def test_reads_web_url_from_cbt(self, tmp_path):
        cbt = tmp_path / "a.cbt"
        data = b'<ComicInfo><Title>T</Title><Web>https://fsicomics.com/x/</Web></ComicInfo>'
        with tarfile.open(cbt, "w") as tf:
            info = tarfile.TarInfo("ComicInfo.xml")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        assert _cbz_source_url(cbt) == "https://fsicomics.com/x/"

    def test_not_a_tar_returns_empty(self, tmp_path):
        cbt = tmp_path / "a.cbt"
        cbt.write_bytes(b"not a tar")
        assert _cbz_source_url(cbt) == ""


class TestResolveCbzPath:
    def _make_cbz(self, path: Path, url: str) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "ComicInfo.xml",
                f'<ComicInfo><Title>T</Title><Web>{url}</Web></ComicInfo>',
            )

    def _make_archive(self, path: Path, url: str) -> None:
        if path.suffix.lower() == ".cbt":
            import tarfile

            data = f'<ComicInfo><Title>T</Title><Web>{url}</Web></ComicInfo>'.encode()
            with tarfile.open(path, "w") as tf:
                info = tarfile.TarInfo("ComicInfo.xml")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        else:
            self._make_cbz(path, url)

    def test_new_file_returns_base(self, tmp_path):
        series = tmp_path / "Series"
        assert _resolve_archive_path(series, "Chapter 1", "https://x/1/", "", False, True, "cbz") == \
            series / "Chapter 1.cbz"

    def test_same_source_skips(self, tmp_path):
        series = tmp_path / "Series"
        series.mkdir()
        self._make_cbz(series / "Chapter 1.cbz", "https://x/1/")
        assert _resolve_archive_path(series, "Chapter 1", "https://x/1/", "111", False, True, "cbz") is None

    def test_same_source_ignores_trailing_slash(self, tmp_path):
        series = tmp_path / "Series"
        series.mkdir()
        self._make_cbz(series / "Chapter 1.cbz", "https://x/1/")
        assert _resolve_archive_path(series, "Chapter 1", "https://x/1", "111", False, True, "cbz") is None

    def test_different_source_disambiguates(self, tmp_path):
        series = tmp_path / "Series"
        series.mkdir()
        self._make_cbz(series / "Chapter 1.cbz", "https://x/1/")
        result = _resolve_archive_path(series, "Chapter 1", "https://x/2/", "222", False, True, "cbz")
        assert result == series / "Chapter 1 (222).cbz"

    def test_different_source_no_post_id_skips(self, tmp_path):
        series = tmp_path / "Series"
        series.mkdir()
        self._make_cbz(series / "Chapter 1.cbz", "https://x/1/")
        assert _resolve_archive_path(series, "Chapter 1", "https://x/2/", "", False, True, "cbz") is None

    def test_disambiguated_exists_skips(self, tmp_path):
        series = tmp_path / "Series"
        series.mkdir()
        self._make_cbz(series / "Chapter 1.cbz", "https://x/1/")
        self._make_cbz(series / "Chapter 1 (222).cbz", "https://x/2/")
        assert _resolve_archive_path(series, "Chapter 1", "https://x/2/", "222", False, True, "cbz") is None

    def test_force_overwrites_base(self, tmp_path):
        series = tmp_path / "Series"
        series.mkdir()
        self._make_cbz(series / "Chapter 1.cbz", "https://x/1/")
        assert _resolve_archive_path(series, "Chapter 1", "https://x/1/", "111", True, True, "cbz") == \
            series / "Chapter 1.cbz"

    def test_partial_marker_retries(self, tmp_path):
        """A chapter that previously failed part-way (marker present) must be
        re-downloaded into the same file, never skipped."""
        series = tmp_path / "Series"
        series.mkdir()
        cbz = series / "Chapter 1.cbz"
        self._make_cbz(cbz, "https://x/1/")
        (series / "Chapter 1.cbz.partial").touch()
        assert _resolve_archive_path(series, "Chapter 1", "https://x/1/", "111", False, True, "cbz") == \
            cbz

    def test_partial_with_different_source_retries_base(self, tmp_path):
        series = tmp_path / "Series"
        series.mkdir()
        self._make_cbz(series / "Chapter 1.cbz", "https://x/1/")
        (series / "Chapter 1.cbz.partial").touch()
        assert _resolve_archive_path(series, "Chapter 1", "https://x/2/", "222", False, True, "cbz") == \
            series / "Chapter 1.cbz"

    def test_zip_target_new_file_returns_zip(self, tmp_path):
        series = tmp_path / "Series"
        assert _resolve_archive_path(series, "Chapter 1", "https://x/1/", "", False, True, "zip") == \
            series / "Chapter 1.zip"

    def test_zip_target_same_source_skips(self, tmp_path):
        series = tmp_path / "Series"
        series.mkdir()
        self._make_archive(series / "Chapter 1.zip", "https://x/1/")
        assert _resolve_archive_path(series, "Chapter 1", "https://x/1/", "111", False, True, "zip") is None

    def test_other_format_counts_as_existing(self, tmp_path):
        """A format switch must not download a duplicate: an existing .zip
        (same source) blocks a fresh .cbz."""
        series = tmp_path / "Series"
        series.mkdir()
        self._make_archive(series / "Chapter 1.zip", "https://x/1/")
        assert _resolve_archive_path(series, "Chapter 1", "https://x/1/", "111", False, True, "cbz") is None

    def test_other_format_different_source_disambiguates(self, tmp_path):
        series = tmp_path / "Series"
        series.mkdir()
        self._make_archive(series / "Chapter 1.cbt", "https://x/1/")
        result = _resolve_archive_path(series, "Chapter 1", "https://x/2/", "222", False, True, "cbz")
        assert result == series / "Chapter 1 (222).cbz"

    def test_disambiguated_other_format_skips(self, tmp_path):
        series = tmp_path / "Series"
        series.mkdir()
        self._make_archive(series / "Chapter 1 (222).zip", "https://x/2/")
        assert _resolve_archive_path(series, "Chapter 1", "https://x/2/", "222", False, True, "cbz") is None

    def test_partial_other_format_retries_that_file(self, tmp_path):
        series = tmp_path / "Series"
        series.mkdir()
        cbt = series / "Chapter 1.cbt"
        self._make_archive(cbt, "https://x/1/")
        (series / "Chapter 1.cbt.partial").touch()
        assert _resolve_archive_path(series, "Chapter 1", "https://x/1/", "111", False, True, "cbz") == cbt


class TestDownloadedIndexPartial:
    def test_partial_cbz_excluded(self, tmp_path):
        """A partially-downloaded CBZ must not pre-skip a rerun."""
        root = tmp_path / "out"
        root.mkdir()
        series = root / "S"
        series.mkdir()
        cbz = series / "Chapter 1.cbz"
        with zipfile.ZipFile(cbz, "w") as zf:
            zf.writestr(
                "ComicInfo.xml",
                '<ComicInfo><Title>T</Title><Web>https://x/1/</Web></ComicInfo>',
            )
        index = _build_downloaded_index(root)
        assert normalize_url("https://x/1/") in index
        assert index[normalize_url("https://x/1/")] == cbz

        (series / "Chapter 1.cbz.partial").touch()
        index = _build_downloaded_index(root)
        assert normalize_url("https://x/1/") not in index


_SIGINT_SCRIPT = """
import asyncio, os, signal, sys
import comic_dl.cli as cli
from comic_dl.cli import (
    _handle_interrupt, stop_requested, _WORK_TASK,
    flush_debug_file, _cleanup_temp_dir, active_partial_files,
)
from comic_dl.ui import (
    active_snapshot, teardown_active, print_interrupt,
    err_console,
)

async def work():
    while not stop_requested():
        await asyncio.sleep(0.1)

async def main():
    task = asyncio.create_task(work())
    cli._WORK_TASK = task
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _handle_interrupt, sig, None)
            except (NotImplementedError, OSError):
                pass
    await task
    cli._WORK_TASK = None
    if stop_requested():
        progress = active_snapshot()
        teardown_active()
        flush_debug_file()
        _cleanup_temp_dir()
        partial = bool(active_partial_files())
        print_interrupt(progress if partial else "", partial=partial)
        sys.stderr.flush()
        return 130
    return 0

raise SystemExit(asyncio.run(main()))
"""


class TestSigintHandling:
    @pytest.fixture(autouse=True)
    def _reset_stop_state(self):
        reset_stop()
        yield
        reset_stop()

    def test_single_sigint_exits_130(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", _SIGINT_SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(1.5)
        os.kill(proc.pid, signal.SIGINT)
        _out, err = proc.communicate(timeout=20)
        assert proc.returncode == 130
        assert "Interrupted." in err
        assert "Traceback" not in err

    def test_single_sigint_exits_without_second_press(self):
        env = {**os.environ, "GRACE_SLEEP": "30"}
        proc = subprocess.Popen(
            [sys.executable, "-c", _SIGINT_SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env,
        )
        try:
            time.sleep(1.5)
            os.kill(proc.pid, signal.SIGINT)
            _out, err = proc.communicate(timeout=15)
            assert proc.returncode == 130
            assert "Traceback" not in err
        except subprocess.TimeoutExpired as err:
            proc.kill()
            proc.wait(timeout=5)
            raise AssertionError(
                "single SIGINT did not exit; still waiting for a second press"
            ) from err

    def test_interrupt_before_work_task_raises_keyboard_interrupt(self):
        import comic_dl.cli as cli
        cli._WORK_TASK = None
        try:
            _handle_interrupt(signal.SIGINT, None)
            raise AssertionError("expected KeyboardInterrupt")
        except KeyboardInterrupt:
            pass

    def test_single_interrupted_line_and_teardown_before_print(self, monkeypatch, capsys):
        """First SIGINT sets stop flag; second within grace tears down live
        renderer and prints exactly one 'Interrupted.' line as durable scrollback."""
        import comic_dl.cli as cli
        from comic_dl.ui import register_active, teardown_active

        class FakeLive:
            stopped = False

            def stop(self):
                self.stopped = True

        fake_live = FakeLive()
        register_active(fake_live, lambda: "Downloaded 3/5 images")  # type: ignore[arg-type]
        exited: list[int] = []
        monkeypatch.setattr(cli.os, "_exit", lambda code: exited.append(code))
        monkeypatch.setattr(cli, "_WORK_TASK", object())
        monkeypatch.setattr(cli, "active_partial_files", lambda: {"/tmp/x.part"})
        try:
            # First press: sets stop flag, no os._exit.
            cli._handle_interrupt(2, None)
            assert cli._STOP_REQUESTED is True
            assert exited == []
            # Second press within grace window: force exit.
            cli._handle_interrupt(2, None)
        finally:
            teardown_active()

        assert exited == [130]
        assert fake_live.stopped is True
        err = capsys.readouterr().err
        assert err.count("Interrupted.") == 1
        assert "Downloaded 3/5 images" in err

    def test_interrupt_flushes_debug_file_and_cleans_temp(self, monkeypatch, capsys):
        """The interrupt path must flush the --debug-file buffer (os._exit
        skips atexit) and remove the temp tree before exiting."""
        import comic_dl.cli as cli
        from comic_dl.ui import register_active, teardown_active

        class FakeLive:
            def stop(self):
                pass

        register_active(FakeLive(), lambda: "Downloaded 1/1 images")
        exited: list[int] = []
        flushed: list[bool] = []
        tmp_clean: list[bool] = []
        monkeypatch.setattr(cli.os, "_exit", lambda code: exited.append(code))
        monkeypatch.setattr(cli, "_WORK_TASK", object())
        monkeypatch.setattr(cli, "active_partial_files", lambda: set())
        monkeypatch.setattr(
            cli, "flush_debug_file", lambda: flushed.append(True)
        )
        monkeypatch.setattr(
            cli, "_cleanup_temp_dir", lambda: tmp_clean.append(True)
        )
        try:
            # First press: sets stop flag, no os._exit.
            cli._handle_interrupt(2, None)
            assert exited == []
            # Second press within grace: force exit with teardown.
            cli._handle_interrupt(2, None)
        finally:
            teardown_active()

        assert exited == [130]
        assert flushed == [True]
        assert tmp_clean == [True]

    def _run_interrupt(self, monkeypatch, capsys, partial_returns):
        """Invoke _handle_interrupt twice: first sets flag, second forces exit."""
        import comic_dl.cli as cli
        from comic_dl.ui import register_active, teardown_active

        class FakeLive:
            def stop(self):
                pass

        register_active(FakeLive(), lambda: "Downloaded 3/5 images")
        exited: list[int] = []
        monkeypatch.setattr(cli.os, "_exit", lambda code: exited.append(code))
        monkeypatch.setattr(cli, "_WORK_TASK", object())
        monkeypatch.setattr(cli, "active_partial_files", lambda: partial_returns)
        try:
            cli._handle_interrupt(2, None)  # first press: sets flag
            cli._handle_interrupt(2, None)  # second press: force exit
        finally:
            teardown_active()
        return capsys.readouterr().err

    def test_interrupt_no_partial_omits_resume_line(self, monkeypatch, capsys):
        err = self._run_interrupt(monkeypatch, capsys, set())
        assert err.count("Interrupted.") == 1
        assert "Partial save kept" not in err

    def test_interrupt_with_partial_mentions_resume(self, monkeypatch, capsys):
        err = self._run_interrupt(monkeypatch, capsys, {"/tmp/x.part"})
        assert err.count("Interrupted.") == 1
        assert "Partial save kept" in err

    def test_second_sigint_outside_grace_resets_timer(self, monkeypatch, capsys):
        """A second SIGINT after the grace window resets the state machine."""
        import comic_dl.cli as cli
        from comic_dl.ui import register_active, teardown_active

        class FakeLive:
            def stop(self):
                pass

        register_active(FakeLive(), lambda: "")
        exited: list[int] = []
        monkeypatch.setattr(cli.os, "_exit", lambda code: exited.append(code))
        monkeypatch.setattr(cli, "_WORK_TASK", object())
        monkeypatch.setattr(cli, "active_partial_files", lambda: set())
        try:
            # First press: sets flag.
            cli._handle_interrupt(2, None)
            assert cli._STOP_REQUESTED is True
            # Simulate grace window expired by resetting press time far back.
            cli._STOP_PRESS_TIME = cli.time.monotonic() - 10
            # Second press outside grace: resets (treats as fresh first press).
            cli._handle_interrupt(2, None)
            assert cli._STOP_REQUESTED is True
            assert exited == []  # still no os._exit
        finally:
            teardown_active()
            cli.reset_stop()


class TestGracefulStopBoundary:
    """Phase 4 boundary tests: in-flight items complete before unwinding;
    no new items start after stop is requested."""

    @pytest.fixture(autouse=True)
    def _reset_stop_state(self):
        reset_stop()
        yield
        reset_stop()

    def test_process_url_returns_interrupted_when_stop_requested(self):
        """process_url returns ('interrupted', '') when stop_requested()."""
        request_stop()
        status, msg = asyncio.run(
            process_url(
                "https://example.com/comic/1",
                Path("/tmp/test"),
                concurrency=5,
                force=False,
            )
        )
        assert status == "interrupted"
        assert msg == ""

    def test_run_urls_exits_130_on_graceful_stop(self, monkeypatch):
        """_run_urls returns EXIT_INTERRUPTED when stop_requested() after gather."""
        import comic_dl.cli as cli
        from comic_dl.errors import EXIT_INTERRUPTED

        call_count = 0

        async def _mock_process_url(**kwargs):
            nonlocal call_count
            call_count += 1
            # Second call: simulate stop requested after first item completes
            if call_count == 2:
                cli.request_stop()
            return "downloaded", ""

        args = argparse.Namespace(
            json=False, quiet=True, chapters=None, urls_from_file=False,
            parallel=1, output="/tmp/test", force=False, dry_run=False,
            concurrency=5, max_image_size=100 * 1024 * 1024, max_size=0,
            chapter_parallel=1, compress="stored", format="cbz",
        )
        monkeypatch.setattr(cli, "_build_downloaded_index", lambda p: {})
        monkeypatch.setattr(cli, "_open_library", lambda p: type(
            "Lib", (), {"available": True, "close": lambda s: None}
        )())
        monkeypatch.setattr(cli, "process_url", _mock_process_url)
        monkeypatch.setattr(cli, "print_failure_recap", lambda *a, **kw: None)
        monkeypatch.setattr(cli, "print_batch_summary", lambda *a, **kw: None)

        result = asyncio.run(
            cli._run_urls(["https://a.com/1", "https://b.com/2"], args)
        )
        assert result == EXIT_INTERRUPTED

    def test_run_update_stops_between_series(self, monkeypatch):
        """_run_update breaks out of the series loop when stop_requested()."""
        import comic_dl.cli as cli
        from comic_dl.errors import EXIT_INTERRUPTED

        series_processed = []

        class FakeScraper:
            async def scrape_series(self, *a, **kw):
                return None

        async def _fake_process_series(**kwargs):
            series_processed.append(kwargs.get("url", ""))
            # Simulate stop requested after first series
            if len(series_processed) == 1:
                cli.request_stop()
            return True

        monkeypatch.setattr(cli, "_process_series", _fake_process_series)
        monkeypatch.setattr(cli, "_resolve_series", lambda lib, t: {
            "series_id": "1", "title": t, "source": "https://a.com/s1"
        })
        monkeypatch.setattr(cli, "get_series_scraper", lambda d: FakeScraper())
        monkeypatch.setattr(cli, "generic_enabled", lambda: False)
        monkeypatch.setattr(cli, "normalize_url", lambda u: u)

        # Mock library
        fake_lib = type("Lib", (), {
            "available": True,
            "list_series": lambda s: [
                {"series_id": "1", "title": "S1", "source": "https://a.com/s1"},
                {"series_id": "2", "title": "S2", "source": "https://a.com/s2"},
            ],
            "get_chapters": lambda s, id: [],
            "close": lambda s: None,
        })()
        monkeypatch.setattr(cli, "_open_library", lambda p: fake_lib)

        result = asyncio.run(
            cli._run_update(["all"])
        )
        assert result == EXIT_INTERRUPTED
        # Only first series should have been processed
        assert len(series_processed) == 1

    def test_run_update_parallel_processes_all_series(self, monkeypatch):
        """--parallel > 1 runs series concurrently and reports each."""
        import comic_dl.cli as cli
        from comic_dl.errors import EXIT_OK

        series_processed = []

        class FakeScraper:
            async def scrape_series(self, *a, **kw):
                return None

        async def _fake_process_series(**kwargs):
            series_processed.append(kwargs.get("url", ""))
            return True

        monkeypatch.setattr(cli, "_process_series", _fake_process_series)
        monkeypatch.setattr(cli, "get_series_scraper", lambda d: FakeScraper())
        monkeypatch.setattr(cli, "generic_enabled", lambda: False)
        monkeypatch.setattr(cli, "normalize_url", lambda u: u)

        fake_lib = type("Lib", (), {
            "available": True,
            "list_series": lambda s: [
                {"series_id": "1", "title": "S1", "source": "https://a.com/s1"},
                {"series_id": "2", "title": "S2", "source": "https://b.com/s2"},
                {"series_id": "3", "title": "S3", "source": "https://c.com/s3"},
            ],
            "get_chapters": lambda s, id: [],
            "close": lambda s: None,
        })()
        monkeypatch.setattr(cli, "_open_library", lambda p: fake_lib)

        result = asyncio.run(
            cli._run_update(["all", "--parallel", "2", "-q"])
        )
        assert result == EXIT_OK
        assert len(series_processed) == 3

    def test_run_update_parallel_rejects_zero(self, monkeypatch):
        """--parallel 0 is a usage error, not silent sequential fallback."""
        import comic_dl.cli as cli
        from comic_dl.errors import EXIT_USAGE

        fake_lib = type("Lib", (), {
            "available": True,
            "list_series": lambda s: [],
            "close": lambda s: None,
        })()
        monkeypatch.setattr(cli, "_open_library", lambda p: fake_lib)

        result = asyncio.run(cli._run_update(["all", "--parallel", "0"]))
        assert result == EXIT_USAGE


class TestStopRequestPrimitive:
    def test_stop_requested_default_false(self):
        reset_stop()
        assert stop_requested() is False

    def test_request_stop_sets_flag(self):
        reset_stop()
        request_stop()
        assert stop_requested() is True
        reset_stop()

    def test_reset_stop_clears_flag(self):
        request_stop()
        assert stop_requested() is True
        reset_stop()
        assert stop_requested() is False


class TestResumeCommand:
    def test_default_returns_comic_dl(self):
        result = resume_command([])
        assert result == "comic-dl"

    def test_preserves_basic_args(self):
        result = resume_command(["comic-dl", "-u", "https://example.com/g/1/", "-o", "/tmp/out"])
        assert "comic-dl" in result
        assert "https://example.com/g/1/" in result
        assert "/tmp/out" in result

    def test_strips_debug_file(self):
        result = resume_command(["comic-dl", "--debug-file", "/tmp/dbg.log", "-u", "https://example.com/"])
        assert "--debug-file" not in result
        assert "/tmp/dbg.log" not in result
        assert "https://example.com/" in result

    def test_strips_no_color(self):
        result = resume_command(["comic-dl", "--no-color", "-u", "https://example.com/"])
        assert "--no-color" not in result

    def test_strips_quiet(self):
        result = resume_command(["comic-dl", "-q", "-u", "https://example.com/"])
        assert "-q" not in result

    def test_redacts_cookie_values(self):
        result = resume_command(["comic-dl", "--cookie", "secret123", "-u", "https://example.com/"])
        assert "secret123" not in result
        assert "<redacted>" in result

    def test_preserves_chapters_flag(self):
        result = resume_command(["comic-dl", "-u", "https://example.com/", "--chapters", "1-5"])
        assert "--chapters" in result
        assert "1-5" in result

    def test_preserves_config_flag(self):
        result = resume_command(["comic-dl", "--config", "/tmp/cfg.toml", "-u", "https://example.com/"])
        assert "--config" in result
        assert "/tmp/cfg.toml" in result

    def test_falls_back_to_sys_argv(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["comic-dl", "-u", "https://example.com/"])
        result = resume_command()
        assert "https://example.com/" in result


class TestLibraryDashAliases:
    pytestmark = pytest.mark.asyncio

    def _patch(self, monkeypatch, argv):
        calls = []

        def fake_run(command, command_argv):
            calls.append((command, command_argv))
            return 42

        monkeypatch.setattr("comic_dl.cli.run_library_command", fake_run)
        monkeypatch.setattr("sys.argv", ["comic-dl", *argv])
        return calls

    async def test_double_dash_alias_dispatches_to_subcommand(self, monkeypatch):
        calls = self._patch(monkeypatch, ["--list", "-o", "/tmp"])
        from comic_dl.cli import main
        assert await main() == 42
        assert calls == [("list", ["-o", "/tmp"])]

    async def test_bare_subcommand_still_works(self, monkeypatch):
        calls = self._patch(monkeypatch, ["list", "-o", "/tmp"])
        from comic_dl.cli import main
        assert await main() == 42
        assert calls == [("list", ["-o", "/tmp"])]

    async def test_info_and_other_aliases(self, monkeypatch):
        calls = self._patch(monkeypatch, ["--info", "Some Series"])
        from comic_dl.cli import main
        assert await main() == 42
        assert calls == [("info", ["Some Series"])]

    async def test_quiet_flag_stripped_for_library_commands(self, monkeypatch):
        """`-q/--quiet` is a global display flag, so a library subcommand
        that doesn't declare it must still parse instead of exiting 2."""
        for flag in ("-q", "--quiet"):
            calls = self._patch(monkeypatch, ["list", flag, "-o", "/tmp"])
            from comic_dl.cli import main
            assert await main() == 42
            assert calls == [("list", ["-o", "/tmp"])]


class TestPerChapterRows:
    """Issue 3: each chapter of a series gets its own Activity row, and the
    row is retired into the completed section once the chapter finishes
    (instead of one shared row reused by every chapter)."""

    pytestmark = pytest.mark.asyncio

    def _make(self, key="chapters:1"):
        from comic_dl.ui import Activity

        act = Activity(quiet=False)
        sink = act.row(key)
        return act, sink

    async def test_stage_and_progress_live_on_own_row(self):
        act, sink = self._make()
        sink.stage("Downloading images...")
        assert act._rows["chapters:1"].stage == "Downloading images..."
        # The page count now lives in the row's progress bar, not the text.
        sink.show_progress(10)
        assert act._rows["chapters:1"].stage == "Downloading images..."
        assert "chapters:1" in act._progress
        sink.update_progress(7)
        task = act._progress["chapters:1"].tasks[0]
        assert task.completed == 7
        # A second chapter gets its own row; the first is untouched.
        act.row("chapters:2")
        assert act._order == ["chapters:1", "chapters:2"]
        assert act._rows["chapters:2"].stage == ""

    async def test_succeed_prints_durable_line_and_retires_row(self, capsys):
        act, sink = self._make()
        await sink.succeed("Saved: Chapter 1.cbz (1.2 MB)")
        # The row is retired to the completed section instead of staying a
        # live spinner; the durable line goes to scrollback.
        assert act._rows["chapters:1"].status == "done"
        assert act._rows["chapters:1"].ok is True
        assert "chapters:1" in act._order
        out = capsys.readouterr().out
        assert "Saved: Chapter 1.cbz" in out

    async def test_succeed_clears_page_bar_before_retiring_row(self, capsys):
        act, sink = self._make()
        sink.show_progress(10)
        await sink.succeed("Saved: x.cbz")
        assert "chapters:1" not in act._progress
        assert act._rows["chapters:1"].status == "done"

    async def test_fail_prints_error_and_retires_row(self, capsys):
        act, sink = self._make()
        await sink.fail("Chapter 2: connection failed")
        assert act._rows["chapters:1"].status == "done"
        assert act._rows["chapters:1"].ok is False
        assert "chapters:1" in act._order
        # Durable result lines go to stdout through the Live console so they
        # survive in scrollback (see _print_durable).
        out = capsys.readouterr()
        assert "Chapter 2: connection failed" in out.out


class TestHelpCommand:
    """``comic-dl help [COMMAND]`` routing."""

    pytestmark = pytest.mark.asyncio

    async def test_help_without_arg_prints_main_help(self, monkeypatch, capsys):
        from comic_dl.cli import main

        monkeypatch.setattr("sys.argv", ["prog", "help"])
        assert await main() == 0
        out = capsys.readouterr().out
        assert "Usage:" in out
        assert "comic-dl help <command>" in out.replace("\n", " ")

    async def test_help_update_shows_full_update_options(self, monkeypatch, capsys):
        from comic_dl.cli import main

        monkeypatch.setattr("sys.argv", ["prog", "help", "update"])
        assert await main() == 0
        out = " ".join(capsys.readouterr().out.replace("\n", " ").split())
        assert "comic-dl update" in out
        assert "--chapter-parallel" in out
        assert "--concurrency" in out
        assert "--parallel" in out

    async def test_help_list_shows_library_options(self, monkeypatch, capsys):
        from comic_dl.cli import main

        monkeypatch.setattr("sys.argv", ["prog", "help", "list"])
        assert await main() == 0
        out = " ".join(capsys.readouterr().out.replace("\n", " ").split())
        assert "comic-dl list" in out
        assert "--source" in out
        assert "--json" in out

    async def test_help_unknown_command_exits_usage(self, monkeypatch, capsys):
        from comic_dl.cli import main

        monkeypatch.setattr("sys.argv", ["prog", "help", "nonsense"])
        assert await main() == 2
        err = capsys.readouterr().err
        assert "unknown command 'nonsense'" in err


class TestNoColorFlag:
    def test_parsed_by_first_stage(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--no-color"],
        )
        _, args = parse_urls()
        assert args.no_color is True

    def test_sets_console_no_color(self, monkeypatch):
        from comic_dl.ui import console, err_console

        original = (console.no_color, err_console.no_color)
        try:
            monkeypatch.setattr(
                "sys.argv",
                ["prog", "--url", "https://e-hentai.org/g/1/a/", "--no-color"],
            )
            parse_urls()
            assert console.no_color is True
        finally:
            console.no_color, err_console.no_color = original

    async def test_sets_no_color_for_dispatch(self, monkeypatch):
        from comic_dl.cli import main
        from comic_dl.ui import console, err_console

        original = (console.no_color, err_console.no_color)
        try:
            monkeypatch.setattr(
                "sys.argv", ["prog", "--no-color", "config", "path"]
            )
            assert await main() == 0
            assert console.no_color is True
            assert err_console.no_color is True
        finally:
            console.no_color, err_console.no_color = original


class TestColorModeFlag:
    def _restore(self):
        from comic_dl.ui import console, err_console

        console.no_color = False
        err_console.no_color = False
        console._force_terminal = None
        err_console._force_terminal = None

    def test_parsed_by_first_stage(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--color", "always"],
        )
        _, args = parse_urls()
        assert args.color == "always"

    async def test_invalid_value_returns_usage_error(self, monkeypatch, capsys):
        from comic_dl.cli import main

        monkeypatch.setattr("sys.argv", ["prog", "--color", "bogus", "config", "path"])
        assert await main() == EXIT_USAGE
        err = capsys.readouterr().err
        assert "invalid choice" in err

    async def test_no_color_beats_color_always(self, monkeypatch):
        from comic_dl.cli import main
        from comic_dl.ui import console

        try:
            monkeypatch.setattr(
                "sys.argv", ["prog", "--no-color", "--color", "always", "config", "path"]
            )
            assert await main() == 0
            assert console.no_color is True
        finally:
            self._restore()

    async def test_color_always_forces_terminal(self, monkeypatch):
        from comic_dl.cli import main
        from comic_dl.ui import console, err_console

        try:
            monkeypatch.setattr("sys.argv", ["prog", "--color", "always", "config", "path"])
            assert await main() == 0
            assert console._force_terminal is True
            assert err_console._force_terminal is True
            assert console.no_color is False
        finally:
            self._restore()

    async def test_color_never_pins_no_color(self, monkeypatch):
        from comic_dl.cli import main
        from comic_dl.ui import console

        try:
            monkeypatch.setattr("sys.argv", ["prog", "--color", "never", "config", "path"])
            assert await main() == 0
            assert console.no_color is True
            assert console._force_terminal is False
        finally:
            self._restore()

    async def test_env_no_color_overridden_by_flag_always(self, monkeypatch):
        from comic_dl.cli import main
        from comic_dl.ui import console

        try:
            monkeypatch.setenv("NO_COLOR", "1")
            monkeypatch.setattr(
                "sys.argv", ["prog", "--color", "always", "config", "path"]
            )
            assert await main() == 0
            assert console.no_color is False
        finally:
            monkeypatch.delenv("NO_COLOR", raising=False)
            self._restore()


class TestConfigFlag:
    def test_parsed_and_applied(self, monkeypatch, tmp_path):
        cfg = tmp_path / "conf.toml"
        cfg.write_text(f'output = "{tmp_path}"\n', encoding="utf-8")
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--config", str(cfg)],
        )
        from comic_dl.config import config_path

        try:
            _, args = parse_urls()
            assert config_path() == cfg
            assert args.output == tmp_path
        finally:
            from comic_dl.config import set_config_path

            set_config_path(None)

    def test_env_var_fallback(self, monkeypatch, tmp_path):
        cfg = tmp_path / "conf.toml"
        monkeypatch.setenv("COMIC_DL_CONFIG", str(cfg))
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/"])
        from comic_dl.config import config_path

        try:
            parse_urls()
            assert config_path() == cfg
        finally:
            from comic_dl.config import set_config_path

            set_config_path(None)

    def test_flag_wins_over_env(self, monkeypatch, tmp_path):
        flag_cfg = tmp_path / "flag.toml"
        flag_cfg.write_text("", encoding="utf-8")
        monkeypatch.setenv("COMIC_DL_CONFIG", str(tmp_path / "env.toml"))
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--config", str(flag_cfg)],
        )
        from comic_dl.config import config_path

        try:
            parse_urls()
            assert config_path() == flag_cfg
        finally:
            from comic_dl.config import set_config_path

            set_config_path(None)


class TestNoConfigFlag:
    def test_parsed_and_applied_on_download_path(self, monkeypatch):
        from comic_dl.config import no_config_active

        try:
            monkeypatch.setattr(
                "sys.argv",
                ["prog", "--url", "https://e-hentai.org/g/1/a/", "--no-config"],
            )
            parse_urls()
            assert no_config_active() is True
        finally:
            from comic_dl.config import set_no_config

            set_no_config(False)

    async def test_config_and_no_config_mutually_exclusive(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--config", "x", "--no-config", "-u",
             "https://e-hentai.org/g/1/a/"],
        )
        with pytest.raises(SystemExit):
            parse_urls()

    async def test_subcommand_ignores_config_file(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        cfg = tmp_path / "c.toml"
        cfg.write_text("concurrency = 3\n", encoding="utf-8")
        try:
            monkeypatch.setattr(
                "sys.argv",
                ["prog", "--no-config", "config", "show", "--config", str(cfg)],
            )
            assert await main() == 0
            out = capsys.readouterr().out
            assert "concurrency = 5" in out
        finally:
            from comic_dl.config import set_no_config

            set_no_config(False)


class TestNoCacheFlag:
    def test_flag_disables_cache(self, monkeypatch):
        from comic_dl import cache

        assert cache.cache_enabled() is True
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--no-cache"],
        )
        _, args = parse_urls()
        assert args.no_cache is True
        # `--no-cache` flows through _apply_config -> runtime [http] cache=false,
        # so this run's scrapes bypass the disk cache entirely.
        assert cache.cache_enabled() is False


class TestBannerPolicy:
    def test_banner_suppressed_off_tty(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "comic_dl.cli.is_interactive", lambda: False,
        )
        called = []

        import comic_dl.cli as cli

        monkeypatch.setattr(cli, "print_banner", lambda: called.append(True))
        monkeypatch.setattr(
            "sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/"]
        )
        parse_urls()
        assert called == []

    def test_banner_shown_on_tty(self, monkeypatch):
        monkeypatch.setattr(
            "comic_dl.cli.is_interactive", lambda: True,
        )
        called = []

        import comic_dl.cli as cli

        monkeypatch.setattr(cli, "print_banner", lambda: called.append(True))
        monkeypatch.setattr(
            "sys.argv", ["prog", "--url", "https://e-hentai.org/g/1/a/"]
        )
        parse_urls()
        assert called == [True]

    def test_no_banner_flag_still_wins(self, monkeypatch):
        monkeypatch.setattr(
            "comic_dl.cli.is_interactive", lambda: True,
        )
        called = []

        import comic_dl.cli as cli

        monkeypatch.setattr(cli, "print_banner", lambda: called.append(True))
        monkeypatch.setattr(
            "sys.argv",
            ["prog", "--url", "https://e-hentai.org/g/1/a/", "--no-banner"],
        )
        parse_urls()
        assert called == []


class TestConfigVerb:
    async def test_path(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        monkeypatch.setattr(
            "sys.argv", ["prog", "config", "path", "--config", str(tmp_path / "c.toml")]
        )
        assert await main() == 0
        out = capsys.readouterr().out
        # Strip folding newlines: Rich wraps long paths at the console width.
        assert str(tmp_path / "c.toml") in out.replace("\n", "")

    async def test_show_missing_config(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        monkeypatch.setattr(
            "sys.argv", ["prog", "config", "show", "--config", str(tmp_path / "c.toml")]
        )
        assert await main() == 0
        err = capsys.readouterr().err
        assert "No config file" in err

    async def test_init_then_show(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        cfg = tmp_path / "c.toml"
        monkeypatch.setattr("sys.argv", ["prog", "config", "init", "--config", str(cfg)])
        assert await main() == 0
        assert cfg.exists()
        out = capsys.readouterr().out
        assert "Wrote config" in out

        monkeypatch.setattr("sys.argv", ["prog", "config", "show", "--config", str(cfg)])
        assert await main() == 0
        out = capsys.readouterr().out
        assert "output =" in out
        assert "[http]" in out

    async def test_init_refuses_overwrite_without_force(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        cfg = tmp_path / "c.toml"
        cfg.write_text("output = '/tmp'\n", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["prog", "config", "init", "--config", str(cfg)])
        assert await main() == 2
        assert cfg.read_text(encoding="utf-8") == "output = '/tmp'\n"

    async def test_init_force_overwrites(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        cfg = tmp_path / "c.toml"
        cfg.write_text("output = '/tmp'\n", encoding="utf-8")
        monkeypatch.setattr(
            "sys.argv", ["prog", "config", "init", "--force", "--config", str(cfg)]
        )
        assert await main() == 0
        out = capsys.readouterr().out
        assert "Wrote config" in out
        assert "output =" in cfg.read_text(encoding="utf-8")

    async def test_bare_config_shows_effective(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        cfg = tmp_path / "c.toml"
        cfg.write_text("concurrency = 3\n", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["prog", "config", "--config", str(cfg)])
        assert await main() == 0
        out = capsys.readouterr().out
        assert "concurrency = 3" in out

    async def test_show_is_resolved_effective(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        cfg = tmp_path / "c.toml"
        cfg.write_text('concurrency = 3\noutput = "/tmp/x"\n', encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["prog", "config", "show", "--config", str(cfg)])
        assert await main() == 0
        out = capsys.readouterr().out
        assert 'concurrency = 3' in out
        assert 'output = "/tmp/x"' in out
        assert "parallel = 5" in out

    async def test_list_outputs_plain_toml(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        cfg = tmp_path / "c.toml"
        cfg.write_text('concurrency = 3\n', encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["prog", "config", "list", "--config", str(cfg)])
        assert await main() == 0
        out = capsys.readouterr().out
        assert "concurrency = 3" in out
        assert "Loaded from" not in out

    async def test_validate_ok(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        cfg = tmp_path / "c.toml"
        cfg.write_text(
            "concurrency = 3\n[http]\nrate-enabled = true\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            "sys.argv", ["prog", "config", "validate", "--config", str(cfg)]
        )
        assert await main() == 0
        assert "Config OK" in capsys.readouterr().out

    async def test_validate_reports_problems(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        cfg = tmp_path / "c.toml"
        cfg.write_text('concurrency = "many"\n[http]\nsolver = "nope"\n', encoding="utf-8")
        monkeypatch.setattr(
            "sys.argv", ["prog", "config", "validate", "--config", str(cfg)]
        )
        assert await main() == 1
        err = capsys.readouterr().err
        assert "concurrency" in err
        assert "solver" in err

    async def test_validate_invalid_toml(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        cfg = tmp_path / "c.toml"
        cfg.write_text("this is not [ valid", encoding="utf-8")
        monkeypatch.setattr(
            "sys.argv", ["prog", "config", "validate", "--config", str(cfg)]
        )
        assert await main() == 1
        assert "Invalid TOML" in capsys.readouterr().err

    async def test_edit_creates_and_opens(self, monkeypatch, capsys, tmp_path):
        from comic_dl.cli import main

        cfg = tmp_path / "c.toml"
        editor = tmp_path / "fake-editor.sh"
        marker = tmp_path / "ran.txt"
        editor.write_text("#!/bin/sh\necho ran > \"$MARK\"\n", encoding="utf-8")
        editor.chmod(0o755)
        monkeypatch.setenv("EDITOR", str(editor))
        monkeypatch.setenv("MARK", str(marker))
        monkeypatch.setattr("sys.argv", ["prog", "config", "edit", "--config", str(cfg)])
        assert await main() == 0
        assert marker.exists()
        assert cfg.exists()


class TestCompletionVerb:
    async def test_bash(self, monkeypatch, capsys):
        from comic_dl.cli import main

        monkeypatch.setattr("sys.argv", ["prog", "completion", "bash"])
        assert await main() == 0
        out = capsys.readouterr().out
        assert "complete -o default -F _comic_dl_complete comic-dl" in out
        assert "--chapter-parallel" in out
        assert "--config" in out
        assert "update" in out

    async def test_zsh(self, monkeypatch, capsys):
        from comic_dl.cli import main

        monkeypatch.setattr("sys.argv", ["prog", "completion", "zsh"])
        assert await main() == 0
        out = capsys.readouterr().out
        assert "#compdef comic-dl" in out
        assert "compdef _comic_dl comic-dl" in out

    async def test_fish(self, monkeypatch, capsys):
        from comic_dl.cli import main

        monkeypatch.setattr("sys.argv", ["prog", "completion", "fish"])
        assert await main() == 0
        out = capsys.readouterr().out
        assert "complete -c comic-dl -f" in out
        assert "__fish_seen_subcommand_from update" in out

    async def test_invalid_shell(self, monkeypatch, capsys):
        from comic_dl.cli import main

        monkeypatch.setattr("sys.argv", ["prog", "completion", "pwsh"])
        assert await main() == 2
        err = capsys.readouterr().err
        assert "invalid choice" in err


class TestListSourcesJson:
    async def test_json_before_flag(self, monkeypatch, capsys):
        from comic_dl.cli import main
        from comic_dl.ui import set_json_mode

        try:
            monkeypatch.setattr("sys.argv", ["prog", "--json", "--list-sources"])
            assert await main() == 0
            out = capsys.readouterr().out
            import json as _json

            payload = _json.loads(out)
            assert payload["schema_version"] == 1
            assert payload["sources"]
        finally:
            set_json_mode(False)

    async def test_flag_before_json(self, monkeypatch, capsys):
        from comic_dl.cli import main
        from comic_dl.ui import set_json_mode

        try:
            monkeypatch.setattr("sys.argv", ["prog", "--list-sources", "--json"])
            assert await main() == 0
            out = capsys.readouterr().out
            import json as _json

            payload = _json.loads(out)
            assert payload["schema_version"] == 1
        finally:
            set_json_mode(False)


class TestRestorePagesFromArchive:
    """Partial-rerun resume: previous good pages are seeded into tmp_dir."""

    def _make_partial(self, path, pages, comicinfo=True):
        with zipfile.ZipFile(path, "w") as zf:
            for name, payload in pages.items():
                zf.writestr(name, payload)
            if comicinfo:
                zf.writestr("ComicInfo.xml", "<xml/>")

    def test_restores_only_page_members(self, tmp_path):
        cbz = tmp_path / "Ch.cbz"
        self._make_partial(
            cbz,
            {"Page_0001.webp": b"a", "Page_0003.jpg": b"b"},
        )
        dest = tmp_path / "tmp"
        dest.mkdir()
        n = cli._restore_pages_from_archive(cbz, dest)
        assert n == 2
        assert (dest / "Page_0001.webp").read_bytes() == b"a"
        assert (dest / "Page_0003.jpg").read_bytes() == b"b"
        assert not (dest / "ComicInfo.xml").exists()

    def test_rejects_nested_and_non_page_names(self, tmp_path):
        cbz = tmp_path / "Ch.cbz"
        self._make_partial(
            cbz,
            {"../evil.webp": b"x", "sub/Page_0002.webp": b"y",
             "cover.txt": b"z", "Page_0005.png": b"ok"},
            comicinfo=False,
        )
        dest = tmp_path / "tmp"
        dest.mkdir()
        assert cli._restore_pages_from_archive(cbz, dest) == 1
        assert (dest / "Page_0005.png").exists()

    def test_corrupt_zip_restores_nothing(self, tmp_path):
        cbz = tmp_path / "Ch.cbz"
        cbz.write_bytes(b"not a zip")
        dest = tmp_path / "tmp"
        dest.mkdir()
        assert cli._restore_pages_from_archive(cbz, dest) == 0

    def test_oversized_member_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_RESTORE_PAGE_CAP", 4)
        cbz = tmp_path / "Ch.cbz"
        self._make_partial(
            cbz, {"Page_0001.jpg": b"12345", "Page_0002.jpg": b"ok"}
        )
        dest = tmp_path / "tmp"
        dest.mkdir()
        assert cli._restore_pages_from_archive(cbz, dest) == 1

    def test_cbt_supported(self, tmp_path):
        import tarfile as tf_mod

        cbt = tmp_path / "Ch.cbt"
        with tf_mod.open(cbt, "w") as tf:
            for name in ("Page_0001.jpg", "Page_0002.jpg"):
                data = name.encode()
                info = tf_mod.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        dest = tmp_path / "tmp"
        dest.mkdir()
        assert cli._restore_pages_from_archive(cbt, dest) == 2

    def test_missing_archive_returns_zero(self, tmp_path):
        dest = tmp_path / "tmp"
        dest.mkdir()
        assert cli._restore_pages_from_archive(tmp_path / "nope.cbz", dest) == 0


class TestSingleUrlVerdict:
    """A clean single-URL download ends with an explicit success block,
    mirroring the partial/failure verdict blocks."""

    def _args(self):
        return argparse.Namespace(
            json=False, quiet=False, chapters=None, urls_from_file=False,
            parallel=1, output="/tmp/out", force=False, dry_run=False,
            concurrency=5, max_image_size=100 * 1024 * 1024, max_size=0,
            chapter_parallel=1, compress="stored", format="cbz",
        )

    def _setup(self, monkeypatch):
        monkeypatch.setattr(cli, "_build_downloaded_index", lambda p: {})
        monkeypatch.setattr(cli, "_open_library", lambda p: type(
            "Lib", (), {"available": True, "close": lambda s: None}
        )())

    def test_success_prints_verdict(self, monkeypatch, capsys):
        async def _ok(**kwargs):
            stats = kwargs["stats"]
            stats.status = "success"
            stats.output_path = "/tmp/out/Series/Ch.cbz"
            return "downloaded", "Ch.cbz"

        self._setup(monkeypatch)
        monkeypatch.setattr(cli, "process_url", _ok)
        code = asyncio.run(
            cli._run_urls(["https://a.com/1"], self._args())
        )
        out = capsys.readouterr().out
        assert code == EXIT_OK
        assert "Downloaded: Ch.cbz" in out

    def test_partial_no_success_verdict(self, monkeypatch, capsys):
        async def _partial(**kwargs):
            stats = kwargs["stats"]
            stats.status = "partial"
            stats.missing_pages = 3
            stats.total_pages = 84
            return "partial", "x.cbz"

        self._setup(monkeypatch)
        monkeypatch.setattr(cli, "process_url", _partial)
        code = asyncio.run(
            cli._run_urls(["https://a.com/1"], self._args())
        )
        captured = capsys.readouterr()
        assert code == EXIT_ERROR
        assert "Downloaded:" not in captured.out
        assert "partial download" in captured.err

    def test_quiet_suppresses_verdict(self, monkeypatch, capsys):
        async def _ok(**kwargs):
            stats = kwargs["stats"]
            stats.status = "success"
            return "downloaded", ""

        self._setup(monkeypatch)
        monkeypatch.setattr(cli, "process_url", _ok)
        args = self._args()
        args.quiet = True
        asyncio.run(cli._run_urls(["https://a.com/1"], args))
        assert "Downloaded:" not in capsys.readouterr().out

    def test_partial_with_path_output_single_verdict(self, monkeypatch, capsys):
        """Path output must not crash print_partial_block (rich.markup.escape
        rejects Path) and must not be tallied twice when it does."""
        # The batch runner passes args.output, which argparse builds as a
        # Path, straight into the partial block.  An escape error used to be
        # swallowed by the per-URL exception handler, which re-appended the
        # URL to the failed list ("Processed 8 URLs ... 4 failed" for 2
        # partials) and reported "Unexpected internal error.".
        async def _partial(**kwargs):
            stats = kwargs["stats"]
            stats.status = "partial"
            stats.missing_pages = 3
            stats.total_pages = 84
            return "partial", "x.cbz"

        self._setup(monkeypatch)
        monkeypatch.setattr(cli, "process_url", _partial)
        args = self._args()
        args.output = Path("/tmp/out")
        code = asyncio.run(cli._run_urls(["https://a.com/1"], args))
        captured = capsys.readouterr()
        assert code == EXIT_ERROR
        assert "partial download" in captured.err
        assert "rerun to resume" in captured.err
        assert "Unexpected internal error" not in captured.err
