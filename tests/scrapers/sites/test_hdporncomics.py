from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

import comic_dl.scrapers.sites.hdporncomics as hd_module
from comic_dl.errors import ScrapeError
from comic_dl.scrapers.sites.hdporncomics import (
    DOMAIN,
    HDPornComicsScraper,
    _extract_artists,
    _extract_images,
    _extract_post_id,
    _extract_tags,
    _strip_title_suffix,
    is_gallery_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

GALLERY_URL = "https://hdporncomics.com/cinderfrost-chapter-3-furries-sex-comic/"

GALLERY_PAGE = """
<html lang="en-US"><head>
<title>CinderFrost Chapter 3 comic porn | HD Porn Comics</title>
<meta property="og:description" content="Read CinderFrost Chapter 3."/>
</head><body class="post-template-default single single-post postid-62383">
<h1>CinderFrost Chapter 3 comic porn</h1>
<div id="imgBox"><img alt="CinderFrost Chapter 3 comic porn thumbnail 001" src="https://l.hdporncomics.com/thumbs/aaaa/cinderfrost-chapter-3-001.jpg"/></div>
<div class="reader">
<img alt="CinderFrost Chapter 3 comic porn sex 1" src="https://l.hdporncomics.com/thumbs/bbbb/cinderfrost-chapter-3-001.jpg"/>
<img alt="CinderFrost Chapter 3 comic porn sex 2" src="https://l.hdporncomics.com/thumbs/bbbb/cinderfrost-chapter-3-002.jpg"/>
<img alt="CinderFrost Chapter 3 comic porn sex 3" src="https://l.hdporncomics.com/thumbs/bbbb/cinderfrost-chapter-3-003.jpg"/>
</div>
<div class="related">
<img alt="Other post" src="https://l.hdporncomics.com/thumbs/cccc/other-001.jpg"/>
</div>
<a href="https://hdporncomics.com/tag/full-color/">Full Color</a>
<a href="https://hdporncomics.com/tag/full-color/">Full Color</a>
<a href="/artist/demicoeur/">Demicoeur</a>
</body></html>
"""


class _FakeSession:
    def __init__(self, html):
        self.html = html
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        return 200, {}, self.html.encode()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(hd_module, "jar_cookies_for", lambda url: {})
    from comic_dl import cf as cf_module

    cf_module._replay_dead.clear()


class TestUrlPatterns:
    def test_valid_gallery_urls(self):
        assert is_gallery_url(GALLERY_URL)
        assert is_gallery_url("https://www.hdporncomics.com/foo-bar-sex-comic")

    def test_invalid_gallery_urls(self):
        assert not is_gallery_url("")
        assert not is_gallery_url("https://hdporncomics.com/")
        assert not is_gallery_url("https://hdporncomics.com/comics/tags/")
        assert not is_gallery_url("https://hdporncomics.com/tag/full-color/")
        assert not is_gallery_url("https://other.com/foo-sex-comic/")

    def test_matches_url(self):
        scraper = HDPornComicsScraper()
        assert scraper.matches_url(GALLERY_URL)
        assert not scraper.matches_url("https://hdporncomics.com/")

    def test_strip_title(self):
        assert _strip_title_suffix("CinderFrost Chapter 3 comic porn") == "CinderFrost Chapter 3"
        assert _strip_title_suffix("Plain Title") == "Plain Title"


class TestExtraction:
    def test_images_largest_group_no_thumbs(self):
        soup = BeautifulSoup(GALLERY_PAGE, "lxml")
        images = _extract_images(soup, GALLERY_URL)
        assert [i.page_number for i in images] == [1, 2, 3]
        assert all("/thumbs/bbbb/" in i.url for i in images)

    def test_images_empty(self):
        assert _extract_images(BeautifulSoup("<html></html>", "lxml"), GALLERY_URL) == []

    def test_post_id(self):
        soup = BeautifulSoup(GALLERY_PAGE, "lxml")
        assert _extract_post_id(soup) == "62383"
        assert _extract_post_id(BeautifulSoup("<html></html>", "lxml")) == ""

    def test_tags_artists(self):
        soup = BeautifulSoup(GALLERY_PAGE, "lxml")
        assert _extract_tags(soup).count("Full Color") == 2
        assert _extract_artists(soup) == ["Demicoeur"]


class TestHDPornComicsScraper:
    @pytest.mark.asyncio
    async def test_scrape_plain_replay_wins(self, monkeypatch):
        monkeypatch.setattr(hd_module, "jar_cookies_for", lambda url: {"cf_clearance": "abc"})
        monkeypatch.setattr(hd_module.webview, "session_enabled", lambda: True)
        monkeypatch.setattr(
            hd_module.webview,
            "ensure_session",
            lambda url: (_ for _ in ()).throw(AssertionError("no session on fresh cookie")),
        )
        scraper = HDPornComicsScraper()
        meta = await scraper.scrape(
            GALLERY_URL, _MockSession(lambda url: _MockResponse(GALLERY_PAGE))
        )

        assert meta.series_title == "CinderFrost Chapter 3"
        assert meta.total_pages == 3
        assert meta.language == "en"

    @pytest.mark.asyncio
    async def test_scrape_session_fallback(self, monkeypatch):
        monkeypatch.setattr(hd_module, "jar_cookies_for", lambda url: {"cf_clearance": "stale"})
        challenged = _MockResponse(b"Just a moment", status=403)
        challenged.headers = {"server": "cloudflare"}
        fake = _FakeSession(GALLERY_PAGE)

        async def _fake_ensure_session(url):
            return fake

        monkeypatch.setattr(hd_module.webview, "session_enabled", lambda: True)
        monkeypatch.setattr(hd_module.webview, "ensure_session", _fake_ensure_session)
        scraper = HDPornComicsScraper()
        meta = await scraper.scrape(GALLERY_URL, _MockSession(lambda url: challenged))

        assert meta.series_title == "CinderFrost Chapter 3"
        assert meta.total_pages == 3
        assert fake.calls, "expected a session request after the challenged replay"

    @pytest.mark.asyncio
    async def test_scrape_challenge_without_session_raises_hint(self, monkeypatch):
        challenged = _MockResponse(b"Just a moment", status=403)
        challenged.headers = {"server": "cloudflare"}
        monkeypatch.setattr(hd_module.webview, "session_enabled", lambda: False)

        async def _no_retry(url):
            return False

        monkeypatch.setattr("comic_dl.cf.handle_challenge", _no_retry)
        scraper = HDPornComicsScraper()
        with pytest.raises(ScrapeError, match="Cloudflare challenged"):
            await scraper.scrape(GALLERY_URL, _MockSession(lambda url: challenged))

    @pytest.mark.asyncio
    async def test_homepage_url_rejected(self):
        scraper = HDPornComicsScraper()
        with pytest.raises(ScrapeError, match="listing page"):
            await scraper.scrape(
                "https://hdporncomics.com/",
                _MockSession(lambda url: _MockResponse("<html></html>")),
            )

    def test_registered(self):
        from comic_dl.scrapers import list_sources

        by_domain = {e.domain: e for e in list_sources()}
        assert by_domain[DOMAIN].name == "hdporncomics"
        assert not by_domain[DOMAIN].has_series
