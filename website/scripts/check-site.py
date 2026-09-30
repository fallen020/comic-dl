"""Post-build integrity gate for the docs site. Fails the workflow when
published artifacts, SEO/AI surfaces, or internal links regress.

Usage: python3 scripts/check-site.py (run from website/ after build:search)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "dist"

REQUIRED_FILES = [
    "robots.txt",
    "sitemap-index.xml",
    "sitemap-0.xml",
    "llms.txt",
    "og-image.png",
    "NOTICES",
    "404.html",
    "index.html",
]

HREF_RE = re.compile(r'href="(/comic-dl/[^"#]+)(#[^"]*)?"')
CANONICAL_RE = re.compile(r'<link rel="canonical" href="([^"]+)"')
REQUIRED_HEAD_RES = [
    re.compile(r'<link rel="canonical"'),
    re.compile(r'<meta property="og:url"'),
]


def resolve(url: str) -> Path | None:
    """Map a site-relative `/comic-dl/...` URL to a file under `dist/`.

    Directories are resolved to their `index.html`. Returns ``None`` when the
    target does not exist as a file.
    """
    path = url[len("/comic-dl/") :].lstrip("/")
    target = DIST / path
    if target.is_dir():
        target = target / "index.html"
    return target if target.is_file() else None


def check_llms_index(errors: list[str]) -> None:
    """Every docs page must be listed in the index at its canonical URL.

    A reader that trusts the index follows it instead of crawling, so a page
    missing from it is invisible. Comparing against each page's own canonical
    tag keeps the host in one place.
    """
    index = (DIST / "llms.txt").read_text()
    if not index.startswith("# comic-dl\n"):
        errors.append("llms.txt: does not start with the `# comic-dl` heading")
    for page in sorted(DIST.glob("docs/**/index.html")):
        match = CANONICAL_RE.search(page.read_text())
        if match is None:
            errors.append(f"llms.txt: no canonical URL on {page.relative_to(DIST)}")
        elif match.group(1) not in index:
            errors.append(f"llms.txt: does not list {match.group(1)}")


def main() -> int:
    """Run the post-build integrity checks.

    Returns 0 when all required files exist, all HTML pages have canonical/og
    tags, the llms.txt index covers every docs page, and all internal links
    resolve. Returns 1 on any failure.
    """
    errors: list[str] = []
    for name in REQUIRED_FILES:
        if not (DIST / name).is_file():
            errors.append(f"missing published file: dist/{name}")

    pages = [p for p in DIST.rglob("*.html") if "pagefind" not in p.parts]
    if not pages:
        errors.append("no HTML pages built")
    for page in pages:
        html = page.read_text()
        if page.name != "404.html":
            for pattern in REQUIRED_HEAD_RES:
                if not pattern.search(html):
                    errors.append(f"{page.relative_to(DIST)}: missing {pattern.pattern}")
        for match in HREF_RE.finditer(html):
            url, frag = match.group(1), match.group(2) or ""
            target = resolve(url)
            if target is None:
                errors.append(f"{page.relative_to(DIST)}: broken link {url}{frag}")
                continue
            if frag and frag != "#main-content":
                anchor = frag[1:]
                text = target.read_text()
                if f'id="{anchor}"' not in text and f"id='{anchor}'" not in text:
                    errors.append(f"{page.relative_to(DIST)}: broken anchor {url}{frag}")

    if (DIST / "llms.txt").is_file():
        check_llms_index(errors)

    for error in sorted(set(errors)):
        print(f"check-site: {error}")
    if errors:
        print(f"check-site: {len(set(errors))} problem(s)")
        return 1
    print(f"check-site: OK ({len(pages)} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
