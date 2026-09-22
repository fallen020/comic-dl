from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.errors import ScrapeError
from comic_dl.scrapers.sites._vcomics import (
    _chapter_number,
    _chapter_number_from_slug,
    _decode_rsc_string,
    _extract_cover,
    _extract_description,
    _extract_images,
    _extract_series_title,
    _locked_price,
    _post_id_from_series_html,
    _series_slug_from_url,
    chapter_url_re,
    series_url_re,
)
from comic_dl.scrapers.sites.nyxscans import DOMAIN as NYX_DOMAIN
from comic_dl.scrapers.sites.nyxscans import NyxScansScraper
from comic_dl.scrapers.sites.vortexscans import DOMAIN as VX_DOMAIN
from comic_dl.scrapers.sites.vortexscans import VortexScansScraper
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "shadow-slave"
SERIES_URL = f"https://vortexscans.org/series/{SLUG}"
CHAPTER_URL = f"https://vortexscans.org/series/{SLUG}/chapter-1"

SERIES_PAGE = """
<html lang="en"><head>
    <title>Shadow Slave Manhwa - Vortex Scans</title>
    <meta property="og:description" content="I will survive."/>
    <meta property="og:image" content="https://vortexscans.org/api/og-image/series/shadow-slave/x"/>
</head><body>
<h1 itemprop="name">Shadow Slave</h1>
<img alt="Cover of Shadow Slave" src="https://storage.vortexscans.org/cover.webp"/>
<dl><dt>Status</dt><dd>ONGOING</dd><dt>Type</dt><dd>Manhwa</dd></dl>
<a href="/series/shadow-slave/chapter-9">Chapter 9</a>
<a href="/series/shadow-slave/chapter-1">Chapter 1</a>
<script>self.$R=self.$R||{};$R[96]={data:$R[97]={slug:"shadow-slave",post:$R[98]={id:611,slug:"shadow-slave",postTitle:"Shadow Slave",postContent:"\\x3Cp>Full synopsis here.\\x3C/p>"}}}}</script>
</body></html>
"""

LOCKED_PAGE = """
<html lang="en"><head><title>Shadow Slave Chapter 23 | Vortex Scans</title></head><body>
<div class="comic-reader-stage"><div>Shadow Slave</div><div>Chapter</div><div>23</div>
<div>Locked Chapter</div><div>This premium chapter is waiting to be unlocked. 100 coins. Please login.</div></div>
</body></html>
"""

CHAPTER_PAGE = """
<html lang="en"><head><title>Shadow Slave Chapter 1 | Vortex Scans</title></head><body>
<img data-reader-page-image="true" src="https://storage.vortexscans.org/upload/series/shadow-slave/u/page-01.webp" alt="Shadow Slave Chapter 1 Page 1"/>
<img data-reader-page-image="true" src="https://storage.vortexscans.org/upload/series/shadow-slave/u/page-02.webp" alt="Shadow Slave Chapter 1 Page 2"/>
<img src="https://vortexscans.org/logo.png"/>
</body></html>
"""

CHAPTERS_PAGE_1 = {
    "post": {
        "chapters": [
            {"id": i, "slug": f"chapter-{i}", "number": i, "title": ""} for i in range(1, 11)
        ]
    },
    "totalChapterCount": 12,
}
CHAPTERS_PAGE_2 = {
    "post": {
        "chapters": [
            {"id": 11, "slug": "chapter-11", "number": 11, "title": ""},
            {"id": 12, "slug": "chapter-12", "number": 12, "title": "Finale"},
        ]
    },
    "totalChapterCount": 12,
}
POSTS_SEARCH = {
    "posts": [
        {"slug": "other", "genres": [{"name": "Nope"}]},
        {"slug": SLUG, "genres": [{"name": "Action"}, {"name": "Fantasy"}]},
    ]
}


def _handler(url):
    if url == SERIES_URL:
        return _MockResponse(SERIES_PAGE)
    if "api/chapters" in url:
        if "skip=10" in url:
            return _MockResponse(json_data=CHAPTERS_PAGE_2)
        return _MockResponse(json_data=CHAPTERS_PAGE_1)
    if "api/posts" in url:
        return _MockResponse(json_data=POSTS_SEARCH)
    return _MockResponse(CHAPTER_PAGE)


class TestUrlPatterns:
    def test_series(self):
        assert series_url_re(VX_DOMAIN).match(SERIES_URL)
        assert not series_url_re(VX_DOMAIN).match(CHAPTER_URL)
        assert series_url_re(NYX_DOMAIN).match("https://nyxscans.com/series/x")
        assert not series_url_re(VX_DOMAIN).match("https://nyxscans.com/series/x")

    def test_chapter(self):
        assert chapter_url_re(VX_DOMAIN).match(CHAPTER_URL)
        assert chapter_url_re(VX_DOMAIN).match(f"{SERIES_URL}/chapter-1.5")
        assert not chapter_url_re(VX_DOMAIN).match(SERIES_URL)

    def test_matches(self):
        scraper = VortexScansScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert scraper.matches_series_url(SERIES_URL)
        assert not scraper.matches_series_url(CHAPTER_URL)
        assert not scraper.matches_url("https://vortexscans.org/")

    def test_slug(self):
        assert _series_slug_from_url(SERIES_URL) == SLUG
        assert _series_slug_from_url(CHAPTER_URL) == SLUG
        assert _series_slug_from_url("https://vortexscans.org/") == ""

    def test_post_id_exact_slug(self):
        assert _post_id_from_series_html(SERIES_PAGE, SLUG) == 611
        assert _post_id_from_series_html(SERIES_PAGE, "other") is None
        assert _post_id_from_series_html("<html></html>", SLUG) is None

    def test_chapter_number(self):
        assert _chapter_number({"number": 9, "slug": "chapter-9"}) == "9"
        assert _chapter_number({"number": 1.5, "slug": "chapter-1.5"}) == "1.5"
        assert _chapter_number({"slug": "chapter-3"}) == "3"
        assert _chapter_number({}) is None
        assert _chapter_number_from_slug(CHAPTER_URL) == "1"


class TestExtraction:
    def test_series_title(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_series_title(soup, {}) == "Shadow Slave"

    def test_images(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        images = _extract_images(soup, CHAPTER_URL)
        assert len(images) == 2
        assert images[0].url.endswith("page-01.webp")
        assert [i.page_number for i in images] == [1, 2]

    def test_cover_prefers_cover_alt(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_cover(soup, {}) == "https://storage.vortexscans.org/cover.webp"

    def test_cover_falls_back_to_og(self):
        from comic_dl.scrapers.base import meta_index

        soup = BeautifulSoup(
            '<html><head><meta property="og:image" content="https://x/og.webp"/>'
            "</head><body></body></html>",
            "lxml",
        )
        assert _extract_cover(soup, meta_index(soup)) == "https://x/og.webp"

    def test_description_from_post_content(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_description(SERIES_PAGE, soup, {}) == "Full synopsis here."

    def test_description_falls_back_to_og(self):
        from comic_dl.scrapers.base import meta_index

        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert "I will survive" in _extract_description("<html></html>", soup, meta_index(soup))

    def test_decode_rsc_string(self):
        assert _decode_rsc_string("\\x3Cp>Hi\\x3C/p>") == "<p>Hi</p>"

    def test_locked_price(self):
        soup = BeautifulSoup(LOCKED_PAGE, "lxml")
        assert _locked_price(soup) == "100"
        assert _locked_price(BeautifulSoup(CHAPTER_PAGE, "lxml")) is None


class TestVortexScraper:
    @pytest.mark.asyncio
    async def test_scrape_chapter_success(self):
        scraper = VortexScansScraper()
        meta = await scraper.scrape(CHAPTER_URL, _MockSession(_handler))

        assert meta.series_title == "Shadow Slave"
        assert meta.chapter_title == "Chapter 1"
        assert meta.chapter_number == "1"
        assert meta.total_pages == 2
        assert meta.language == "en"

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        scraper = VortexScansScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(
                CHAPTER_URL, _MockSession(lambda url: _MockResponse("<html><body></body></html>"))
            )

    @pytest.mark.asyncio
    async def test_scrape_locked_chapter_raises(self):
        scraper = VortexScansScraper()
        with pytest.raises(ScrapeError, match="locked"):
            await scraper.scrape(
                "https://vortexscans.org/series/shadow-slave/chapter-23",
                _MockSession(lambda url: _MockResponse(LOCKED_PAGE)),
            )

    @pytest.mark.asyncio
    async def test_homepage_url_rejected(self):
        scraper = VortexScansScraper()
        with pytest.raises(ScrapeError, match="listing page"):
            await scraper.scrape(
                "https://vortexscans.org/", _MockSession(lambda url: _MockResponse("<html></html>"))
            )

    @pytest.mark.asyncio
    async def test_scrape_series_paginates_api(self):
        scraper = VortexScansScraper()
        series = await scraper.scrape_series(SERIES_URL, _MockSession(_handler))

        assert series.series_title == "Shadow Slave"
        assert series.title_no == SLUG
        assert len(series.chapters) == 12
        assert series.chapters[0]["episode_no"] == "1"
        assert series.chapters[-1]["title"] == "Finale"
        assert series.chapters[-1]["url"] == f"{SERIES_URL}/chapter-12"

    def test_registered(self):
        from comic_dl.scrapers import list_sources

        by_domain = {e.domain: e for e in list_sources()}
        assert by_domain[VX_DOMAIN].name == "vortexscans"
        assert by_domain[VX_DOMAIN].has_series
        assert by_domain[NYX_DOMAIN].name == "nyxscans"
        assert by_domain[NYX_DOMAIN].has_series
        assert NyxScansScraper().api_base == "https://api.nyxscans.com"
