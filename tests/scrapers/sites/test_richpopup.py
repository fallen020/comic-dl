from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.errors import ScrapeError
from comic_dl.scrapers.base import meta_index
from comic_dl.scrapers.sites.richpopup import (
    DOMAIN,
    RichPopupScraper,
    _extract_artists,
    _extract_images,
    _extract_post_id,
    _extract_tags,
    _extract_title,
    is_gallery_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

GALLERY_URL = "https://richpopup.com/29038-immoral-desires-12.html"

GALLERY_PAGE = """
<html lang="en"><head>
<title>Immoral Desires 12 &raquo; RichPopUp.com Porn Comics</title>
<meta property="og:description" content="Porn Comics: Immoral Desires 12. 142 pages."/>
<meta property="og:image" content="https://richpopup.com/comic/no8koexzgw0sn2iy4kib/1.webp"/>
<meta name="news_keywords" content="incest,milf,mother"/>
</head><body>
<aside><a href="https://richpopup.com/13002-among-us-the-series-part-2.html">
<img src="/comic/yt4giqiw0d0qq4n1tw00/thumb/main_yt4giqiw0d0qq4n1tw00.jpg"/></a></aside>
<article class="rp-article"><div class="rp-title"><h1>Immoral Desires 12</h1>
<dl class="rp-meta">
<div class="rp-meta__row"><dt>Artists:</dt><dd><a href="https://richpopup.com/artauthor/daval3d/">daval3d</a></dd></div>
<div class="rp-meta__row"><dt>Tags:</dt><dd><div class="rp-tag"><a href="https://richpopup.com/tags/incest/">incest</a></div>
<div class="rp-tag"><a href="https://richpopup.com/tags/milf/">milf</a></div></dd></div>
</dl></div>
<div class="title_spoiler"><div class="rp-desc">
<img data-src="/comic/no8koexzgw0sn2iy4kib/1.webp" alt="Immoral Desires 12" width="100%"/>
<img data-original="/comic/no8koexzgw0sn2iy4kib/2.webp" data-src="/comic/no8koexzgw0sn2iy4kib/2.webp" width="100%"/>
<img src="/comic/no8koexzgw0sn2iy4kib/3.webp" width="100%"/>
<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" width="100%"/>
<img data-src="/comic/no8koexzgw0sn2iy4kib/2.webp" width="100%"/>
</div></div></article></body></html>
"""


class TestUrlPatterns:
    def test_valid_gallery_urls(self):
        assert is_gallery_url(GALLERY_URL)
        assert is_gallery_url("https://www.richpopup.com/123-some-slug.html/")

    def test_invalid_gallery_urls(self):
        assert not is_gallery_url("")
        assert not is_gallery_url("https://richpopup.com/")
        assert not is_gallery_url("https://richpopup.com/tags/milf/")
        assert not is_gallery_url("https://richpopup.com/page/2/")
        assert not is_gallery_url("https://other.com/29038-immoral-desires-12.html")

    def test_matches_url(self):
        scraper = RichPopupScraper()
        assert scraper.matches_url(GALLERY_URL)
        assert not scraper.matches_url("https://richpopup.com/tags/milf/")


class TestExtraction:
    def test_images_scope_and_attrs(self):
        images = _extract_images(BeautifulSoup(GALLERY_PAGE, "lxml"), GALLERY_URL)
        assert [i.page_number for i in images] == [1, 2, 3]
        assert images[0].url == "https://richpopup.com/comic/no8koexzgw0sn2iy4kib/1.webp"
        assert all("/thumb/" not in i.url for i in images)

    def test_images_empty(self):
        assert _extract_images(BeautifulSoup("<html></html>", "lxml"), GALLERY_URL) == []

    def test_title_post_id_tags_artists(self):
        soup = BeautifulSoup(GALLERY_PAGE, "lxml")
        idx = meta_index(soup)
        assert _extract_title(soup, idx) == "Immoral Desires 12"
        assert _extract_post_id(GALLERY_URL) == "29038"
        assert _extract_tags(soup, idx) == ["incest", "milf"]
        assert _extract_artists(soup) == ["daval3d"]

    def test_tags_fall_back_to_news_keywords(self):
        soup = BeautifulSoup(
            '<html><head><meta name="news_keywords" content="incest, milf"/></head></html>',
            "lxml",
        )
        assert _extract_tags(soup, meta_index(soup)) == ["incest", "milf"]


class TestRichPopupScraper:
    @pytest.mark.asyncio
    async def test_scrape(self):
        scraper = RichPopupScraper()
        meta = await scraper.scrape(
            GALLERY_URL, _MockSession(lambda url: _MockResponse(GALLERY_PAGE))
        )
        assert meta.series_title == "Immoral Desires 12"
        assert meta.total_pages == 3
        assert meta.language == "en"
        assert meta.cover_url == "https://richpopup.com/comic/no8koexzgw0sn2iy4kib/1.webp"

    @pytest.mark.asyncio
    async def test_listing_url_rejected(self):
        scraper = RichPopupScraper()
        with pytest.raises(ScrapeError, match="listing page"):
            await scraper.scrape(
                "https://richpopup.com/tags/milf/",
                _MockSession(lambda url: _MockResponse("<html></html>")),
            )

    @pytest.mark.asyncio
    async def test_no_images(self):
        scraper = RichPopupScraper()
        with pytest.raises(ScrapeError, match="No images found"):
            await scraper.scrape(
                GALLERY_URL, _MockSession(lambda url: _MockResponse("<html></html>"))
            )

    def test_registered(self):
        from comic_dl.scrapers import list_sources

        by_domain = {e.domain: e for e in list_sources()}
        assert by_domain[DOMAIN].name == "richpopup"
        assert not by_domain[DOMAIN].has_series
