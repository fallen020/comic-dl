#!/usr/bin/env python3
"""Gate that catches docs/website drift CI cannot: broken internal links,
`{#...}` heading IDs leaking into plain Markdown, and link targets that treat
file extensions (.mdx) as URLs.

Astro maps `src/content/docs/<slug>.mdx` to the route `<slug>/` (the site sets
`trailingSlash: 'always'`), so relative links inside website pages resolve
against route paths, not file paths: `../usage/self-update/` is valid,
`../usage/self-update.mdx` can never be. Route resolution treats the source
page as a directory: `configure/config/` + `../rate-limiting/` goes to
`configure/rate-limiting/`, not the top-level page. docs/ links are ordinary
file paths resolved against the containing directory.

Checks are bounded on purpose: route-level resolution and the `{#...}` scan.
Content equality between the trees is NOT enforced -- website pages are a
hand-maintained fork (frontmatter, unwrapped prose, renamed headings).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
WEBSITE = ROOT / "website" / "src" / "content" / "docs"

LINK_RE = re.compile(r"\]\(([^)]+)\)")


def check_md_links(prefix: str, root: Path, *, routes: bool) -> list[str]:
    """Return `file:line: link` strings for internal links that miss their target."""
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".md", ".mdx") or not path.is_file():
            continue
        rel = path.relative_to(root)
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            for target in LINK_RE.findall(line):
                if target.startswith(("#", "mailto:", "http://", "https://")):
                    continue
                frag = target.split("#", 1)[0] or None
                if frag is None:
                    continue
                parsed = urlparse(frag)
                if parsed.scheme or parsed.netloc:
                    continue
                if routes:
                    base = (Path(rel.parent) / rel.stem).as_posix()
                    target_path = (Path(base) / parsed.path).as_posix()
                    if target_path.rstrip("/").endswith((".mdx", ".md")):
                        findings.append(f"{prefix}{rel}:{line_no}: link to `{frag}`")
                        continue
                    parts = [p for p in target_path.split("/") if p not in ("", ".")]
                    resolved = []
                    for part in parts:
                        if part == "..":
                            if resolved:
                                resolved.pop()
                            else:
                                break
                        else:
                            resolved.append(part)
                    else:
                        slug = "/".join(resolved).removesuffix("/")
                        if (
                            not (root / f"{slug}.mdx").exists()
                            and not (root / f"{slug}.md").exists()
                        ):
                            findings.append(f"{prefix}{rel}:{line_no}: link to `{frag}`")
                else:
                    target_path = (path.parent / frag).resolve()
                    try:
                        target_path.relative_to(root)
                    except ValueError:
                        continue
                    if not target_path.is_file():
                        findings.append(f"{prefix}{rel}:{line_no}: broken link `{frag}`")
    return findings


def check_heading_ids(prefix: str, root: Path) -> list[str]:
    """Flag Astro `{#slug}` heading markers, which plain Markdown renders literally."""
    findings: list[str] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\{#[\w-]+\}\s*$", line):
                findings.append(
                    f"{prefix}{rel}:{line_no}: Astro heading ID `{{#...}}`"
                    " does not render on GitHub"
                )
    return findings


def main() -> int:
    """Run every mirror check; exit 1 on any finding."""
    check = "--check" in sys.argv
    site_findings = check_md_links("website: ", WEBSITE, routes=True)
    docs_findings = check_md_links("docs: ", DOCS, routes=False)
    id_findings = check_heading_ids("docs: ", DOCS)
    all_findings = site_findings + docs_findings + id_findings
    for finding in all_findings:
        print(finding)
    if all_findings:
        if check:
            print(f"Docs mirror check failed: {len(all_findings)} finding(s).")
            return 1
        return 1
    print("Docs mirror check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
