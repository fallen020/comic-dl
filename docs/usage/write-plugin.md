# Writing a Plugin

Add a new site to comic-dl without forking the repository. A plugin is a
separate Python package that registers a `Source` class through the
`comic_dl.sources` entry-point group.

!!! warning
    Plugins are arbitrary code. A scraper plugin runs with your user
    account's privileges on every scrape. Only install plugins you trust.

## The plugin contract

A plugin exports one or more `Source` classes. Each class implements:

| Member | Required | Description |
| :----- | :------- | :---------- |
| `domain` | yes | Canonical host (e.g. `"mysite.example"`). One source owns one domain. |
| `capabilities` | no | Set of `"chapter"` / `"series"`. Defaults to `{"chapter"}`. |
| `name`, `version` | no | Shown by `--list-sources`. Default to class name / `"plugin"`. |
| `priority` | no | `int`, default `0`. Set `> 0` to override a built-in for the same domain. |
| `matches_url(url)` | no | Return `True` when this source handles `url`. Defaults to host matching. |
| `async scrape(url, client)` | if chapter | Fetch one gallery/chapter. Returns `PostMetadata`. |
| `async scrape_series(url, client)` | if series | Fetch a series listing. Returns `SeriesMetadata`. |

## Result types

`scrape` returns `comic_dl.models.PostMetadata`. Build it directly:

```python
from comic_dl.models import ImageItem, PostMetadata
```

Or build a `ScrapedChapter` and convert:

```python
from comic_dl.models import (
    ChapterInfo,
    ImageItem,
    ScrapedChapter,
    SourceInfo,
    chapter_to_post_metadata,
)

return chapter_to_post_metadata(ScrapedChapter(...))
```

`scrape_series` returns `comic_dl.models.SeriesMetadata` — a `series_title`
plus `chapters` as a list of dicts with `title`, `episode_no`, and `url`.

## Safe fetching

For security invariants to hold (hard timeout, per-hop redirect validation,
no redirects to loopback/private/metadata addresses), fetch through
`BaseScraper`:

```python
from comic_dl.scrapers.base import BaseScraper

soup = await BaseScraper.fetch_html(url, client)
```

## Minimal example

```python
# my_site/source.py
from curl_cffi.requests import AsyncSession

from comic_dl.models import ImageItem, PostMetadata


class MySiteSource:
    domain = "mysite.example"
    name = "my-site"
    version = "1.0.0"
    capabilities = {"chapter"}
    priority = 0

    def matches_url(self, url: str) -> bool:
        return url.startswith("https://mysite.example/g/")

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        # ... parse the page ...
        return PostMetadata(
            series_title="Series",
            chapter_title="Chapter",
            images=[
                ImageItem(
                    url="https://cdn.mysite.example/1.jpg",
                    page_number=1,
                    filename="001.jpg",
                )
            ],
        )
```

A complete, installable reference plugin lives in
[`examples/plugin-example/`](https://github.com/fallen020/comic-dl/blob/main/examples/plugin-example/pyproject.toml).

## Registering the entry point

Declare the entry point in your plugin's `pyproject.toml`:

```toml
[project.entry-points."comic_dl.sources"]
mysite = "my_site.source:MySiteSource"
```

The dotted value must resolve to a `Source` class (imported with no arguments)
or to an iterable of such classes.

After installing the plugin, restart the CLI. The source appears in
`comic-dl --list-sources` and handles matching URLs.

## Conflict handling

When two sources claim the same domain, the higher `priority` wins. On a tie,
the first registration is kept (built-ins register first at priority `0`). A
plugin replacing a built-in must set `priority > 0`.

## Testing

The registry functions in `comic_dl.scrapers.registry` are unit-testable
without a network. Register a fake source, resolve a URL, and assert the state.
