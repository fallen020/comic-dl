from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.hivetoons import (
    DOMAIN,
    HiveToonsScraper,
    _chapter_number_from_url,
    _extract_authors,
    _extract_cover,
    _extract_description,
    _extract_genres,
    _extract_images,
    _extract_lang,
    _extract_series_title,
    _extract_status,
    _series_slug_from_url,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "eleceed"
SERIES_URL = f"https://hivetoons.org/series/{SLUG}/"
CHAPTER_URL = f"https://hivetoons.org/series/{SLUG}/chapter-416/"
IMG_BASE = f"https://storage.hivetoon.com/public/upload/series/{SLUG}/74450708/page-"
COVER = "https://storage.hivetoon.com/public//upload/2024/11/20/cover_1732118474124.webp"

SERIES_PAGE = f"""
<html lang="en"><head>
    <title>Eleceed Manhwa</title>
    <meta property="og:title" content="Eleceed"/>
    <meta property="og:description" content="Jiwoo is a kind-hearted young man..."/>
    <meta property="og:image" content="https://hivetoons.org/api/og-image/series/eleceed/card.webp"/>
</head><body>
<div><h1 class="font-semibold text-medium">Status</h1><span>ONGOING</span></div>
<div><h1 class="font-semibold text-medium">Author</h1><span>Son jeho</span></div>
<h1 class="break-words text-2xl font-bold leading-[1.5rem] text-foreground">Eleceed</h1>
<img src="{COVER}" alt="Cover of Eleceed"/>
<a href="/series?genres=%2B5">Action</a>
<a href="/series?genres=%2B6">Comedy</a>
<a href="/series/eleceed/chapter-418">
    <span>Chapter 418</span>
    <div title="Careful Preparation">Careful Preparation</div>
</a>
<a href="/series/eleceed/chapter-417">
    <span>Chapter 417</span>
    <div title="Fulfilling the Promise">Fulfilling the Promise</div>
</a>
""" + """
<script>{"id":[0,1],"number":[0,416],"slug":[0,"chapter-416"],"title":[0,"Divine Judgement"],"createdAt":[0,"2026-09-01T14:47:38.933Z"],"isLocked":[0,false]}</script>
<script>{"id":[0,2],"number":[0,417],"slug":[0,"chapter-417"],"title":[0,"Fulfilling the Promise"],"createdAt":[0,"2026-09-02T14:47:38.933Z"],"isLocked":[0,false]}</script>
<script>{"id":[0,3],"number":[0,418],"slug":[0,"chapter-418"],"title":[0,""],"createdAt":[0,"2026-09-03T14:47:38.933Z"],"isLocked":[0,false]}</script>
<script>{"id":[0,4],"number":[0,419],"slug":[0,"chapter-419"],"title":[0,"Locked Chapter"],"createdAt":[0,"2026-09-04T14:47:38.933Z"],"isLocked":[0,true]}</script>
</body></html>
"""

CHAPTER_PAGE = f"""
<html lang="en"><head>
    <title>Eleceed Chapter 416</title>
    <meta property="og:title" content="Eleceed Chapter 416"/>
    <script type="application/ld+json">
    {{"@context": "https://schema.org", "@type": "Article",
      "headline": "Eleceed Chapter 416 - Divine Judgement"}}
    </script>
</head><body>
<article class="immersive-reader">
<div class="comic-images-wrapper reader-mode-strip">
    <figure class="image-container"><img src="{IMG_BASE}0001_00_a.webp"/></figure>
    <figure class="image-container"><img src="{IMG_BASE}0002_01_b.webp?v=9"/></figure>
    <figure class="image-container"><img src="{IMG_BASE}0003_01_c.webp"/></figure>
</div>
</article>
<img src="https://image-comic.pstatic.net/webtoon/717481/416/thumbnail_202x120_abc.jpg"/>
</body></html>
"""


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url("https://www.hivetoons.org/series/foo")
        assert is_series_url("https://hivetoons.org/series/foo/")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://hivetoons.org/")
        assert not is_series_url("https://hivetoons.org/series/")
        assert not is_series_url(CHAPTER_URL)
        assert not is_series_url("https://other.com/series/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url("https://hivetoons.org/series/foo/chapter-1/")
        assert is_chapter_url("https://hivetoons.org/series/foo/chapter-25")
        assert is_chapter_url("https://hivetoons.org/series/foo/chapter-408.5/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://hivetoons.org/series/foo")
        assert not is_chapter_url("https://hivetoons.org/series/foo/")
        assert not is_chapter_url("https://other.com/series/foo/chapter-1/")

    def test_matches_url(self):
        scraper = HiveToonsScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert not scraper.matches_url("https://hivetoons.org/")

    def test_matches_series_url(self):
        scraper = HiveToonsScraper()
        assert scraper.matches_series_url(SERIES_URL)
        assert not scraper.matches_series_url(CHAPTER_URL)

    def test_series_slug(self):
        assert _series_slug_from_url(CHAPTER_URL) == SLUG
        assert _series_slug_from_url(SERIES_URL) == SLUG
        assert _series_slug_from_url("https://hivetoons.org/") == ""

    def test_chapter_number(self):
        assert _chapter_number_from_url(CHAPTER_URL) == "416"
        assert _chapter_number_from_url("https://hivetoons.org/series/foo/chapter-25/") == "25"
        assert _chapter_number_from_url("https://hivetoons.org/series/foo/chapter-408.5/") == "408.5"
        assert _chapter_number_from_url(SERIES_URL) is None


class TestExtraction:
    def test_series_title_from_h1(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_series_title(soup, {}) == "Eleceed"

    def test_series_title_from_og_fallback(self):
        soup = BeautifulSoup(
            '<html><head><meta property="og:title" content="Eleceed"/>'
            "</head><body></body></html>",
            "lxml",
        )
        from comic_dl.scrapers.base import meta_index

        assert _extract_series_title(soup, meta_index(soup)) == "Eleceed"

    def test_authors_genres_status(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_authors(soup) == ["Son jeho"]
        assert _extract_genres(soup) == ["Action", "Comedy"]
        assert _extract_status(soup) == "ONGOING"

    def test_cover_prefers_cover_img_over_og_card(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        from comic_dl.scrapers.base import meta_index

        assert _extract_cover(soup, meta_index(soup)) == COVER

    def test_description_from_og(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        from comic_dl.scrapers.base import meta_index

        assert _extract_description(soup, meta_index(soup)).startswith("Jiwoo")

    def test_lang(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_lang(soup) == "en"

    def test_images_scoped_to_reader_wrapper(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 3
        assert images[0].url == f"{IMG_BASE}0001_00_a.webp"
        # query stripped, thumbnail outside the wrapper excluded
        assert images[1].url == f"{IMG_BASE}0002_01_b.webp"
        assert all("pstatic" not in i.url for i in images)
        assert [i.page_number for i in images] == [1, 2, 3]

    def test_no_images_without_wrapper(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_images(soup) == []


class TestHiveToonsScraper:
    def test_domain_attr(self):
        scraper = HiveToonsScraper()
        assert scraper.domain == DOMAIN

    @pytest.mark.asyncio
    async def test_scrape_chapter_with_enrichment(self):
        def handler(url):
            if url == SERIES_URL:
                return _MockResponse(SERIES_PAGE)
            if url == CHAPTER_URL:
                return _MockResponse(CHAPTER_PAGE)
            raise AssertionError(f"unexpected URL: {url}")

        session = _MockSession(handler)
        scraper = HiveToonsScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)

        assert meta.series_title == "Eleceed"
        assert meta.chapter_title == "Chapter 416 - Divine Judgement"
        assert meta.chapter_number == "416"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.service == DOMAIN
        assert meta.authors == ["Son jeho"]
        assert meta.genres == ["Action", "Comedy"]
        assert meta.status == "ONGOING"
        assert meta.description.startswith("Jiwoo")
        assert meta.cover_url == COVER
        assert meta.total_pages == 3

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        page = CHAPTER_PAGE.replace('<div class="comic-images-wrapper reader-mode-strip">', "")

        def handler(url):
            return _MockResponse(page)

        session = _MockSession(handler)
        scraper = HiveToonsScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if url == SERIES_URL:
                raise ConnectionError("boom")
            return _MockResponse(CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = HiveToonsScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)
        assert meta.series_title == "Untitled"
        assert meta.chapter_title == "Eleceed Chapter 416 - Divine Judgement"
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = _MockSession(lambda url: _MockResponse(SERIES_PAGE))
        scraper = HiveToonsScraper()
        series = await scraper.scrape_series(SERIES_URL, session)

        assert series.series_title == "Eleceed"
        assert series.description.startswith("Jiwoo")
        assert series.cover_url == COVER
        assert series.title_no == SLUG
        # embedded payload wins over links; locked chapter skipped
        assert len(series.chapters) == 3
        # ascending order
        assert series.chapters[0]["episode_no"] == "416"
        assert series.chapters[0]["title"] == "Chapter 416 - Divine Judgement"
        assert series.chapters[1]["episode_no"] == "417"
        assert series.chapters[-1]["episode_no"] == "418"
        assert series.chapters[-1]["title"] == "Chapter 418"
        assert series.chapters[-1]["url"] == "https://hivetoons.org/series/eleceed/chapter-418"

    @pytest.mark.asyncio
    async def test_scrape_series_falls_back_to_links(self):
        import re

        page = re.sub(r'<script>\{"id".*?</script>', "", SERIES_PAGE)
        session = _MockSession(lambda url: _MockResponse(page))
        scraper = HiveToonsScraper()
        series = await scraper.scrape_series(SERIES_URL, session)

        assert len(series.chapters) == 2
        assert series.chapters[0]["episode_no"] == "417"
        assert series.chapters[0]["title"] == "Chapter 417 - Fulfilling the Promise"
        assert series.chapters[-1]["episode_no"] == "418"

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        import re

        page = SERIES_PAGE.replace("/chapter-418", "/other-418").replace(
            "/chapter-417", "/other-417"
        )
        page = re.sub(r'<script>\{"id".*?</script>', "", page)
        session = _MockSession(lambda url: _MockResponse(page))
        scraper = HiveToonsScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(SERIES_URL, session)
