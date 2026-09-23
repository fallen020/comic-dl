from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from comic_dl.scrapers.sites.tapas import (
    DOMAIN,
    TapasScraper,
    _episode_id_from_url,
    _extract_cover,
    _extract_creator,
    _extract_reader_images,
    _extract_synopsis,
    _is_free,
    _series_id_from_series_html,
    _series_slug_from_episode,
    is_episode_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

SLUG = "Lets-Play-official"
SID = "313219"
SERIES_URL = f"https://tapas.io/series/{SLUG}"
EPISODE_URL = "https://tapas.io/episode/3667907"

SERIES_PAGE = """
<html><head><title>Let's Play | Tapas Comics</title>
<meta property="og:description" content="A romance story."/>
<meta property="og:image" content="https://us-a.tapas.io/sa/cover.png"/>
</head><body>
<p class="center-info__title">Let's Play</p>
<p class="js-ep-cnt">190 episodes</p>
<div data-series-id="313219"></div>
<div data-series-id="313219"></div>
<div data-series-id="335435"></div>
<ul class="list-body js-episodes"></ul>
</body></html>
"""

EPISODES_P1 = {
    "code": 200,
    "data": {
        "episodes": [
            {"id": 3667907, "title": "Episode 0", "free": True, "must_pay": False},
            {"id": 3667908, "title": "Episode 1", "free": False, "must_pay": True},
        ],
        "pagination": {"has_next": True},
    },
}
EPISODES_P2 = {
    "code": 200,
    "data": {
        "episodes": [{"id": 3667909, "title": "", "free": True}],
        "pagination": {"has_next": False},
    },
}

EPISODE_PAGE = """
<html><head><title>Read Let's Play :: Episode 0 | Tapas Comics</title></head><body>
<div class="viewer__header"><p class="title">Episode 0</p></div>
<p class="center-info__title">Let's Play</p>
<article class="viewer__body js-episode-article">
<img class="content__img js-lazy" data-src="https://us-a.tapas.io/pc/2f/a-0.jpg?__token__=x" src="data:image/gif;base64,px"/>
<img class="content__img js-lazy" data-src="https://us-a.tapas.io/pc/2f/a-1.jpg?__token__=x"/>
</article>
</body></html>
"""


def _handler(url):
    if url == SERIES_URL:
        return _MockResponse(SERIES_PAGE)
    if "/episodes" in url:
        if "page=2" in url:
            return _MockResponse(json_data=EPISODES_P2)
        return _MockResponse(json_data=EPISODES_P1)
    return _MockResponse(EPISODE_PAGE)


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url(SERIES_URL)
        assert is_series_url(f"{SERIES_URL}/")
        assert is_series_url("https://www.tapas.io/series/Foo-Bar")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://tapas.io/")
        assert not is_series_url("https://tapas.io/series/")
        assert not is_series_url(EPISODE_URL)
        assert not is_series_url("https://other.io/series/Foo")

    def test_valid_episode_urls(self):
        assert is_episode_url(EPISODE_URL)
        assert is_episode_url("https://tapas.io/episode/1")

    def test_invalid_episode_urls(self):
        assert not is_episode_url("")
        assert not is_episode_url(SERIES_URL)
        assert not is_episode_url("https://tapas.io/episode/abc")

    def test_ids(self):
        assert _episode_id_from_url(EPISODE_URL) == "3667907"
        assert _episode_id_from_url(SERIES_URL) == ""
        assert _series_id_from_series_html(SERIES_PAGE) == SID
        assert _series_id_from_series_html("<html></html>") == ""

    def test_matches(self):
        scraper = TapasScraper()
        assert scraper.matches_url(SERIES_URL)
        assert scraper.matches_url(EPISODE_URL)
        assert scraper.matches_series_url(SERIES_URL)
        assert not scraper.matches_series_url(EPISODE_URL)

    def test_is_free(self):
        assert _is_free({"free": True})
        assert not _is_free({"free": True, "must_pay": True})
        assert not _is_free({"free": False})
        assert not _is_free({})
        assert not _is_free([])

    def test_reader_images_skip_placeholders(self):
        soup = BeautifulSoup(EPISODE_PAGE, "lxml")
        images = _extract_reader_images(soup, EPISODE_URL)
        assert len(images) == 2
        assert images[0].url.startswith("https://us-a.tapas.io/pc/")

    def test_synopsis(self):
        soup = BeautifulSoup(SERIES_PAGE, "lxml")
        assert _extract_synopsis(soup) == ""
        html = '<div class="js-series-description">Real story here.</div>'
        assert _extract_synopsis(BeautifulSoup(html, "lxml")) == "Real story here."

    def test_series_slug_from_episode(self):
        html = (
            '<a href="/series/Real-Slug/info">x</a><a href="/series/Other-Slug">Recommendation</a>'
        )
        assert _series_slug_from_episode(BeautifulSoup(html, "lxml")) == "Real-Slug"
        assert _series_slug_from_episode(BeautifulSoup("<html></html>", "lxml")) == ""

    def test_cover_prefers_thumb_anchor(self):
        html = (
            '<a class="thumb js-series-btn"><img src="https://us-a.tapas.io/sa/cover_z.jpg"/></a>'
        )
        soup = BeautifulSoup(html, "lxml")
        assert _extract_cover(soup, {}) == "https://us-a.tapas.io/sa/cover_z.jpg"

    def test_cover_falls_back_to_og(self):
        from comic_dl.scrapers.base import meta_index

        soup = BeautifulSoup(
            '<html><head><meta property="og:image" content="https://x/banner.png"/>'
            "</head><body></body></html>",
            "lxml",
        )
        assert _extract_cover(soup, meta_index(soup)) == "https://x/banner.png"

    def test_creator(self):
        html = '<a href="/SomeStudio">Some Studio</a><p class="author-label">Creator</p>'
        assert _extract_creator(BeautifulSoup(html, "lxml")) == "Some Studio"
        assert _extract_creator(BeautifulSoup("<html></html>", "lxml")) == ""


class TestTapasScraper:
    @pytest.mark.asyncio
    async def test_scrape_chapter_success(self):
        scraper = TapasScraper()
        meta = await scraper.scrape(EPISODE_URL, _MockSession(_handler))

        assert meta.series_title == "Let's Play"
        assert meta.chapter_title == "Episode 0"
        assert meta.total_pages == 2
        assert meta.language == "en"

    @pytest.mark.asyncio
    async def test_scrape_chapter_enriches_description(self):
        episode = EPISODE_PAGE.replace(
            "</body>",
            '<a href="/series/Lets-Play-official/info">series</a></body>',
        )
        series = SERIES_PAGE.replace(
            "</body>",
            '<div class="js-series-description">Enriched story.</div>'
            '<a class="thumb js-series-btn"><img src="https://us-a.tapas.io/sa/cover_z.jpg"/></a>'
            '<a href="/Studio">Studio</a><p class="author-label">Creator</p></body>',
        )

        def handler(url):
            if "/series/" in url:
                return _MockResponse(series)
            return _MockResponse(episode)

        scraper = TapasScraper()
        meta = await scraper.scrape(EPISODE_URL, _MockSession(handler))
        assert meta.description == "Enriched story."
        assert meta.cover_url == "https://us-a.tapas.io/sa/cover_z.jpg"
        assert meta.authors == ["Studio"]

    @pytest.mark.asyncio
    async def test_scrape_locked_episode_raises(self):
        scraper = TapasScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape(
                EPISODE_URL,
                _MockSession(lambda url: _MockResponse("<html><body>locked</body></html>")),
            )

    @pytest.mark.asyncio
    async def test_scrape_series_skips_locked(self):
        scraper = TapasScraper()
        series = await scraper.scrape_series(SERIES_URL, _MockSession(_handler))

        assert series.series_title == "Let's Play"
        assert series.title_no == SLUG
        assert len(series.chapters) == 2
        assert series.chapters[0]["url"] == "https://tapas.io/episode/3667907"
        assert series.chapters[1]["url"] == "https://tapas.io/episode/3667909"
        assert series.chapters[1]["title"] == "Episode 3667909"

    @pytest.mark.asyncio
    async def test_homepage_url_rejected(self):
        from comic_dl.errors import ScrapeError

        scraper = TapasScraper()
        with pytest.raises(ScrapeError, match="listing page"):
            await scraper.scrape(
                "https://tapas.io/", _MockSession(lambda url: _MockResponse("<html></html>"))
            )

    def test_registered(self):
        from comic_dl.scrapers import list_sources

        by_domain = {e.domain: e for e in list_sources()}
        assert by_domain[DOMAIN].name == "tapas"
        assert by_domain[DOMAIN].has_series
