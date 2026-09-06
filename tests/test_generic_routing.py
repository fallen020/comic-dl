"""Offline routing tests for the generic fallback scraper (CLI wiring)."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import pytest

from comic_dl import cli
from comic_dl.config import set_runtime_download
from comic_dl.models import ImageItem, PostMetadata, SeriesMetadata

GALLERY_URL = "https://unknown-gallery.example/read/chapter/12"
SERIES_URL = "https://unknown-series.example/manga/foo"
IMAGE_URL = "https://unknown-gallery.example/cdn/01.jpg"


class _FakeAsyncSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _fake_transport(monkeypatch):
    """Keep routing tests offline: no real AsyncSession is constructed and
    URL validation is permissive (no DNS)."""
    monkeypatch.setattr("comic_dl.cli.AsyncSession", _FakeAsyncSession)

    async def _permissive(url):
        return url

    monkeypatch.setattr(
        "comic_dl.scrapers.base.validate_request_url_async", _permissive
    )
    monkeypatch.setattr(
        "comic_dl.scrapers.generic.validate_request_url", lambda url: url
    )


@pytest.fixture(autouse=True)
def _reset_download_flags():
    yield
    from comic_dl import config

    config._RUNTIME_DOWNLOAD.clear()


class _FakeGeneric:
    """Deterministic stand-in for GenericScraper with recorded calls."""

    def __init__(self, kind):
        self.kind = kind
        self.detect_calls: list[str] = []
        self.scrape_calls: list[str] = []
        self.scrape_series_calls: list[str] = []

    async def detect(self, url, client):
        self.detect_calls.append(url)
        return self.kind

    async def scrape(self, url, client):
        self.scrape_calls.append(url)
        return PostMetadata(
            series_title="Generic Series",
            chapter_title="Generic Chapter",
            images=[ImageItem(url=IMAGE_URL, page_number=1, filename="01.jpg")],
            total_pages=1,
        )

    async def scrape_series(self, url, client):
        self.scrape_series_calls.append(url)
        return SeriesMetadata(
            series_title="Generic Series",
            chapters=[
                {"title": "Chapter 1", "url": GALLERY_URL, "episode_no": "1"},
                {"title": "Chapter 2", "url": GALLERY_URL, "episode_no": "2"},
                {"title": "Chapter 3", "url": GALLERY_URL, "episode_no": "3"},
            ],
        )


def _patch_generic(monkeypatch, fake):
    monkeypatch.setattr("comic_dl.cli.get_generic_scraper", lambda: fake)


def _patch_chapter_scraper(monkeypatch, overrides):
    from comic_dl.scrapers import registry

    real = registry.get_chapter_scraper

    def lookup(domain):
        if domain in overrides:
            return overrides[domain]
        return real(domain)

    monkeypatch.setattr("comic_dl.cli.get_chapter_scraper", lookup)


def _patch_series_scraper(monkeypatch, overrides):
    from comic_dl.scrapers import registry

    real = registry.get_series_scraper

    def lookup(domain):
        if domain in overrides:
            return overrides[domain]
        return real(domain)

    monkeypatch.setattr("comic_dl.cli.get_series_scraper", lookup)


class TestProcessUrl:
    @pytest.mark.asyncio
    async def test_known_source_wins_over_generic(self, monkeypatch, tmp_path):
        """A registered source is used; generic is never consulted."""
        calls = []

        class KnownScraper:
            async def scrape(self, url, client):
                calls.append(url)
                return PostMetadata(
                    series_title="Known",
                    chapter_title="Ch",
                    images=[ImageItem(url=IMAGE_URL, page_number=1, filename="01.jpg")],
                    total_pages=1,
                )

        fake = _FakeGeneric("gallery")

        async def _boom(url, client):
            raise AssertionError("generic must not run for a known source")

        fake.detect = _boom
        monkeypatch.setattr("comic_dl.cli.get_generic_scraper", lambda: fake)
        _patch_chapter_scraper(monkeypatch, {"e-hentai.org": KnownScraper()})

        async def fake_download(images, dest_dir, *a, **kw):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", fake_download)

        status, _ = await cli.process_url(
            url="https://e-hentai.org/g/1/abc/",
            output_dir=Path(tmp_path),
            concurrency=1,
            force=False,
            quiet=True,
        )
        assert status == "downloaded"
        assert calls == ["https://e-hentai.org/g/1/abc"]

    @pytest.mark.asyncio
    async def test_unknown_host_generic_gallery(self, monkeypatch, tmp_path):
        fake = _FakeGeneric("gallery")
        _patch_generic(monkeypatch, fake)
        _patch_chapter_scraper(monkeypatch, {})
        _patch_series_scraper(monkeypatch, {})

        async def fake_download(images, dest_dir, *a, **kw):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", fake_download)

        status, _ = await cli.process_url(
            url=GALLERY_URL,
            output_dir=Path(tmp_path),
            concurrency=1,
            force=False,
            quiet=True,
        )
        assert status == "downloaded"
        assert fake.detect_calls == [GALLERY_URL]
        assert fake.scrape_calls == [GALLERY_URL]

    @pytest.mark.asyncio
    async def test_unknown_host_generic_series(self, monkeypatch, tmp_path):
        fake = _FakeGeneric("series")
        _patch_generic(monkeypatch, fake)
        _patch_chapter_scraper(monkeypatch, {})
        _patch_series_scraper(monkeypatch, {})
        calls: list[str] = []
        scrapers: list[object] = []

        async def fake_process_series(**kwargs):
            calls.append(kwargs["url"])
            scrapers.append(kwargs["scraper"])
            return True

        monkeypatch.setattr("comic_dl.cli._process_series", fake_process_series)

        status, _ = await cli.process_url(
            url=SERIES_URL,
            output_dir=Path(tmp_path),
            concurrency=1,
            force=False,
            quiet=True,
        )
        assert status == "downloaded"
        assert fake.detect_calls == [SERIES_URL]
        assert calls == [SERIES_URL]
        assert scrapers == [fake]

    @pytest.mark.asyncio
    async def test_generic_disabled_restores_unsupported_url(self, monkeypatch, tmp_path):
        fake = _FakeGeneric("gallery")
        _patch_generic(monkeypatch, fake)
        _patch_chapter_scraper(monkeypatch, {})
        set_runtime_download(generic=False)

        status, _ = await cli.process_url(
            url=GALLERY_URL,
            output_dir=Path(tmp_path),
            concurrency=1,
            force=False,
            quiet=True,
        )
        assert status == "failed"
        assert fake.detect_calls == []

    @pytest.mark.asyncio
    async def test_generic_finds_nothing_falls_through_to_unsupported(
        self, monkeypatch, tmp_path
    ):
        fake = _FakeGeneric(None)
        _patch_generic(monkeypatch, fake)
        _patch_chapter_scraper(monkeypatch, {})

        status, _ = await cli.process_url(
            url=GALLERY_URL,
            output_dir=Path(tmp_path),
            concurrency=1,
            force=False,
            quiet=True,
        )
        assert status == "failed"
        assert fake.detect_calls == [GALLERY_URL]

    @pytest.mark.asyncio
    async def test_known_domain_format_error_unchanged(self, monkeypatch, tmp_path):
        """Malformed known-domain URLs still reject before generic runs."""
        fake = _FakeGeneric("gallery")
        _patch_generic(monkeypatch, fake)
        _patch_chapter_scraper(monkeypatch, {})

        status, _ = await cli.process_url(
            url="https://webtoons.com/not/a/valid/webtoon/url",
            output_dir=Path(tmp_path),
            concurrency=1,
            force=False,
            quiet=True,
        )
        assert status == "failed"
        assert fake.detect_calls == []


class TestPreviewUrl:
    @pytest.mark.asyncio
    async def test_generic_gallery_preview(self, monkeypatch, tmp_path):
        fake = _FakeGeneric("gallery")
        _patch_generic(monkeypatch, fake)
        _patch_chapter_scraper(monkeypatch, {})

        entry = await cli._preview_url(GALLERY_URL, index={}, force=False)
        assert entry["kind"] == "chapter"
        assert entry["title"] == "Generic Chapter"
        assert entry["detail"] == "1 page"
        assert fake.detect_calls == [GALLERY_URL]

    @pytest.mark.asyncio
    async def test_generic_series_preview(self, monkeypatch, tmp_path):
        fake = _FakeGeneric("series")
        _patch_generic(monkeypatch, fake)
        _patch_chapter_scraper(monkeypatch, {})

        entry = await cli._preview_url(SERIES_URL, index={}, force=False)
        assert entry["kind"] == "series"
        assert entry["title"] == "Generic Series"
        assert entry["detail"] == "3 chapters"
        assert fake.detect_calls == [SERIES_URL]

    @pytest.mark.asyncio
    async def test_generic_disabled_preview_errors(self, monkeypatch, tmp_path):
        fake = _FakeGeneric("gallery")
        _patch_generic(monkeypatch, fake)
        _patch_chapter_scraper(monkeypatch, {})
        set_runtime_download(generic=False)

        entry = await cli._preview_url(GALLERY_URL, index={}, force=False)
        assert entry["action"] == "error"
        assert "Unsupported URL" in entry["detail"]
        assert fake.detect_calls == []


class TestLibraryUpdate:
    def _seed_unknown_series(self, root: Path) -> None:
        from comic_dl.library import Library, library_path

        lib = Library(library_path(root))
        lib.open()
        lib.upsert_series(
            "unknown-series.example:1",
            title="Generic Series",
            source=SERIES_URL,
            source_site="unknown-series.example",
            relative_path="Generic Series",
        )
        lib.close()

    def test_update_routes_unknown_source_to_generic(self, tmp_path, monkeypatch, capsys):
        from comic_dl import cli as cli_module

        self._seed_unknown_series(tmp_path)
        fake = _FakeGeneric("series")
        _patch_generic(monkeypatch, fake)
        _patch_chapter_scraper(monkeypatch, {})
        _patch_series_scraper(monkeypatch, {})

        scrapers: list[object] = []

        async def stub(**kwargs):
            scrapers.append(kwargs["scraper"])
            return True

        monkeypatch.setattr(cli_module, "_process_series", stub)

        code = asyncio.run(cli_module._run_update(["-o", str(tmp_path), "all"]))
        assert code == 0
        assert scrapers == [fake]
        assert fake.detect_calls == [SERIES_URL]

    def test_update_skips_unknown_source_when_generic_disabled(
        self, tmp_path, monkeypatch, capsys
    ):
        from comic_dl import cli as cli_module

        self._seed_unknown_series(tmp_path)
        fake = _FakeGeneric("series")
        _patch_generic(monkeypatch, fake)
        _patch_chapter_scraper(monkeypatch, {})
        _patch_series_scraper(monkeypatch, {})
        set_runtime_download(generic=False)

        async def stub(**kwargs):
            raise AssertionError("generic must not run when disabled")

        monkeypatch.setattr(cli_module, "_process_series", stub)

        code = asyncio.run(cli_module._run_update(["-o", str(tmp_path), "all"]))
        assert code == 0
        assert fake.detect_calls == []
        out = capsys.readouterr().err
        assert "skipping" in out.lower()


class TestFlagParsing:
    def test_parser_exposes_no_generic_flag(self):
        parser = cli._build_first_stage_parser()
        args = parser.parse_args(["--no-generic"])
        assert args.no_generic is True
        args2 = parser.parse_args([])
        assert args2.no_generic is False

    def test_no_generic_flag_sets_runtime_download(self, monkeypatch):

        calls = []

        def fake_set(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr("comic_dl.cli.set_runtime_download", fake_set)
        monkeypatch.setattr("comic_dl.cli.set_runtime_http", lambda **k: None)
        monkeypatch.setattr("comic_dl.cli.http_setting", lambda *a, **k: None)
        monkeypatch.setattr("comic_dl.cli.validate_impersonate", lambda p: None)
        monkeypatch.setattr("comic_dl.cli.impersonate_is_deprecated", lambda p: False)

        args = argparse.Namespace(
            output=None, concurrency=None, parallel=None, chapter_parallel=None,
            max_image_size=None, max_size=None, compress=None, format=None,
            impersonate=None, solver=None, no_cookie=False, no_cache=False,
            no_rate=False, no_generic=True,
        )
        cli._apply_config(args)
        assert {"generic": False} in calls

    def test_flag_off_leaves_generic_enabled(self, monkeypatch):

        calls = []

        def fake_set(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr("comic_dl.cli.set_runtime_download", fake_set)
        monkeypatch.setattr("comic_dl.cli.set_runtime_http", lambda **k: None)
        monkeypatch.setattr("comic_dl.cli.http_setting", lambda *a, **k: None)
        monkeypatch.setattr("comic_dl.cli.validate_impersonate", lambda p: None)
        monkeypatch.setattr("comic_dl.cli.impersonate_is_deprecated", lambda p: False)

        args = argparse.Namespace(
            output=Path("/tmp"), concurrency=1, parallel=1, chapter_parallel=1,
            max_image_size=1, max_size=0, compress="stored", format="cbz",
            impersonate=None, solver=None, no_cookie=False, no_cache=False,
            no_rate=False, no_generic=False,
        )
        cli._apply_config(args)
        assert calls == []

    def test_config_download_generic_false_disables(self, tmp_path, monkeypatch):
        from comic_dl import config

        cfg = tmp_path / "config.toml"
        cfg.write_text('[download]\ngeneric = false\n', encoding="utf-8")
        monkeypatch.setattr(config, "config_path", lambda: cfg)
        assert config.generic_enabled() is False

    def test_generic_enabled_by_default(self):
        from comic_dl import config

        config._RUNTIME_DOWNLOAD.clear()
        assert config.generic_enabled() is True
