"""Chapter-selection flag parsing and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..utils import canonical_chapter_number


@dataclass(frozen=True)
class ChapterSelection:
    """A resolved chapter-selection choice.

    ``kind`` is one of:
      - ``"all"``: every chapter (the default),
      - ``"indices"``: exactly the chapters named by ``indices``,
      - ``"quit"``: abort the run without writing anything.

    ``indices`` are either canonical *chapter numbers* (``--chapters``; the
    flag is number-based, so ``0`` can name a prologue/promo chapter) or
    1-based *list positions* (the interactive picker). ``by_number`` records
    which reading a selection uses.
    """

    kind: Literal["all", "indices", "quit"]
    indices: frozenset[int] | None = None
    by_number: bool = False


class ChapterSelectionQuit(Exception):
    """Raised when the user cancels chapter selection (q / Esc).

    Caught in ``_run_urls`` to stop the whole run cleanly (exit 0) before
    any files or library rows are written for the current series.
    """


def chapter_numbers_in(chapters: list[dict]) -> set[int]:
    """The canonical numeric chapter numbers present in a scraped series.

    Chapters whose ``episode_no`` is absent or non-numeric (e.g. a
    "Season 1 End" extra) carry no number and can never match a ``--chapters``
    selection.
    """
    return {
        int(n)
        for ch in chapters
        if (n := canonical_chapter_number(ch.get("episode_no") or "")).isdigit()
    }


def chapter_matches_number(ch: dict, number: int) -> bool:
    """True when chapter ``ch`` bears canonical number ``number``.

    Number comparison is done on the normalized label, so a site's ``"10.0"``
    matches ``--chapters 10`` without ever colliding with chapter 1.
    """
    return canonical_chapter_number(ch.get("episode_no") or "") == str(number)


def parse_chapter_selection(spec: str) -> ChapterSelection:
    """Parse a chapter-selection spec into a :class:`ChapterSelection`.

    Grammar (whitespace-tolerant, case-insensitive):
      - empty / ``a`` / ``all`` → every chapter
      - ``q`` / ``quit`` → abort the run
      - comma-separated tokens, each ``N`` or ``N-M`` (inclusive) → the
        chapters bearing those canonical numbers; ``0`` selects a
        prologue/promo chapter numbered 0

    Raises :class:`ValueError` with a specific message on malformed input.
    Whether a named number actually exists is only known after scraping, so
    that check lives in the caller.
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
        if end < start:
            raise ValueError(
                f"Invalid chapter selection {spec!r}: reversed range {token}."
            )
        indices.update(range(start, end + 1))
    return ChapterSelection(kind="indices", indices=frozenset(indices), by_number=True)


def validate_chapter_flag(spec: str) -> None:
    """Validate a ``--chapters`` value's syntax without knowing the total.

    Whether a named number exists is checked per-series after scraping
    (totals are unknown up front); this rejects malformed specs before any
    network work.

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
        if end < start:
            raise ValueError(
                f"Invalid --chapters value {spec!r}: reversed range {token}."
            )
