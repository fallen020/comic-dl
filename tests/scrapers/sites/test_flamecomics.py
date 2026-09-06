from __future__ import annotations

import json

import pytest

from comic_dl.scrapers.sites.flamecomics import (
    DOMAIN,
    FlameScraper,
    _canonical_chapter_number,
    is_chapter_url,
    is_series_url,
)
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession


class TestUrlPatterns:
    def test_valid_series_urls(self):
        assert is_series_url("https://flamecomics.xyz/series/123/")
        assert is_series_url("https://www.flamecomics.xyz/series/456/")

    def test_invalid_series_urls(self):
        assert not is_series_url("")
        assert not is_series_url("https://flamecomics.xyz/")
        assert not is_series_url("https://other.com/series/1/")
        assert not is_series_url("https://flamecomics.xyz/series/abc/")

    def test_valid_chapter_urls(self):
        assert is_chapter_url("https://flamecomics.xyz/series/123/a1b2c3d4/")
        assert is_chapter_url("https://www.flamecomics.xyz/series/456/ff001122/")

    def test_invalid_chapter_urls(self):
        assert not is_chapter_url("")
        assert not is_chapter_url("https://flamecomics.xyz/")
        assert not is_chapter_url("https://flamecomics.xyz/series/123/")
        assert not is_chapter_url("https://flamecomics.xyz/series/abc/xyz/")


class TestCanonicalChapterNumber:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("10.0", "10"),
            ("100", "100"),
            ("1", "1"),
            ("123.0", "123"),
            ("1.5", "1.5"),
            ("10", "10"),
            ("0", "0"),
            ("ch10", "ch10"),
            ("", ""),
            (" 42 ", "42"),
        ],
    )
    def test_canonical(self, raw, expected):
        assert _canonical_chapter_number(raw) == expected


class TestFlameScraper:
    def test_domain_attr(self):
        scraper = FlameScraper()
        assert scraper.domain == DOMAIN

    @pytest.mark.asyncio
    async def test_scrape_no_images_raises(self):
        html = b"<html><head><title>Test</title></head><body></body></html>"
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        with pytest.raises(ValueError, match="No images found"):
            await scraper.scrape("https://flamecomics.xyz/series/1/a1b2/", session)

    @pytest.mark.asyncio
    async def test_scrape_with_jsonld(self):
        ld_json = json.dumps({
            "@type": "Chapter",
            "name": "My Series - Chapter 1",
            "isPartOf": {"name": "My Series"},
        })
        html = (
            b"<html><head>"
            b'<title>Chapter 1 - My Series - Flame Comics</title>'
            b'<meta property="og:description" content="Great chapter"/>'
            b'<meta property="og:image" content="https://cdn.flamecomics.xyz/cover.jpg"/>'
            b'<meta property="og:site_name" content="Flame Comics"/>'
            b'<script type="application/ld+json">' + ld_json.encode() + b'</script>'
            b'</head><body>'
            b'<img src="https://cdn.flamecomics.xyz/assets/read/page1.jpg" alt="001.jpg"/>'
            b'<img src="https://cdn.flamecomics.xyz/uploads/page2.jpg" alt="002.jpg"/>'
            b'</body></html>'
        )
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        meta = await scraper.scrape("https://flamecomics.xyz/series/1/a1b2/", session)

        assert meta.series_title == "My Series"
        assert meta.chapter_title == "Chapter 1"
        assert len(meta.images) == 1
        assert meta.description == "Great chapter"
        assert meta.cover_url == "https://cdn.flamecomics.xyz/cover.jpg"
        assert meta.service == DOMAIN

    @pytest.mark.asyncio
    async def test_scrape_fallback_title(self):
        html = (
            b"<html><head>"
            b'<title>Chapter 5 - My Series - Flame Comics</title>'
            b'<meta property="og:site_name" content="Flame Comics"/>'
            b'</head><body>'
            b'<img src="https://cdn.flamecomics.xyz/uploads/p1.jpg" alt="001.jpg"/>'
            b'</body></html>'
        )
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        meta = await scraper.scrape("https://flamecomics.xyz/series/1/b2c3/", session)

        assert meta.chapter_title == "Chapter 5"
        assert len(meta.images) == 1

    @pytest.mark.asyncio
    async def test_next_data_cover_beats_stale_og_image(self):
        """The site's own CDN cover wins; og:image is only a fallback."""
        next_data = {
            "props": {
                "pageProps": {
                    "series": {
                        "series_id": 42,
                        "title": "Test Series",
                        "cover": "cover.webp",
                    },
                    "chapter": {"chapter": "1", "token": "aaa111"},
                }
            }
        }
        html = (
            b"<html><head>"
            b'<meta property="og:image" content="https://cdn.flamecomics.xyz/stale-preview.jpg"/>'
            b'<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(next_data).encode()
            + b'</script>'
            b'</head><body>'
            b'<img src="https://cdn.flamecomics.xyz/uploads/p1.jpg" alt="001.jpg"/>'
            b'</body></html>'
        )
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        meta = await scraper.scrape("https://flamecomics.xyz/series/42/a1b2/", session)

        assert meta.cover_url == (
            "https://cdn.flamecomics.xyz/uploads/images/series/42/cover.webp"
        )

    @pytest.mark.asyncio
    async def test_scrape_series(self):
        next_data = {
            "props": {
                "pageProps": {
                    "series": {
                        "series_id": 42,
                        "title": "Test Series",
                        "description": "<p>A great series</p>",
                        "cover": "cover.webp",
                    },
                    "chapters": [
                        {"chapter": "1", "token": "aaa111", "title": "Prologue"},
                        {"chapter": "2", "token": "bbb222", "title": ""},
                    ],
                }
            }
        }
        html = (
            b"<html><head>"
            b'<title>Test Series - Flame Comics</title>'
            b'<meta property="og:site_name" content="Flame Comics"/>'
            b'<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(next_data).encode()
            + b'</script>'
            b'</head><body></body></html>'
        )
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        info = await scraper.scrape_series("https://flamecomics.xyz/series/42/", session)

        assert info.series_title == "Test Series"
        assert info.description == "A great series"
        assert info.cover_url == "https://cdn.flamecomics.xyz/uploads/images/series/42/cover.webp"
        assert len(info.chapters) == 2
        assert info.chapters[0]["title"] == "Ch. 2"
        assert info.chapters[0]["episode_no"] == "2"
        assert info.chapters[1]["title"] == "Ch. 1 - Prologue"
        assert info.chapters[1]["episode_no"] == "1"

    @pytest.mark.asyncio
    async def test_scrape_series_keeps_trailing_zero_chapters(self):
        """Chapter 100 and 10.0 must not be mangled into chapter 1."""
        next_data = {
            "props": {
                "pageProps": {
                    "series": {
                        "series_id": 7,
                        "title": "Long Series",
                        "cover": "cover.webp",
                    },
                    "chapters": [
                        {"chapter": "100", "token": "aaa", "title": ""},
                        {"chapter": "10.0", "token": "bbb", "title": ""},
                        {"chapter": "1", "token": "ccc", "title": ""},
                    ],
                }
            }
        }
        html = (
            b"<html><head>"
            b'<title>Long Series - Flame Comics</title>'
            b'<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(next_data).encode()
            + b'</script>'
            b"</head><body></body></html>"
        )
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        info = await scraper.scrape_series("https://flamecomics.xyz/series/7/", session)

        numbers = {ch["episode_no"] for ch in info.chapters}
        assert numbers == {"100", "10", "1"}
        titles = [ch["title"] for ch in info.chapters]
        assert "Ch. 100" in titles
        assert "Ch. 10" in titles
        assert "Ch. 1" in titles

    @pytest.mark.asyncio
    async def test_scrape_chapter_keeps_trailing_zero_number(self):
        next_data = {
            "props": {
                "pageProps": {
                    "chapter": {
                        "chapter_title": "Finale",
                        "chapter": "100",
                    },
                    "series": {"title": "Long Series"},
                }
            }
        }
        html = (
            b"<html><head>"
            b'<title>Finale - Long Series - Flame Comics</title>'
            b'<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(next_data).encode()
            + b'</script>'
            b"</head><body>"
            b'<img src="https://cdn.flamecomics.xyz/uploads/p1.jpg" alt="001.jpg"/>'
            b"</body></html>"
        )
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        meta = await scraper.scrape("https://flamecomics.xyz/series/7/fff/", session)
        assert meta.chapter_title == "Finale"
        assert meta.chapter_number == "100"

    @pytest.mark.asyncio
    async def test_scrape_chapter_enriches_from_series_page(self):
        chapter_html = (
            b"<html><head>"
            b'<title>Ep 1 - My Series - Flame Comics</title>'
            b'<meta property="og:site_name" content="Flame Comics"/>'
            b'<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({
                "props": {
                    "pageProps": {
                        "chapter": {"chapter_title": "Ep 1", "chapter": "1"},
                        "series": {"title": "My Series", "series_id": 42},
                    }
                }
            }).encode()
            + b'</script>'
            b"</head><body>"
            b'<img src="https://cdn.flamecomics.xyz/uploads/p1.jpg" alt="001.jpg"/>'
            b"</body></html>"
        )
        series_html = (
            b"<html><head>"
            b'<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({
                "props": {
                    "pageProps": {
                        "series": {
                            "series_id": 42,
                            "title": "My Series",
                            "type": "Manhwa",
                            "publisher": ["Acme Studio"],
                            "status": "Hiatus",
                            "year": 2021,
                        }
                    }
                }
            }).encode()
            + b'</script>'
            b"</head><body></body></html>"
        )

        def handler(url):
            if url.endswith("/series/42/"):
                return _MockResponse(series_html)
            return _MockResponse(chapter_html)

        session = _MockSession(handler)
        scraper = FlameScraper()
        meta = await scraper.scrape("https://flamecomics.xyz/series/42/a1b2/", session)

        assert meta.publisher == "Acme Studio"
        assert meta.status == "Hiatus"
        assert meta.year == 2021
        assert meta.reading_direction == "ltr"

    @pytest.mark.asyncio
    async def test_scrape_series_no_next_data_raises(self):
        html = b"<html><head><title>Test</title></head><body></body></html>"
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        with pytest.raises(ValueError, match="Could not find series data"):
            await scraper.scrape_series("https://flamecomics.xyz/series/1/", session)

    @staticmethod
    def _chapter_html(next_data: dict, ld_json: dict | None = None) -> bytes:
        body = (
            b'<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(next_data).encode()
            + b"</script>"
        )
        if ld_json is not None:
            body += (
                b'<script type="application/ld+json">'
                + json.dumps(ld_json).encode()
                + b"</script>"
            )
        return (
            b"<html><head>"
            b'<meta property="og:site_name" content="Flame Comics"/>'
            + body
            + b"</head><body>"
            b'<img src="https://cdn.flamecomics.xyz/uploads/p1.jpg" alt="001.jpg"/>'
            b"</body></html>"
        )

    @pytest.mark.asyncio
    async def test_chapter_title_ignores_series_title_field(self):
        """chapter['title'] nests the series title on FlameComics; the real
        label lives in JSON-LD (name minus the series prefix)."""
        next_data = {
            "props": {
                "pageProps": {
                    "chapter": {
                        "chapter": "4.00",
                        "token": "d64caa",
                        "title": "My Series",
                        "chapter_title": "",
                    }
                }
            }
        }
        ld_json = {
            "@type": "Chapter",
            "name": "My Series - Chapter 4",
            "isPartOf": {"name": "My Series"},
        }
        html = self._chapter_html(next_data, ld_json)
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        meta = await scraper.scrape("https://flamecomics.xyz/series/42/d64caa/", session)

        assert meta.series_title == "My Series"
        assert meta.chapter_title == "Chapter 4"
        assert meta.chapter_number == "4"

    @pytest.mark.asyncio
    async def test_chapter_title_synthesized_from_number(self):
        """Without JSON-LD or a <title> tag, the chapter number becomes the
        label instead of leaking the series title."""
        next_data = {
            "props": {
                "pageProps": {
                    "chapter": {
                        "chapter": "7",
                        "token": "fff111",
                        "title": "My Series",
                        "chapter_title": "",
                    }
                }
            }
        }
        html = self._chapter_html(next_data)
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        meta = await scraper.scrape("https://flamecomics.xyz/series/42/fff111/", session)

        assert meta.chapter_title == "Ch. 7"
        assert meta.chapter_number == "7"

    @pytest.mark.asyncio
    async def test_chapter_title_falls_back_to_token(self):
        """A chapter page with no number, JSON-LD, or <title> still gets a
        non-empty label (the token) so archives never get an empty stem."""
        next_data = {
            "props": {
                "pageProps": {
                    "chapter": {
                        "token": "fff222",
                        "title": "My Series",
                        "chapter_title": "",
                    }
                }
            }
        }
        html = self._chapter_html(next_data)
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        meta = await scraper.scrape("https://flamecomics.xyz/series/42/fff222/", session)

        assert meta.chapter_title == "fff222"

    @pytest.mark.asyncio
    async def test_chapter_data_enrichment(self):
        """Chapter pages carry cover/tags/description; use them instead of
        og:image and an empty genre list."""
        next_data = {
            "props": {
                "pageProps": {
                    "chapter": {
                        "chapter": "2",
                        "chapter_title": "Ep 2",
                        "title": "My Series",
                        "cover": "cover.webp",
                        "series_id": 10,
                        "tags": ["Action", "Drama"],
                        "description": "<p>The real description</p>",
                        "language": "English",
                    }
                }
            }
        }
        html = self._chapter_html(next_data)
        session = _MockSession(lambda url: _MockResponse(html))
        scraper = FlameScraper()
        meta = await scraper.scrape("https://flamecomics.xyz/series/10/aaa/", session)

        assert meta.cover_url == (
            "https://cdn.flamecomics.xyz/uploads/images/series/10/cover.webp"
        )
        assert meta.genres == ["Action", "Drama"]
        assert meta.description == "The real description"
        assert meta.language == "English"
