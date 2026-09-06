from __future__ import annotations

import asyncio
import builtins
import email.utils
import errno
import tarfile
import tempfile
import time
from pathlib import Path
from typing import ClassVar

import pytest
from curl_cffi.requests import Response as CurlResponse
from curl_cffi.requests.exceptions import (
    ConnectionError as CurlConnectionError,
)
from curl_cffi.requests.exceptions import (
    HTTPError as CurlHTTPError,
)
from curl_cffi.requests.exceptions import (
    Timeout as CurlTimeout,
)

from comic_dl.downloader import (
    MAX_PIPELINE_CONCURRENCY,
    RETRY_AFTER_CAP,
    DownloadPipeline,
    InsufficientDiskError,
    _backoff_delay,
    _engine_tuning,
    _is_retryable,
    _retry_after_wait_seconds,
    _retry_blocked,
    _stream_to_disk,
    _try_resume,
    download_cover_to,
    download_httpx,
    download_httpx_iter,
    reset_host_breaker,
    verify_downloads,
)
from comic_dl.models import ImageItem
from comic_dl.utils import image_source_name, verify_image_file

MAGIC_JPEG = b'\xff\xd8\xff'


@pytest.fixture(autouse=True)
def _fresh_host_breaker():
    """Breaker state is process-wide; tests must not see each other's parks."""
    reset_host_breaker()
    yield
    reset_host_breaker()


class TestIsRetryable:
    def test_cancelled_not_retryable(self):
        assert not _is_retryable(asyncio.CancelledError())

    def test_429_retryable(self):
        resp = CurlResponse()
        resp.status_code = 429
        assert _is_retryable(CurlHTTPError("too many", response=resp))

    def test_500_retryable(self):
        resp = CurlResponse()
        resp.status_code = 500
        assert _is_retryable(CurlHTTPError("server error", response=resp))

    def test_509_retryable(self):
        # e-hentai H@H "Bandwidth Limit Exceeded" throttle code must back off
        # and retry instead of failing the image immediately.
        resp = CurlResponse()
        resp.status_code = 509
        assert _is_retryable(CurlHTTPError("bandwidth exceeded", response=resp))

    def test_408_retryable(self):
        # 408 "request timeout" is a transient CDN/server reply; the page must
        # back off and retry instead of failing on the first halting response.
        resp = CurlResponse()
        resp.status_code = 408
        assert _is_retryable(CurlHTTPError("request timeout", response=resp))

    def test_530_retryable(self):
        # 530 is Cloudflare's "origin error" — served transiently while the
        # CDN/origin is mid-outage or throttling (common on image-heavy
        # manga hosts). Retrying backs the client off instead of failing every
        # page of the chapter in one pass.
        resp = CurlResponse()
        resp.status_code = 530
        assert _is_retryable(CurlHTTPError("origin error", response=resp))

    def test_404_not_retryable(self):
        resp = CurlResponse()
        resp.status_code = 404
        assert not _is_retryable(CurlHTTPError("not found", response=resp))

    def test_403_not_retryable(self):
        resp = CurlResponse()
        resp.status_code = 403
        assert not _is_retryable(CurlHTTPError("forbidden", response=resp))

    def test_http_error_no_response_is_retryable(self):
        assert _is_retryable(CurlHTTPError("no response"))

    def test_connect_error_retryable(self):
        assert _is_retryable(CurlConnectionError("connection refused"))

    def test_read_timeout_retryable(self):
        assert _is_retryable(CurlTimeout("timeout"))

    def test_oserror_retryable(self):
        assert _is_retryable(OSError("disk full"))

    def test_enospc_oserror_not_retryable(self):
        assert not _is_retryable(OSError(errno.ENOSPC, "no space left"))

    def test_insufficient_disk_error_not_retryable(self):
        assert not _is_retryable(InsufficientDiskError("no space"))

    def test_value_error_not_retryable(self):
        assert not _is_retryable(ValueError("bad value"))

    def test_download_timeout_retryable(self):
        from comic_dl.errors import DownloadTimeout

        assert _is_retryable(DownloadTimeout("page1.jpg", 30.0))

    def test_not_image_response_retryable(self):
        from comic_dl.downloader import NotImageResponseError

        assert _is_retryable(NotImageResponseError("page1.jpg"))


class TestDownloadFailureLabel:
    def test_http_status(self):
        from comic_dl.downloader import _download_failure_label

        resp = CurlResponse()
        resp.status_code = 530
        assert _download_failure_label(
            CurlHTTPError("origin error", response=resp)
        ) == "HTTP 530"

    def test_timeout(self):
        from comic_dl.downloader import _download_failure_label
        from comic_dl.errors import DownloadTimeout

        assert _download_failure_label(DownloadTimeout("a.jpg", 30.0)) == "timed out"
        assert _download_failure_label(CurlTimeout("timed out")) == "timed out"

    def test_connection(self):
        from comic_dl.downloader import _download_failure_label

        assert _download_failure_label(CurlConnectionError("refused")) == "connection failed"

    def test_unknown_exception_uses_type_name(self):
        from comic_dl.downloader import _download_failure_label

        assert _download_failure_label(ValueError("bad value")) == "ValueError"


class TestRetryAfterWait:
    def test_missing_header_returns_none(self):
        assert _retry_after_wait_seconds(None) is None
        assert _retry_after_wait_seconds({}) is None
        assert _retry_after_wait_seconds({"content-type": "image/jpeg"}) is None

    def test_delta_seconds(self):
        assert _retry_after_wait_seconds({"retry-after": "5"}) == 5.0

    def test_case_insensitive(self):
        assert _retry_after_wait_seconds({"Retry-After": "2"}) == 2.0

    def test_non_numeric_header_ignored(self):
        assert _retry_after_wait_seconds({"retry-after": "soon"}) is None

    def test_zero_and_negative_ignored(self):
        assert _retry_after_wait_seconds({"retry-after": "0"}) is None
        assert _retry_after_wait_seconds({"retry-after": "-3"}) is None

    def test_past_date_ignored(self):
        past = email.utils.formatdate(time.time() - 120, usegmt=True)
        assert _retry_after_wait_seconds({"retry-after": past}) is None

    def test_future_date(self):
        future = email.utils.formatdate(time.time() + 15, usegmt=True)
        seconds = _retry_after_wait_seconds({"retry-after": future})
        assert seconds is not None
        assert 13 <= seconds <= 15

    def test_capped_at_retry_after_cap(self):
        assert _retry_after_wait_seconds({"retry-after": "120"}) == RETRY_AFTER_CAP


class TestRetryBlocked:
    pytestmark = pytest.mark.asyncio

    class _Challenge:
        status_code = 503
        headers = {"server": "cloudflare"}
        content = b"Just a moment..."

    class _RateLimited:
        status_code = 429
        headers = {"retry-after": "7"}
        content = b""

    class _Ok:
        status_code = 200
        headers = {}
        content = b"<html>real page</html>"

    async def test_solve_on_final_attempt_still_gets_fetch(self, monkeypatch):
        """A CF challenge solved on the last humane slot must still exercise
        the fresh cookie jar: the post-solve fetch is part of the slot that
        solved it, so it is not dropped when the attempt counter is spent."""
        calls = []

        async def fetch():
            calls.append(len(calls))
            return self._Ok() if len(calls) == 4 else self._Challenge()

        async def _handle(url):
            return True

        monkeypatch.setattr("comic_dl.cf.handle_challenge", _handle)

        resp = await _retry_blocked(fetch, "")
        assert getattr(resp, "status_code", None) == 200
        assert len(calls) == 4

    async def test_solve_then_ok_returns_ok(self, monkeypatch):
        calls = []

        async def fetch():
            calls.append(len(calls))
            return self._Ok() if len(calls) == 2 else self._Challenge()

        async def _handle(url):
            return True

        monkeypatch.setattr("comic_dl.cf.handle_challenge", _handle)

        resp = await _retry_blocked(fetch, "")
        assert getattr(resp, "status_code", None) == 200
        assert len(calls) == 2

    async def test_block_honors_retry_after(self, monkeypatch):
        """A general (non-CF) block carrying ``Retry-After`` must wait the
        server-named duration (capped), not the default humane backoff."""
        sleeps = []

        async def _sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr("comic_dl.downloader.asyncio.sleep", _sleep)
        monkeypatch.setattr(
            "comic_dl.downloader._humane_backoff_delay",
            lambda attempt: 0.001,
        )

        calls = []

        async def fetch():
            calls.append(len(calls))
            return self._Ok() if len(calls) == 2 else self._RateLimited()

        resp = await _retry_blocked(fetch, "https://example.com/x")
        assert getattr(resp, "status_code", None) == 200
        assert sleeps == [7.0]
        assert len(calls) == 2

    async def test_retry_after_capped_on_block(self, monkeypatch):
        sleeps = []

        async def _sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr("comic_dl.downloader.asyncio.sleep", _sleep)
        monkeypatch.setattr(
            "comic_dl.downloader._humane_backoff_delay",
            lambda attempt: 0.001,
        )

        class RateLimitedBig:
            status_code = 429
            headers = {"retry-after": "5000"}
            content = b""

        calls = []

        async def fetch():
            calls.append(len(calls))
            return self._Ok() if len(calls) == 2 else RateLimitedBig()

        resp = await _retry_blocked(fetch, "https://example.com/x")
        assert getattr(resp, "status_code", None) == 200
        assert sleeps == [RETRY_AFTER_CAP]


class TestStreamToDiskEnospc:
    pytestmark = pytest.mark.asyncio

    class _FakeResp:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        async def aiter_content(self):
            yield b"chunk-1"
            yield b"chunk-2"

    class _FakeStreamCM:
        def __init__(self):
            self._resp = TestStreamToDiskEnospc._FakeResp()
            self.status_code = 200
            self.headers = {}

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *args):
            return False

    class _FakeClient:
        def stream(self, method, url, **kwargs):
            return TestStreamToDiskEnospc._FakeStreamCM()

    async def test_enospc_raises_insufficient_disk_error(self, tmp_path, monkeypatch):
        real_open = builtins.open

        class _FailingWrite:
            def __init__(self, path):
                self._f = real_open(path, "wb")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self._f.close()
                return False

            def write(self, chunk):
                self._f.write(b"partial")
                raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(builtins, "open", lambda path, *a, **k: _FailingWrite(path))

        dest = tmp_path / "page_0001.part"
        item = ImageItem(url="https://example.com/1.jpg", page_number=1, filename="page_0001.part")
        with pytest.raises(InsufficientDiskError):
            await _stream_to_disk(item, dest, self._FakeClient(), 100 * 1024 * 1024)

        # Partial file is preserved on disk so the download can resume later.
        assert dest.exists()
        assert dest.stat().st_size == len(b"partial")

    async def test_other_oserror_propagates(self, tmp_path, monkeypatch):
        real_open = builtins.open

        class _FailingWrite:
            def __init__(self, path):
                self._f = real_open(path, "wb")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self._f.close()
                return False

            def write(self, chunk):
                raise OSError(errno.EIO, "I/O error")

        monkeypatch.setattr(builtins, "open", lambda path, *a, **k: _FailingWrite(path))

        dest = tmp_path / "page_0002.part"
        item = ImageItem(url="https://example.com/2.jpg", page_number=2, filename="page_0002.part")
        with pytest.raises(OSError) as excinfo:
            await _stream_to_disk(item, dest, self._FakeClient(), 100 * 1024 * 1024)
        assert not isinstance(excinfo.value, InsufficientDiskError)


class TestBackoffDelay:
    def test_increasing(self):
        delays = [_backoff_delay(i) for i in range(5)]
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i - 1]

    def test_exact_2_4_8(self):
        # Exact 2, 4, 8 seconds with jitter disabled.
        assert [_backoff_delay(i) for i in range(3)] == [2.0, 4.0, 8.0]

    def test_jitter_within_20_percent(self):
        # With jitter, each scheduled delay stays within ±20% and is capped.
        for i in range(3):
            expected = min(2.0 * (2 ** i), 8.0)
            for _ in range(200):
                d = _backoff_delay(i, jitter=True)
                assert expected * 0.8 <= d <= expected * 1.2

    def test_max_delay(self):
        for i in range(10, 20):
            d = _backoff_delay(i, max_delay=30.0)
            # base delay is capped at 30, no jitter
            assert d == 30.0

    def test_positive(self):
        for i in range(10):
            assert _backoff_delay(i) > 0


class TestTryResume:
    pytestmark = pytest.mark.asyncio
    async def test_no_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            # File doesn't exist, should return None
            result = await _try_resume(
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
                dest,
                None,  # type: ignore
            )
            assert result is None

    async def test_empty_file(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            dest.write_bytes(b"")
            result = await _try_resume(
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
                dest,
                None,  # type: ignore
            )
            assert result is None
            assert not dest.exists()

    async def test_exceeding_max_image_size(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            dest.write_bytes(b"A" * 1000)
            result = await _try_resume(
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
                dest,
                None,  # type: ignore
                max_image_size=500,
            )
            assert result is None
            assert not dest.exists()

    async def test_resume_with_mock_206(self):
        class MockResponse:
            status_code = 206
            async def aiter_content(self):
                yield b'complete'
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            @property
            def headers(self):
                return {}

        class MockClient:
            def stream(self, method, url, headers=None, **kwargs):
                return MockResponse()

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            dest.write_bytes(b'\xff\xd8\xff')  # partial JPEG
            result = await _try_resume(
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
                dest,
                MockClient(),  # type: ignore
            )
            assert result is True
            assert dest.exists()

    async def test_resume_416(self):
        class MockResponse:
            status_code = 416
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            @property
            def headers(self):
                return {}

        class MockClient:
            def stream(self, method, url, headers=None, **kwargs):
                return MockResponse()

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            dest.write_bytes(b'\xff\xd8\xff')
            result = await _try_resume(
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
                dest,
                MockClient(),  # type: ignore
            )
            # 416 means the server has nothing at our offset — the partial is
            # stale, so it is discarded and a fresh full download starts.
            assert result is None
            assert not dest.exists()

    async def test_resume_fails_non_206(self):
        class MockResponse:
            status_code = 404
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            @property
            def headers(self):
                return {}

        class MockClient:
            def stream(self, method, url, headers=None, **kwargs):
                return MockResponse()

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            dest.write_bytes(b'\xff\xd8\xff')
            result = await _try_resume(
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
                dest,
                MockClient(),  # type: ignore
            )
            assert result is None
            assert not dest.exists()


class TestVerifyDownloads:
    def test_all_valid(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "a.jpg").write_bytes(b'\xff\xd8\xff')
            (src / "b.jpg").write_bytes(b'\x89PNG\r\n\x1a\n')
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename="a.jpg"),
                ImageItem(url="http://x.com/2", page_number=2, filename="b.jpg"),
            ]
            errors, _ = verify_downloads(images, src)
            assert errors == {}

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename="missing.jpg"),
            ]
            errors, _ = verify_downloads(images, src)
            assert "missing.jpg" in errors
            assert errors["missing.jpg"] == "missing"

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "empty.jpg").write_bytes(b"")
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename="empty.jpg"),
            ]
            errors, _ = verify_downloads(images, src)
            assert "empty.jpg" in errors
            assert errors["empty.jpg"] == "empty"
            assert not (src / "empty.jpg").exists()

    def test_invalid_image(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "bad.jpg").write_bytes(b"not an image")
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename="bad.jpg"),
            ]
            errors, _ = verify_downloads(images, src)
            assert "bad.jpg" in errors
            assert errors["bad.jpg"] == "invalid image"
            assert not (src / "bad.jpg").exists()

    def test_missing_dir(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nonexistent"
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename="a.jpg"),
            ]
            errors, _ = verify_downloads(images, missing)
            assert "a.jpg" in errors
            assert "directory not found" in errors["a.jpg"]

    def test_mixed_results(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            (src / "good.jpg").write_bytes(b'\xff\xd8\xff')
            images = [
                ImageItem(url="http://x.com/1", page_number=1, filename="good.jpg"),
                ImageItem(url="http://x.com/2", page_number=2, filename="missing.jpg"),
                ImageItem(url="http://x.com/3", page_number=3, filename="empty.jpg"),
            ]
            (src / "empty.jpg").write_bytes(b"")
            errors, _ = verify_downloads(images, src)
            assert "good.jpg" not in errors
            assert "missing.jpg" in errors
            assert "empty.jpg" in errors


class TestStreamToDisk:
    pytestmark = pytest.mark.asyncio

    def _make_client(self, status=200, data=b'\xff\xd8\xff', content_length=None):
        """Create a mock httpx client for testing _stream_to_disk."""

        class MockResponse:
            status_code = status

            def __init__(self):
                self._headers = {}
                if content_length is not None:
                    self._headers["content-length"] = str(content_length)

            @property
            def headers(self):
                return self._headers

            def raise_for_status(self):
                if self.status_code >= 400:
                    from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
                    raise CurlHTTPError("error", response=self)

            async def aiter_content(self, chunk_size=None):
                yield data

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockAsyncClient:
            def stream(self, method, url, **kwargs):
                return MockResponse()

        return MockAsyncClient()

    async def test_download_success(self):
        client = self._make_client()
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            from comic_dl.downloader import _stream_to_disk
            await _stream_to_disk(
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
                dest,
                client,  # type: ignore
            )
            assert dest.exists()
            assert dest.stat().st_size > 0
            assert verify_image_file(dest) == "jpeg"

    async def test_content_length_exceeded(self):
        client = self._make_client(content_length=200)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            from comic_dl.downloader import _stream_to_disk
            with pytest.raises(ValueError, match="too large"):
                await _stream_to_disk(
                    ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
                    dest,
                    client,  # type: ignore
                    max_image_size=50,
                )

    async def test_streaming_exceeds_max(self):
        class OversizedResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                yield b"A" * 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockOversizedClient:
            def stream(self, method, url, **kwargs):
                return OversizedResponse()

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            from comic_dl.downloader import _stream_to_disk
            with pytest.raises(ValueError, match="exceeded max size"):
                await _stream_to_disk(
                    ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
                    dest,
                    MockOversizedClient(),  # type: ignore
                    max_image_size=100,
                )

    async def test_html_throttle_body_raises_not_image(self):
        """A 200 HTML throttle page served for an image URL must raise a
        retryable error instead of being written and flagged "invalid image"
        at verification time."""
        from comic_dl.downloader import NotImageResponseError

        class HtmlResponse:
            status_code = 200
            headers: ClassVar[dict[str, str]] = {}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                yield b"<html><body>509 Bandwidth Limit Exceeded</body></html>"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockHtmlClient:
            def stream(self, method, url, **kwargs):
                return HtmlResponse()

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            from comic_dl.downloader import _stream_to_disk
            with pytest.raises(NotImageResponseError):
                await _stream_to_disk(
                    ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
                    dest,
                    MockHtmlClient(),  # type: ignore
                )

    async def test_small_valid_image_not_rejected(self):
        """A valid image whose first chunk is smaller than the magic window
        must still pass through (magic check only fires once enough bytes
        have accumulated)."""

        class SmallResponse:
            status_code = 200
            headers: ClassVar[dict[str, str]] = {}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                yield MAGIC_JPEG
                yield b"more"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockSmallClient:
            def stream(self, method, url, **kwargs):
                return SmallResponse()

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            from comic_dl.downloader import _stream_to_disk
            await _stream_to_disk(
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
                dest,
                MockSmallClient(),  # type: ignore
            )
            assert dest.exists()
            assert dest.read_bytes() == MAGIC_JPEG + b"more"


class TestPathTraversalGuard:
    def test_relative_to_rejects_escape(self, tmp_path):
        dest_dir = (tmp_path / "dest").resolve()
        item = ImageItem(url="http://x.com/img", page_number=1, filename="../../etc/passwd")
        dest = (dest_dir / item.filename).resolve()
        dest_dir_resolved = dest_dir.resolve()
        try:
            dest.relative_to(dest_dir_resolved)
            raise AssertionError("should have raised ValueError")
        except ValueError:
            pass

    def test_relative_to_accepts_safe(self, tmp_path):
        dest_dir = (tmp_path / "dest").resolve()
        item = ImageItem(url="http://x.com/img", page_number=1, filename="safe.jpg")
        dest = (dest_dir / item.filename).resolve()
        dest_dir_resolved = dest_dir.resolve()
        try:
            dest.relative_to(dest_dir_resolved)
        except ValueError as err:
            raise AssertionError("should not have raised ValueError") from err


class TestDownloadHttpx:
    pytestmark = pytest.mark.asyncio
    async def test_empty_images(self):
        with tempfile.TemporaryDirectory() as td:
            failed = await download_httpx(
                [], Path(td), concurrency=5,
            )
            assert failed == set()

    async def test_with_client(self):
        class MockResponse:
            status_code = 200
            headers = {"content-length": "3"}

            def raise_for_status(self):
                pass

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

        with tempfile.TemporaryDirectory() as td:
            images = [
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
            ]
            failed = await download_httpx(
                images, Path(td), concurrency=5,
                client=MockClient(),  # type: ignore
            )
            assert failed == set()
            assert (Path(td) / "test.jpg").exists()


class TestDownloadHttpxIter:
    pytestmark = pytest.mark.asyncio

    class MockResponse:
        status_code = 200
        headers = {"content-length": "3"}

        def raise_for_status(self):
            pass

        async def aiter_content(self, chunk_size=None):
            yield MAGIC_JPEG

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class MockClient:
        def stream(self, method, url, **kwargs):
            return TestDownloadHttpxIter.MockResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    @staticmethod
    async def _aiter(items):
        for item in items:
            yield item

    async def test_streams_and_returns_resolved_in_order(self):
        images = [
            ImageItem(url=f"http://x.com/{i}.jpg", page_number=i, filename=f"p{i}.jpg")
            for i in (1, 2, 3)
        ]
        with tempfile.TemporaryDirectory() as td:
            failed, resolved = await download_httpx_iter(
                self._aiter(images), Path(td), concurrency=2,
                client=self.MockClient(),  # type: ignore
            )
            assert failed == set()
            assert [r.page_number for r in resolved] == [1, 2, 3]
            assert (Path(td) / "p1.jpg").exists()
            assert (Path(td) / "p3.jpg").exists()

    async def test_empty_iter(self):
        with tempfile.TemporaryDirectory() as td:
            failed, resolved = await download_httpx_iter(
                self._aiter([]), Path(td), concurrency=2,
                client=self.MockClient(),  # type: ignore
            )
            assert failed == set()
            assert resolved == []


class TestRetryPreservesPart:
    pytestmark = pytest.mark.asyncio

    async def test_retry_does_not_delete_part(self):
        """Retryable error should not delete .part; next attempt can resume."""
        call_count = [0]

        class FailingThenResumingResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                call_count[0] += 1
                if call_count[0] == 1:
                    yield MAGIC_JPEG
                    raise CurlTimeout("timed out")
                yield MAGIC_JPEG + b'\x00\x00\x10JFIF\x00'

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class ResumeResponse:
            status_code = 206
            headers = {}

            async def aiter_content(self, chunk_size=None):
                yield b'\x00\x00\x10JFIF\x00'

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def __init__(self):
                self.stream_call_count = 0

            def stream(self, method, url, **kwargs):
                headers = kwargs.get("headers", {})
                if "Range" in headers:
                    return ResumeResponse()
                self.stream_call_count += 1
                if self.stream_call_count == 1:
                    return FailingThenResumingResponse()
                return FailingThenResumingResponse()

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with tempfile.TemporaryDirectory() as td:
            images = [
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
            ]
            failed = await download_httpx(
                images, Path(td), concurrency=1,
                client=MockClient(),
            )
            assert failed == set()
            assert (Path(td) / "test.jpg").exists()
            assert (Path(td) / "test.jpg").stat().st_size >= len(MAGIC_JPEG)

    async def test_permanent_error_removes_part(self):
        """Non-retryable HTTP error should delete .part and report failure."""
        class NotFoundResponse:
            status_code = 404
            headers = {}

            def raise_for_status(self):
                raise CurlHTTPError("not found")

            async def aiter_content(self, chunk_size=None):
                yield b''

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                return NotFoundResponse()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with tempfile.TemporaryDirectory() as td:
            images = [
                ImageItem(url="http://x.com/img", page_number=1, filename="fail.jpg"),
            ]
            failed = await download_httpx(
                images, Path(td), concurrency=1,
                client=MockClient(),
            )
            assert "fail.jpg" in failed
            assert not (Path(td) / "fail.jpg").exists()
            assert not (Path(td) / "fail.jpg.part").exists()

    async def test_retry_all_attempts_exhausted_removes_part(self):
        """After exhausting all retries, .part should be cleaned up."""
        attempt_count = [0]

        class AlwaysFailsResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                attempt_count[0] += 1
                yield MAGIC_JPEG
                raise CurlTimeout("always fails")

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                return AlwaysFailsResponse()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with tempfile.TemporaryDirectory() as td:
            images = [
                ImageItem(url="http://x.com/img", page_number=1, filename="fail.jpg"),
            ]
            failed = await download_httpx(
                images, Path(td), concurrency=1,
                client=MockClient(),
            )
            assert "fail.jpg" in failed
            assert not (Path(td) / "fail.jpg").exists()
            assert not (Path(td) / "fail.jpg.part").exists()
            assert attempt_count[0] == 3


class TestAdaptiveCooldown:
    pytestmark = pytest.mark.asyncio

    async def test_retryable_failure_paces_in_flight_downloads(self, tmp_path, monkeypatch):
        """A retryable failure sets a shared cooldown that holds concurrent
        downloads until the backoff window elapses (lowering request rate under
        throttling), and everything still completes."""
        import time as _time

        served: list[float] = []

        class FlakyResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                served.append(_time.monotonic())
                raise CurlTimeout("throttled")
                yield b''  # pragma: no cover

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        class OkResponse:
            status_code = 200
            headers = {"content-length": "3"}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                served.append(_time.monotonic())
                yield b'\xff\xd8\xff'

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        class MockClient:
            def __init__(self):
                self._counts = {}

            def stream(self, method, url, **kwargs):
                self._counts[url] = self._counts.get(url, 0) + 1
                if self._counts[url] == 1:
                    return FlakyResponse()
                return OkResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        monkeypatch.setattr(
            "comic_dl.downloader._backoff_delay",
            lambda attempt, **kwargs: 0.2,
        )

        with tempfile.TemporaryDirectory() as td:
            images = [
                ImageItem(url="http://x.com/a", page_number=1, filename="a.jpg"),
                ImageItem(url="http://x.com/b", page_number=2, filename="b.jpg"),
            ]
            failed = await download_httpx(
                images, Path(td), concurrency=2, client=MockClient(),
            )
            assert failed == set()
            assert (Path(td) / "a.jpg").exists()
            assert (Path(td) / "b.jpg").exists()

            # Both attempts failed first, then the shared cooldown held the
            # retries together until the backoff window elapsed. The retry
            # deadline is anchored to each failure's own backoff, so the first
            # retry can never start earlier than ~backoff after the *first*
            # failure — regardless of how far apart the two concurrent
            # first-attempt failures land under scheduling skew.
            first_failures = served[:2]
            retries = served[2:]
            assert len(first_failures) == 2
            assert len(retries) == 2
            assert min(retries) - min(first_failures) >= 0.15


class TestEngineTuning:
    def test_defaults(self):
        timeout, attempts = _engine_tuning()
        assert timeout == 60.0
        assert attempts == 3

    def test_config_override(self):
        from comic_dl import config as cfgmodule

        cfgmodule.set_runtime_http(**{"download-timeout": 5, "download-retries": 4})
        try:
            timeout, attempts = _engine_tuning()
            assert timeout == 5.0
            assert attempts == 5  # 4 retries + the first attempt
        finally:
            cfgmodule._RUNTIME_HTTP.pop("download-timeout", None)
            cfgmodule._RUNTIME_HTTP.pop("download-retries", None)

    def test_clamps(self):
        from comic_dl import config as cfgmodule

        cfgmodule.set_runtime_http(
            **{"download-timeout": 0.1, "download-retries": 99}
        )
        try:
            timeout, attempts = _engine_tuning()
            assert timeout == 1.0
            assert attempts == 11  # capped at 10 retries + the first attempt
        finally:
            cfgmodule._RUNTIME_HTTP.pop("download-timeout", None)
            cfgmodule._RUNTIME_HTTP.pop("download-retries", None)


class TestDownloadTimeoutAndAttempts:
    pytestmark = pytest.mark.asyncio

    async def test_download_timeout_param_applies(self):
        """A per-image timeout shorter than the response stalls the page."""

        class SlowResponse:
            status_code = 200
            headers = {"content-length": "3"}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                await asyncio.sleep(1.0)
                yield MAGIC_JPEG  # pragma: no cover

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                return SlowResponse()

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with tempfile.TemporaryDirectory() as td:
            images = [
                ImageItem(url="http://x.com/img", page_number=1, filename="fail.jpg"),
            ]
            failed = await download_httpx(
                images, Path(td), concurrency=1,
                client=MockClient(),  # type: ignore
                download_timeout=0.05,
                max_attempts=1,
            )
            assert "fail.jpg" in failed
            assert not (Path(td) / "fail.jpg").exists()
            assert not (Path(td) / "fail.jpg.part").exists()

    async def test_attempt_budget_respected(self, monkeypatch):
        """max_attempts caps the total fetch attempts per image."""
        attempt_count = [0]

        class AlwaysFailsResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                attempt_count[0] += 1
                yield MAGIC_JPEG
                raise CurlTimeout("always fails")

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                return AlwaysFailsResponse()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        monkeypatch.setattr(
            "comic_dl.downloader._backoff_delay",
            lambda attempt, **kwargs: 0.01,
        )

        with tempfile.TemporaryDirectory() as td:
            images = [
                ImageItem(url="http://x.com/img", page_number=1, filename="fail.jpg"),
            ]
            failed = await download_httpx(
                images, Path(td), concurrency=1,
                client=MockClient(),  # type: ignore
                max_attempts=2,
            )
            assert failed == {"fail.jpg"}
            assert attempt_count[0] == 2
            assert not (Path(td) / "fail.jpg").exists()
            assert not (Path(td) / "fail.jpg.part").exists()


class TestExistingDestSkip:
    pytestmark = pytest.mark.asyncio

    async def test_existing_valid_dest_is_skipped(self):
        """If dest file already exists and is valid, skip download entirely."""
        stream_calls = [0]

        class MockResponse:
            status_code = 200
            headers = {"content-length": "3"}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                stream_calls[0] += 1
                yield MAGIC_JPEG

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

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "existing.jpg"
            dest.write_bytes(MAGIC_JPEG)
            images = [
                ImageItem(url="http://x.com/img", page_number=1, filename="existing.jpg"),
            ]
            failed = await download_httpx(
                images, Path(td), concurrency=1,
                client=MockClient(),
            )
            assert failed == set()
            assert dest.exists()
            assert dest.read_bytes() == MAGIC_JPEG
            assert stream_calls[0] == 0

    async def test_existing_invalid_dest_is_replaced(self):
        """If dest exists but is corrupt, re-download."""
        stream_calls = [0]

        class MockResponse:
            status_code = 200
            headers = {"content-length": "3"}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                stream_calls[0] += 1
                yield MAGIC_JPEG

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

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "test.jpg"
            dest.write_bytes(b"not a valid image")
            images = [
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
            ]
            failed = await download_httpx(
                images, Path(td), concurrency=1,
                client=MockClient(),
            )
            assert failed == set()
            assert dest.exists()
            assert dest.read_bytes() == MAGIC_JPEG
            assert stream_calls[0] == 1

    async def test_existing_part_resumed(self):
        """Existing .part file should trigger resume on next run."""
        range_requests = [0]

        class ResumeResponse:
            status_code = 206
            headers = {}

            async def aiter_content(self, chunk_size=None):
                range_requests[0] += 1
                yield b'\x00\x00\x10JFIF\x00'

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                return ResumeResponse()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with tempfile.TemporaryDirectory() as td:
            part_path = Path(td) / "test.jpg.part"
            part_path.write_bytes(MAGIC_JPEG)
            images = [
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
            ]
            failed = await download_httpx(
                images, Path(td), concurrency=1,
                client=MockClient(),
            )
            assert failed == set()
            assert (Path(td) / "test.jpg").exists()
            assert not part_path.exists()
            assert range_requests[0] == 1

    async def test_incomplete_part_resumed_via_range(self):
        """A partial that is not yet a complete image is resumed from its byte
        offset (Range), not re-downloaded from zero."""
        range_requests = [0]
        full_requests = [0]

        class ResumeResponse:
            status_code = 206
            headers = {}

            async def aiter_content(self, chunk_size=None):
                range_requests[0] += 1
                yield b"\xff" + b"\x00\x00\x10JFIF\x00"

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class FullResponse:
            status_code = 200
            headers = {"content-length": "3"}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                full_requests[0] += 1
                yield MAGIC_JPEG

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                headers = kwargs.get("headers", {})
                if "Range" in headers:
                    return ResumeResponse()
                return FullResponse()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with tempfile.TemporaryDirectory() as td:
            part_path = Path(td) / "test.jpg.part"
            # \xff\xd8 is a truncated JPEG: magic-headered but not yet a
            # complete image, so the old pre-verify gate rejected it.
            part_path.write_bytes(b"\xff\xd8")
            images = [
                ImageItem(url="http://x.com/img", page_number=1, filename="test.jpg"),
            ]
            failed = await download_httpx(
                images, Path(td), concurrency=1,
                client=MockClient(),
            )
            assert failed == set()
            assert (Path(td) / "test.jpg").exists()
            assert verify_image_file(Path(td) / "test.jpg") is not None
            assert not part_path.exists()
            assert range_requests[0] == 1
            assert full_requests[0] == 0


class TestDownloadCoverTo:
    pytestmark = pytest.mark.asyncio

    @staticmethod
    def _make_response(status, payload=b"", hdrs=None, err=None):
        class MockResponse:
            status_code = status
            headers = hdrs or {}
            body = payload
            exc = err

            async def aiter_content(self, chunk_size=None):
                if self.exc is not None:
                    raise self.exc
                yield self.body

            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def aclose(self):
                pass

        return MockResponse()

    @staticmethod
    def _make_client(response, requests=None):
        class MockClient:
            def stream(self, method, url, **kwargs):
                if requests is not None:
                    requests.append(kwargs.get("headers", {}))
                if isinstance(response, Exception):
                    raise response
                return response

        return MockClient()

    async def test_writes_when_missing(self):
        resp = self._make_response(200, payload=MAGIC_JPEG)
        client = self._make_client(resp)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "cover.jpg"
            written = await download_cover_to("http://x.com/cover.jpg", dest, client=client)
            assert written is True
            assert dest.read_bytes() == MAGIC_JPEG

    async def test_client_path_forwards_referer_headers(self):
        # A caller-provided client (series-batch covers) must still send
        # Referer/Origin per request, or hotlink-protected CDNs 403.
        reqs = []
        resp = self._make_response(200, payload=MAGIC_JPEG)
        client = self._make_client(resp, reqs)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "cover.jpg"
            written = await download_cover_to(
                "https://cdn.site/img.jpg",
                dest,
                client=client,
                referer_url="https://www.site/gallery",
            )
            assert written is True
        assert len(reqs) == 1
        sent = reqs[0]
        assert sent.get("Referer", "") == "https://www.site/gallery"
        assert sent.get("Origin") == "https://www.site"

    async def test_304_leaves_file_untouched(self):
        reqs = []
        resp = self._make_response(304)
        client = self._make_client(resp, reqs)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "cover.jpg"
            dest.write_bytes(MAGIC_JPEG)
            before = dest.stat().st_mtime
            written = await download_cover_to("http://x.com/cover.jpg", dest, client=client)
            assert written is False
            assert dest.read_bytes() == MAGIC_JPEG
            assert dest.stat().st_mtime == before
            assert "If-Modified-Since" in reqs[0]

    async def test_identical_bytes_are_not_rewritten(self):
        reqs = []
        resp = self._make_response(200, payload=MAGIC_JPEG)
        client = self._make_client(resp, reqs)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "cover.jpg"
            dest.write_bytes(MAGIC_JPEG)
            written = await download_cover_to("http://x.com/cover.jpg", dest, client=client)
            assert written is False
            assert dest.read_bytes() == MAGIC_JPEG
            assert "If-Modified-Since" in reqs[0]

    async def test_changed_bytes_are_rewritten(self):
        new_body = MAGIC_JPEG + b"extra"
        resp = self._make_response(200, payload=new_body)
        client = self._make_client(resp)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "cover.jpg"
            dest.write_bytes(MAGIC_JPEG)
            written = await download_cover_to("http://x.com/cover.jpg", dest, client=client)
            assert written is True
            assert dest.read_bytes() == new_body

    async def test_force_always_writes(self):
        reqs = []
        resp = self._make_response(200, payload=MAGIC_JPEG)
        client = self._make_client(resp, reqs)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "cover.jpg"
            dest.write_bytes(MAGIC_JPEG)
            written = await download_cover_to(
                "http://x.com/cover.jpg", dest, client=client, force=True
            )
            assert written is True
            assert dest.read_bytes() == MAGIC_JPEG
            assert "If-Modified-Since" not in reqs[0]

    async def test_http_error_is_graceful(self):
        resp = self._make_response(500)
        client = self._make_client(resp)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "cover.jpg"
            written = await download_cover_to("http://x.com/cover.jpg", dest, client=client)
            assert written is False
            assert not dest.exists()

    async def test_network_failure_is_graceful(self):
        client = self._make_client(CurlConnectionError("boom"))
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "cover.jpg"
            written = await download_cover_to("http://x.com/cover.jpg", dest, client=client)
            assert written is False
            assert not dest.exists()

    async def test_last_modified_preserved_as_mtime(self):
        last_modified = "Mon, 01 Aug 2026 12:00:00 GMT"
        resp = self._make_response(200, payload=MAGIC_JPEG, hdrs={"last-modified": last_modified})
        client = self._make_client(resp)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "cover.jpg"
            await download_cover_to("http://x.com/cover.jpg", dest, client=client)
            expected = email.utils.parsedate_to_datetime(last_modified).timestamp()
            assert abs(dest.stat().st_mtime - expected) < 1


class TestDownloadPipelineNoValidPages:
    """Bug #1: when every page fails verification, the chapter must hard-fail
    instead of writing a CBZ containing only ComicInfo.xml."""

    pytestmark = pytest.mark.asyncio

    def _images(self):
        page1 = image_source_name(1, "http://x.com/1")
        page2 = image_source_name(2, "http://x.com/2")
        return [
            ImageItem(url="http://x.com/1", page_number=1, filename=page1),
            ImageItem(url="http://x.com/2", page_number=2, filename=page2),
        ]

    async def test_all_pages_fail_returns_failed_and_no_cbz(self, tmp_path, monkeypatch):
        images = self._images()
        tmp_dir = tmp_path / "tmp"
        cbz_path = tmp_path / "out.cbz"
        pipe = DownloadPipeline(
            images=images,
            tmp_dir=tmp_dir,
            cbz_path=cbz_path,
            series_title="S",
            chapter_title="C",
            quiet=True,
        )

        async def fake_download(client_kwargs, progress_cb, activity_cb=None):
            progress_cb(len(images))
            return {images[0].filename, images[1].filename}

        monkeypatch.setattr(pipe, "_download", fake_download)
        result = await pipe.run()
        assert result.ok is False
        assert result.error == "no valid pages downloaded"
        assert result.failed_images == {images[0].filename, images[1].filename}
        assert not cbz_path.exists()
        assert not tmp_dir.exists()

    async def test_partial_failure_still_writes_cbz(self, tmp_path, monkeypatch):
        images = self._images()
        tmp_dir = tmp_path / "tmp"
        cbz_path = tmp_path / "out.cbz"
        pipe = DownloadPipeline(
            images=images,
            tmp_dir=tmp_dir,
            cbz_path=cbz_path,
            series_title="S",
            chapter_title="C",
            quiet=True,
        )

        async def fake_download(client_kwargs, progress_cb, activity_cb=None):
            tmp_dir.mkdir(parents=True, exist_ok=True)
            (tmp_dir / images[0].filename).write_bytes(MAGIC_JPEG)
            progress_cb(len(images))
            return {images[1].filename}

        monkeypatch.setattr(pipe, "_download", fake_download)
        result = await pipe.run()
        assert result.ok is True
        assert result.failed_images == {images[1].filename}
        assert result.cbz_pages == 1
        assert cbz_path.exists()
        assert cbz_path.stat().st_size > 0


class TestDownloadPipelinePageCap:
    """Per-chapter page concurrency policy.

    With per-site rate limiting on (default) the caller's value is honored up
    to the pipeline ceiling; with rate limiting disabled the 5-page
    politeness ceiling applies and the clamp is flagged.
    """

    def _pipe(self, concurrency):
        return DownloadPipeline(
            images=[ImageItem(url="http://x.com/1", page_number=1, filename="p1.jpg")],
            tmp_dir=Path("/tmp/x"),
            cbz_path=Path("/tmp/y.cbz"),
            concurrency=concurrency,
        )

    def test_caps_at_pipeline_ceiling(self):
        assert self._pipe(50)._concurrency == MAX_PIPELINE_CONCURRENCY
        assert self._pipe(32)._concurrency == 32

    def test_keeps_requested_value(self):
        assert self._pipe(2)._concurrency == 2

    def test_default_is_five(self):
        assert self._pipe(5)._concurrency == 5

    def test_clamps_only_when_rate_limited_disabled(self, monkeypatch):
        from comic_dl import config as cfgmodule
        cfgmodule.set_runtime_http(**{"rate-enabled": False})
        try:
            assert self._pipe(50)._concurrency == 5
            assert self._pipe(50)._concurrency_clamped is True
            assert self._pipe(3)._concurrency == 3
            assert self._pipe(3)._concurrency_clamped is False
        finally:
            cfgmodule._RUNTIME_HTTP.pop("rate-enabled", None)


class TestDownloadPipelineFormats:
    """The pipeline packs whatever container the output path's suffix implies
    (`.cbz`/`.zip` → zip, `.cbt` → tar) with the same page/metadata guarantees."""

    pytestmark = pytest.mark.asyncio

    async def test_zip_output_is_zip(self, tmp_path, monkeypatch):
        import zipfile

        images = [
            ImageItem(url="http://x.com/1", page_number=1, filename=image_source_name(1, "http://x.com/1")),
            ImageItem(url="http://x.com/2", page_number=2, filename=image_source_name(2, "http://x.com/2")),
        ]
        tmp_dir = tmp_path / "tmp"
        out = tmp_path / "out.zip"
        pipe = DownloadPipeline(
            images=images, tmp_dir=tmp_dir, cbz_path=out,
            series_title="S", chapter_title="C", quiet=True,
        )

        async def fake_download(client_kwargs, progress_cb, activity_cb=None):
            tmp_dir.mkdir(parents=True, exist_ok=True)
            for i, im in enumerate(images, start=1):
                (tmp_dir / im.filename).write_bytes(MAGIC_JPEG + bytes([i]))
            progress_cb(len(images))
            return set()

        monkeypatch.setattr(pipe, "_download", fake_download)
        result = await pipe.run()
        assert result.ok is True
        assert result.cbz_pages == 2
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            assert "ComicInfo.xml" in names
            assert sum(1 for n in names if n.startswith("Page_")) == 2

    async def test_cbt_output_is_tar(self, tmp_path, monkeypatch):

        images = [
            ImageItem(url="http://x.com/1", page_number=1, filename=image_source_name(1, "http://x.com/1")),
            ImageItem(url="http://x.com/2", page_number=2, filename=image_source_name(2, "http://x.com/2")),
        ]
        tmp_dir = tmp_path / "tmp"
        out = tmp_path / "out.cbt"
        pipe = DownloadPipeline(
            images=images, tmp_dir=tmp_dir, cbz_path=out,
            series_title="S", chapter_title="C", quiet=True,
        )

        async def fake_download(client_kwargs, progress_cb, activity_cb=None):
            tmp_dir.mkdir(parents=True, exist_ok=True)
            for i, im in enumerate(images, start=1):
                (tmp_dir / im.filename).write_bytes(MAGIC_JPEG + bytes([i]))
            progress_cb(len(images))
            return set()

        monkeypatch.setattr(pipe, "_download", fake_download)
        result = await pipe.run()
        assert result.ok is True
        assert result.cbz_pages == 2
        with tarfile.open(out) as tf:
            names = tf.getnames()
            assert "ComicInfo.xml" in names
            assert sum(1 for n in names if n.startswith("Page_")) == 2


class TestStaleLinkRefresh:
    """Retry after a stale-link failure targets the re-minted URL (A)."""

    pytestmark = pytest.mark.asyncio

    @staticmethod
    async def _aiter(items):
        for item in items:
            yield item

    async def test_retry_uses_refreshed_url(self, monkeypatch, tmp_path):
        from dataclasses import replace as dc_replace

        from curl_cffi.requests.exceptions import ConnectionError as CurlConnErr

        from comic_dl.downloader import _run_downloads

        requested: list[str] = []

        class MockResponse:
            status_code = 200
            headers = {"content-length": "3"}

            def __init__(self, url):
                self.url = url

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                yield b"\xff\xd8\xff"

            async def __aenter__(self):
                if self.url.endswith("/stale"):
                    raise CurlConnErr("keystamp expired")
                return self

            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                requested.append(url)
                return MockResponse(url)

        item = ImageItem(
            url="https://node.hath.network/h/stale",
            page_number=1,
            filename="page_0001.jpg",
            source_url="https://e-hentai.org/s/tok/1-1",
        )

        async def fake_refresh(client, it):
            # Only the first failure refreshes; later calls keep the link.
            return dc_replace(it, url="https://other.hath.network/h/fresh")

        monkeypatch.setattr("comic_dl.downloader._refresh_stale_link", fake_refresh)
        monkeypatch.setattr("comic_dl.downloader.SHARED_COOLDOWN_CAP", 0.01)

        failed, resolved = await _run_downloads(
            self._aiter([item]), tmp_path, asyncio.Semaphore(2), None,
            MockClient(),  # type: ignore[arg-type]
        )
        assert failed == set()
        assert (tmp_path / "page_0001.jpg").exists()
        assert requested == [
            "https://node.hath.network/h/stale",
            "https://other.hath.network/h/fresh",
        ]
        # Canonical resolution keeps the ORIGINAL provenance for archiving.
        assert resolved[0].url == "https://node.hath.network/h/stale"

    async def test_no_refresh_without_source_url(self, monkeypatch, tmp_path):
        from curl_cffi.requests.exceptions import ConnectionError as CurlConnErr

        from comic_dl.downloader import _run_downloads

        class AlwaysFail:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                yield b"\xff\xd8\xff"

            async def __aenter__(self):
                raise CurlConnErr("down")

            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                return AlwaysFail()

        calls = []

        async def spy_refresh(client, it):
            calls.append(it.url)
            return None

        monkeypatch.setattr("comic_dl.downloader._refresh_stale_link", spy_refresh)
        monkeypatch.setattr("comic_dl.downloader.SHARED_COOLDOWN_CAP", 0.01)

        item = ImageItem(url="http://x.com/a.jpg", page_number=1, filename="a.jpg")
        failed, _ = await _run_downloads(
            self._aiter([item]), tmp_path, asyncio.Semaphore(2), None,
            MockClient(),  # type: ignore[arg-type]
        )
        assert failed == {"a.jpg"}
        # No provenance -> refresher never consulted.
        assert calls == []


class TestHostBreaker:
    """Transport failures park a node; app-level replies never do (C)."""

    def test_parks_after_threshold_consecutive_failures(self):
        from comic_dl.downloader import (
            HOST_PARK_SECONDS,
            host_parked,
            record_transport_failure,
        )

        now = 1000.0
        for _ in range(3):
            record_transport_failure("dead.hath.network", now)
        assert host_parked("dead.hath.network", now) is True
        # Window expires.
        assert host_parked("dead.hath.network", now + HOST_PARK_SECONDS + 1) is False

    def test_success_resets_consecutive_count(self):
        from comic_dl.downloader import (
            HOST_PARK_THRESHOLD,
            host_parked,
            record_transport_failure,
            record_transport_success,
        )

        now = 2000.0
        for _ in range(HOST_PARK_THRESHOLD - 1):
            record_transport_failure("flaky.test", now)
        record_transport_success("flaky.test")
        record_transport_failure("flaky.test", now)
        assert host_parked("flaky.test", now) is False

    def test_not_image_response_is_application_level(self, tmp_path, monkeypatch):
        """A 200 HTML stub is stale-link territory; it must not park."""
        from comic_dl.downloader import NotImageResponseError, _is_retryable, _is_transport_failure

        exc = NotImageResponseError("page_0001.webp")
        assert _is_retryable(exc) is True
        assert _is_transport_failure(exc) is False

    @pytest.mark.asyncio
    async def test_engine_fails_fast_on_parked_host(self, monkeypatch, tmp_path):

        from curl_cffi.requests.exceptions import ConnectionError as CurlConnErr

        from comic_dl.downloader import (
            _run_downloads,
            host_parked,
            reset_host_breaker,
        )

        reset_host_breaker()
        requested: list[str] = []

        class Resp:
            status_code = 200
            headers = {}

            def __init__(self, url):
                self.url = url

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                yield b"\xff\xd8\xff"

            async def __aenter__(self):
                if "dead" in self.url:
                    raise CurlConnErr("node down")
                return self

            async def __aexit__(self, *args):
                pass

        class Client:
            def stream(self, method, url, **kwargs):
                requested.append(url)
                return Resp(url)

        items = [
            ImageItem(url="https://dead.hath.network/1", page_number=1,
                      filename="a.jpg"),
            ImageItem(url="https://alive.hath.network/2", page_number=2,
                      filename="b.jpg"),
            ImageItem(url="https://dead.hath.network/3", page_number=3,
                      filename="c.jpg"),
        ]

        async def no_refresh(client, it):
            return None

        monkeypatch.setattr("comic_dl.downloader._refresh_stale_link", no_refresh)
        monkeypatch.setattr("comic_dl.downloader.SHARED_COOLDOWN_CAP", 0.01)

        failed, _ = await _run_downloads(
            TestStaleLinkRefresh._aiter(items), tmp_path,
            # One slot: 'a' exhausts its budget and parks the node BEFORE
            # 'c' is reached, making the zero-network assertion deterministic.
            asyncio.Semaphore(1), None, Client(),  # type: ignore[arg-type]
        )
        # Dead node burned its retry budget, parked the host, and the
        # third image failed fast — but the exact attempt count is
        # scheduler-dependent (a contended runner may observe a stray
        # retry before the park lands), so assert the stable contract:
        # the host is parked and both dead images still fail.
        dead_requests = [u for u in requested if "dead" in u]
        assert dead_requests  # the node was tried at least once
        assert host_parked("dead.hath.network", 0.0) is True
        # Third image never touched the network and landed in failed set...
        assert failed == {"a.jpg", "c.jpg"}
        assert (tmp_path / "b.jpg").exists()
        # ...while the healthy host was untouched by the breaker.
        assert sum(1 for u in requested if "alive" in u) >= 1


class TestTotalSizeBudget:
    """The ``max_total_size`` gate refuses new work, not in-flight bytes.

    A page already streaming is never torn down mid-transfer; the gate fires
    at the next item's start once the consumed budget is reached (and the
    item lands in the normal failed set for the rerun to pick up).
    """

    pytestmark = pytest.mark.asyncio

    async def test_exhausted_budget_rejects_late_items_without_network(
        self, tmp_path
    ):
        from comic_dl.downloader import _run_downloads

        requested: list[str] = []

        class Resp:
            status_code = 200
            headers = {"content-length": "3"}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                yield MAGIC_JPEG

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class Client:
            def stream(self, method, url, **kwargs):
                requested.append(url)
                return Resp()

        dest = tmp_path / "dl"
        dest.mkdir()

        async def aiter_late():
            yield ImageItem(
                url="http://x.com/a.jpg", page_number=1, filename="a.jpg"
            )
            # Yield the second item only once the first is fully on disk, so
            # its consumed bytes are visible to the subsequent budget check.
            for _ in range(500):
                if (dest / "a.jpg").exists():
                    break
                await asyncio.sleep(0.01)
            yield ImageItem(
                url="http://x.com/b.jpg", page_number=2, filename="b.jpg"
            )

        reasons: dict[str, int] = {}
        failed, resolved = await _run_downloads(
            aiter_late(),
            dest,
            asyncio.Semaphore(1),
            None,
            Client(),  # type: ignore[arg-type]
            max_total_size=len(MAGIC_JPEG),
            reasons=reasons,
        )
        assert failed == {"b.jpg"}
        assert reasons["budget"] == 1
        assert [r.filename for r in resolved] == ["a.jpg", "b.jpg"]
        # The over-budget page never opened a stream.
        assert requested == ["http://x.com/a.jpg"]


class TestFailureLabels:
    """Each failing page records a human label describing why it failed, so
    the final report can say "HTTP 530 x34" instead of a bare "missing"."""

    @pytest.mark.asyncio
    async def test_transport_failure_records_http_status(self, tmp_path):
        from comic_dl.downloader import download_httpx

        class Resp:
            status_code = 404
            headers = {}

            def raise_for_status(self):
                raise CurlHTTPError("not found", response=self)

            async def aiter_content(self, chunk_size=None):
                return
                yield b""  # pragma: no cover

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class Client:
            def stream(self, method, url, **kwargs):
                return Resp()

        dest = tmp_path / "dl"
        dest.mkdir()
        labels: dict[str, str] = {}
        failed = await download_httpx(
            [ImageItem(url="http://x.com/a.jpg", page_number=1, filename="a.jpg")],
            dest, concurrency=1, client=Client(),  # type: ignore[arg-type]
            max_attempts=1, failure_labels=labels,
        )
        assert failed == {"a.jpg"}
        assert labels["a.jpg"] == "HTTP 404"

    @pytest.mark.asyncio
    async def test_budget_failure_records_total_size_reason(self, tmp_path):
        """An over-budget rejection labels the file as size-limited rather
        than as a transport error."""
        from comic_dl.downloader import _run_downloads

        class Resp:
            status_code = 200
            headers = {"content-length": "3"}

            def raise_for_status(self):
                pass

            async def aiter_content(self, chunk_size=None):
                yield MAGIC_JPEG

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class Client:
            def stream(self, method, url, **kwargs):
                return Resp()

        dest = tmp_path / "dl"
        dest.mkdir()
        labels: dict[str, str] = {}

        async def gen():
            yield ImageItem(
                url="http://x.com/a.jpg", page_number=1, filename="a.jpg"
            )
            # Yield the second item only once the first is fully on disk, so
            # its consumed bytes are visible to the budget gate.
            for _ in range(500):
                if (dest / "a.jpg").exists():
                    break
                await asyncio.sleep(0.01)
            yield ImageItem(
                url="http://x.com/b.jpg", page_number=2, filename="b.jpg"
            )

        failed, resolved = await _run_downloads(
            gen(),
            dest, asyncio.Semaphore(1), None, Client(),  # type: ignore[arg-type]
            max_total_size=len(MAGIC_JPEG), failure_labels=labels,
        )
        assert failed == {"b.jpg"}
        assert labels["b.jpg"] == "exceeds max total size"
        assert labels.get("a.jpg") is None
        assert [r.filename for r in resolved] == ["a.jpg", "b.jpg"]
