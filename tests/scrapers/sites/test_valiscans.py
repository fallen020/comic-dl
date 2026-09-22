from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.errors import ScrapeError
from comic_dl.scrapers.sites._valiscans import (
    _chapter_number_from_url,
    _extract_cover,
    _extract_genres,
    _extract_status,
    _extract_title,
    _is_free,
    _series_slug_from_url,
    chapter_url_re,
    extract_rsc_array,
    extract_rsc_string,
    series_url_re,
)
from comic_dl.scrapers.sites.divascans import DOMAIN as DIVA_DOMAIN
from comic_dl.scrapers.sites.valirscans import DOMAIN as VALI_DOMAIN
from comic_dl.scrapers.sites.valirscans import ValirScansScraper
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "after-the-possessor-left"
SERIES_URL = f"https://valirscans.org/series/comic/{SLUG}"
CHAPTER_URL = f"https://valirscans.org/series/comic/{SLUG}/chapter/1"

SERIES_PAGE = (
    "<html><head><title>After the Possessor Left by Jae Gyeom | ValirScans</title>"
    '<meta property="og:title" content="After the Possessor Left"/>'
    '<meta property="og:description" content="Possession story."/>'
    '<meta property="og:image" content="https://media.valirscans.org/covers/x.webp"/>'
    "</head><body>"
    # Escaped RSC flight data, as served (note $undefined + !1 spellings).
    r"\"chapters\":[{\"id\":\"c1\",\"number\":1,\"title\":\"Chapter 1\",\"isLocked\":false,"
    r"\"coinPrice\":0,\"hasAccess\":true},{\"id\":\"c2\",\"number\":2,\"title\":\"\","
    r"\"isLocked\":!1,\"coinPrice\":100,\"hasAccess\":!0,\"purchaseCount\":\"$undefined\"},"
    r"{\"id\":\"c3\",\"number\":3,\"title\":\"\",\"isLocked\":false,\"hasAccess\":true}]"
    r"\"genres\":[{\"name\":\"Action\"},{\"name\":\"Fantasy\"}]"
    r"\"status\":\"ONGOING\""
    r"\"description\":\"\u003cp\u003eFull synopsis here.\u003c/p\u003e\""
    r"\"coverImage\":\"/uploads/api/aa91cover.jpg\""
    "</body></html>"
)

PAGES = {
    "pages": [
        {
            "pageNumber": 1,
            "imageUrl": "https://media.valirscans.org/series/x/0001/p-a.webp",
            "isRedacted": False,
            "isEncrypted": False,
        },
        {
            "pageNumber": 2,
            "imageUrl": "https://media.valirscans.org/series/x/0001/p-b.webp",
            "isRedacted": False,
            "isEncrypted": False,
        },
        {"pageNumber": 3, "imageUrl": "", "isRedacted": True, "isEncrypted": False},
    ]
}


def _handler(url):
    if "/series/comic/" in url and "/chapter/" not in url:
        return _MockResponse(SERIES_PAGE)
    return _MockResponse(json_data=PAGES)


class TestUrlPatterns:
    def test_series(self):
        assert series_url_re(VALI_DOMAIN).match(SERIES_URL)
        assert not series_url_re(VALI_DOMAIN).match(CHAPTER_URL)
        assert not series_url_re(VALI_DOMAIN).match("https://valirscans.org/series/novel/x")
        assert series_url_re(DIVA_DOMAIN).match(
            "https://divascans.org/series/comic/back-alley-illegal-clinic"
        )

    def test_chapter(self):
        assert chapter_url_re(VALI_DOMAIN).match(CHAPTER_URL)
        assert not chapter_url_re(VALI_DOMAIN).match(SERIES_URL)

    def test_matches(self):
        scraper = ValirScansScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(CHAPTER_URL)
        assert scraper.matches_series_url(SERIES_URL)
        assert not scraper.matches_series_url(CHAPTER_URL)
        assert not scraper.matches_url("https://valirscans.org/series/novel/x")

    def test_slugs(self):
        assert _series_slug_from_url(SERIES_URL) == SLUG
        assert _series_slug_from_url(CHAPTER_URL) == SLUG
        assert _series_slug_from_url("https://valirscans.org/") == ""
        assert _chapter_number_from_url(CHAPTER_URL) == "1"
        assert _chapter_number_from_url(SERIES_URL) is None


class TestExtraction:
    def test_rsc_array(self):
        entries = extract_rsc_array(SERIES_PAGE, "chapters")
        assert len(entries) == 3
        assert entries[0]["id"] == "c1"
        assert entries[1]["isLocked"] is True
        assert entries[1]["hasAccess"] is False
        assert entries[2]["hasAccess"] is True

    def test_rsc_array_missing(self):
        assert extract_rsc_array("<html></html>", "chapters") == []
        assert extract_rsc_array(SERIES_PAGE, "nope") == []

    def test_rsc_string(self):
        raw = r"\"description\":\"\u003cp\u003eHi.\u003c/p\u003e\",\"next\":1"
        assert extract_rsc_string(raw, "description") == "<p>Hi.</p>"
        assert extract_rsc_string(raw, "missing") == ""
        assert extract_rsc_string("<html></html>", "description") == ""

    def test_cover_media_rule(self):
        from comic_dl.scrapers.base import meta_index

        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        cover = _extract_cover("https://divascans.org", SERIES_PAGE, soup, meta_index(soup))
        assert cover == "https://media.divascans.org/api/aa91cover.jpg"

    def test_cover_urljoin_fallback(self):
        from comic_dl.scrapers.base import meta_index

        raw = SERIES_PAGE.replace("/uploads/api/aa91cover.jpg", "/uploads/series/c.webp")
        soup = BeautifulSoup(raw, "lxml")
        assert _extract_cover("https://divascans.org", raw, soup, meta_index(soup)) == (
            "https://divascans.org/uploads/series/c.webp"
        )

    def test_is_free(self):
        assert _is_free({"isLocked": False, "hasAccess": True})
        assert not _is_free({"isLocked": True, "hasAccess": False})
        assert not _is_free({"isLocked": False, "hasAccess": False})
        assert not _is_free({})

    def test_title_genres_status(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_title(soup, {}) == "After the Possessor Left"
        assert _extract_genres(SERIES_PAGE) == ["Action", "Fantasy"]
        assert _extract_status(SERIES_PAGE) == "Ongoing"
        assert _extract_status("<html></html>") is None


class TestValirScraper:
    @pytest.mark.asyncio
    async def test_scrape_chapter_success(self):
        session = _MockSession(_handler)
        scraper = ValirScansScraper()
        meta = await scraper.scrape(CHAPTER_URL, session)

        assert meta.series_title == "After the Possessor Left"
        assert meta.chapter_title == "Chapter 1"
        assert meta.chapter_number == "1"
        assert meta.total_pages == 2
        assert meta.genres == ["Action", "Fantasy"]
        assert meta.status == "Ongoing"
        assert meta.description == "Full synopsis here."
        api_calls = [kw for url, kw in session.requests if "page-urls" in url]
        assert api_calls, "expected a page-urls API call"
        assert api_calls[0].get("headers", {}).get("Sec-Fetch-Dest") == "empty"

    @pytest.mark.asyncio
    async def test_scrape_novel_url_rejected(self):
        scraper = ValirScansScraper()
        with pytest.raises(ScrapeError, match="Novel chapters"):
            await scraper.scrape(
                "https://valirscans.org/series/novel/love-camp/chapter/86",
                _MockSession(lambda url: _MockResponse("<html></html>")),
            )

    @pytest.mark.asyncio
    async def test_scrape_locked_chapter_raises(self):
        scraper = ValirScansScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(
                f"https://valirscans.org/series/comic/{SLUG}/chapter/2",
                _MockSession(_handler),
            )

    @pytest.mark.asyncio
    async def test_scrape_series_skips_locked(self):
        scraper = ValirScansScraper()
        series = await scraper.scrape_series(SERIES_URL, _MockSession(_handler))

        assert series.series_title == "After the Possessor Left"
        assert series.title_no == SLUG
        assert len(series.chapters) == 2
        assert series.chapters[0]["episode_no"] == "1"
        assert series.chapters[1]["episode_no"] == "3"
        assert series.chapters[1]["title"] == "Chapter 3"

    @pytest.mark.asyncio
    async def test_homepage_url_rejected(self):
        from comic_dl.errors import ScrapeError

        scraper = ValirScansScraper()
        with pytest.raises(ScrapeError, match="listing page"):
            await scraper.scrape(
                "https://valirscans.org/",
                _MockSession(lambda url: _MockResponse("<html></html>")),
            )

    def test_registered(self):
        from comic_dl.scrapers import list_sources

        by_domain = {e.domain: e for e in list_sources()}
        assert by_domain[VALI_DOMAIN].name == "valirscans"
        assert by_domain[VALI_DOMAIN].has_series
        assert by_domain[DIVA_DOMAIN].name == "divascans"
        assert by_domain[DIVA_DOMAIN].has_series
