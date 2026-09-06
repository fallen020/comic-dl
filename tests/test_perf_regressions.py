"""Performance regression smoke tests (offline, no wall-clock assertions).

The Phase 1-2 optimizations (cover session reuse, verify-once, scrape-cache
hits) are *behavioral contracts*, not timings — each can be locked with a
cheap invariant that holds without a network or a stopwatch. These tests fail
loudly if a future change reintroduces the waste they guard against:

- ``perf`` marker: registered in ``pyproject.toml``; never asserted on wall
  clock, so CI stays deterministic.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from comic_dl.downloader import download_cover_to, verify_downloads
from comic_dl.models import ImageItem

pytestmark = pytest.mark.perf


def _cover_response(status: int, payload: bytes = b""):
    class MockResponse:
        status_code = status
        headers = {}

        async def aiter_content(self, chunk_size=None):
            yield payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aclose(self):
            pass

    return MockResponse()


class TestCoverSessionReuse:
    """Covers must reuse an existing session, never create a fresh one."""

    pytestmark = [pytest.mark.perf, pytest.mark.asyncio]

    async def test_cover_with_client_creates_no_new_session(self, monkeypatch):
        """Passing a batch client must never fall back to the shared lazy
        session (or spin up a new AsyncSession) — that would pay connection-
        pool warm-up per cover."""

        def _boom():
            raise AssertionError("cover opened a new/shared session")

        monkeypatch.setattr("comic_dl.downloader._shared_cover_client", _boom)

        class MockClient:
            def stream(self, method, url, **kwargs):
                return _cover_response(200, payload=b"\xff\xd8\xff")

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "cover.jpg"
            written = await download_cover_to(
                "https://kagane.to/cover.jpg", dest, client=MockClient()
            )
            assert written is True
            assert dest.exists()

    async def test_cover_without_client_uses_shared_session(self, monkeypatch):
        """The no-client path must go through one process-wide shared session
        (Phase 1: ``_shared_cover_client``), not one session per cover."""

        seen: list[str] = []

        class MockClient:
            def stream(self, method, url, **kwargs):
                seen.append(url)
                return _cover_response(200, payload=b"\xff\xd8\xff")

        monkeypatch.setattr(
            "comic_dl.downloader._shared_cover_client", lambda: MockClient()
        )
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "cover.jpg"
            written = await download_cover_to("https://kagane.to/cover.jpg", dest)
            assert written is True
            assert seen == ["https://kagane.to/cover.jpg"]


class TestCacheHitSkipsNetwork:
    """A fresh cache hit must serve from disk with zero network requests."""

    pytestmark = pytest.mark.perf

    @pytest.fixture(autouse=True)
    def _isolated_cache(self, tmp_path):
        from comic_dl import cache

        cache.set_cache_dir(tmp_path / "http")
        yield
        cache.set_cache_dir(None)

    async def test_fresh_cache_hit_makes_zero_requests(self):
        from comic_dl.scrapers.base import BaseScraper

        url = "https://kagane.to/manga/foo"

        class Resp:
            status_code = 200
            text = "<html>cached page</html>"
            content = text.encode()
            headers = {"content-type": "text/html"}

            def raise_for_status(self):
                pass

        calls: list[str] = []

        class MockClient:
            async def get(self, url, **kwargs):
                calls.append(url)
                return Resp()

        first = await BaseScraper._timeout_get(url, MockClient())
        assert first.text == "<html>cached page</html>"
        assert len(calls) == 1
        # A second (fresh) hit must not touch the network at all.
        second = await BaseScraper._timeout_get(url, MockClient())
        assert second.text == "<html>cached page</html>"
        assert len(calls) == 1


class TestVerifyOnce:
    """Files whose magic bytes were sniffed during the stream must not be
    re-read by verify_downloads."""

    pytestmark = pytest.mark.perf
    MAGIC_JPEG = b"\xff\xd8\xff"

    def test_known_formats_skip_header_reread(self, monkeypatch):
        def _boom(path):
            raise AssertionError(f"verify_downloads re-read a known file: {path}")

        monkeypatch.setattr("comic_dl.downloader.verify_image_file", _boom)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            (dest / "page_0001.jpg").write_bytes(self.MAGIC_JPEG)
            (dest / "page_0002.jpg").write_bytes(self.MAGIC_JPEG)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename="page_0001.jpg"),
                ImageItem(url="http://x.com/2", page_number=2, filename="page_0002.jpg"),
            ]
            errors, verified = verify_downloads(
                images,
                dest,
                known_formats={"page_0001.jpg": "jpeg", "page_0002.jpg": "jpeg"},
            )
            assert errors == {}
            assert verified == {"page_0001.jpg": "jpeg", "page_0002.jpg": "jpeg"}

    def test_unknown_file_still_verified(self, monkeypatch):
        """Files NOT in known_formats still get the header re-read — the
        verify-once optimization must not weaken verification for resumes
        or other paths that skipped the stream sniff."""
        from comic_dl.downloader import verify_image_file

        called: list[Path] = []
        real = verify_image_file

        def _spy(path):
            called.append(Path(path))
            return real(path)

        monkeypatch.setattr("comic_dl.downloader.verify_image_file", _spy)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            (dest / "page_0001.jpg").write_bytes(self.MAGIC_JPEG)
            (dest / "page_0002.jpg").write_bytes(self.MAGIC_JPEG)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename="page_0001.jpg"),
                ImageItem(url="http://x.com/2", page_number=2, filename="page_0002.jpg"),
            ]
            errors, verified = verify_downloads(images, dest, known_formats={})
            assert errors == {}
            assert set(verified) == {"page_0001.jpg", "page_0002.jpg"}
            assert len(called) == 2  # both fell through to the real verifier
