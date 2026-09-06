"""Network-safety / SSRF regression tests.

These run fully offline against a loopback asyncio server. Three layers are
covered:

1. ``validate_request_url`` refuses non-http(s) schemes and hosts that resolve
   to loopback/private/link-local/metadata addresses.
2. ``downloader._open_stream`` follows redirects manually and re-validates
   every hop, so a hostile redirect can never tunnel onto an internal endpoint,
   and caps the number of hops.
3. ``scrapers.base._timeout_get`` applies the same per-hop validation to the
   scrape path, so a page that is public today cannot 302 its way onto a
   loopback/private/metadata address.
"""

from __future__ import annotations

import pytest

from comic_dl import downloader, utils
from comic_dl.downloader import RequestBlockedError, _open_stream
from comic_dl.scrapers.base import BaseScraper

from ._server import FakeHttpServer, NetHttpClient

_timeout_get = BaseScraper._timeout_get


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:80/x",
        "http://localhost/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://172.16.0.1/x",
        "http://100.64.0.1/x",
        "http://100.64.5.7/x",
        "http://100.127.255.255/x",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
        "file:///etc/passwd",
        "gopher://x/y",
        "ftp://x/y",
        "http:///nohost",
    ],
)
def test_validate_request_url_blocks_hostile(url: str) -> None:
    with pytest.raises(RequestBlockedError):
        utils.validate_request_url(url)


@pytest.mark.parametrize(
    "url",
    ["https://example.com/x", "http://www.pawchive.pw/a/b"],
)
def test_validate_request_url_allows_public(url: str) -> None:
    assert utils.validate_request_url(url) == url


@pytest.mark.asyncio
async def test_open_stream_blocks_private_url_before_connect() -> None:
    """A private URL is refused before any connection is opened."""
    routes = {"/img": (200, {"Content-Type": "image/jpeg"}, b"\xff\xd8\xff")}
    async with FakeHttpServer(routes) as srv:
        client = NetHttpClient(srv.host, srv.port)
        with pytest.raises(RequestBlockedError):
            await _open_stream(client, f"{srv.url}/img")
        assert srv.hits == []


@pytest.mark.asyncio
async def test_open_stream_follows_redirect_each_hop_validated() -> None:
    """Redirects are followed hop-by-hop and the final body returned. The
    guard is temporarily relaxed to localhost so the loopback server stands in
    as the final destination."""
    routes = {
        "/a": (302, {"Location": "/b"}, b""),
        "/b": (200, {"Content-Type": "image/jpeg"}, b"\xff\xd8\xff"),
    }
    async with FakeHttpServer(routes) as srv:
        client = NetHttpClient(srv.host, srv.port)
        monkeypatching = pytest.MonkeyPatch()
        monkeypatching.setattr(downloader, "validate_request_url_async", _fake_permissive)
        monkeypatching.setattr(downloader, "resolve_redirect_url_async", _fake_resolve)
        try:
            resp = await _open_stream(client, f"{srv.url}/a")
            assert resp.status_code == 200
            body = b"".join([c async for c in resp.aiter_content()])
            assert body == b"\xff\xd8\xff"
            assert srv.hits == ["/a", "/b"]
        finally:
            monkeypatching.undo()


@pytest.mark.asyncio
async def test_open_stream_caps_redirect_loop() -> None:
    """An endless redirect chain is aborted at MAX_REDIRECTS instead of
    hanging."""
    routes = {"/r": (302, {"Location": "/r"}, b"")}
    async with FakeHttpServer(routes) as srv:
        client = NetHttpClient(srv.host, srv.port)
        monkeypatching = pytest.MonkeyPatch()
        monkeypatching.setattr(downloader, "validate_request_url_async", _fake_permissive)
        monkeypatching.setattr(downloader, "resolve_redirect_url_async", _fake_resolve)
        try:
            with pytest.raises(RequestBlockedError):
                await _open_stream(client, f"{srv.url}/r")
            assert len(srv.hits) <= utils.MAX_REDIRECTS + 1
        finally:
            monkeypatching.undo()


@pytest.mark.asyncio
async def test_timeout_get_blocks_private_url_before_connect() -> None:
    """The scrape path refuses a private URL before any request is made."""
    routes = {"/img": (200, {"Content-Type": "text/html"}, b"<html></html>")}
    async with FakeHttpServer(routes) as srv:
        client = NetHttpClient(srv.host, srv.port)
        with pytest.raises(RequestBlockedError):
            await _timeout_get(f"{srv.url}/img", client)
        assert srv.hits == []


@pytest.mark.asyncio
async def test_timeout_get_follows_redirect_each_hop_validated() -> None:
    """Redirect hops in the scrape path are followed manually and re-validated
    (guard relaxed to localhost so the loopback server stands in)."""
    routes = {
        "/a": (302, {"Location": "/b"}, b""),
        "/b": (200, {"Content-Type": "text/html"}, b"<html></html>"),
    }
    async with FakeHttpServer(routes) as srv:
        client = NetHttpClient(srv.host, srv.port)
        monkeypatching = pytest.MonkeyPatch()
        from comic_dl.scrapers import base as scrape_base

        monkeypatching.setattr(scrape_base, "validate_request_url_async", _fake_permissive)
        monkeypatching.setattr(scrape_base, "resolve_redirect_url_async", _fake_resolve)
        try:
            resp = await _timeout_get(f"{srv.url}/a", client)
            assert resp.status_code == 200
            assert srv.hits == ["/a", "/b"]
        finally:
            monkeypatching.undo()


@pytest.mark.asyncio
async def test_timeout_get_caps_redirect_loop() -> None:
    """An endless redirect chain in the scrape path is aborted at
    MAX_REDIRECTS instead of hanging."""
    routes = {"/r": (302, {"Location": "/r"}, b"")}
    async with FakeHttpServer(routes) as srv:
        client = NetHttpClient(srv.host, srv.port)
        monkeypatching = pytest.MonkeyPatch()
        from comic_dl.scrapers import base as scrape_base

        monkeypatching.setattr(scrape_base, "validate_request_url_async", _fake_permissive)
        monkeypatching.setattr(scrape_base, "resolve_redirect_url_async", _fake_resolve)
        try:
            with pytest.raises(RequestBlockedError):
                await _timeout_get(f"{srv.url}/r", client)
            assert len(srv.hits) <= utils.MAX_REDIRECTS + 1
        finally:
            monkeypatching.undo()


@pytest.mark.asyncio
async def test_timeout_get_head_method() -> None:
    """HEAD requests go through the same validated-request path."""
    routes = {"/img": (200, {"Content-Type": "image/jpeg"}, b"")}
    async with FakeHttpServer(routes) as srv:
        client = NetHttpClient(srv.host, srv.port)
        monkeypatching = pytest.MonkeyPatch()
        import comic_dl.scrapers.base as scrape_base

        monkeypatching.setattr(scrape_base, "validate_request_url_async", _fake_permissive)
        try:
            resp = await _timeout_get(f"{srv.url}/img", client, method="HEAD")
            assert resp.status_code == 200
            assert srv.hits == ["/img"]
        finally:
            monkeypatching.undo()


@pytest.mark.asyncio
async def test_ehentai_gallery_page_validates_redirect_hops() -> None:
    """e-hentai gallery fetches follow redirects manually, re-validating hops."""
    routes = {
        "/g/123/abc/": (302, {"Location": "/b"}, b""),
        "/b": (
            200,
            {"Content-Type": "text/html"},
            b'<html><body><div id="gdt"><a href="/s/tok/1">x</a></div></body></html>',
        ),
    }
    async with FakeHttpServer(routes) as srv:
        client = NetHttpClient(srv.host, srv.port)
        monkeypatching = pytest.MonkeyPatch()
        import comic_dl.scrapers.base as scrape_base

        monkeypatching.setattr(scrape_base, "validate_request_url_async", _fake_permissive)
        monkeypatching.setattr(scrape_base, "resolve_redirect_url_async", _fake_resolve)
        try:
            from comic_dl.scrapers.sites.ehentai import _fetch_gallery_page

            urls = await _fetch_gallery_page(f"{srv.url}/g/123/abc/", client)
            assert srv.hits == ["/g/123/abc/", "/b"]
            assert urls == [f"{srv.url}/s/tok/1"]
        finally:
            monkeypatching.undo()


@pytest.mark.asyncio
async def test_pawchive_full_resolution_validates_redirect_hops() -> None:
    """pawchive HEAD probes follow redirects manually, re-validating hops."""
    routes = {
        "/data/x.jpg": (302, {"Location": "/y"}, b""),
        "/y": (200, {"Content-Type": "image/jpeg"}, b""),
    }
    async with FakeHttpServer(routes) as srv:
        client = NetHttpClient(srv.host, srv.port)
        monkeypatching = pytest.MonkeyPatch()
        import comic_dl.scrapers.base as scrape_base

        monkeypatching.setattr(scrape_base, "validate_request_url_async", _fake_permissive)
        monkeypatching.setattr(scrape_base, "resolve_redirect_url_async", _fake_resolve)
        try:
            from comic_dl.scrapers.sites.pawchive import _try_full_resolution

            result = await _try_full_resolution(
                client, f"{srv.url}/thumbnail/data/x.jpg"
            )
            assert result == f"{srv.url}/data/x.jpg"
            assert srv.hits == ["/data/x.jpg", "/y"]
        finally:
            monkeypatching.undo()


async def _fake_permissive(url: str) -> str:
    return url


async def _fake_resolve(base: str, location: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, location)
