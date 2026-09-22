from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.errors import ScrapeError
from comic_dl.scrapers.sites.manhuato import (
    DOMAIN,
    ManhuatoScraper,
    _chapter_number_from_slug,
    _chapter_number_from_text,
    _clean_description,
    _clean_image_url,
    _extract_chapter_title,
    _extract_description,
    _extract_genres,
    _extract_images,
    _extract_series_title,
    _info_rows,
    _series_slug_from_url,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "absolute-regression"
SERIES_URL = f"https://manhuato.com/manhua/{SLUG}"
CHAPTER_URL = f"https://manhuato.com/manhua/{SLUG}-chapter-120-ch403512"
COVER = "https://media.manhuato.com/files/images/thumbs/absolute-regression.webp"

SERIES_PAGE = f"""
<html lang="en-US"><head>
    <title>Absolute Regression Manga | ManhuaTo</title>
    <meta property="og:site_name" content="ManhuaTo.com"/>
    <meta property="og:image" content="{COVER}"/>
    <meta property="og:description" content="Read Absolute Regression Manga in English Online For Free at ManhuaTo."/>
</head><body>
<h1>Absolute Regression</h1>
<span class="line-text">Genres</span><span class="line-content">Comics | Action | Fantasy</span>
<span class="line-text">Type:</span><span class="line-content">Comics</span>
<span class="line-text">Status:</span><span class="line-content">Ongoing</span>
<ul class="chapter-list col-123">
    <li class="citem"><a href="/manhua/{SLUG}-chapter-120-ch403512">Chapter 120</a><span class="time">September 22, 2026</span></li>
    <li class="citem"><a href="/manhua/{SLUG}-chapter-119-ch403511">Chapter 119</a><span class="time">September 21, 2026</span></li>
    <li class="citem"><a href="/manhua/{SLUG}-chapter-120-ch403512">Chapter 120</a></li>
</ul>
</body></html>
"""

CHAPTER_PAGE = f"""
<html lang="en-US"><head>
    <title>Absolute Regression Manga - Chapter 120 | ManhuaTo</title>
    <meta property="og:site_name" content="ManhuaTo.com"/>
    <meta property="og:image" content="{COVER}"/>
    <meta property="og:description" content="Read Absolute Regression Manga - Chapter 120 in English Online For Free at ManhuaTo.A brief description of the manhua Absolute Regression:&#10;A hunter rises."/>
</head><body>
<h1>Absolute Regression - Chapter 120</h1>
<div class="chapter-content">
    <div class="item-photo"><img src="https://cdn.manhuato.com/images/manga/Absolute Regression/chapter-120/0.webp"/></div>
    <div class="item-photo"><img src="https://cdn.manhuato.com/images/manga/Absolute Regression/chapter-120/1.webp"/></div>
    <div class="item-photo"><img data-src="https://cdn.manhuato.com/images/manga/Absolute Regression/chapter-120/2.webp"/></div>
</div>
<img src="https://manhuato.com/logo.png"/>
</body></html>
"""


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url(f"{SERIES_URL}/")
        assert is_series_url("https://www.manhuato.com/manga/foo")
        assert is_series_url("https://manhuato.com/manga/foo-bar-2")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://manhuato.com/")
        assert not is_series_url("https://manhuato.com/manhua/")
        assert not is_series_url(CHAPTER_URL)
        assert not is_series_url("https://other.com/manhua/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url(f"{CHAPTER_URL}/")
        assert is_chapter_url("https://www.manhuato.com/manga/foo-chapter-3-ch99")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url(SERIES_URL)
        assert not is_chapter_url("https://manhuato.com/manhua/foo-chapter-3")
        assert not is_chapter_url("https://other.com/manhua/foo-chapter-3-ch99")

    def test_matches_url(self):
        scraper = ManhuatoScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert not scraper.matches_url("https://manhuato.com/")

    def test_matches_series_url(self):
        scraper = ManhuatoScraper()
        assert scraper.matches_series_url(SERIES_URL)
        assert not scraper.matches_series_url(CHAPTER_URL)

    def test_series_slug(self):
        assert _series_slug_from_url(SERIES_URL) == ("manhua", SLUG)
        assert _series_slug_from_url(CHAPTER_URL) == ("manhua", SLUG)
        assert _series_slug_from_url("https://manhuato.com/manga/x-chapter-1-ch2") == (
            "manga",
            "x",
        )
        assert _series_slug_from_url("https://manhuato.com/") == ("", "")

    def test_chapter_number(self):
        assert _chapter_number_from_slug(CHAPTER_URL) == "120"
        assert _chapter_number_from_slug(SERIES_URL) is None
        assert _chapter_number_from_text("Chapter 120") == "120"
        assert _chapter_number_from_text("Chapter 12.5") == "12.5"
        assert _chapter_number_from_text("Prologue") is None


class TestExtraction:
    def test_series_title_from_h1(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_series_title(soup, {}) == "Absolute Regression"

    def test_info_rows(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        rows = _info_rows(soup)
        assert rows["Status"] == "Ongoing"
        assert rows["Type"] == "Comics"
        assert _extract_genres(rows) == ["Comics", "Action", "Fantasy"]

    def test_chapter_title_split_from_h1(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        assert _extract_chapter_title(soup, {}, "Absolute Regression") == "Chapter 120"

    def test_image_urls_quoted(self):
        assert _clean_image_url("https://cdn.manhuato.com/images/manga/A B/0.webp") == (
            "https://cdn.manhuato.com/images/manga/A%20B/0.webp"
        )

    def test_images_only_from_reader(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 3
        assert images[0].url == (
            "https://cdn.manhuato.com/images/manga/Absolute%20Regression/chapter-120/0.webp"
        )
        assert [i.page_number for i in images] == [1, 2, 3]

    def test_no_images(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_images(soup) == []

    def test_clean_description_strips_seo_prefix(self):
        raw = (
            "Read Foo Manga - Chapter 1 in English Online For Free at ManhuaTo."
            "A brief description of the manhua Foo:\nReal story here."
        )
        assert _clean_description(raw) == "Real story here."

    def test_clean_description_strips_type_header(self):
        raw = "Read FooManga in English Online For Free at ManhuaTo.Foo Manhwa\nReal story."
        assert _clean_description(raw) == "Real story."

    def test_clean_description_keeps_plain_text(self):
        assert _clean_description("Just a synopsis.") == "Just a synopsis."
        assert _clean_description("") == ""

    def test_extract_description(self):
        from comic_dl.scrapers.base import meta_index

        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        assert _extract_description(soup, meta_index(soup)) == "A hunter rises."

    @pytest.mark.asyncio
    async def test_homepage_url_rejected_with_listing_error(self):
        scraper = ManhuatoScraper()
        with pytest.raises(ScrapeError, match="listing page"):
            await scraper.scrape(
                "https://manhuato.com/", _MockSession(lambda url: _MockResponse("<html></html>"))
            )

    @pytest.mark.asyncio
    async def test_chapter_url_rejected_by_series_scrape(self):
        scraper = ManhuatoScraper()
        with pytest.raises(ScrapeError, match="listing page"):
            await scraper.scrape_series(
                "https://manhuato.com/manhua/x-chapter-1-ch2",
                _MockSession(lambda url: _MockResponse("<html></html>")),
            )


def _session(handler):
    return _MockSession(handler)


def _handler(url):
    if url == SERIES_URL:
        return _MockResponse(SERIES_PAGE)
    return _MockResponse(CHAPTER_PAGE)


class TestManhuatoScraper:
    @pytest.mark.asyncio
    async def test_scrape_chapter_success(self):
        scraper = ManhuatoScraper()
        meta = await scraper.scrape(CHAPTER_URL, _session(_handler))

        assert meta.series_title == "Absolute Regression"
        assert meta.chapter_title == "Chapter 120"
        assert meta.chapter_number == "120"
        assert meta.total_pages == 3
        assert meta.genres == ["Comics", "Action", "Fantasy"]
        assert meta.status == "Ongoing"
        assert meta.language == "en"
        assert meta.images[0].url.startswith("https://cdn.manhuato.com/")
        assert "%20" in meta.images[0].url

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        scraper = ManhuatoScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(
                CHAPTER_URL, _session(lambda url: _MockResponse("<html><body></body></html>"))
            )

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        scraper = ManhuatoScraper()
        series = await scraper.scrape_series(SERIES_URL, _session(_handler))

        assert series.series_title == "Absolute Regression"
        assert series.title_no == SLUG
        assert len(series.chapters) == 2
        assert series.chapters[0]["episode_no"] == "119"
        assert series.chapters[1]["episode_no"] == "120"
        assert series.chapters[1]["url"] == CHAPTER_URL

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        scraper = ManhuatoScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(
                SERIES_URL,
                _session(lambda url: _MockResponse("<html><body><h1>X</h1></body></html>")),
            )

    def test_registered(self):
        from comic_dl.scrapers import list_sources

        by_domain = {e.domain: e for e in list_sources()}
        assert by_domain[DOMAIN].name == "manhuato"
        assert by_domain[DOMAIN].has_series
