"""Chapter download state manifest: per-page status on disk, resumable across runs.

Lives inside a chapter's temp dir as ``.comic-dl-state.json`` and is rewritten
atomically (tmp + :func:`os.replace`) as pages reach a terminal state, so a
killed process never leaves a torn file. On a partial download the CLI copies
it beside the CBZ as ``{name}.state.json``; a rerun reads it for the exact
failed set and per-page reasons without scanning the archive. Page state maps
DOWNLOAD outcome, not archive membership — a page the archiver later dedupes as
a duplicate still counts as done.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ImageItem

SCHEMA_VERSION = 1

STATE_PENDING = "pending"
STATE_DONE = "done"
STATE_FAILED = "failed"

MANIFEST_NAME = ".comic-dl-state.json"


@dataclass(slots=True)
class PageState:
    """One page's terminal (or pending) download state."""

    status: str
    error: str = ""
    size: int = 0


def _parse_entry(raw: dict[str, Any]) -> tuple[PageState, int] | None:
    status = raw.get("status")
    if status not in (STATE_PENDING, STATE_DONE, STATE_FAILED):
        return None
    try:
        page = int(raw.get("page", 0))
    except (TypeError, ValueError):
        page = 0
    state = PageState(
        status=status,
        error=str(raw.get("error", "") or ""),
        size=int(raw.get("size", 0) or 0),
    )
    return state, page


class ChapterManifest:
    """Per-chapter page tracking, keyed by the stable page filename."""

    def __init__(self, path: Path, chapter_id: str = "") -> None:
        self._path = path
        self._chapter_id = chapter_id
        self._pages: dict[str, dict[str, Any]] = {}

    @classmethod
    def load(cls, path: Path) -> ChapterManifest | None:
        """Read ``path``; ``None`` when missing, malformed, or from another schema."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            return None
        raw_pages = data.get("pages")
        if not isinstance(raw_pages, dict):
            return None
        manifest = cls(path, chapter_id=str(data.get("chapter_id") or ""))
        for name, entry in raw_pages.items():
            parsed = _parse_entry(entry) if isinstance(entry, dict) else None
            if parsed is None:
                continue
            state, page = parsed
            manifest._pages[str(name)] = {
                "page": page,
                "status": state.status,
                "error": state.error,
                "size": state.size,
            }
        return manifest

    @property
    def path(self) -> Path:
        return self._path

    @property
    def chapter_id(self) -> str:
        return self._chapter_id

    def get(self, filename: str) -> PageState | None:
        parsed = _parse_entry(self._pages.get(filename, {}))
        if parsed is None:
            return None
        state, _ = parsed
        return state

    def seed(self, images: list[ImageItem]) -> None:
        """Mark every page ``pending`` (spec pipeline phase 1), then save once."""
        for img in images:
            self._pages.setdefault(
                img.filename,
                {"page": img.page_number, "status": STATE_PENDING, "error": "", "size": 0},
            )
        self.save()

    def record(
        self, filename: str, status: str, error: str = "", size: int = 0
    ) -> None:
        """Set one page's state and persist atomically."""
        entry = self._pages.setdefault(filename, {"status": STATE_PENDING, "error": "", "size": 0})
        entry["status"] = status
        entry["error"] = error
        entry["size"] = size
        self.save()

    def settle(
        self,
        images: list[ImageItem],
        failed: set[str],
        failure_labels: dict[str, str],
        dest_dir: Path,
    ) -> None:
        """Settle every known page to its terminal state in one atomic write."""
        for img in images:
            if img.filename in failed:
                self._update(
                    img.filename,
                    STATE_FAILED,
                    failure_labels.get(img.filename, ""),
                    0,
                )
                continue
            size = 0
            dest = dest_dir / img.filename
            try:
                if dest.is_file():
                    size = dest.stat().st_size
            except OSError:
                size = 0
            self._update(img.filename, STATE_DONE, "", size)
        self.save()

    def failed(self) -> list[str]:
        """Filenames currently recorded as failed, in seed order."""
        return [name for name, e in self._pages.items() if e.get("status") == STATE_FAILED]

    def paged(self) -> list[dict[str, Any]]:
        """Entries with the downloader's canonical page numbers attached."""
        return [
            {"name": name, "page": e.get("page", 0), **dict(e)}
            for name, e in self._pages.items()
        ]

    def _update(self, filename: str, status: str, error: str, size: int) -> None:
        entry = self._pages.setdefault(
            filename,
            {"page": 0, "status": STATE_PENDING, "error": "", "size": 0},
        )
        entry = self._pages[filename]
        entry["status"] = status
        entry["error"] = error
        entry["size"] = size

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "chapter_id": self._chapter_id,
            "total_pages": len(self._pages),
            "pages": self._pages,
        }
        fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".state.", suffix=".tmp"
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp_path, self._path)
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
