from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest
from curl_cffi.requests.exceptions import (
    HTTPError as CurlHTTPError,
)

from comic_dl.archiver import create_archive
from comic_dl.cli import (
    ChapterSelectionQuit,
    _process_series,
    parse_urls,
    process_url,
    request_stop,
    reset_stop,
)
from comic_dl.downloader import (
    download_httpx,
    verify_downloads,
)
from comic_dl.models import ImageItem, PostMetadata
from comic_dl.scrapers.sites.webtoon import (
    _normalize_episode_title,
    _strip_trailing_noise,
    scrape_chapter,
    scrape_series,
)
from comic_dl.ui import ETA, make_download_progress, make_spinner
from comic_dl.utils import (
    is_valid_ehentai_url,
    is_valid_pawchive_url,
    is_valid_webtoon_url,
    normalize_url,
    sanitize_filename,
    verify_image_file,
)


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

# ===========================================================================
# CLI & ARGUMENT PARSING
# ===========================================================================

class TestHelpFlag:
    def test_long_help(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--help"])
        with pytest.raises(SystemExit):
            parse_urls()

    def test_short_help(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-h"])
        with pytest.raises(SystemExit):
            parse_urls()

    def test_question_mark(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-?"])
        with pytest.raises(SystemExit):
            parse_urls()


class TestUrlArgument:
    def test_valid_url(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://example.com/"])
        urls, args = parse_urls()
        assert len(urls) == 1
        assert urls[0] == "https://example.com/"
        assert not args.quiet
        assert args.concurrency == 5

    def test_short_url(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-u", "https://example.com/"])
        urls, _args = parse_urls()
        assert len(urls) == 1

    def test_missing_url_value(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--url"])
        with pytest.raises(SystemExit):
            parse_urls()

    def test_empty_url(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--url", ""])
        # Empty URL with interactive prompt will fail; just verify no crash
        with contextlib.suppress(SystemExit, EOFError, OSError):
            _urls, _ = parse_urls()

    def test_url_rejects_non_http(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["prog", "--url", "/tmp/some/links.md"])
        with pytest.raises(SystemExit):
            parse_urls()
        err = capsys.readouterr().err
        assert "Must start with http://" in err
        assert "-f/--file" in err


class TestFileArgument:
    def test_valid_file(self, tmp_path, monkeypatch):
        f = tmp_path / "urls.txt"
        f.write_text("https://example.com/a/\nhttps://example.com/b/\n")
        monkeypatch.setattr("sys.argv", ["prog", "--file", str(f)])
        urls, _ = parse_urls()
        assert len(urls) == 2

    def test_short_file(self, tmp_path, monkeypatch):
        f = tmp_path / "urls.txt"
        f.write_text("https://example.com/a/\n")
        monkeypatch.setattr("sys.argv", ["prog", "-f", str(f)])
        urls, _ = parse_urls()
        assert len(urls) == 1

    def test_missing_file(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--file", "/nonexistent/file.txt"])
        with pytest.raises(SystemExit):
            parse_urls()

    def test_empty_file(self, tmp_path, monkeypatch):
        f = tmp_path / "empty.txt"
        f.write_text("")
        monkeypatch.setattr("sys.argv", ["prog", "--file", str(f)])
        with pytest.raises(SystemExit):
            parse_urls()

    def test_file_with_blank_lines(self, tmp_path, monkeypatch):
        f = tmp_path / "urls.txt"
        f.write_text("https://example.com/a/\n\n\nhttps://example.com/b/\n")
        monkeypatch.setattr("sys.argv", ["prog", "--file", str(f)])
        urls, _ = parse_urls()
        assert len(urls) == 2


class TestOutputArgument:
    def test_short_output(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.argv", ["prog", "-u", "https://example.com/", "-o", str(tmp_path / "out")])
        _, args = parse_urls()
        assert args.output == tmp_path / "out"


class TestFlags:
    def test_force(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-u", "https://example.com/", "--force"])
        _, args = parse_urls()
        assert args.force is True

    def test_quiet(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-u", "https://example.com/", "--quiet"])
        _, args = parse_urls()
        assert args.quiet is True

    def test_short_quiet(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-u", "https://example.com/", "-q"])
        _, args = parse_urls()
        assert args.quiet is True

    def test_verbose(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-u", "https://example.com/", "-vvv"])
        _, args = parse_urls()
        assert args.verbose == 3

    def test_concurrency(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-u", "https://example.com/", "-c", "20"])
        _, args = parse_urls()
        assert args.concurrency == 20

    def test_invalid_concurrency(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-u", "https://example.com/", "-c", "abc"])
        with pytest.raises(SystemExit):
            parse_urls()

    def test_max_image_size(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-u", "https://example.com/", "--max-image-size", "1048576"])
        _, args = parse_urls()
        assert args.max_image_size == 1048576

    def test_max_size(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-u", "https://example.com/", "--max-size", "52428800"])
        _, args = parse_urls()
        assert args.max_size == 52428800

    def test_unknown_flag(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "-u", "https://example.com/", "--unknown"])
        with pytest.raises(SystemExit):
            parse_urls()

    def test_multiple_urls_via_repeated(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--url", "https://a.com/", "--url", "https://b.com/"])
        # argparse stores the last value; just verify no crash
        urls, _ = parse_urls()
        assert len(urls) == 1





# ===========================================================================
# URL VALIDATION
# ===========================================================================

class TestValidateUrl:
    def test_valid_webtoon_series(self):
        url = "https://www.webtoons.com/en/action/nano-machine/list?title_no=4344"
        result = normalize_url(url)
        assert "www.webtoons.com" in result
        assert "title_no=4344" in result

    def test_valid_webtoon_mobile(self):
        url = "https://m.webtoons.com/en/action/s/list?title_no=1"
        result = normalize_url(url)
        assert "webtoons.com" in result


class TestIsValidWebtoonUrl:
    def test_desktop_series(self):
        assert is_valid_webtoon_url(
            "https://www.webtoons.com/en/action/s/list?title_no=1"
        ) is True

    def test_mobile_series(self):
        assert is_valid_webtoon_url(
            "https://m.webtoons.com/en/action/s/list?title_no=1"
        ) is True

    def test_desktop_chapter(self):
        assert is_valid_webtoon_url(
            "https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1"
        ) is True

    def test_no_query(self):
        assert is_valid_webtoon_url("https://www.webtoons.com/en/action/s/list") is False

    def test_wrong_domain(self):
        assert is_valid_webtoon_url("https://example.com/list?title_no=1") is False

    def test_no_scheme(self):
        assert is_valid_webtoon_url("www.webtoons.com/en/action/s/list?title_no=1") is False


class TestIsValidEhentaiUrl:
    def test_valid(self):
        assert is_valid_ehentai_url("https://e-hentai.org/g/123456/abc123/") is True

    def test_valid_no_trailing_slash(self):
        assert is_valid_ehentai_url("https://e-hentai.org/g/123456/abc123") is True

    def test_invalid_no_match(self):
        assert is_valid_ehentai_url("https://example.com/") is False

    def test_no_scheme(self):
        assert is_valid_ehentai_url("e-hentai.org/g/123/abc") is False


class TestIsValidPawchiveUrl:
    def test_valid(self):
        assert is_valid_pawchive_url("https://pawchive.pw/p/user/1/post/2/") is True

    def test_invalid(self):
        assert is_valid_pawchive_url("https://example.com/") is False


# ===========================================================================
# WEBTOON PARSING
# ===========================================================================

class TestNormalizeEpisodeTitle:
    def test_basic_episode(self):
        assert _normalize_episode_title("Ep. 1") == "Ep. 1"

    def test_episode_with_title(self):
        assert _normalize_episode_title("Ep. 1 - Prologue") == "Ep. 1 - Prologue"

    def test_chapter_format(self):
        assert _normalize_episode_title("Chapter 1") == "Ep. 1"

    def test_chapter_with_title(self):
        assert _normalize_episode_title("Ch. 5 - The Beginning") == "Ep. 5 - The Beginning"

    def test_no_match_stays_unchanged(self):
        assert _normalize_episode_title("Prologue") == "Prologue"

    def test_trailing_badge_UP(self):
        assert _normalize_episode_title("Ep. 1 UP") == "Ep. 1"

    def test_trailing_badge_NEW(self):
        assert _normalize_episode_title("Ch. 3 - Title NEW") == "Ep. 3 - Title"

    def test_trailing_badge_HOT(self):
        assert _normalize_episode_title("Ep. 5 HOT") == "Ep. 5"

    def test_trailing_badge_heart(self):
        assert _normalize_episode_title("Ep. 2 ♥") == "Ep. 2"
        assert _normalize_episode_title("Ep. 2 ★") == "Ep. 2"

    def test_trailing_badge_like(self):
        assert _normalize_episode_title("Chapter 10 like") == "Ep. 10"

    def test_multiple_badges(self):
        assert _normalize_episode_title("Ep. 7 UP NEW") == "Ep. 7"

    def test_episode_case_insensitive(self):
        assert _normalize_episode_title("EPISODE 1") == "Ep. 1"

    def test_chapter_case_insensitive(self):
        assert _normalize_episode_title("chapter 2") == "Ep. 2"

    def test_episode_with_dash_and_badge(self):
        assert _normalize_episode_title("Ep. 10 - Title UP") == "Ep. 10 - Title"

    def test_badge_only_in_rest(self):
        result = _normalize_episode_title("Ep. 15 - The Adventure NEW")
        assert "NEW" not in result

    def test_trailing_dash_after_strip(self):
        assert _normalize_episode_title("Ep. 1 -") == "Ep. 1"


class TestStripTrailingNoise:
    def test_up(self):
        assert _strip_trailing_noise("Title UP") == "Title"

    def test_new(self):
        assert _strip_trailing_noise("Chapter NEW") == "Chapter"

    def test_hot(self):
        assert _strip_trailing_noise("Season 2 HOT") == "Season 2"

    def test_best(self):
        assert _strip_trailing_noise("Final BEST") == "Final"

    def test_heart(self):
        assert _strip_trailing_noise("Title ♥") == "Title"

    def test_no_noise(self):
        assert _strip_trailing_noise("Clean Title") == "Clean Title"

    def test_empty(self):
        assert _strip_trailing_noise("") == ""


# ===========================================================================
# WEBTOON SCRAPING (with mocked data)
# ===========================================================================

class TestScrapeWebtoonChapter:
    pytestmark = pytest.mark.asyncio

    async def test_invalid_url(self):
        with pytest.raises(ValueError, match="Invalid WEBTOON"):
            await scrape_chapter("https://example.com", None)

    async def test_no_images_raises(self):
        html = "<html><head></head><body></body></html>"

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    text = html
                    def raise_for_status(self):
                        pass
                return Resp()

        with pytest.raises(ValueError, match="No images found"):
            await scrape_chapter(
                "https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1",
                MockClient(),
            )


class TestScrapeWebtoonSeries:
    pytestmark = pytest.mark.asyncio

    async def test_invalid_url(self):
        with pytest.raises(ValueError, match="Invalid WEBTOON"):
            await scrape_series("https://example.com", None)

    async def test_no_chapters_raises(self):
        html = "<html><head></head><body></body></html>"

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    text = html
                    def raise_for_status(self):
                        pass
                return Resp()

        with pytest.raises(ValueError, match="No chapters found"):
            await scrape_series(
                "https://www.webtoons.com/en/action/s/list?title_no=1",
                MockClient(),
            )


# ===========================================================================
# CBZ INTEGRITY TESTS
# ===========================================================================

class TestCbzIntegrity:
    """Open generated CBZ files to verify they are valid and readable."""

    def test_cbz_is_valid_zip(self):
        import zipfile

        from comic_dl.utils import image_source_name
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "tmp"
            tmp.mkdir()
            (tmp / image_source_name(1, "http://x.com/1.jpg")).write_bytes(b'\xff\xd8\xff')
            (tmp / image_source_name(2, "http://x.com/2.png")).write_bytes(b'\x89PNG\r\n\x1a\n')
            images = [
                ImageItem(url="http://x.com/1.jpg", page_number=1, filename=image_source_name(1, "http://x.com/1.jpg")),
                ImageItem(url="http://x.com/2.png", page_number=2, filename=image_source_name(2, "http://x.com/2.png")),
            ]
            cbz = Path(td) / "test.cbz"
            create_archive(images, tmp, cbz, "S", "C")

            with zipfile.ZipFile(cbz, 'r') as zf:
                assert zf.testzip() is None
                names = zf.namelist()
                assert len(names) >= 3
                assert "ComicInfo.xml" in names

    def test_cbz_pages_in_correct_order(self):
        import zipfile

        from comic_dl.utils import image_source_name
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "tmp"
            tmp.mkdir()
            images = []
            for i in range(1, 4):
                url = f"http://x.com/{i}.jpg"
                src_name = image_source_name(i, url)
                (tmp / src_name).write_bytes(b'\xff\xd8\xff' + bytes([i]))
                images.append(
                    ImageItem(url=url, page_number=i, filename=src_name)
                )
            cbz = Path(td) / "test.cbz"
            create_archive(images, tmp, cbz, "S", "C")

            with zipfile.ZipFile(cbz, 'r') as zf:
                names = zf.namelist()
                img_names = [n for n in names if n != "ComicInfo.xml"]
                assert img_names == ["Page_0001.jpeg", "Page_0002.jpeg", "Page_0003.jpeg"]


# ===========================================================================
# E2E PROCESS URL (with mocked data)
# ===========================================================================

class TestProcessUrlE2E:
    pytestmark = pytest.mark.asyncio

    async def test_unsupported_url_does_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            status, _label = await process_url(
                url="https://example.com/bad",
                output_dir=Path(td),
                concurrency=5,

                force=False,
            )
        assert status == "failed"

    async def test_webtoon_chapter_creates_cbz(self, monkeypatch):
        async def mock_scrape(url, client):
            return PostMetadata(
                series_title="Test Series",
                chapter_title="Chapter 1",
                images=[
                    ImageItem(url="http://x.com/1", page_number=1, filename="page1.jpg"),
                    ImageItem(url="http://x.com/2", page_number=2, filename="page2.jpg"),
                ],
                total_pages=2,
                service="webtoons.com",
                user_id="1",
                post_id="1",
            )

        async def mock_download(images, dest_dir, *args, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b'\xff\xd8\xff')
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)

        class MockWebtoon:
            domain = "www.webtoons.com"
            async def scrape(self, url, client):
                return await mock_scrape(url, client)
        _patch_chapter_scraper(monkeypatch, {"webtoons.com": MockWebtoon()})

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            await process_url(
                url="https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1",
                output_dir=out,
                concurrency=5,

                force=False,
                quiet=True,
            )
            series_dir = out / "Test Series"
            assert series_dir.exists()
            cbz_files = list(series_dir.glob("*.cbz"))
            assert len(cbz_files) == 1

    async def test_skip_existing_cbz(self, monkeypatch):
        async def mock_scrape(url, client):
            return PostMetadata(
                series_title="S", chapter_title="C",
                images=[ImageItem(url="http://x.com/1", page_number=1, filename="p.jpg")],
            )

        class _Mock:
            async def scrape(self, url, client):
                return await mock_scrape(url, client)
        _patch_chapter_scraper(monkeypatch, {"pawchive.pw": _Mock()})

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            sdir = out / "S"
            sdir.mkdir(parents=True)
            cbz = sdir / "C.cbz"
            cbz.write_bytes(b"fake cbz")
            await process_url(
                url="https://pawchive.pw/p/user/1/post/2/",
                output_dir=out,
                concurrency=1,

                force=False,
                quiet=True,
            )
            assert cbz.read_bytes() == b"fake cbz"

    async def test_force_overwrites_cbz(self, monkeypatch):
        async def mock_scrape(url, client):
            return PostMetadata(
                series_title="S", chapter_title="C",
                images=[ImageItem(url="http://x.com/1", page_number=1, filename="p.jpg")],
            )

        async def mock_download(images, dest_dir, *args, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b'\xff\xd8\xff')
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)

        class _Mock:
            async def scrape(self, url, client):
                return await mock_scrape(url, client)
        _patch_chapter_scraper(monkeypatch, {"pawchive.pw": _Mock()})

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            sdir = out / "S"
            sdir.mkdir(parents=True)
            cbz = sdir / "C.cbz"
            cbz.write_bytes(b"old")
            await process_url(
                url="https://pawchive.pw/p/user/1/post/2/",
                output_dir=out,
                concurrency=1,

                force=True,
                quiet=True,
            )
            assert cbz.read_bytes() != b"old"


class TestTextOnlyPostE2E:
    pytestmark = pytest.mark.asyncio

    async def test_text_only_post_saves_markdown(self, monkeypatch):
        async def mock_scrape(url, client):
            return PostMetadata(
                series_title="S",
                chapter_title="Announcement",
                images=[],
                text_content="Hello everyone, Chapter 12 will be late.\nThanks!",
            )

        class _Mock:
            async def scrape(self, url, client):
                return await mock_scrape(url, client)
        _patch_chapter_scraper(monkeypatch, {"pawchive.pw": _Mock()})

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            status, label = await process_url(
                url="https://pawchive.pw/p/user/1/post/2/",
                output_dir=out,
                concurrency=1,
                force=False,
                quiet=True,
            )
            assert status == "downloaded"
            assert label == "Announcement.md"
            md = out / "S" / "Announcement.md"
            assert md.exists()
            text = md.read_text()
            assert "Chapter 12 will be late" in text
            assert text.startswith(
                f"<!-- source: {normalize_url('https://pawchive.pw/p/user/1/post/2/')} -->\n"
            )

            from comic_dl.cli import _build_downloaded_index
            index = _build_downloaded_index(out)
            assert index[normalize_url("https://pawchive.pw/p/user/1/post/2/")] == md

    async def test_text_only_post_skips_without_force(self, monkeypatch):
        async def mock_scrape(url, client):
            return PostMetadata(
                series_title="S",
                chapter_title="Announcement",
                images=[],
                text_content="new content",
            )

        class _Mock:
            async def scrape(self, url, client):
                return await mock_scrape(url, client)
        _patch_chapter_scraper(monkeypatch, {"pawchive.pw": _Mock()})

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            sdir = out / "S"
            sdir.mkdir(parents=True)
            md = sdir / "Announcement.md"
            md.write_text("old content")
            status, _label = await process_url(
                url="https://pawchive.pw/p/user/1/post/2/",
                output_dir=out,
                concurrency=1,
                force=False,
                quiet=True,
            )
            assert status == "skipped"
            assert md.read_text() == "old content"


# ===========================================================================
# WEBTOON SERIES PROCESSING (with mocked data)
# ===========================================================================

class TestProcessWebtoonSeriesE2E:
    pytestmark = pytest.mark.asyncio

    async def test_series_creates_multiple_cbz(self, monkeypatch):
        from comic_dl.models import SeriesMetadata

        chapters_data = [
            {"title": "Chapter 1", "episode_no": "1", "url": "https://webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1"},
            {"title": "Chapter 2", "episode_no": "2", "url": "https://webtoons.com/en/action/s/ep-2/viewer?title_no=1&episode_no=2"},
        ]

        async def mock_scrape_series(url, client):
            return SeriesMetadata(
                series_title="Test Series",
                description="A test",
                cover_url="",
                title_no="1",
                chapters=chapters_data,
            )

        async def mock_scrape_chapter(url, client):
            ep_no = "1" if "episode_no=1" in url else "2"
            return PostMetadata(
                series_title="Test Series",
                chapter_title=f"Chapter {ep_no}",
                images=[
                    ImageItem(url=f"http://x.com/{ep_no}/1", page_number=1, filename=f"p{ep_no}_1.jpg"),
                    ImageItem(url=f"http://x.com/{ep_no}/2", page_number=2, filename=f"p{ep_no}_2.jpg"),
                ],
                total_pages=2,
            )

        async def mock_download(*args, **kwargs):
            images = args[0]
            dest_dir = args[1]
            for img in images:
                (dest_dir / img.filename).write_bytes(b'\xff\xd8\xff')
            return set()

        class MockWebtoon:
            domain = "www.webtoons.com"
            async def scrape_series(self, url, client):
                return await mock_scrape_series(url, client)
            async def scrape(self, url, client):
                return await mock_scrape_chapter(url, client)
        mock_scraper = MockWebtoon()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)

        with tempfile.TemporaryDirectory() as td:
            await _process_series(
                mock_scraper,
                url="https://www.webtoons.com/en/action/s/list?title_no=1",
                output_dir=Path(td),
                concurrency=5,

                force=False,
                quiet=True,
            )
            series_dir = Path(td) / "Test Series"
            assert series_dir.exists()
            cbz_files = sorted(series_dir.glob("*.cbz"))
            assert len(cbz_files) == 2


class TestSeriesCoverAndNomedia:
    pytestmark = pytest.mark.asyncio

    async def test_series_writes_cover_and_nomedia(self, monkeypatch):
        from comic_dl.models import SeriesMetadata

        calls = []

        async def mock_download(*args, **kwargs):
            images = args[0]
            dest_dir = args[1]
            for img in images:
                (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        async def mock_cover(url, dest_path, **kwargs):
            calls.append(kwargs)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(b"coverdata")
            return True

        async def mock_scrape_series(url, client):
            return SeriesMetadata(
                series_title="Test Series",
                description="A test",
                cover_url="https://example.com/cover.jpg",
                title_no="1",
                chapters=[
                    {"title": "Chapter 1", "episode_no": "1",
                     "url": "https://webtoons.com/...ep-1..."},
                ],
            )

        async def mock_scrape_chapter(url, client):
            return PostMetadata(
                series_title="Test Series", chapter_title="Chapter 1",
                images=[ImageItem(url="http://x.com/1", page_number=1, filename="p.jpg")],
                total_pages=1,
            )

        class MockWebtoon:
            domain = "www.webtoons.com"
            async def scrape_series(self, url, client):
                return await mock_scrape_series(url, client)
            async def scrape(self, url, client):
                return await mock_scrape_chapter(url, client)

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)
        monkeypatch.setattr("comic_dl.cli.download_cover_to", mock_cover)

        with tempfile.TemporaryDirectory() as td:
            await _process_series(
                MockWebtoon(),
                url="https://www.webtoons.com/en/action/s/list?title_no=1",
                output_dir=Path(td),
                concurrency=5,
                force=False,
                quiet=True,
            )
            series_dir = Path(td) / "Test Series"
            assert (series_dir / "cover.jpg").read_bytes() == b"coverdata"
            assert (series_dir / "ComicInfo.xml").exists()
            assert (Path(td) / ".nomedia").exists()
            assert calls and calls[0]["force"] is False

    async def test_process_url_writes_cover_and_nomedia(self, monkeypatch):
        calls = []

        async def mock_cover(url, dest_path, **kwargs):
            calls.append(kwargs)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(b"coverdata")
            return True

        async def mock_scrape(url, client):
            return PostMetadata(
                series_title="S", chapter_title="C",
                cover_url="https://example.com/cover.jpg",
                images=[ImageItem(url="http://x.com/1", page_number=1, filename="p.jpg")],
                total_pages=1,
            )

        class MockWebtoon:
            domain = "www.webtoons.com"
            async def scrape(self, url, client):
                return await mock_scrape(url, client)

        _patch_chapter_scraper(monkeypatch, {"webtoons.com": MockWebtoon()})
        monkeypatch.setattr("comic_dl.cli.download_cover_to", mock_cover)

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            await process_url(
                url="https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1",
                output_dir=out,
                concurrency=5,
                force=False,
                quiet=True,
            )
            series_dir = out / "S"
            assert (series_dir / "cover.jpg").read_bytes() == b"coverdata"
            assert (series_dir / "ComicInfo.xml").exists()
            assert (out / ".nomedia").exists()
            assert calls and calls[0]["force"] is False


class TestSeriesSameTitleCollision:
    """Two chapters sharing a title but with different post IDs must both
    download (disambiguated by post_id), and a re-run must be idempotent.
    """

    pytestmark = pytest.mark.asyncio

    URL_A = "https://fsicomics.com/alien-abduction-chapter-3-arisane/"
    URL_B = "https://fsicomics.com/alien-abduction-chapter-3-arisane-2/"

    def _chapters(self):
        return [
            {"title": "Chapter 3", "episode_no": "1", "url": self.URL_A},
            {"title": "Chapter 3", "episode_no": "2", "url": self.URL_B},
        ]

    def _make_scraper(self):
        from comic_dl.models import SeriesMetadata

        async def mock_scrape_series(url, client):
            return SeriesMetadata(
                series_title="Test Series",
                description="A test",
                cover_url="",
                title_no="1",
                chapters=self._chapters(),
            )

        async def mock_scrape_chapter(url, client):
            post_id = "828653" if url == self.URL_A else "828652"
            return PostMetadata(
                series_title="Test Series",
                chapter_title="Chapter 3",
                images=[
                    ImageItem(url=f"http://x.com/{post_id}/1", page_number=1, filename=f"p{post_id}_1.jpg"),
                    ImageItem(url=f"http://x.com/{post_id}/2", page_number=2, filename=f"p{post_id}_2.jpg"),
                ],
                total_pages=2,
                post_id=post_id,
            )

        class MockFsicomix:
            domain = "fsicomics.com"

            async def scrape_series(self, url, client):
                return await mock_scrape_series(url, client)

            async def scrape(self, url, client):
                return await mock_scrape_chapter(url, client)

        return MockFsicomix()

    async def _run(self, monkeypatch, tmp_path, scraper):
        async def mock_download(*args, **kwargs):
            images = args[0]
            dest_dir = args[1]
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b'\xff\xd8\xff')
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)
        return await _process_series(
            scraper,
            url="https://fsicomics.com/all-porn-comics/s/",
            output_dir=tmp_path,
            concurrency=1,
            force=False,
            quiet=True,
        )

    async def test_same_title_chapters_download_to_distinct_files(self, monkeypatch, tmp_path):
        ok = await self._run(monkeypatch, tmp_path, self._make_scraper())
        assert ok
        cbz_files = sorted(f.name for f in (tmp_path / "Test Series").glob("*.cbz"))
        # The base-named chapter and the post-ID-disambiguated chapter are
        # both written; which chapter claims the base name depends on
        # processing order, so only the invariant is asserted.
        assert len(cbz_files) == 2
        assert cbz_files[-1] == "Chapter 3.cbz"
        assert cbz_files[0] in ("Chapter 3 (828652).cbz", "Chapter 3 (828653).cbz")

    async def test_rerun_is_idempotent(self, monkeypatch, tmp_path):
        scraper = self._make_scraper()
        assert await self._run(monkeypatch, tmp_path, scraper)
        assert await self._run(monkeypatch, tmp_path, scraper)

        cbz_files = sorted(f.name for f in (tmp_path / "Test Series").glob("*.cbz"))
        assert len(cbz_files) == 2
        assert cbz_files[-1] == "Chapter 3.cbz"
        assert cbz_files[0] in ("Chapter 3 (828652).cbz", "Chapter 3 (828653).cbz")


# ===========================================================================
# INCREMENTAL SERIES UPDATES (SQLite library)
# ===========================================================================

class TestSeriesIncrementalUpdates:
    """Repeated series runs must reuse prior downloads via the library DB,
    skip scraping existing chapters, and honour --force."""

    pytestmark = pytest.mark.asyncio

    URL_1 = "https://fsicomics.com/series-ep-1/"
    URL_2 = "https://fsicomics.com/series-ep-2/"

    def _chapters(self):
        return [
            {"title": "Chapter 1", "episode_no": "1", "url": self.URL_1},
            {"title": "Chapter 2", "episode_no": "2", "url": self.URL_2},
        ]

    def _make_scraper(self, scrape_log):
        from comic_dl.models import SeriesMetadata

        async def mock_scrape_series(url, client):
            return SeriesMetadata(
                series_title="Test Series",
                description="A test",
                cover_url="",
                title_no="1",
                chapters=self._chapters(),
            )

        async def mock_scrape_chapter(url, client):
            scrape_log.append(url)
            ep = "1" if url == self.URL_1 else "2"
            return PostMetadata(
                series_title="Test Series",
                chapter_title=f"Chapter {ep}",
                images=[
                    ImageItem(url=f"http://x.com/{ep}/1", page_number=1, filename=f"p{ep}_1.jpg"),
                    ImageItem(url=f"http://x.com/{ep}/2", page_number=2, filename=f"p{ep}_2.jpg"),
                ],
                total_pages=2,
                post_id=ep,
            )

        class MockFsicomix:
            domain = "fsicomics.com"

            async def scrape_series(self, url, client):
                return await mock_scrape_series(url, client)

            async def scrape(self, url, client):
                return await mock_scrape_chapter(url, client)

        return MockFsicomix()

    async def _run(self, monkeypatch, tmp_path, scraper, force=False):
        async def mock_download(*args, **kwargs):
            images = args[0]
            dest_dir = args[1]
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b'\xff\xd8\xff')
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)
        return await _process_series(
            scraper,
            url="https://fsicomics.com/all-porn-comics/s/",
            output_dir=tmp_path,
            concurrency=1,
            force=force,
            quiet=True,
        )

    async def test_first_run_downloads_all_and_records(self, monkeypatch, tmp_path):
        log = []
        scraper = self._make_scraper(log)
        assert await self._run(monkeypatch, tmp_path, scraper)
        assert len(list((tmp_path / "Test Series").glob("*.cbz"))) == 2

        db = tmp_path / ".comic-dl" / "library.db"
        assert db.exists()
        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute(
                "SELECT url, cbz FROM chapters ORDER BY url"
            ).fetchall()
            series = conn.execute(
                "SELECT series_id, source_site FROM series"
            ).fetchall()
        assert len(rows) == 2
        assert {r[0] for r in rows} == {
            "https://fsicomics.com/series-ep-1",
            "https://fsicomics.com/series-ep-2",
        }
        assert series == [("fsicomics.com:1", "fsicomics.com")]

    async def test_rerun_downloads_nothing_and_skips_scraping(self, monkeypatch, tmp_path):
        log = []
        scraper = self._make_scraper(log)
        assert await self._run(monkeypatch, tmp_path, scraper)
        assert len(log) == 2

        assert await self._run(monkeypatch, tmp_path, scraper)
        assert len(log) == 2  # existing chapters were never scraped again
        assert len(list((tmp_path / "Test Series").glob("*.cbz"))) == 2

    async def test_force_redownloads_even_when_recorded(self, monkeypatch, tmp_path):
        log = []
        scraper = self._make_scraper(log)
        assert await self._run(monkeypatch, tmp_path, scraper, force=True)
        assert await self._run(monkeypatch, tmp_path, scraper, force=True)
        assert len(log) == 4  # --force bypasses the have set
        assert len(list((tmp_path / "Test Series").glob("*.cbz"))) == 2

    async def test_preseeded_cbz_reconciled_without_db(self, monkeypatch, tmp_path):
        series_dir = tmp_path / "Test Series"
        series_dir.mkdir(parents=True)
        with zipfile.ZipFile(series_dir / "Chapter 1.cbz", "w") as zf:
            zf.writestr(
                "ComicInfo.xml",
                f"<ComicInfo><Title>T</Title><Web>{self.URL_1}</Web></ComicInfo>",
            )

        log = []
        scraper = self._make_scraper(log)
        assert await self._run(monkeypatch, tmp_path, scraper)
        assert len(log) == 1  # only Chapter 2 was scraped
        assert len(list(series_dir.glob("*.cbz"))) == 2

    async def test_update_downloads_new_chapters_only(self, monkeypatch, tmp_path, capsys):
        from comic_dl import cli as cli_mod
        from comic_dl.models import SeriesMetadata

        log = []
        scraper = self._make_scraper(log)
        assert await self._run(monkeypatch, tmp_path, scraper)
        assert len(list((tmp_path / "Test Series").glob("*.cbz"))) == 2
        log.clear()

        def chapters_with_new():
            return [*self._chapters(), {"title": "Chapter 3", "episode_no": "3", "url": "https://fsicomics.com/series-ep-3/"}]

        async def mock_scrape_chapter(url, client):
            log.append(url)
            ep = url.rstrip("/").rsplit("-", 1)[1]
            return PostMetadata(
                series_title="Test Series",
                chapter_title=f"Chapter {ep}",
                images=[
                    ImageItem(
                        url=f"http://x.com/{ep}/1",
                        page_number=1,
                        filename=f"p{ep}_1.jpg",
                    )
                ],
                total_pages=1,
                post_id=ep,
            )

        async def mock_scrape_series(url, client):
            return SeriesMetadata(
                series_title="Test Series",
                description="",
                cover_url="",
                title_no="1",
                chapters=chapters_with_new(),
            )

        class Mock3:
            domain = "fsicomics.com"

            async def scrape_series(self, url, client):
                return await mock_scrape_series(url, client)

            async def scrape(self, url, client):
                return await mock_scrape_chapter(url, client)

        async def dummy_download(*args, **kwargs):
            images = args[0]
            dest_dir = args[1]
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", dummy_download)
        monkeypatch.setattr(
            "comic_dl.cli.get_series_scraper",
            lambda domain: Mock3(),
        )

        code = await cli_mod._run_update(["-o", str(tmp_path), "all"])
        assert code == 0
        # Only the newly-released chapter was fetched and saved.
        assert log == ["https://fsicomics.com/series-ep-3/"]
        assert len(list((tmp_path / "Test Series").glob("*.cbz"))) == 3
        out = capsys.readouterr().out
        assert "1 had new chapters" in out

        with sqlite3.connect(str(tmp_path / ".comic-dl" / "library.db")) as conn:
            rows = conn.execute(
                "SELECT url FROM chapters ORDER BY url"
            ).fetchall()
        assert len(rows) == 3


# ===========================================================================
# CHAPTER SELECTION (--chapters flag + interactive checkbox path)
# ===========================================================================

class TestSeriesChapterSelection:
    """Chapter selection must filter downloads for a 4-chapter mock series."""

    pytestmark = pytest.mark.asyncio

    URLS: tuple[str, ...] = tuple(
        f"https://fsicomics.com/series-ep-{i}/" for i in range(1, 5)
    )

    def _chapters(self):
        return [
            {"title": f"Chapter {i}", "episode_no": str(i), "url": self.URLS[i - 1]}
            for i in range(1, 5)
        ]

    def _make_scraper(self, scrape_log):
        from comic_dl.models import SeriesMetadata

        async def mock_scrape_series(url, client):
            return SeriesMetadata(
                series_title="Test Series",
                description="",
                cover_url="",
                title_no="1",
                chapters=self._chapters(),
            )

        async def mock_scrape_chapter(url, client):
            scrape_log.append(url)
            ep = self.URLS.index(url) + 1
            return PostMetadata(
                series_title="Test Series",
                chapter_title=f"Chapter {ep}",
                images=[
                    ImageItem(url=f"http://x.com/{ep}/1", page_number=1, filename=f"p{ep}_1.jpg"),
                    ImageItem(url=f"http://x.com/{ep}/2", page_number=2, filename=f"p{ep}_2.jpg"),
                ],
                total_pages=2,
                post_id=str(ep),
            )

        class MockFsicomix:
            domain = "fsicomics.com"

            async def scrape_series(self, url, client):
                return await mock_scrape_series(url, client)

            async def scrape(self, url, client):
                return await mock_scrape_chapter(url, client)

        return MockFsicomix()

    def _mock_download(self, monkeypatch):
        async def mock_download(images, dest_dir, *args, **kwargs):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)

    async def _run(self, monkeypatch, tmp_path, log, **kwargs):
        self._mock_download(monkeypatch)
        kwargs.setdefault("force", False)
        kwargs.setdefault("quiet", True)
        return await _process_series(
            self._make_scraper(log),
            url="https://fsicomics.com/all-porn-comics/s/",
            output_dir=tmp_path,
            concurrency=1,
            **kwargs,
        )

    async def test_flag_selects_subset(self, monkeypatch, tmp_path):
        log = []
        ok = await self._run(monkeypatch, tmp_path, log, chapters_spec="2,4")
        assert ok
        assert sorted(log) == sorted([self.URLS[1], self.URLS[3]])
        cbz = sorted(f.name for f in (tmp_path / "Test Series").glob("*.cbz"))
        assert cbz == ["Chapter 2.cbz", "Chapter 4.cbz"]

    async def test_flag_range(self, monkeypatch, tmp_path):
        log = []
        ok = await self._run(monkeypatch, tmp_path, log, chapters_spec="1-3")
        assert ok
        assert sorted(log) == sorted(self.URLS[:3])
        assert len(list((tmp_path / "Test Series").glob("*.cbz"))) == 3

    async def test_flag_all_keyword_downloads_everything(self, monkeypatch, tmp_path):
        log = []
        ok = await self._run(monkeypatch, tmp_path, log, chapters_spec="all")
        assert ok
        assert len(log) == 4

    async def test_force_with_selection(self, monkeypatch, tmp_path):
        log = []
        ok = await self._run(monkeypatch, tmp_path, log, force=True, chapters_spec="3")
        assert ok
        assert log == [self.URLS[2]]
    async def test_interactive_cancel_writes_nothing(self, monkeypatch, tmp_path):
        log = []
        monkeypatch.setattr("comic_dl.cli._prompt_chapter_selection", lambda *a, **k: None)
        with pytest.raises(ChapterSelectionQuit):
            await self._run(monkeypatch, tmp_path, log, interactive=True)
        assert log == []
        assert not (tmp_path / "Test Series").exists()
        db = tmp_path / ".comic-dl" / "library.db"
        if db.exists():
            with sqlite3.connect(str(db)) as conn:
                assert conn.execute("SELECT * FROM series").fetchall() == []

    async def test_interactive_selection_filters(self, monkeypatch, tmp_path):
        log = []
        monkeypatch.setattr("comic_dl.cli._prompt_chapter_selection", lambda *a, **k: {2, 4})
        ok = await self._run(monkeypatch, tmp_path, log, interactive=True)
        assert ok
        assert sorted(log) == sorted([self.URLS[1], self.URLS[3]])

    async def test_interactive_empty_selection_skips(self, monkeypatch, tmp_path):
        log = []
        monkeypatch.setattr("comic_dl.cli._prompt_chapter_selection", lambda *a, **k: set())
        ok = await self._run(monkeypatch, tmp_path, log, interactive=True)
        assert ok
        assert log == []
        assert not (tmp_path / "Test Series").exists()

    async def test_flag_wins_over_prompt(self, monkeypatch, tmp_path):
        log = []
        called = {"prompt": False}

        def fake_prompt(*a, **k):
            called["prompt"] = True
            return {1, 2, 3, 4}

        monkeypatch.setattr("comic_dl.cli._prompt_chapter_selection", fake_prompt)
        ok = await self._run(
            monkeypatch, tmp_path, log, chapters_spec="2", interactive=True,
        )
        assert ok
        assert not called["prompt"]
        assert log == [self.URLS[1]]


# ===========================================================================
# PROGRESS BAR UI TESTS
# ===========================================================================

class TestMakeDownloadProgress:
    def test_columns_configured(self):
        progress = make_download_progress()
        columns = progress.columns
        col_types = [type(c).__name__ for c in columns]
        assert "TextColumn" in col_types
        assert "BarColumn" in col_types
        assert "TaskProgressColumn" not in col_types
        # The page tally and inline bytes/speed/ETA stats are handled by the
        # compact bar, so no duplicate percentage column is configured.
        assert len(col_types) == 3

    def test_eta_column_hides_on_complete(self):

        from rich.progress import Progress
        p = Progress()
        column = ETA()
        with p:
            task_id = p.add_task("test", total=10)
            p.update(task_id, completed=10)
            task = p._tasks[task_id]
            result = column.render(task)
            assert result.plain == ""

    def test_eta_column_shows_during_progress(self):

        from rich.progress import Progress
        p = Progress()
        column = ETA()
        with p:
            task_id = p.add_task("test", total=10)
            p.update(task_id, completed=5)
            task = p._tasks[task_id]
            # Force a speed by sleeping so Rich has time data
            result = column.render(task)
            assert isinstance(result.plain, str)


class TestMakeSpinner:
    def test_spinner_created(self):
        spinner = make_spinner()
        assert spinner is not None


# ===========================================================================
# ERROR HANDLING - NETWORK FAILURE SIMULATION
# ===========================================================================

class TestNetworkErrorHandling:
    pytestmark = pytest.mark.asyncio

    async def test_http_404_error(self):
        """404 should NOT be retried; file should be added to failed set."""
        class MockResponse:
            status_code = 404
            headers = {}
            def raise_for_status(self):
                raise CurlHTTPError("404")
            async def aiter_content(self, chunk_size=None):
                yield b''
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                return MockResponse()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        images = [ImageItem(url="http://x.com/img", page_number=1, filename="fail.jpg")]
        with tempfile.TemporaryDirectory() as td:
            failed = await download_httpx(images, Path(td), concurrency=1, client=MockClient())
            assert "fail.jpg" in failed

    async def test_http_429_retry_then_succeed(self):
        from curl_cffi.requests import Response as CurlResponse
        call_count = [0]

        class MockResponse:
            status_code = 200
            headers = {"content-length": "3", "content-type": "image/jpeg"}
            def raise_for_status(self):
                call_count[0] += 1
                if call_count[0] == 1:
                    resp = CurlResponse()
                    resp.status_code = 429
                    raise CurlHTTPError("too many", response=resp)
            async def aiter_content(self, chunk_size=None):
                yield b'\xff\xd8\xff'
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                return MockResponse()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        images = [ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg")]
        with tempfile.TemporaryDirectory() as td:
            failed = await download_httpx(images, Path(td), concurrency=1, client=MockClient())
            assert failed == set()
            assert (Path(td) / "test.jpg").exists()
            assert call_count[0] == 2  # first attempt 429, second succeeds


# ===========================================================================
# EDGE CASES
# ===========================================================================

class TestEdgeCases:
    def test_zero_byte_image_handling(self):
        """Zero-byte files should be detected and removed."""
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "empty.jpg").write_bytes(b"")
            images = [ImageItem(url="http://x.com/1", page_number=1, filename="empty.jpg")]
            errors, _ = verify_downloads(images, src)
            assert "empty.jpg" in errors
            assert not (src / "empty.jpg").exists()

    def test_html_error_page_detection(self):
        """HTML served as an image should be caught by verify_image_file."""
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "error.html").write_bytes(b"<html>404 Not Found</html>")
            fmt = verify_image_file(src / "error.html")
            assert fmt is None


# ===========================================================================
# PERFORMANCE / SCALING CHECKS
# ===========================================================================

class TestPerformance:
    def test_sanitize_filename_long_input(self):
        """Should handle very long filenames without crashing."""
        long = "a" * 10000
        result = sanitize_filename(long)
        assert len(result) <= 255

    def test_large_image_list_verify(self):
        """Verify_downloads should handle many images efficiently."""
        images = [
            ImageItem(url=f"http://x.com/{i}", page_number=i, filename=f"p{i}.jpg")
            for i in range(1000)
        ]
        with tempfile.TemporaryDirectory() as td:
            errors, _ = verify_downloads(images, Path(td))
            assert len(errors) == 1000

    def test_create_archive_many_pages(self):
        """Creating a CBZ with many pages should not crash."""
        from comic_dl.utils import image_source_name
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "tmp"
            tmp.mkdir()
            images = []
            for i in range(1, 101):
                url = f"http://x.com/{i}.jpg"
                src_name = image_source_name(i, url)
                (tmp / src_name).write_bytes(b'\xff\xd8\xff' + bytes([i % 256]) + bytes([i // 256]))
                images.append(
                    ImageItem(url=url, page_number=i, filename=src_name)
                )
            cbz = Path(td) / "test.cbz"
            added, skipped = create_archive(images, tmp, cbz, "S", "C")
            assert added == 100
            assert skipped == []


# ===========================================================================
# SERIES PARTIAL-CHAPTER ACCOUNTING
# ===========================================================================

class TestSeriesPartialChapterAccounting:
    """A chapter whose CBZ is saved but missing pages must be classified as
    "partial", never as a plain failure (regression for the CLI report
    "Downloaded : 0 chapters (N failed)" after a partial chapter landed)."""

    pytestmark = pytest.mark.asyncio

    URL = "https://fsicomics.com/series/s/"

    def _make_scraper(self):
        from comic_dl.models import SeriesMetadata

        async def mock_scrape_series(url, client):
            return SeriesMetadata(
                series_title="Test Series",
                description="A test",
                cover_url="",
                title_no="1",
                chapters=[
                    {"title": "Chapter 1", "episode_no": "1",
                     "url": "https://fsicomics.com/ep-1/"},
                    {"title": "Chapter 2", "episode_no": "2",
                     "url": "https://fsicomics.com/ep-2/"},
                ],
            )

        async def mock_scrape_chapter(url, client):
            ep = "1" if "ep-1" in url else "2"
            return PostMetadata(
                series_title="Test Series",
                chapter_title=f"Chapter {ep}",
                images=[
                    ImageItem(url=f"http://x.com/{ep}/1", page_number=1, filename=f"p{ep}_1.jpg"),
                    ImageItem(url=f"http://x.com/{ep}/2", page_number=2, filename=f"p{ep}_2.jpg"),
                ],
                total_pages=2,
                post_id=ep,
            )

        class MockFsicomix:
            domain = "fsicomics.com"

            async def scrape_series(self, url, client):
                return await mock_scrape_series(url, client)

            async def scrape(self, url, client):
                return await mock_scrape_chapter(url, client)

        return MockFsicomix()

    async def _run(self, monkeypatch, tmp_path, *, quiet=True):
        async def mock_download(*args, **kwargs):
            images, dest_dir = args[0], args[1]
            dest_dir.mkdir(parents=True, exist_ok=True)
            # First page of each chapter lands; the second page fails, which
            # the verify step reports as missing.
            for i, img in enumerate(images):
                if i == 0:
                    (dest_dir / img.filename).write_bytes(b"\xff\xd8\xff")
            return set()

        monkeypatch.setattr("comic_dl.downloader.download_httpx", mock_download)
        from comic_dl.cli import DownloadStats

        stats = DownloadStats()
        ok = await _process_series(
            self._make_scraper(),
            url=self.URL,
            output_dir=tmp_path,
            concurrency=1,
            force=False,
            quiet=quiet,
            stats=stats,
        )
        return ok, stats

    async def test_partial_series_accounted_distinctly(self, monkeypatch, tmp_path):
        ok, stats = await self._run(monkeypatch, tmp_path)
        assert not ok
        # Every chapter saved a CBZ but dropped one page → every chapter is
        # partial.  None may be counted as a completed download.
        assert stats.chapters_downloaded == 0
        assert stats.chapters_partial == 2
        series = tmp_path / "Test Series"
        assert (series / "Chapter 1.cbz").exists()
        assert (series / "Chapter 1.cbz.partial").exists()
        assert (series / "Chapter 2.cbz").exists()
        assert (series / "Chapter 2.cbz.partial").exists()

    async def test_summary_shows_partial_not_success(
        self, monkeypatch, tmp_path, capsys
    ):
        ok, _stats = await self._run(monkeypatch, tmp_path, quiet=False)
        assert not ok
        captured = capsys.readouterr()
        assert "(2 partial)" in captured.out
        assert "Download incomplete" in captured.out
        assert "Downloaded : 0 chapters" in captured.out

    async def test_summary_loses_success_verdict_on_interrupt(
        self, monkeypatch, tmp_path, capsys
    ):
        """A graceful stop mid-download must not print 'Download complete'."""
        request_stop()
        try:
            _ok, _stats = await self._run(monkeypatch, tmp_path, quiet=False)
        finally:
            reset_stop()
        captured = capsys.readouterr()
        assert "Interrupted" in captured.out
        assert "Download complete" not in captured.out
