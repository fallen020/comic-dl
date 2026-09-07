"""Offline tests for the WeebCentral scraper (no live network)."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.errors import ScrapeError
from comic_dl.scrapers.sites.weebcentral import (
    DOMAIN,
    WeebCentralScraper,
    _extract_chapter_context,
    _extract_chapter_list,
    _extract_images,
    _extract_series_meta,
    _split_chapter_label,
    is_chapter_url,
    is_series_url,
    scrape_chapter,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SERIES_ID = "01J76XY8G1GK8EJ9VQG92C3DKM"
CHAPTER_ID = "01J76XZ4PC3VW91BYFBQJA44C3"
SERIES_URL = f"https://weebcentral.com/series/{SERIES_ID}/Aria"
CHAPTER_URL = f"https://weebcentral.com/chapters/{CHAPTER_ID}"
IMAGES_URL = f"{CHAPTER_URL}/images?reading_style=long_strip"
LIST_URL = f"https://weebcentral.com/series/{SERIES_ID}/full-chapter-list"

CHAPTER_PAGE = f"""
<html><head>
<title>Navigation 67.5 | Aria | Weeb Central</title>
<meta property="og:title" content="Navigation 67.5 | Aria | Weeb Central">
<meta property="og:image" content="https://temp.compsci88.com/cover/fallback/{SERIES_ID}.jpg">
</head><body>
<a href="/series/{SERIES_ID}/Aria"><span>Aria</span></a>
<button><span>Navigation 67.5</span></button>
</body></html>
"""

IMAGES_FRAGMENT = """
<section id="chapter-images">
<img src="https://official.lowee.us/manga/Aria/0067.5-001.png" alt="Page 1">
<img src="https://official.lowee.us/manga/Aria/0067.5-002.png" alt="Page 2">
<img src="https://official.lowee.us/manga/Aria/0067.5-002.png" alt="Page 2">
<img src="/static/images/broken_image.jpg" alt="broken">
<img src="/relative/path.png" alt="relative">
</section>
"""

SERIES_PAGE = f"""
<html><head>
<title>Aria | Weeb Central</title>
<meta property="og:title" content="Aria | Weeb Central">
<meta property="og:description" content="A gondolier story on Aqua.">
<meta property="og:image" content="https://temp.compsci88.com/cover/fallback/{SERIES_ID}.jpg">
</head><body><h1>Aria</h1></body></html>
"""

FULL_LIST = """
<a href="/chapters/AAA">Navigation 3</a>
<a href="/chapters/BBB">Navigation 2</a>
<a href="/chapters/CCC">Navigation 1</a>
"""


class TestUrlPatterns:
    def test_chapter_url(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url(f"{CHAPTER_URL}/")
        assert not is_chapter_url(SERIES_URL)
        assert not is_series_url(CHAPTER_URL)

    def test_series_url(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url(f"https://weebcentral.com/series/{SERIES_ID}")
        assert not is_series_url("https://weebcentral.com/")
        assert not is_chapter_url("https://weebcentral.com/search")


class TestSplitChapterLabel:
    def test_type_and_number(self):
        assert _split_chapter_label("Navigation 67.5") == ("Navigation", "67.5")

    def test_bare_label_has_no_number(self):
        assert _split_chapter_label("Special") == ("Special", None)


class TestExtractImages:
    def test_order_dedupe_and_skips(self):
        items = _extract_images(BeautifulSoup(IMAGES_FRAGMENT, "lxml"))
        assert [i.url for i in items] == [
            "https://official.lowee.us/manga/Aria/0067.5-001.png",
            "https://official.lowee.us/manga/Aria/0067.5-002.png",
        ]
        assert [i.page_number for i in items] == [1, 2]

    def test_empty_fragment(self):
        assert _extract_images(BeautifulSoup("<section></section>", "lxml")) == []


class TestChapterContext:
    def test_series_link_and_og_title(self):
        from comic_dl.scrapers.base import meta_index

        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        series, url, label = _extract_chapter_context(soup, meta_index(soup))
        assert series == "Aria"
        assert url == SERIES_URL
        assert label == "Navigation 67.5"

    def test_button_fallback_without_og_title(self):
        soup = BeautifulSoup(
            '<html><body><button><span>Episode 12</span></button></body></html>',
            "lxml",
        )
        series, _url, label = _extract_chapter_context(soup, {})
        assert series == ""
        assert label == "Episode 12"

    def test_random_nav_link_never_wins_as_series(self):
        soup = BeautifulSoup(
            f'<html><body><a href="/series/random">Random</a>'
            f'<a href="/series/{SERIES_ID}/Aria"><span>Aria</span></a></body></html>',
            "lxml",
        )
        series, url, _label = _extract_chapter_context(soup, {})
        assert series == "Aria"
        assert url == SERIES_URL


class TestSeriesMeta:
    def test_title_description_cover(self):
        from comic_dl.scrapers.base import meta_index

        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        title, desc, cover = _extract_series_meta(soup, meta_index(soup))
        assert title == "Aria"
        assert desc == "A gondolier story on Aqua."
        assert cover == f"https://temp.compsci88.com/cover/fallback/{SERIES_ID}.jpg"


class TestExtractChapterList:
    def test_newest_first_absolute_urls(self):
        items = _extract_chapter_list(BeautifulSoup(FULL_LIST, "lxml"))
        assert [label for label, _ in items] == [
            "Navigation 3",
            "Navigation 2",
            "Navigation 1",
        ]
        assert all(url.startswith("https://weebcentral.com/chapters/") for _, url in items)


class TestWeebCentralScraper:
    def test_domain_attr(self):
        assert WeebCentralScraper().domain == DOMAIN

    def test_matches_url(self):
        scraper = WeebCentralScraper()
        assert scraper.matches_url(CHAPTER_URL)
        assert scraper.matches_url(SERIES_URL)
        assert not scraper.matches_url("https://weebcentral.com/search")

    @pytest.mark.asyncio
    async def test_scrape_chapter(self):
        def handler(url):
            if url == CHAPTER_URL:
                return _MockResponse(CHAPTER_PAGE)
            if url == IMAGES_URL:
                return _MockResponse(IMAGES_FRAGMENT)
            raise AssertionError(f"unexpected URL: {url}")

        meta = await scrape_chapter(CHAPTER_URL, _MockSession(handler))
        assert meta.series_title == "Aria"
        assert meta.chapter_title == "Navigation 67.5"
        assert meta.chapter_number == "67.5"
        assert len(meta.images) == 2
        assert meta.images[0].page_number == 1
        assert meta.images[0].url.endswith("0067.5-001.png")
        assert meta.cover_url.endswith(f"{SERIES_ID}.jpg")

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images(self):
        def handler(url):
            if url == CHAPTER_URL:
                return _MockResponse(CHAPTER_PAGE)
            return _MockResponse("<section id='chapter-images'></section>")

        with pytest.raises(ScrapeError, match="No images found"):
            await scrape_chapter(CHAPTER_URL, _MockSession(handler))

    @pytest.mark.asyncio
    async def test_scrape_series_ascending(self):
        def handler(url):
            if url == SERIES_URL:
                return _MockResponse(SERIES_PAGE)
            if url == LIST_URL:
                return _MockResponse(FULL_LIST)
            raise AssertionError(f"unexpected URL: {url}")

        scraper = WeebCentralScraper()
        series = await scraper.scrape_series(SERIES_URL, _MockSession(handler))
        assert series.series_title == "Aria"
        assert series.description == "A gondolier story on Aqua."
        assert [c["episode_no"] for c in series.chapters] == ["1", "2", "3"]
        assert series.chapters[0]["url"] == "https://weebcentral.com/chapters/CCC"

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters(self):
        def handler(url):
            if url == SERIES_URL:
                return _MockResponse(SERIES_PAGE)
            return _MockResponse("<div></div>")

        with pytest.raises(ScrapeError, match="No chapters found"):
            await WeebCentralScraper().scrape_series(SERIES_URL, _MockSession(handler))
