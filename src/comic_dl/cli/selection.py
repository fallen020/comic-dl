"""Chapter-selection flag parsing and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ChapterSelection:
    """A resolved chapter-selection choice.

    ``kind`` is one of:
      - ``"all"``: every chapter (the default),
      - ``"indices"``: exactly the 1-based ``indices``,
      - ``"quit"``: abort the run without writing anything.
    """

    kind: Literal["all", "indices", "quit"]
    indices: frozenset[int] | None = None


class ChapterSelectionQuit(Exception):
    """Raised when the user cancels chapter selection (q / Esc).

    Caught in ``_run_urls`` to stop the whole run cleanly (exit 0) before
    any files or library rows are written for the current series.
    """


def parse_chapter_selection(spec: str, total: int) -> ChapterSelection:
    """Parse a chapter-selection spec into a :class:`ChapterSelection`.

    Grammar (whitespace-tolerant, case-insensitive):
      - empty / ``a`` / ``all`` → every chapter
      - ``q`` / ``quit`` → abort the run
      - comma-separated tokens, each ``N`` or ``N-M`` (inclusive, 1-based)
        → the listed chapters

    Raises :class:`ValueError` with a specific message on malformed input.
    """
    raw = spec.strip().lower()
    if raw in ("", "a", "all"):
        return ChapterSelection(kind="all")
    if raw in ("q", "quit"):
        return ChapterSelection(kind="quit")
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        raise ValueError(
            f"Invalid chapter selection {spec!r}: empty list."
        )
    indices: set[int] = set()
    for token in tokens:
        if token in ("a", "all", "q", "quit"):
            raise ValueError(
                f"Invalid chapter selection {spec!r}: cannot mix "
                f"'{token}' with a chapter list."
            )
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not m:
            raise ValueError(
                f"Invalid chapter selection {spec!r}: {token!r} is not a "
                f"number or range (e.g. 1-3)."
            )
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) is not None else start
        if start < 1:
            raise ValueError(
                f"Invalid chapter selection {spec!r}: chapter indices are "
                f"1-based (got {token})."
            )
        if end < start:
            raise ValueError(
                f"Invalid chapter selection {spec!r}: reversed range {token}."
            )
        if end > total:
            raise ValueError(
                f"Invalid chapter selection {spec!r}: chapter {end} is out "
                f"of range (series has {total} chapters)."
            )
        indices.update(range(start, end + 1))
    return ChapterSelection(kind="indices", indices=frozenset(indices))


def validate_chapter_flag(spec: str) -> None:
    """Validate a ``--chapters`` value's syntax without knowing the total.

    Per-series bounds are checked after scraping (totals are unknown up
    front); this rejects malformed specs before any network work.

    Unlike :func:`parse_chapter_selection`, ``q``/``quit`` are rejected
    here: ``--chapters`` is a non-interactive flag, so a "cancel" token
    would only ever be a silent no-op mistake. Interactive cancels go
    through the checkbox selector instead.
    """
    raw = spec.strip().lower()
    if raw in ("", "a", "all"):
        return
    if raw in ("q", "quit"):
        raise ValueError(
            f"Invalid --chapters value {spec!r}: 'q'/'quit' only makes "
            "sense in the interactive chapter selector."
        )
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        raise ValueError(f"Invalid --chapters value {spec!r}: empty list.")
    for token in tokens:
        if token in ("a", "all", "q", "quit"):
            raise ValueError(
                f"Invalid --chapters value {spec!r}: cannot mix "
                f"'{token}' with a chapter list."
            )
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not m:
            raise ValueError(
                f"Invalid --chapters value {spec!r}: {token!r} is not a "
                f"number or range (e.g. 1-3)."
            )
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) is not None else start
        if start < 1:
            raise ValueError(
                f"Invalid --chapters value {spec!r}: chapter indices are "
                f"1-based (got {token})."
            )
        if end < start:
            raise ValueError(
                f"Invalid --chapters value {spec!r}: reversed range {token}."
            )
