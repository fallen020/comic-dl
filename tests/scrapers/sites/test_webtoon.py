from __future__ import annotations

import json
from typing import ClassVar

import pytest

from comic_dl.scrapers.sites.webtoon import (
    PATTERN,
    _extract_chapters_from_json,
    _extract_images_from_json,
    _find_image_list,
    _find_json_script,
    _parse_url,
    is_chapter_url,
    is_series_url,
    normalize_webtoon_url,
    scrape_chapter,
    scrape_series,
)


class TestWebtoonPattern:
    def test_desktop_series(self):
        m = PATTERN.match(
            "https://www.webtoons.com/en/action/nano-machine/list?title_no=4344"
        )
        assert m
        assert m.group(1) == "en"
        assert m.group(2) == "action"
        assert m.group(3) == "nano-machine"
        assert m.group(4) is None
        assert m.group(5) == "list"
        assert m.group(6) == "4344"
        assert m.group(7) is None

    def test_mobile_series(self):
        m = PATTERN.match(
            "https://m.webtoons.com/en/action/nano-machine/list?title_no=4344"
        )
        assert m
        assert m.group(5) == "list"

    def test_desktop_chapter(self):
        m = PATTERN.match(
            "https://www.webtoons.com/en/action/nano-machine/ep-1-prologue/"
            "viewer?title_no=4344&episode_no=1"
        )
        assert m
        assert m.group(4) == "ep-1-prologue"
        assert m.group(5) == "viewer"
        assert m.group(7) == "1"

    def test_mobile_chapter(self):
        m = PATTERN.match(
            "https://m.webtoons.com/en/action/nano-machine/"
            "ep-2-chapter-1-mashin-the-demonic-spirit/"
            "viewer?title_no=4344&episode_no=2"
        )
        assert m
        assert m.group(4) == "ep-2-chapter-1-mashin-the-demonic-spirit"
        assert m.group(5) == "viewer"
        assert m.group(7) == "2"

    def test_invalid_domain(self):
        assert PATTERN.match("https://example.com/list?title_no=1") is None

    def test_no_query(self):
        assert PATTERN.match("https://www.webtoons.com/en/action/s/list") is None

    def test_trailing_slash_chapter(self):
        m = PATTERN.match(
            "https://www.webtoons.com/en/action/nano-machine/"
            "ep-1-prologue/viewer?title_no=4344&episode_no=1/"
        )
        assert m
        assert m.group(5) == "viewer"

    def test_unicode_slug(self):
        m = PATTERN.match(
            "https://www.webtoons.com/en/fantasy/모험자/list?title_no=4321"
        )
        assert m
        assert m.group(3) == "모험자"

    def test_non_numeric_title_no(self):
        assert PATTERN.match(
            "https://www.webtoons.com/en/action/s/list?title_no=abc"
        ) is None

    def test_non_numeric_episode_no(self):
        assert PATTERN.match(
            "https://www.webtoons.com/en/action/s/ep-1/viewer"
            "?title_no=1&episode_no=x"
        ) is None

    def test_query_only_no_path(self):
        assert PATTERN.match("https://www.webtoons.com/?title_no=4344") is None

    def test_extra_path_segment_after_viewer(self):
        assert PATTERN.match(
            "https://www.webtoons.com/en/action/s/viewer/extra"
            "?title_no=1&episode_no=1"
        ) is None


class TestParseUrl:
    def test_series(self):
        info = _parse_url(
            "https://www.webtoons.com/en/action/nano-machine/list?title_no=4344"
        )
        assert info["lang"] == "en"
        assert info["action"] == "list"
        assert info["title_no"] == "4344"

    def test_chapter(self):
        info = _parse_url(
            "https://m.webtoons.com/en/action/nano-machine/ep-1-prologue/"
            "viewer?title_no=4344&episode_no=1"
        )
        assert info["action"] == "viewer"
        assert info["episode_no"] == "1"

    def test_invalid(self):
        assert _parse_url("https://example.com") is None

    def test_extra_query_params_accepted(self):
        info = _parse_url(
            "https://www.webtoons.com/en/action/nano-machine/list"
            "?title_no=4344&utm_source=share&webtoon-platform-redirect=true"
        )
        assert info["action"] == "list"
        assert info["title_no"] == "4344"
        assert info["series_slug"] == "nano-machine"

    def test_extra_query_params_viewer(self):
        info = _parse_url(
            "https://www.webtoons.com/en/action/s/ep-1/viewer"
            "?title_no=4011&episode_no=230&foo=bar"
        )
        assert info["action"] == "viewer"
        assert info["episode_no"] == "230"

    def test_title_no_only_still_none(self):
        assert _parse_url("https://www.webtoons.com/?title_no=4344") is None


class TestDetectUrlType:
    def test_series_list(self):
        assert is_series_url(
            "https://www.webtoons.com/en/action/nano-machine/list?title_no=4344"
        ) is True
        assert is_chapter_url(
            "https://www.webtoons.com/en/action/nano-machine/list?title_no=4344"
        ) is False

    def test_chapter_viewer(self):
        assert is_chapter_url(
            "https://www.webtoons.com/en/action/nano-machine/"
            "ep-1-prologue/viewer?title_no=4344&episode_no=1"
        ) is True
        assert is_series_url(
            "https://www.webtoons.com/en/action/nano-machine/"
            "ep-1-prologue/viewer?title_no=4344&episode_no=1"
        ) is False

    def test_invalid_returns_false(self):
        assert is_series_url("https://example.com") is False
        assert is_chapter_url("https://example.com") is False

    def test_mismatched_action_vs_query(self):
        # A list URL carrying an episode_no still classifies as a series page.
        assert is_series_url(
            "https://www.webtoons.com/en/action/s/list?title_no=1&episode_no=5"
        ) is True

    def test_trailing_slash_classified(self):
        assert is_chapter_url(
            "https://www.webtoons.com/en/action/s/ep-1/viewer"
            "?title_no=1&episode_no=1/"
        ) is True


class TestNormalizeUrl:
    def test_mobile_to_desktop(self):
        result = normalize_webtoon_url(
            "https://m.webtoons.com/en/action/nano-machine/list?title_no=4344"
        )
        assert "www.webtoons.com" in result
        assert "m.webtoons.com" not in result

    def test_chapter_preserved(self):
        result = normalize_webtoon_url(
            "https://m.webtoons.com/en/action/nano-machine/"
            "ep-1-prologue/viewer?title_no=4344&episode_no=1"
        )
        assert "/ep-1-prologue/viewer" in result
        assert "episode_no=1" in result

    def test_already_desktop(self):
        url = "https://www.webtoons.com/en/action/s/list?title_no=1"
        assert normalize_webtoon_url(url) == url

    def test_invalid_unchanged(self):
        assert normalize_webtoon_url("https://example.com") == "https://example.com"

    def test_trailing_slash_stripped(self):
        result = normalize_webtoon_url(
            "https://www.webtoons.com/en/action/nano-machine/"
            "ep-1/viewer?title_no=4344&episode_no=1/"
        )
        assert result.endswith("episode_no=1")
        assert not result.endswith("/")

    def test_unicode_slug_preserved(self):
        result = normalize_webtoon_url(
            "https://www.webtoons.com/en/fantasy/모험자/list?title_no=4321"
        )
        assert "/모험자/list" in result

    def test_mismatched_title_no_episode_no_preserved(self):
        result = normalize_webtoon_url(
            "https://www.webtoons.com/en/action/other-series/"
            "ep-9/viewer?title_no=99&episode_no=7"
        )
        assert "title_no=99" in result
        assert "episode_no=7" in result
        assert "/ep-9/viewer" in result


class TestFindJsonScript:
    def test_next_data(self):
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"key":"val"}}}'
            "</script></html>"
        )
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        data = _find_json_script(soup)
        assert data == {"props": {"pageProps": {"key": "val"}}}

    def test_initial_state(self):
        html = (
            '<html><script>window.__INITIAL_STATE__ = '
            '{"episode":{"title":"Test"}}</script></html>'
        )
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        data = _find_json_script(soup)
        assert data == {"episode": {"title": "Test"}}

    def test_no_script(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<html></html>", "lxml")
        assert _find_json_script(soup) is None


class TestFindImageList:
    def test_next_data_path(self):
        data = {
            "props": {
                "pageProps": {
                    "episode": {
                        "images": [
                            {"url": "https://x.com/img1.jpg"},
                            {"url": "https://x.com/img2.jpg"},
                        ]
                    }
                }
            }
        }
        result = _find_image_list(data)
        assert len(result) == 2
        assert result[0]["url"] == "https://x.com/img1.jpg"

    def test_episode_images(self):
        data = {"episode": {"images": [{"url": "https://x.com/1.jpg"}]}}
        result = _find_image_list(data)
        assert len(result) == 1

    def test_episode_image_list(self):
        data = {"episode": {"imageList": ["https://x.com/1.jpg", "https://x.com/2.jpg"]}}
        result = _find_image_list(data)
        assert len(result) == 2

    def test_none(self):
        assert _find_image_list({}) is None


class TestScrapeChapter:
    pytestmark = pytest.mark.asyncio

    _chapter_html = (
        "<html><head>"
        '<meta property="og:title" content="Prologue">'
        '<meta property="og:description" content="A great chapter">'
        '<meta property="og:image" content="https://x.com/cover.jpg">'
        '<meta property="og:url" content="https://www.webtoons.com/en/action/s/ep-1-prologue/viewer?title_no=1&episode_no=1">'
        '<title>Prologue - Webtoon</title>'
        "</head><body></body></html>"
    )

    _next_data: ClassVar[dict] = {
        "props": {
            "pageProps": {
                "episode": {
                    "title": "Prologue",
                    "images": [
                        {"url": "https://webtoon-phinf.pstatic.net/1.jpg"},
                        {"url": "https://webtoon-phinf.pstatic.net/2.jpg"},
                    ],
                }
            }
        }
    }

    class MockClient:
        async def get(self, url, **kwargs):
            class Resp:
                status_code = 200
                text = TestScrapeChapter._chapter_html

                def raise_for_status(self):
                    pass

            return Resp()

    class MockClientWithData:
        async def get(self, url, **kwargs):
            html = TestScrapeChapter._chapter_html.replace(
                "</head>",
                '<script id="__NEXT_DATA__" type="application/json">'
                + json.dumps(TestScrapeChapter._next_data)
                + "</script></head>",
            )

            class Resp:
                status_code = 200
                text = html

                def raise_for_status(self):
                    pass

            return Resp()

    async def test_invalid_url(self):
        with pytest.raises(ValueError, match="Invalid WEBTOON"):
            await scrape_chapter("https://example.com", None)

    async def test_meta_tags_fallback(self):
        with pytest.raises(ValueError, match="No images found"):
            await scrape_chapter(
                "https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1",
                self.MockClient(),  # type: ignore
            )

    async def test_with_next_data(self):
        meta = await scrape_chapter(
            "https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1",
            self.MockClientWithData(),  # type: ignore
        )
        assert meta.chapter_title == "Prologue"
        assert len(meta.images) == 2
        assert meta.images[0].url == "https://webtoon-phinf.pstatic.net/1.jpg"
        assert meta.images[0].page_number == 1
        assert meta.service == "webtoons.com"
        assert meta.user_id == "1"
        assert meta.total_pages == 2

    async def test_writer_artist_split(self):
        html = TestScrapeChapter._chapter_html.replace(
            "</head>",
            '<meta property="com-linewebtoon:webtoon:author" content="Disney, Adriano Barone"/>'
            '<meta property="com-linewebtoon:webtoon:artist" content="Fabrizio Cosentino"/>'
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(TestScrapeChapter._next_data)
            + "</script></head>",
        )

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    text = html

                    def raise_for_status(self):
                        pass

                return Resp()

        meta = await scrape_chapter(
            "https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1",
            MockClient(),  # type: ignore
        )
        assert meta.authors == ["Disney", "Adriano Barone"]
        assert meta.artists == ["Fabrizio Cosentino"]
        assert meta.authors != meta.artists
        # Webtoons are vertical / left-to-right; never manga right-to-left.
        assert meta.reading_direction == "ltr"

    async def test_superhero_genre_from_class(self):
        html = TestScrapeChapter._chapter_html.replace(
            "</head>",
            '<h2 class="genre g_superhero">Superhero</h2>'
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(TestScrapeChapter._next_data)
            + "</script></head>",
        )

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    text = html

                    def raise_for_status(self):
                        pass

                return Resp()

        meta = await scrape_chapter(
            "https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1",
            MockClient(),  # type: ignore
        )
        assert meta.genres == ["Superhero"]

    async def test_genre_from_keywords_when_class_unknown(self):
        html = TestScrapeChapter._chapter_html.replace(
            "</head>",
            '<meta name="keywords" content="Weekly, Superhero, Action"/>'
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(TestScrapeChapter._next_data)
            + "</script></head>",
        )

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    text = html

                    def raise_for_status(self):
                        pass

                return Resp()

        meta = await scrape_chapter(
            "https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1",
            MockClient(),  # type: ignore
        )
        assert meta.genres == ["Superhero"]

    async def test_genre_from_hyphenated_keyword(self):
        # Live keywords use "Super-hero"; the map key is "superhero".
        html = TestScrapeChapter._chapter_html.replace(
            "</head>",
            '<meta name="keywords" content="Weekly, Super-hero, Action"/>'
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(TestScrapeChapter._next_data)
            + "</script></head>",
        )

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    text = html

                    def raise_for_status(self):
                        pass

                return Resp()

        meta = await scrape_chapter(
            "https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1",
            MockClient(),  # type: ignore
        )
        assert meta.genres == ["Superhero"]

    async def test_artist_falls_back_to_author(self):
        html = TestScrapeChapter._chapter_html.replace(
            "</head>",
            '<meta property="com-linewebtoon:webtoon:author" content="Disney, Adriano Barone"/>'
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(TestScrapeChapter._next_data)
            + "</script></head>",
        )

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    text = html

                    def raise_for_status(self):
                        pass

                return Resp()

        meta = await scrape_chapter(
            "https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1",
            MockClient(),  # type: ignore
        )
        assert meta.authors == ["Disney", "Adriano Barone"]
        assert meta.artists == ["Disney", "Adriano Barone"]

    async def test_no_images_raises(self):
        html = (
            "<html><head>"
            '<meta property="og:title" content="Empty">'
            "</head><body></body></html>"
        )

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    text = html

                    def raise_for_status(self):
                        pass

                return Resp()

        with pytest.raises(ValueError, match="No images found"):
            await scrape_chapter(
                "https://www.webtoons.com/en/action/s/ep-1/viewer?title_no=1&episode_no=1",
                MockClient(),  # type: ignore
            )


class TestScrapeSeries:
    pytestmark = pytest.mark.asyncio

    _series_html = (
        "<html><head>"
        '<meta property="og:title" content="Nano Machine">'
        '<meta property="og:description" content="A cool series">'
        '<meta property="og:image" content="https://x.com/cover.jpg">'
        "</head><body></body></html>"
    )

    _series_next_data: ClassVar[dict] = {
        "props": {
            "pageProps": {
                "series": {
                    "title": "Nano Machine",
                    "description": "A cool series",
                    "thumbnail": "https://x.com/cover.jpg",
                },
                "episodes": [
                    {
                        "title": "Prologue",
                        "episodeNo": 1,
                        "url": (
                            "/en/action/nano-machine/ep-1-prologue/viewer?"
                            "title_no=4344&episode_no=1"
                        ),
                    },
                    {
                        "title": "Chapter 1",
                        "episodeNo": 2,
                        "url": (
                            "/en/action/nano-machine/ep-2-chapter-1/viewer?"
                            "title_no=4344&episode_no=2"
                        ),
                    },
                ],
            }
        }
    }

    class MockSeriesClient:
        async def get(self, url, **kwargs):
            html = TestScrapeSeries._series_html.replace(
                "</head>",
                '<script id="__NEXT_DATA__" type="application/json">'
                + json.dumps(TestScrapeSeries._series_next_data)
                + "</script></head>",
            )

            class Resp:
                status_code = 200
                text = html

                def raise_for_status(self):
                    pass

            return Resp()

    async def test_invalid_url(self):
        with pytest.raises(ValueError, match="Invalid WEBTOON"):
            await scrape_series("https://example.com", None)

    async def test_with_next_data(self):
        info = await scrape_series(
            "https://www.webtoons.com/en/action/nano-machine/list?title_no=4344",
            self.MockSeriesClient(),  # type: ignore
        )
        assert info.series_title == "Nano Machine"
        assert info.description == "A cool series"
        assert info.cover_url == "https://x.com/cover.jpg"
        assert len(info.chapters) == 2
        assert info.chapters[0]["title"] == "Prologue"
        assert info.chapters[0]["episode_no"] == "1"
        assert "/ep-1-prologue/viewer" in info.chapters[0]["url"]
        assert info.chapters[1]["episode_no"] == "2"

    async def test_no_chapters_raises(self):
        html = (
            "<html><head>"
            '<meta property="og:title" content="Empty">'
            "</head><body></body></html>"
        )

        class MockClient:
            async def get(self, url, **kwargs):
                class Resp:
                    status_code = 200
                    text = html

                    def raise_for_status(self):
                        pass

                return Resp()

        with pytest.raises(ValueError, match="No chapters found"):
            await scrape_series(
                "https://www.webtoons.com/en/action/s/list?title_no=1",
                MockClient(),  # type: ignore
            )

    async def test_pagination_continues_past_visible_window(self):
        """Episodes on list pages beyond the first page's pagination widget
        must still be collected (the widget only shows a page window)."""
        url = "https://www.webtoons.com/en/action/nano-machine/list?title_no=4344"

        def _ep_item(ep_no: int) -> str:
            href = (
                f"/en/action/nano-machine/ep-{ep_no}/viewer"
                f"?title_no=4344&episode_no={ep_no}"
            )
            return (
                f'<li class="_episodeItem">'
                f'<a href="{href}">'
                f'<span class="subj">Ep. {ep_no}</span></a></li>'
            )

        def _page(*eps: int, page_links: str = "") -> str:
            items = "".join(_ep_item(e) for e in eps)
            return (
                "<html><head>"
                '<meta property="og:title" content="Nano Machine">'
                "</head><body>"
                f"{items}{page_links}"
                "</body></html>"
            )

        page1 = _page(
            1, 2,
            page_links='<a class="pg_page" href="?page=2">2</a>',
        )
        page2 = _page(3, 4)
        empty = "<html><head></head><body></body></html>"

        class MockPaginatedClient:
            async def get(self, url: str, **kwargs):
                html = page1
                if "page=2" in url:
                    html = page2
                elif "page=" in url:
                    html = empty

                class Resp:
                    status_code = 200
                    text = html

                    def raise_for_status(self):
                        pass

                return Resp()

        info = await scrape_series(url, MockPaginatedClient())  # type: ignore
        assert [ch["episode_no"] for ch in info.chapters] == ["1", "2", "3", "4"]


class TestExtractImagesFromJson:
    def test_with_images(self):
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"episode":{"images":['
            '{"url":"https://webtoon-phinf.pstatic.net/1.jpg"},'
            '{"url":"https://webtoon-phinf.pstatic.net/2.jpg"}'
            "]}}}}"
            "</script></html>"
        )
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        images = _extract_images_from_json(soup)
        assert images is not None
        assert len(images) == 2
        assert images[0].url == "https://webtoon-phinf.pstatic.net/1.jpg"
        assert images[0].page_number == 1
        assert images[1].page_number == 2

    def test_deduplicates_by_url(self):
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"episode":{"images":['
            '{"url":"https://webtoon-phinf.pstatic.net/1.jpg"},'
            '{"url":"https://webtoon-phinf.pstatic.net/1.jpg"}'
            "]}}}}"
            "</script></html>"
        )
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        images = _extract_images_from_json(soup)
        assert images is not None
        assert len(images) == 1

    def test_no_json_data(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<html></html>", "lxml")
        assert _extract_images_from_json(soup) is None

    def test_no_images_in_data(self):
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"episode":{}}}}'
            "</script></html>"
        )
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        assert _extract_images_from_json(soup) is None

    def test_image_list_format(self):
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            '{"episode":{"imageList":["https://webtoon-phinf.pstatic.net/1.jpg","https://webtoon-phinf.pstatic.net/2.jpg"]}}'
            "</script></html>"
        )
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        images = _extract_images_from_json(soup)
        assert images is not None
        assert len(images) == 2


class TestExtractChaptersFromJson:
    def test_with_episodes(self):
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"episodes":['
            '{"title":"Prologue","episodeNo":1,"url":"/en/s/ep-1/viewer?title_no=1&episode_no=1"},'
            '{"title":"Chapter 1","episodeNo":2,"url":"/en/s/ep-2/viewer?title_no=1&episode_no=2"}'
            "]}}}"
            "</script></html>"
        )
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        chapters = _extract_chapters_from_json(soup, "https://www.webtoons.com/en/s/list?title_no=1")
        assert chapters is not None
        assert len(chapters) == 2
        assert chapters[0]["title"] == "Prologue"
        assert chapters[0]["episode_no"] == "1"
        assert "ep-1/viewer" in chapters[0]["url"]
        assert chapters[1]["episode_no"] == "2"

    def test_deduplicates_by_episode_no(self):
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"episodes":['
            '{"title":"Prologue","episodeNo":1,'
            '"url":"/en/s/ep-1/viewer?title_no=1&episode_no=1"},'
            '{"title":"Prologue Again","episodeNo":1,'
            '"url":"/en/s/ep-1-dup/viewer?title_no=1&episode_no=1"}'
            "]}}}"
            "</script></html>"
        )
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        chapters = _extract_chapters_from_json(soup, "https://www.webtoons.com/en/s/list?title_no=1")
        assert chapters is not None
        assert len(chapters) == 1

    def test_no_json_data(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<html></html>", "lxml")
        assert _extract_chapters_from_json(soup, "https://example.com") is None

    def test_no_episodes(self):
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{}}}'
            "</script></html>"
        )
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        assert _extract_chapters_from_json(soup, "https://example.com") is None
