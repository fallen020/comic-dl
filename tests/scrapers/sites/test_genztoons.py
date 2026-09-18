from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.genztoons import (
    DOMAIN,
    GenzToonsScraper,
    _chapter_number_from_title,
    _extract_genres,
    _extract_header,
    _extract_images,
    _extract_series_title,
    _extract_stats,
    _series_slug_from_url,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "leveling-up-with-skills"
CHAPTER_UID = "31ad113715e-65b3675e7d7"
SERIES_URL = f"https://genztoons.org/series/{SLUG}/"
CHAPTER_URL = f"https://genztoons.org/chapter/{CHAPTER_UID}/"
IMG_UID = "65b3675e7d7.avif"
COVER = "https://wsrv.nl/?url=cdn.meowing.org/uploads/l0KAI2cQL-m"

SERIES_PAGE = f"""
<html lang="en"><head>
    <title>{SLUG.title()}</title>
    <meta property="og:title" content="Leveling Up With Skills"/>
    <meta property="og:description" content="Kang Tae-san, the strongest human player in Easy mode..."/>
    <meta property="og:image" content="{COVER}"/>
</head><body>
<h1>Leveling Up With Skills</h1>
<div class="w-full flex gap-3 flex-wrap">
    <div class="grid gap-2 h-fit">
        <div class="font-medium"><span>Author</span></div>
        <div class="min-h-8 ...">Demigod</div>
    </div>
    <div class="grid gap-2 h-fit">
        <div class="font-medium"><span>Type</span></div>
        <div class="min-h-8 ...">manhwa</div>
    </div>
    <div class="grid gap-2 h-fit">
        <div class="font-medium"><span>Status</span></div>
        <div class="min-h-8 ...">ongoing</div>
    </div>
</div>
<a href="/series/?genre=action" title="Action">Action</a>
<a href="/series/?genre=adventure" title="Adventure">Adventure</a>
<div id="chapters" class="grid">
    <a href="/chapter/31ad113715e-65b3675e7d7/" title="Chapter 168" p="65b367aa760.avif">Chapter 168</a>
    <a href="/chapter/31ad113715e-65aaa5297f4/" title="Chapter 167" p="65aaa562e48.avif">Chapter 167</a>
    <a href="/chapter/31ad113715e-65a1c6226b0/" title="Chapter 1" p="65a1c8413aa.avif">Chapter 1</a>
</div>
</body></html>
"""

CHAPTER_PAGE = f"""
<html lang="en"><head>
    <title>Leveling Up With Skills Chapter 168</title>
    <meta property="og:title" content="Leveling Up With Skills Chapter 168"/>
    <meta property="og:image" content="https://wsrv.nl/?url=cdn.meowing.org/uploads/{IMG_UID}"/>
</head><body>
<h1><a href="/series/leveling-up-with-skills/" title="Leveling Up With Skills">Leveling Up With Skills</a> - Chapter 168</h1>
<div id="pages">
    <img src="/assets/images/placeholder.svg" count="0" uid="{IMG_UID}" class="lazy w-full myImage" alt="">
    <img src="/assets/images/placeholder.svg" count="1" uid="65b367610b1.avif" class="lazy w-full myImage" alt="">
    <img src="/assets/images/placeholder.svg" count="2" uid="65b3676131a.avif" class="lazy w-full myImage" alt="">
</div>
<img src="https://i0.wp.com/cdn.meowing.org/uploads/thumb.jpg"/>
</body></html>
"""


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url("https://www.genztoons.org/series/foo")
        assert is_series_url("https://genztoons.net/series/foo/")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://genztoons.org/")
        assert not is_series_url("https://genztoons.org/series/")
        assert not is_series_url(CHAPTER_URL)
        assert not is_series_url("https://other.com/series/foo")

    def test_valid_chapter_urls(self):
        assert is_chapter_url(CHAPTER_URL)
        assert is_chapter_url("https://genztoons.org/chapter/abc123/")
        assert is_chapter_url("https://genztoons.net/chapter/abc123")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url(SERIES_URL)
        assert not is_chapter_url("https://genztoons.org/chapter/")
        assert not is_chapter_url("https://other.com/chapter/abc123/")

    def test_matches_url(self):
        scraper = GenzToonsScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert scraper.matches_url("https://genztoons.net/chapter/abc/")
        assert not scraper.matches_url("https://genztoons.org/")

    def test_matches_series_url(self):
        scraper = GenzToonsScraper()
        assert scraper.matches_series_url(SERIES_URL)
        assert not scraper.matches_series_url(CHAPTER_URL)

    def test_series_slug(self):
        assert _series_slug_from_url(SERIES_URL) == SLUG
        assert _series_slug_from_url("https://genztoons.org/") == ""

    def test_chapter_number(self):
        assert _chapter_number_from_title("Chapter 168") == "168"
        assert _chapter_number_from_title("Leveling Up With Skills - Chapter 168") == "168"
        assert _chapter_number_from_title("Leveling Up With Skills") is None


class TestExtraction:
    def test_series_title_from_h1_and_og(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        from comic_dl.scrapers.base import meta_index

        assert _extract_series_title(soup, meta_index(soup)) == "Leveling Up With Skills"

    def test_stats_genres(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_stats(soup) == {
            "Author": "Demigod",
            "Type": "manhwa",
            "Status": "ongoing",
        }
        assert _extract_genres(soup) == ["Action", "Adventure"]

    def test_images_scoped_to_reader(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        images = _extract_images(soup)
        assert len(images) == 3
        assert images[0].url == f"https://cdn.meowing.org/uploads/{IMG_UID}"
        assert images[1].url == "https://cdn.meowing.org/uploads/65b367610b1.avif"
        assert [i.page_number for i in images] == [1, 2, 3]
        assert all("wp.com" not in i.url for i in images)

    def test_images_skips_malformed_uid(self):
        page = CHAPTER_PAGE.replace(
            f'count="0" uid="{IMG_UID}"',
            'count="0" uid="../../etc/passwd"',
        )
        soup = BeautifulSoup(page, "lxml")
        images = _extract_images(soup)
        assert len(images) == 2

    def test_no_images_without_reader(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert _extract_images(soup) == []

    def test_header(self):
        soup = BeautifulSoup(CHAPTER_PAGE, "lxml")
        from comic_dl.scrapers.base import meta_index

        series, slug, title = _extract_header(soup, meta_index(soup))
        assert series == "Leveling Up With Skills"
        assert slug == SLUG
        assert title == "Chapter 168"


class TestGenzToonsScraper:
    def test_domain_attr(self):
        scraper = GenzToonsScraper()
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
        scraper = GenzToonsScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)

        assert meta.series_title == "Leveling Up With Skills"
        assert meta.chapter_title == "Chapter 168"
        assert meta.chapter_number == "168"
        assert meta.language == "en"
        assert meta.reading_direction == "ltr"
        assert meta.service == DOMAIN
        assert meta.authors == ["Demigod"]
        assert meta.genres == ["Action", "Adventure"]
        assert meta.status == "ongoing"
        assert meta.description.startswith("Kang Tae-san")
        assert meta.cover_url == COVER
        assert meta.total_pages == 3
        assert meta.images[0].url == f"https://cdn.meowing.org/uploads/{IMG_UID}"

    @pytest.mark.asyncio
    async def test_scrape_chapter_no_images_raises(self):
        page = CHAPTER_PAGE.replace('<div id="pages">', "")

        def handler(url):
            return _MockResponse(page)

        session = _MockSession(handler)
        scraper = GenzToonsScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(CHAPTER_URL, session)

    @pytest.mark.asyncio
    async def test_scrape_chapter_enrichment_failure_is_best_effort(self):
        def handler(url):
            if url == SERIES_URL:
                raise ConnectionError("boom")
            return _MockResponse(CHAPTER_PAGE)

        session = _MockSession(handler)
        scraper = GenzToonsScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)
        assert meta.series_title == "Leveling Up With Skills"
        assert meta.chapter_title == "Chapter 168"
        assert meta.authors == []
        assert len(meta.images) == 3

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        session = _MockSession(lambda url: _MockResponse(SERIES_PAGE))
        scraper = GenzToonsScraper()
        series = await scraper.scrape_series(SERIES_URL, session)

        assert series.series_title == "Leveling Up With Skills"
        assert series.description.startswith("Kang Tae-san")
        assert series.cover_url == COVER
        assert series.title_no == SLUG
        assert len(series.chapters) == 3
        assert series.chapters[0]["episode_no"] == "1"
        assert series.chapters[0]["title"] == "Chapter 1"
        assert series.chapters[-1]["episode_no"] == "168"
        assert series.chapters[-1]["url"] == (
            "https://genztoons.org/chapter/31ad113715e-65b3675e7d7/"
        )

    @pytest.mark.asyncio
    async def test_scrape_series_no_chapters_raises(self):
        page = SERIES_PAGE.replace('id="chapters"', 'id="chapters_missing"')
        session = _MockSession(lambda url: _MockResponse(page))
        scraper = GenzToonsScraper()
        with pytest.raises(ValueError, match="No chapters found"):
            await scraper.scrape_series(SERIES_URL, session)
