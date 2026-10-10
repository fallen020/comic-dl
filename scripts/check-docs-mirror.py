#!/usr/bin/env python3
"""Gate that catches docs/website drift CI cannot: broken internal links,
`{#...}` heading IDs leaking into plain Markdown, link targets that treat file
extensions (.mdx) as URLs, table rows orphaned after a paragraph, and MkDocs
`!!!` blocks that GitHub renders as literal text.

Astro maps `src/content/docs/<slug>.mdx` to the route `<slug>/` (the site sets
`trailingSlash: 'always'`), so relative links inside website pages resolve
against route paths, not file paths: `../usage/self-update/` is valid,
`../usage/self-update.mdx` can never be. Route resolution treats the source
page as a directory: `configure/config/` + `../rate-limiting/` goes to
`configure/rate-limiting/`, not the top-level page. docs/ links are ordinary
file paths resolved against the containing directory.

Checks are bounded on purpose: route-level resolution, the `{#...}` scan,
and mirror parity (every website page maps to a docs/ source and back).
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
BASE_TS = ROOT / "website" / "src" / "utils" / "base.ts"

LINK_RE = re.compile(r"\]\(([^)]+)\)")


def _docs_source_map() -> dict[str, str]:
    """Parse the DOCS_SOURCE rename map from base.ts (single source of truth).

    Keys are website slugs (`usage/basic`), values are docs/ paths
    (`usage/download.md`). A slug with no entry maps to `<slug>.md`.
    """
    block = BASE_TS.read_text().split("DOCS_SOURCE", 1)[1].split("};", 1)[0]
    return dict(re.findall(r"['\"]?([A-Za-z0-9/_.-]+)['\"]?\s*:\s*['\"]([^'\"]+)['\"]", block))


def check_mirror_parity() -> list[str]:
    """Flag website pages with no docs/ source and docs/ pages with no mirror."""
    findings: list[str] = []
    mapping = _docs_source_map()
    for path in sorted(WEBSITE.rglob("*.mdx")):
        slug = path.relative_to(WEBSITE).with_suffix("").as_posix()
        expected = DOCS / mapping.get(slug, f"{slug}.md")
        if not expected.is_file():
            want = expected.relative_to(DOCS).as_posix()
            findings.append(f"website: {slug}.mdx: no docs/ source (expected `{want}`)")
    reverse = {v: k for k, v in mapping.items()}
    for path in sorted(DOCS.rglob("*.md")):
        rel = path.relative_to(DOCS).as_posix()
        slug = reverse.get(rel, rel[: -len(".md")])
        if not (WEBSITE / f"{slug}.mdx").is_file():
            findings.append(f"docs: {rel}: no website mirror (expected `{slug}.mdx`)")
    return findings


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


def _code_and_prose_lines(path: Path) -> list[tuple[int, str]]:
    """Return `(line_no, text)` for lines outside fenced code blocks."""
    out: list[tuple[int, str]] = []
    fence = False
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        out.append((line_no, line.strip()))
    return out


def check_orphaned_table_rows(prefix: str, root: Path) -> list[str]:
    """Flag a `|` line that continues no table.

    A blank line closes a GFM table, so rows appended after a paragraph render
    as literal text inside that paragraph and silently vanish from the reader's
    view of the table above.
    """
    findings: list[str] = []
    delimiter = re.compile(r"^\|[\s:\-|]+\|$")
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        lines = _code_and_prose_lines(path)
        for index, (line_no, text) in enumerate(lines):
            if not text.startswith("|"):
                continue
            previous = lines[index - 1][1] if index else ""
            if previous.startswith("|"):
                continue
            following = next((t for _, t in lines[index + 1 :]), "")
            if not delimiter.match(following):  # not a new table header
                findings.append(
                    f"{prefix}{rel}:{line_no}: table row after prose, renders as literal text"
                )
    return findings


def check_admonition_syntax(prefix: str, root: Path) -> list[str]:
    """Flag MkDocs `!!!` blocks, which render literally outside MkDocs."""
    findings: list[str] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        for line_no, text in _code_and_prose_lines(path):
            if re.match(r"^!!!\s+\w", text):
                findings.append(
                    f"{prefix}{rel}:{line_no}: MkDocs `!!!` block"
                    " does not render on GitHub, use `> [!NOTE]`"
                )
    return findings


def main() -> int:
    """Run every mirror check; exit 1 on any finding."""
    check = "--check" in sys.argv
    site_findings = check_md_links("website: ", WEBSITE, routes=True)
    docs_findings = check_md_links("docs: ", DOCS, routes=False)
    id_findings = check_heading_ids("docs: ", DOCS)
    table_findings = check_orphaned_table_rows("docs: ", DOCS)
    admonition_findings = check_admonition_syntax("docs: ", DOCS)
    parity_findings = check_mirror_parity()
    all_findings = (
        site_findings
        + docs_findings
        + id_findings
        + table_findings
        + admonition_findings
        + parity_findings
    )
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
