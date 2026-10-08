"""Reference scraper plugin for comic-dl.

This is a minimal, working example of a third-party source. It is a chapter-only
source for the fictional domain ``fake.example`` and returns a single catalog
image without hitting the network, so it is safe to install and inspect.

See docs/usage/write-plugin.md for the full plugin contract.
"""

from __future__ import annotations

from curl_cffi.requests import AsyncSession

from comic_dl.models import (
    ChapterInfo,
    ImageItem,
    PostMetadata,
    ScrapedChapter,
    SourceInfo,
    chapter_to_post_metadata,
)


class FakeExampleSource:
    """Serves ``fake.example/g/{id}`` galleries with one static image."""

    domain = "fake.example"
    name = "fakeexample"
    version = "1.0.0"
    capabilities = frozenset({"chapter"})
    priority = 0

    def matches_url(self, url: str) -> bool:
        return url.startswith("https://fake.example/g/")

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        return chapter_to_post_metadata(
            ScrapedChapter(
                info=ChapterInfo(
                    series_title="Fake Series",
                    chapter_title="Chapter from fake.example",
                ),
                source=SourceInfo(url=url, service="fakeexample", post_id="catalog"),
                images=[
                    ImageItem(
                        url="https://static.fake.example/catalog/001.jpg",
                        page_number=1,
                    )
                ],
            )
        )
