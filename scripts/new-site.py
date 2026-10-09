#!/usr/bin/env python3
"""Scaffold a built-in site scraper plus its test stub.

Writes the site module and its test, then tells you the next steps.
See --help for the full guide.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from rich.console import Console
except ImportError:
    Console = None  # type: ignore[assignment,misc]

REPO = Path(__file__).resolve().parent.parent
SITE_DIR = REPO / "src" / "comic_dl" / "scrapers" / "sites"
TEST_DIR = REPO / "tests" / "scrapers" / "sites"

THEMES = ("madara", "vali", "vcomics", "bare")

_THEMED = {
    "madara": {
        "import": "import re\n\nfrom ..madara import MadaraSeriesSiteScraper",
        "base": "MadaraSeriesSiteScraper",
        "chapter_path": "/series/demo-slug/chapter/1",
        "series_path": "/series/demo-slug/",
        "extra": """
_SERIES_PATH_RE = re.compile(r"^https?://(?:www\\.)?{escaped}/series/[^/]+/?$")
_CHAPTER_PATH_RE = re.compile(r"^https?://(?:www\\.)?{escaped}/series/[^/]+/chapter/[^/]+/?$")
""",
        "attrs": """
    series_url_re = _SERIES_PATH_RE
    chapter_url_re = _CHAPTER_PATH_RE
    chapter_list_selector = ""
    reader_containers = ()
    # TODO: fill the grammars/selectors above from the site's real markup.
""",
    },
    "vali": {
        "import": "from ._valiscans import ValiScansScraper",
        "base": "ValiScansScraper",
        "extra": "",
        "attrs": "",
        "chapter_path": "/series/comic/demo-slug/chapter/1",
        "series_path": "/series/comic/demo-slug/",
    },
    "vcomics": {
        "import": "from ._vcomics import VComicsScraper",
        "base": "VComicsScraper",
        "extra": "",
        "attrs": """
    api_base = "https://api.{domain}"
    site_label = "{display}"
    # TODO: point api_base at the site's real API host.
""",
        "chapter_path": "/series/demo-slug/chapter-1",
        "series_path": "/series/demo-slug/",
    },
}

_THEMED_SITE = '''"""{display} ({domain}) chapter and series scraper."""

from __future__ import annotations

{theme_import}
from ..registry import register_scraper

DOMAIN = "{domain}"
{extra}

@register_scraper(domain=DOMAIN, capabilities={{"chapter", "series"}})
class {klass}({base}):
    """{display} chapter and series scraper."""

    domain = DOMAIN
    name = "{slug}"
    site_id = "{slug}"
    display_name = "{display}"
    chapter_url_pattern = "/series/{{slug}}/chapter/{{n}}"
    series_url_pattern = "/series/{{slug}}/"
    version = "0.1.0"
    test_url = "https://{domain}{chapter_path}"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"
{attrs}'''

_SITE = '''"""{display} ({domain}) chapter scraper."""

from __future__ import annotations

import re

from curl_cffi.requests import AsyncSession

from ...models import (
    ChapterInfo,
    ImageItem,
    PostMetadata,
    ScrapedChapter,
    SourceInfo,
    chapter_to_post_metadata,
)
from ..base import BaseScraper, no_images_error
from ..registry import register_scraper

DOMAIN = "{domain}"
BASE = "https://{domain}"

_SERIES_PATH_RE = re.compile(r"^https?://(?:www\\.)?{escaped}/series/[^/]+/?$")
_CHAPTER_PATH_RE = re.compile(r"^https?://(?:www\\.)?{escaped}/series/[^/]+/chapter/\\d+/?$")
# TODO: adjust the grammars above to the site's real URL shape.


def is_series_url(url: str) -> bool:
    """True when ``url`` points at a series page for this source."""
    return bool(_SERIES_PATH_RE.match(url))


def is_chapter_url(url: str) -> bool:
    """True when ``url`` points at a chapter page for this source."""
    return bool(_CHAPTER_PATH_RE.match(url))


@register_scraper(domain=DOMAIN, capabilities={{"chapter"}})
class {klass}(BaseScraper):
    """{display} chapter scraper."""

    domain = DOMAIN
    name = "{slug}"
    site_id = "{slug}"
    display_name = "{display}"
    chapter_url_pattern = "/series/{{slug}}/chapter/{{n}}"
    series_url_pattern = ""
    series_url_re = _SERIES_PATH_RE
    chapter_url_re = _CHAPTER_PATH_RE
    version = "0.1.0"
    test_url = "https://{domain}{chapter_path}"
    test_url_kind = "chapter"
    minimum_core_version = "0.0.2"

    def matches_url(self, url: str) -> bool:
        return bool(self.chapter_url_re.match(url))

    async def scrape(self, url: str, client: AsyncSession) -> PostMetadata:
        soup, _ = await BaseScraper.fetch_html_raw(url, client)
        images = [
            ImageItem(url=src, page_number=n)
            for n, img in enumerate(soup.select("div.reader img"), start=1)
            if (src := img.get("src"))
        ]
        # TODO: adjust the selector above to the site's reader markup.
        if not images:
            raise no_images_error(f"{{url}} shows no reader images")
        return chapter_to_post_metadata(
            ScrapedChapter(
                info=ChapterInfo(series_title="{display}", chapter_title="Chapter"),
                source=SourceInfo(url=url, service="{slug}", post_id="1"),
                images=images,
            )
        )
'''

_TEST = """from __future__ import annotations

import pytest

from comic_dl.errors import ScrapeError
from comic_dl.scrapers.sites.{mod} import {klass}
from tests.helpers import MockResponse as _MockResponse
from tests.helpers import MockSession as _MockSession

CHAPTER_URL = "https://{domain}{chapter_path}"
{series_const}

class TestUrls:
    def test_chapter_url_routes(self):
        assert {klass}().matches_url(CHAPTER_URL)

{series_tests}    def test_foreign_url_rejected(self):
        assert not {klass}().matches_url("https://other.example/x")


class TestScrape:
    CHAPTER_HTML = (
        "<html><body><div class=\\"reader\\">"
        "<img src=\\"https://{domain}/c/1/001.jpg\\"/>"
        "<img src=\\"https://{domain}/c/1/002.jpg\\"/>"
        "</div></body></html>"
    )

    {skip}@pytest.mark.asyncio
    async def test_scrape_chapter(self):
        session = _MockSession(lambda url: _MockResponse(self.CHAPTER_HTML))
        meta = await {klass}().scrape(CHAPTER_URL, session)
        assert len(meta.images) == 2
        assert meta.total_pages == 2

    {skip}@pytest.mark.asyncio
    async def test_scrape_empty_raises(self):
        session = _MockSession(lambda url: _MockResponse("<html><body></body></html>"))
        with pytest.raises(ScrapeError):
            await {klass}().scrape(CHAPTER_URL, session)
"""

_SERIES_CONST = 'SERIES_URL = "https://{domain}{series_path}"\n'

_SERIES_TESTS = """    def test_series_url_routes(self):
        scraper = {klass}()
        assert scraper.matches_series_url(SERIES_URL)
        assert scraper.matches_url(SERIES_URL)

    def test_chapter_url_is_not_series(self):
        assert not {klass}().matches_series_url(CHAPTER_URL)

"""

_SKIP = """@pytest.mark.skip("TODO: paste real chapter markup into CHAPTER_HTML")
    """


def _console():
    return Console() if Console is not None else None


def _say(c, style: str, text: str) -> None:
    if c is not None:
        c.print(text, style=style)
    else:
        print(text)


def _help() -> int:
    c = _console()
    if c is None:
        print(__doc__)
        return 0
    c.print("[bold]new-site.py[/] — scaffold a built-in site scraper + test.")
    c.print()
    c.print("[bold]Usage:[/]")
    c.print("  [green]new-site.py NAME DOMAIN [--theme THEME][/]  write module + test")
    c.print("  [green]new-site.py --check[/]                      verify Python and trees")
    c.print()
    c.print("[bold]Flags:[/]")
    c.print("  [green]--theme madara|vali|vcomics|bare[/] (default: bare)")
    c.print("  [green]--json[/] machine output")
    c.print("  [green]--help[/] this guide")
    c.print()
    c.print("[bold]Themes:[/]")
    c.print("  [green]bare[/]     chapter-only scraper, grammar + reader selector TODOs")
    c.print("  [green]madara[/]   Madara-theme site, grammar + selector TODOs")
    c.print("  [green]vali[/]     ValiScans-platform site, domain metadata only")
    c.print("  [green]vcomics[/]  VComics-platform site, needs a real api_base")
    c.print()
    c.print("[dim]e.g. new-site.py mangafire mangafire.to --theme madara[/]")
    c.print("[dim]Docs and manifest regenerate from the registry; existing targets abort.[/]")
    return 0


def _check(as_json: bool) -> int:
    info = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_ok": sys.version_info >= (3, 11),
        "site_dir": str(SITE_DIR),
        "site_dir_ok": SITE_DIR.is_dir(),
        "test_dir": str(TEST_DIR),
        "test_dir_ok": TEST_DIR.is_dir(),
    }
    try:
        import pytest

        info["pytest"] = pytest.__version__
    except ImportError:
        info["pytest"] = None
    ok = bool(info["python_ok"] and info["site_dir_ok"] and info["test_dir_ok"] and info["pytest"])
    if as_json:
        print(json.dumps({"ok": ok, **info}))
        return 0 if ok else 1
    c = _console()
    mark = "[green]ok[/]" if info["python_ok"] else "[red]need >= 3.11[/]"
    _say(c, "", f"python {info['python']} {mark}" if c else f"python {info['python']}")
    for label, key in (("site tree", "site_dir_ok"), ("test tree", "test_dir_ok")):
        status = "present" if info[key] else "MISSING"
        _say(c, "", f"{label} {info[key[:-3]]}: {status}")
    _say(c, "", f"pytest {info['pytest'] or 'MISSING'}")
    return 0 if ok else 1


def _usage_error(c, as_json: bool, message: str) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}))
    else:
        _say(c, "red", f"error: {message}")
        _say(c, "dim", "usage: new-site.py NAME DOMAIN [--theme THEME] [--json]")
    return 2


def main(argv: list[str]) -> int:
    """Write the site module and test stub; refuse to overwrite either."""
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    c = _console()
    if not args or args[:1] in (["--help"], ["-h"], ["help"]):
        if len(args) <= 1:
            return _help()
        return _usage_error(c, as_json, "too many arguments for --help")
    if args[:1] == ["--check"] and len(args) == 1:
        return _check(as_json)
    theme = "bare"
    rest: list[str] = []
    it = iter(args)
    for arg in it:
        if arg == "--theme":
            try:
                theme = next(it)
            except StopIteration:
                return _usage_error(c, as_json, "--theme needs a value")
        elif arg.startswith("--"):
            return _usage_error(c, as_json, f"unknown flag {arg}")
        else:
            rest.append(arg)
    if theme not in THEMES:
        return _usage_error(
            c, as_json, f"unknown theme {theme!r}; expected one of {', '.join(THEMES)}"
        )
    if len(rest) != 2:
        return _usage_error(c, as_json, "expected NAME and DOMAIN")
    name, domain = rest

    slug = name.strip().lower()
    if not slug or not slug.replace("-", "").replace("_", "").isalnum():
        return _usage_error(c, as_json, f"bad name {name!r}; expected a slug like 'mangafire'")
    if not (domain and "." in domain and "/" not in domain):
        return _usage_error(c, as_json, f"bad domain {domain!r}")
    mod, klass = (
        slug.replace("-", "_"),
        "".join(p.title() for p in slug.replace("-", "_").split("_")) + "Scraper",
    )
    site_path, test_path = SITE_DIR / f"{mod}.py", TEST_DIR / f"test_{mod}.py"
    if site_path.exists() or test_path.exists():
        present = site_path if site_path.exists() else test_path
        message = f"refusing to overwrite {present}"
        if as_json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            _say(c, "red", message)
        return 1
    fields = {
        "slug": slug,
        "mod": mod,
        "klass": klass,
        "domain": domain,
        "escaped": domain.replace(".", "\\."),
        "display": slug.replace("-", " ").title(),
        "chapter_path": "/series/demo-slug/chapter/1",
        "series_path": "/series/demo-slug/",
    }
    if theme == "bare":
        site_path.write_text(_SITE.format(**fields))
        test_path.write_text(_TEST.format(series_const="", series_tests="", skip="", **fields))
    else:
        params = _THEMED[theme]
        fields.update(chapter_path=params["chapter_path"], series_path=params["series_path"])
        site_path.write_text(
            _THEMED_SITE.format(
                theme_import=params["import"],
                base=params["base"],
                extra=params["extra"].format(**fields),
                attrs=params["attrs"].format(**fields),
                **fields,
            )
        )
        test_path.write_text(
            _TEST.format(
                series_const=_SERIES_CONST.format(**fields),
                series_tests=_SERIES_TESTS.format(**fields),
                skip=_SKIP,
                **fields,
            )
        )
    next_steps = [
        "fill the TODOs in the site module",
        f"uv run pytest {test_path} -q",
        "uv run python scripts/update-sites-docs.py",
    ]
    if as_json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "theme": theme,
                    "site": str(site_path),
                    "test": str(test_path),
                    "next": next_steps,
                }
            )
        )
        return 0
    _say(c, "green", f"scaffolded {theme} site at {site_path}")
    _say(c, "green", f"scaffolded test at {test_path}")
    _say(c, "dim", "next:")
    for step in next_steps:
        _say(c, "dim", f"  {step}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
