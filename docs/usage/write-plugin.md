# Writing a Plugin

Add a new site to comic-dl without forking the repository. A plugin is a
separate Python package that registers a `Source` class through the
`comic_dl.sources` entry-point group.

> [!WARNING]
> Plugins are arbitrary code. A scraper plugin runs with your user
> account's privileges on every scrape. Only install plugins you trust.

## Before you write a parser

Most sites need no new code. Check in this order:

1. **Generic extraction already handles it.** Run `comic-dl --dry-run URL`
   with the fallback on (the default): a direct image URL, an image gallery
   page, or a chapter/series listing in static HTML or embedded JSON comes
   back with a `Using generic extraction for ...` note. If the listing is
   right, stop here — there is nothing to write. The fallback never runs
   JavaScript and never shadows a site scraper; `--no-generic` turns it off.
2. **Known theme.** A WordPress Madara site, a `/series/comic/` ValiScans-platform
   site, or an API-driven VComics site is a metadata-only subclass:
   `uv run python scripts/new-site.py mysite mysite.example --theme madara`
   (or `vali` / `vcomics`) writes the module and its test stub, with TODOs
   marking the site-specific parts.
3. **Bespoke parser.** Only now subclass `BaseScraper`: class-level
   `series_url_re` / `chapter_url_re` with `matches_url` /
   `matches_series_url` derived from them (the `bare` theme stub shows the
   shape), `ScrapedChapter` results, and the standard errors below.

## The plugin contract

A plugin exports one or more `Source` classes. Each class implements:

| Member | Required | Description |
| :----- | :------- | :---------- |
| `domain` | yes | Canonical host (e.g. `"mysite.example"`). One source owns one domain. |
| `capabilities` | no | Set of `"chapter"` / `"series"`. Defaults to `{"chapter"}`. |
| `name`, `version` | no | Shown by `comic-dl plugin list`. Default to class name / `"plugin"`. |
| `priority` | no | `int`, default `0`. Set `> 0` to override a built-in for the same domain. |
| `matches_url(url)` | no | Return `True` when this source handles `url`. Defaults to host matching. |
| `matches_series_url(url)` | no | Return `True` when this source handles a *series* `url`. Defaults to host matching; only checked when the entry also advertises the `series` capability. |
| `async scrape(url, client)` | if chapter | Fetch one gallery/chapter. Returns `PostMetadata`. |
| `async scrape_series(url, client)` | if series | Fetch a series listing. Returns `SeriesMetadata`. |

## Result types

`scrape` returns `comic_dl.models.PostMetadata`, but never hand-build it:
filenames and page counts come from `chapter_to_post_metadata`, which derives
them from a `ScrapedChapter`:

```python
from comic_dl.models import (
    ChapterInfo,
    ImageItem,
    ScrapedChapter,
    SourceInfo,
    chapter_to_post_metadata,
)

return chapter_to_post_metadata(
    ScrapedChapter(
        info=ChapterInfo(series_title="Series", chapter_title="Chapter 1"),
        source=SourceInfo(url=url, service="mysite.example", post_id="1"),
        images=[ImageItem(url="https://cdn.mysite.example/1.jpg", page_number=1)],
    )
)
```

## Errors

Raise `comic_dl.errors.ScrapeError` with a `site_error_code`: the live check
maps the code to `broken / url-gone / unavailable`, and a bare `ValueError`
drops it. The common cases have helpers; always hint at a working example URL:

```python
from comic_dl.scrapers.base import (
    listing_page_error,
    no_chapters_error,
    no_images_error,
)

if not images:
    raise no_images_error("https://mysite.example/g/123 shows no reader images")
if not chapters:
    raise no_chapters_error("https://mysite.example/series/slug lists no chapters")
if "/tag/" in url or "/category/" in url:
    raise listing_page_error("My Site", "https://mysite.example/series/{slug}/")
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

from comic_dl.models import (
    ChapterInfo,
    ImageItem,
    PostMetadata,
    ScrapedChapter,
    SourceInfo,
    chapter_to_post_metadata,
)


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
        return chapter_to_post_metadata(
            ScrapedChapter(
                info=ChapterInfo(series_title="Series", chapter_title="Chapter"),
                source=SourceInfo(url=url, service="my-site", post_id="1"),
                images=[
                    ImageItem(
                        url="https://cdn.mysite.example/1.jpg",
                        page_number=1,
                    )
                ],
            )
        )
```

For a series-capable plugin, add the `series` capability, a
`matches_series_url`, and `scrape_series`:

```python
class MySiteSource:  # series variant
    domain = "mysite.example"
    capabilities = {"chapter", "series"}

    def matches_series_url(self, url: str) -> bool:
        return url.startswith("https://mysite.example/series/")

    async def scrape_series(self, url, client) -> SeriesMetadata: ...
```

The plugin manager validates this contract offline, so you can check a fresh
plugin before installing it:

```bash
comic-dl plugin validate my_site/source.py
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

## PR checklist

Reviewers check boxes, not style:

- [ ] `test_url` (+ `test_url_kind`) points at a live chapter or series page
- [ ] URL valid and invalid cases (`matches_url`, `matches_series_url`, foreign domain)
- [ ] chapter and series paths covered with mocked responses
- [ ] error case covered (empty page raises with a `site_error_code`)
- [ ] docs regen run (`uv run python scripts/update-sites-docs.py`)

## Testing

The registry functions in `comic_dl.scrapers.registry` are unit-testable
without a network. Register a fake source, resolve a URL, and assert the state.
