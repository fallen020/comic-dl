from __future__ import annotations

import json
from pathlib import Path

from comic_dl.manifest import (
    MANIFEST_NAME,
    STATE_DONE,
    STATE_FAILED,
    STATE_PENDING,
    ChapterManifest,
    PageState,
)
from comic_dl.models import ImageItem


def _items():
    return [
        ImageItem(url="http://x.com/1", page_number=1, filename="p1.jpg"),
        ImageItem(url="http://x.com/2", page_number=2, filename="p2.jpg"),
        ImageItem(url="http://x.com/3", page_number=3, filename="p3.jpg"),
    ]


def _load(path: Path) -> ChapterManifest:
    manifest = ChapterManifest.load(path)
    assert manifest is not None
    return manifest


def _page(m: ChapterManifest, name: str) -> PageState:
    state = m.get(name)
    assert state is not None
    return state


class TestChapterManifestRoundTrip:
    def test_seed_marks_all_pending(self, tmp_path):
        m = ChapterManifest(tmp_path / MANIFEST_NAME)
        m.seed(_items())
        m2 = _load(tmp_path / MANIFEST_NAME)
        assert _page(m2, "p1.jpg").status == STATE_PENDING
        assert _page(m2, "p3.jpg").status == STATE_PENDING

    def test_record_persists_statuses(self, tmp_path):
        m = ChapterManifest(tmp_path / MANIFEST_NAME)
        m.seed(_items())
        m.record("p1.jpg", STATE_DONE, size=123)
        m.record("p2.jpg", STATE_FAILED, "HTTP 530")
        m2 = _load(tmp_path / MANIFEST_NAME)
        assert _page(m2, "p1.jpg").status == STATE_DONE
        assert _page(m2, "p1.jpg").size == 123
        assert _page(m2, "p2.jpg").status == STATE_FAILED
        assert _page(m2, "p2.jpg").error == "HTTP 530"

    def test_settle_maps_failed_and_done(self, tmp_path):
        m = ChapterManifest(tmp_path / MANIFEST_NAME)
        m.seed(_items())
        (tmp_path / "p1.jpg").write_bytes(b"\xff\xd8\xff")
        m.settle(
            _items(),
            failed={"p2.jpg"},
            failure_labels={"p2.jpg": "timed out"},
            dest_dir=tmp_path,
        )
        m2 = _load(tmp_path / MANIFEST_NAME)
        assert _page(m2, "p1.jpg").status == STATE_DONE
        assert _page(m2, "p1.jpg").size == 3
        assert _page(m2, "p2.jpg").status == STATE_FAILED
        assert _page(m2, "p2.jpg").error == "timed out"
        # Not in the failed set => done (a missing file would have been added
        # to ``failed`` by the verify step before settle).
        assert _page(m2, "p3.jpg").status == STATE_DONE
        assert m2.failed() == ["p2.jpg"]

    def test_missing_file_loads_none(self, tmp_path):
        assert ChapterManifest.load(tmp_path / MANIFEST_NAME) is None

    def test_malformed_loads_none(self, tmp_path):
        p = tmp_path / MANIFEST_NAME
        p.write_text("{ not json")
        assert ChapterManifest.load(p) is None

    def test_wrong_schema_loads_none(self, tmp_path):
        p = tmp_path / MANIFEST_NAME
        p.write_text(json.dumps({"schema_version": 99, "pages": {}}))
        assert ChapterManifest.load(p) is None

    def test_chapter_id_round_trips(self, tmp_path):
        m = ChapterManifest(tmp_path / MANIFEST_NAME, chapter_id="https://x.com/ch")
        m.seed(_items())
        assert _load(tmp_path / MANIFEST_NAME).chapter_id == "https://x.com/ch"
