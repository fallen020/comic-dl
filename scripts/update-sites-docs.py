#!/usr/bin/env python3
"""Regenerate the supported-sites tables in docs and the website.

The Sites and Per-site features tables are derived from the live registry:
every built-in source appears automatically with its domain, display name,
URL-pattern strings, and chapter/series capabilities. The per-site prose
notes, front matter, and every other section are left untouched.

Run without arguments to rewrite the two tables in place; ``--check`` instead
verifies they are current (used by ``scripts/docs.sh`` so CI fails on drift).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Make the package importable when run from a bare checkout (CI, pre-commit).
# Requires the import just below to carry the deliberate E402 exception.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from comic_dl.scrapers import list_sources  # noqa: E402

#: Domains whose chapter title comes from tag metadata rather than the page.
TITLES_FROM_TAGS = {"e-hentai.org", "webtoons.com"}

DOCS = _REPO / "docs" / "reference" / "supported-sites.md"
WEBSITE = _REPO / "website" / "src" / "content" / "docs" / "reference" / "supported-sites.mdx"

# Prose that states the scraper count outside the generated tables. Their tables
# are untouched; only the number is kept in sync.
COUNTED = (_REPO / "README.md",)

# Any "N built-in scrapers/sources" phrase in prose, so a newly added site
# cannot leave a stale count behind in a file the table generator never touches.
_COUNT_RE = re.compile(r"\b\d+(\s+built-in\s+(?:sources|scrapers))\b")


def _entries() -> list:
    """``(domain, entry)`` pairs sorted by domain, every built-in site."""
    by_domain = {e.domain: e for e in list_sources() if e.builtin}
    missing = sorted(d for d, e in by_domain.items() if not e.display_name.strip())
    if missing:
        raise SystemExit("Built-in sources declare no display_name for: " + ", ".join(missing))
    return [(domain, by_domain[domain]) for domain in sorted(by_domain)]


def _yes(flag: bool) -> str:
    return "Yes" if flag else "—"


def _badge(flag: bool) -> str:
    return '<Badge status="supported" />' if flag else "—"


def _sites_table(entries, cell):
    """One row per site, not per URL pattern.

    A visitor's question is "is my site here?", so the domain is the key they
    scan for. Splitting chapter and series into separate rows repeated the
    name and domain for every dual-capability site, doubling the list to scan
    and making a supported site look like two sites.
    """
    rows = [
        "| Site | Domain | URL patterns | Chapters | Series |",
        "| :--- | :----- | :----------- | :------- | :----- |",
    ]
    for domain, entry in entries:
        display, chapter, series = (
            entry.display_name,
            entry.chapter_url_pattern,
            entry.series_url_pattern,
        )
        # Comma-joined, not <br>: this cell is emitted into a plain-Markdown
        # file too, where a line break would need JSX markup that MDX only
        # accepts self-closed.
        patterns = ", ".join(f"`{p}`" for p in (series, chapter) if p)
        rows.append(
            f"| **{display}** | `{domain}` | {patterns} "
            f"| {cell(entry.has_chapter)} | {cell(entry.has_series)} |"
        )
    return rows


def _features_table(entries, cell):
    header = "| Feature | " + " | ".join(e.display_name for _, e in entries) + " |"
    sep_cols = [":------"] + [":-------"] * len(entries)
    separator = "| " + " | ".join(sep_cols) + " |"
    rows = [header, separator]

    def row(label, values):
        return "| " + label + " | " + " | ".join(values) + " |"

    rows.append(row("Individual posts/chapters", [_yes(e.has_chapter) for d, e in entries]))
    rows.append(row("Series chapter listing", [_yes(e.has_series) for d, e in entries]))
    rows.append(row("Image dedup (SHA-256)", ["Yes"] * len(entries)))
    rows.append(row("Download resume", ["Yes"] * len(entries)))
    rows.append(row("Concurrent downloads", ["Yes"] * len(entries)))
    rows.append(row("Chapter title from tags", [cell(d in TITLES_FROM_TAGS) for d, _ in entries]))
    return rows


def _splice(text: str, start: str, end: str, body: list[str]) -> str:
    """Replace the section between heading ``start`` and heading ``end``."""
    lines = text.splitlines()
    si = lines.index(start)
    ei = lines.index(end)
    return "\n".join([*lines[: si + 1], "", *body, "", *lines[ei:]]) + "\n"


def _wrap_filter(rows: list[str]) -> list[str]:
    """Wrap a rendered table in the website's live filter component.

    Only the site list is filtered. The per-site capability matrix is a
    29-column grid of Yes/— that reads fine as-is and a filter would only hide
    capability facts the reader came to compare.
    """
    return ["<Filter>", "", *rows, "", "</Filter>"]


def _set_count(text: str, count: int) -> str:
    """Rewrite every ``N built-in scrapers`` phrase to the registry count."""
    return _COUNT_RE.sub(lambda m: f"{count}{m.group(1)}", text)


def generate() -> dict:
    """Rendered ``{path: text}`` pairs for every file carrying a site count."""
    entries = _entries()
    count = len(entries)
    md = DOCS.read_text()
    mdx = WEBSITE.read_text()
    md = _splice(md, "## Sites", "## Per-site features", _sites_table(entries, _yes))
    md = _splice(md, "## Per-site features", "## Site notes", _features_table(entries, _yes))
    sites = _wrap_filter(_sites_table(entries, _badge))
    mdx = _splice(mdx, "## Sites", "## Per-site features", sites)
    mdx = _splice(mdx, "## Per-site features", "## Site notes", _features_table(entries, _badge))
    rendered = {
        str(DOCS): _set_count(md, count),
        str(WEBSITE): _set_count(mdx, count),
    }
    for path in COUNTED:
        rendered[str(path)] = _set_count(path.read_text(), count)
    return rendered


def main(argv: list[str]) -> int:
    """Write both docs files, or verify them when ``--check`` is given."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the docs are out of date instead of writing",
    )
    args = parser.parse_args(argv)

    rendered = generate()
    out_of_date = [path for path, text in rendered.items() if Path(path).read_text() != text]
    if args.check:
        if out_of_date:
            print(
                "supported-sites docs are out of date; run "
                "`uv run python scripts/update-sites-docs.py`",
                file=sys.stderr,
            )
            return 1
        return 0
    if out_of_date:
        for path in out_of_date:
            Path(path).write_text(rendered[path])
        print(f"Updated: {', '.join(out_of_date)}")
    else:
        print("supported-sites docs are up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
