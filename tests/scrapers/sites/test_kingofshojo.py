from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.kingofshojo import (
    DOMAIN,
    KingofshojoScraper,
    _chapter_number_from_url,
    _extract_images,
    _extract_series_slug,
    _extract_series_title,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "kill-the-villainess"
CHAPTER_URL = f"https://kingofshojo.com/{SLUG}-chapter-1/"
IMG_BASE = "https://cdn.kingofshojo.com/king-bucket/66601/1/"
COVER = "https://i0.wp.com/kingofshojo.com/wp-content/uploads/2024/03/c165953e-01a5-43be-8a4e-c0e91824e43e.jpg"

CHAPTER_PAGE = f"""
<html lang="en"><head>
    <title>Kill The Villainess Chapter 1 - Kingofshojo</title>
    <meta property="og:image" content="{COVER}"/>
</head><body class="single single-wp-manga postid-67890 reading-manga">
<h1>Kill The Villainess Chapter 1</h1>
<div id="readerarea">
    <p>
        <img decoding="async" src="https://i.ibb.co/CPmfdnY/1-2.jpg" alt="1 2" title="Kill The Villainess Chapter 1 67">
        <img decoding="async" src="{IMG_BASE}1.jpg" alt="1" title="Kill The Villainess Chapter 1 68">
        <img decoding="async" src="{IMG_BASE}2.jpg" alt="2" title="Kill The Villainess Chapter 1 69">
        <img decoding="async" src="{IMG_BASE}3.jpg" alt="3" title="Kill The Villainess Chapter 1 70">
    </p>
</div>
<img src="https://kingofshojo.com/wp-content/uploads/2024/03/wewtwt.png"/>
</body></html>
"""


class TestUrlPatterns:
    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url("https://kingofshojo.com/foo-chapter-1/")
        assert is_chapter_url("https://kingofshojo.com/foo-chapter-25.5/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://kingofshojo.com/manga/foo")
        assert not is_chapter_url("https://kingofshojo.com/manga/foo/")
        assert not is_chapter_url("https://other.com/foo-chapter-1/")

    def test_matches_url(self):
        scraper = KingofshojoScraper()
        assert scraper.matches_url(CHAPTER_URL)
        assert not scraper.matches_url("https://kingofshojo.com/")

    def test_series_slug(self):
        assert _extract_series_slug(CHAPTER_URL) == SLUG
        assert _extract_series_slug("https://kingofshojo.com/") == ""

    def test_chapter_number(self):
        assert _chapter_number_from_url(CHAPTER_URL) == "1"
        assert _chapter_number_from_url("https://kingofshojo.com/foo-chapter-25/") == "25"
        assert _chapter_number_from_url("https://kingofshojo.com/foo-chapter-25.5/") == "25.5"


class TestExtraction:
    def test_series_title_from_meta(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        title = _extract_series_title(soup, {})
        assert "Kill The Villainess" in title

    def test_images_only_from_host(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 4  # 1 from i.ibb.co, 3 from cdn.kingofshojo.com
        assert images[0].url == "https://i.ibb.co/CPmfdnY/1-2.jpg"
        assert images[1].url == f"{IMG_BASE}1.jpg"
        assert images[2].url == f"{IMG_BASE}2.jpg"
        assert images[3].url == f"{IMG_BASE}3.jpg"
        assert [i.page_number for i in images] == [1, 2, 3, 4]

    def test_no_images(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_images(soup) == []


class TestKingofshojoScraper:
    def test_domain_attr(self):
        scraper = KingofshojoScraper()
        assert scraper.domain == DOMAIN

    def test_series_not_supported(self):
        assert is_series_url("https://kingofshojo.com/manga/foo/") is False

    @pytest.mark.asyncio
    async def test_scrape_chapter(self):
        def handler(url):
            return _MockResponse(CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = KingofshojoScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)

        assert "Kill The Villainess" in meta.series_title
        assert meta.chapter_title == "Kill The Villainess Chapter 1"
        assert meta.chapter_number == "1"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.service == DOMAIN
        assert meta.total_pages == 4

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        page = CHAPTER_PAGE.replace(
            f'<img decoding="async" src="{IMG_BASE}1.jpg"', ""
        ).replace(
            f'<img decoding="async" src="{IMG_BASE}2.jpg"', ""
        ).replace(
            f'<img decoding="async" src="{IMG_BASE}3.jpg"', ""
        ).replace(
            '<img decoding="async" src="https://i.ibb.co/CPmfdnY/1-2.jpg"', ""
        )

        def handler(url):
            return _MockResponse(page)

        session = _MockSession(handler)
        scraper = KingofshojoScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_series_raises(self):
        scraper = KingofshojoScraper()
        with pytest.raises(NotImplementedError, match="series scraping not supported"):
            await scraper.scrape_series("https://kingofshojo.com/manga/foo/", None)
