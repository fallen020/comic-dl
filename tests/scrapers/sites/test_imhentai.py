from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.imhentai import (
    DOMAIN,
    IMHentaiScraper,
    _clean_tag_name,
    _extract_language,
    _extract_page_count,
    _extract_reader_image,
    _extract_tags,
    _extract_view_urls,
    _gallery_id_from_url,
    is_gallery_url,
    is_view_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

GID = "1742742"
GALLERY_URL = f"https://imhentai.xxx/gallery/{GID}/"
VIEW_URL = f"https://imhentai.xxx/view/{GID}/1/"

GALLERY_PAGE = """
<html><head><title>Hoobamon - Lux Full Nelson - IMHentai</title></head><body>
<h1>Hoobamon - Lux Full Nelson</h1>
<ul>
<li>Languages:english293095</li>
<li>Pages: 3</li>
</ul>
<a href="/tag/dark-skin/">dark skin118844</a>
<a href="/tag/english/">english293095</a>
<input id="load_pages" value="3"/>
<div class="thumbs">
<a href="/view/1742742/1/"><img src="https://m11.imhentai.xxx/033/hash/1t.jpg"/></a>
<a href="/view/1742742/2/"><img src="https://m11.imhentai.xxx/033/hash/2t.jpg"/></a>
<a href="/view/1742742/2/"><img src="https://m11.imhentai.xxx/033/hash/2t.jpg"/></a>
<a href="/view/1742742/3/"><img src="https://m11.imhentai.xxx/033/hash/3t.jpg"/></a>
</div>
</body></html>
"""


def _view_page(n):
    return f"""
<html><head><title>Hoobamon - Lux Full Nelson - Page {n} - IMHentai</title></head><body>
<img id="gimg" src="https://m11.imhentai.xxx/033/smbqi3gpav/{n}.webp"/>
</body></html>
"""


def _handler(url):
    if "/gallery/" in url:
        return _MockResponse(GALLERY_PAGE)
    n = url.rstrip("/").rsplit("/", 1)[-1]
    return _MockResponse(_view_page(n))


class TestUrlPatterns:
    def test_valid_gallery_urls(self):
        assert is_gallery_url(GALLERY_URL)
        assert is_gallery_url("https://imhentai.xxx/gallery/1")
        assert is_gallery_url("https://www.imhentai.xxx/gallery/99/")

    def test_invalid_gallery_urls(self):
        assert not is_gallery_url("")
        assert not is_gallery_url("https://imhentai.xxx/")
        assert not is_gallery_url(VIEW_URL)
        assert not is_gallery_url("https://other.xxx/gallery/1/")

    def test_valid_view_urls(self):
        assert is_view_url(VIEW_URL)
        assert is_view_url("https://imhentai.xxx/view/5/27")

    def test_invalid_view_urls(self):
        assert not is_view_url("")
        assert not is_view_url(GALLERY_URL)
        assert not is_view_url("https://imhentai.xxx/view/5/")

    def test_gallery_id(self):
        assert _gallery_id_from_url(GALLERY_URL) == GID
        assert _gallery_id_from_url(VIEW_URL) == GID
        assert _gallery_id_from_url("https://imhentai.xxx/") == ""

    def test_matches_url(self):
        scraper = IMHentaiScraper()
        assert scraper.matches_url(GALLERY_URL)
        assert scraper.matches_url(VIEW_URL)
        assert not scraper.matches_url("https://imhentai.xxx/")


class TestExtraction:
    def test_view_urls_deduped_in_order(self):
        soup = BeautifulSoup(GALLERY_PAGE, "lxml")
        assert _extract_view_urls(soup, GID) == [
            f"https://imhentai.xxx/view/{GID}/1/",
            f"https://imhentai.xxx/view/{GID}/2/",
            f"https://imhentai.xxx/view/{GID}/3/",
        ]

    def test_view_urls_ignore_other_galleries(self):
        soup = BeautifulSoup('<a href="/view/999/1/">x</a><a href="/view/1742742/2/">y</a>', "lxml")
        assert _extract_view_urls(soup, GID) == ["https://imhentai.xxx/view/1742742/2/"]

    def test_page_count(self):
        soup = BeautifulSoup(GALLERY_PAGE, "lxml")
        assert _extract_page_count(soup) == 3
        assert _extract_page_count(BeautifulSoup("<html></html>", "lxml")) == 0

    def test_tags_from_slugs(self):
        soup = BeautifulSoup(GALLERY_PAGE, "lxml")
        assert _extract_tags(soup) == ["dark skin", "english"]

    def test_clean_tag_name(self):
        assert _clean_tag_name("dark skin118844") == "dark skin"
        assert _clean_tag_name("english") == "english"

    def test_language(self):
        soup = BeautifulSoup(GALLERY_PAGE, "lxml")
        assert _extract_language(soup, ["dark skin", "english"]) == "english"

    def test_language_strips_counts_and_translated(self):
        soup = BeautifulSoup("<ul><li>Languages:english293105translated344525</li></ul>", "lxml")
        assert _extract_language(soup, []) == "english"

    def test_reader_image(self):
        soup = BeautifulSoup(_view_page(1), "lxml")
        assert _extract_reader_image(soup) == "https://m11.imhentai.xxx/033/smbqi3gpav/1.webp"
        assert _extract_reader_image(BeautifulSoup("<html></html>", "lxml")) == ""


class TestIMHentaiScraper:
    @pytest.mark.asyncio
    async def test_scrape_gallery_success(self):
        scraper = IMHentaiScraper()
        meta = await scraper.scrape(GALLERY_URL, _MockSession(_handler))

        assert meta.series_title == "Hoobamon - Lux Full Nelson"
        assert meta.total_pages == 3
        assert meta.language == "english"
        assert meta.genres == ["dark skin", "english"]
        assert [img.url for img in meta.images] == [
            f"https://m11.imhentai.xxx/033/smbqi3gpav/{n}.webp" for n in (1, 2, 3)
        ]
        assert [img.page_number for img in meta.images] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_scrape_view_url_scrapes_whole_gallery(self):
        scraper = IMHentaiScraper()
        meta = await scraper.scrape(VIEW_URL, _MockSession(_handler))
        assert meta.total_pages == 3
        assert meta.series_title == "Hoobamon - Lux Full Nelson"

    @pytest.mark.asyncio
    async def test_scrape_syntheses_beyond_thumbnails(self):
        gallery = GALLERY_PAGE.replace('value="3"', 'value="5"')

        def handler(url):
            if "/gallery/" in url:
                return _MockResponse(gallery)
            n = url.rstrip("/").rsplit("/", 1)[-1]
            return _MockResponse(_view_page(n))

        scraper = IMHentaiScraper()
        meta = await scraper.scrape(GALLERY_URL, _MockSession(handler))
        assert meta.total_pages == 5
        assert meta.images[-1].url.endswith("/5.webp")

    @pytest.mark.asyncio
    async def test_tag_url_rejected_with_listing_error(self):
        from comic_dl.errors import ScrapeError

        scraper = IMHentaiScraper()
        with pytest.raises(ScrapeError, match="listing page"):
            await scraper.scrape(
                "https://imhentai.xxx/tag/feminization/",
                _MockSession(lambda url: _MockResponse("<html></html>")),
            )

    @pytest.mark.asyncio
    async def test_scrape_no_pages_raises(self):
        scraper = IMHentaiScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(
                GALLERY_URL, _MockSession(lambda url: _MockResponse("<html><body></body></html>"))
            )

    def test_registered(self):
        from comic_dl.scrapers import list_sources

        by_domain = {e.domain: e for e in list_sources()}
        assert by_domain[DOMAIN].name == "imhentai"
        assert not by_domain[DOMAIN].has_series
