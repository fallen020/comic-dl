from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from comic_dl.cli.library import (
    TRASH_TTL_DAYS,
    _purge_trash,
    run_library_command,
)
from comic_dl.library import Library


def _seed(tmp_path: Path, root: Path) -> Library:
    lib = Library(root / ".comic-dl" / "library.db")
    lib.open()
    lib.upsert_series(
        "e-hentai.org:aaa", title="Alpha", source="https://x/",
        source_site="e-hentai.org", relative_path="Alpha",
    )
    lib.upsert_series(
        "e-hentai.org:bbb", title="Beta", source="https://x/",
        source_site="e-hentai.org", relative_path="Beta",
    )
    lib.upsert_chapter(
        "e-hentai.org:aaa", url="https://e-hentai.org/g/aaa/1/",
        chapter_no="1", title="Ch 1", cbz="1.cbz", size_bytes=100, page_count=5,
    )
    lib.upsert_chapter(
        "e-hentai.org:aaa", url="https://e-hentai.org/g/aaa/2/",
        chapter_no="2", title="Ch 2", cbz="2.cbz", size_bytes=200, page_count=6,
    )
    lib.close()

    (root / "Alpha").mkdir(parents=True, exist_ok=True)
    (root / "Alpha" / "1.cbz").write_bytes(b"x")
    (root / "Alpha" / "2.cbz").write_bytes(b"x" * 100)
    return lib


class TestList:
    def test_empty_library_message(self, tmp_path, capsys):
        root = tmp_path / "dl"
        root.mkdir()
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        lib.close()
        assert run_library_command("list", ["-o", str(root)]) == 0
        out = capsys.readouterr().out
        assert "empty" in out.lower()

    def test_no_db(self, tmp_path, capsys):
        root = tmp_path / "dl"
        root.mkdir()
        db = root / ".comic-dl" / "library.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"this is not a sqlite database")
        assert run_library_command("list", ["-o", str(root)]) == 1
        captured = capsys.readouterr()
        assert "no library database" in captured.err.lower()

    def test_lists_series(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("list", ["-o", str(root)]) == 0
        out = capsys.readouterr().out
        assert "Alpha" in out
        assert "Beta" in out
        assert "e-hentai.org" in out
        assert "2" in out  # chapter count for Alpha

    def test_default_output_dir(self, tmp_path, monkeypatch, capsys):
        root = tmp_path / "cwd"
        downloads = root / "downloads"
        root.mkdir()
        monkeypatch.setattr("comic_dl.cli.library.configured_output_dir", lambda: downloads)
        _seed(tmp_path, downloads)
        assert run_library_command("list", []) == 0
        assert "Alpha" in capsys.readouterr().out

    def test_bad_output_dir_exits_usage(self, tmp_path, capsys):
        root = tmp_path / "dl"
        root.mkdir()
        blocker = root / "blocker"
        blocker.write_text("x")
        bad = blocker / "sub"
        assert run_library_command("list", ["-o", str(bad)]) == 2
        err = capsys.readouterr().err
        assert "Library path not found" in err
        assert "-o" in err

    def test_output_dir_is_file_exits_usage(self, tmp_path, capsys):
        root = tmp_path / "dl"
        root.mkdir()
        f = root / "file"
        f.write_text("x")
        assert run_library_command("list", ["-o", str(f)]) == 2
        assert "not a directory" in capsys.readouterr().err

    def test_empty_library_get_started(self, tmp_path, capsys):
        root = tmp_path / "dl"
        root.mkdir()
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        lib.close()
        assert run_library_command("list", ["-o", str(root)]) == 0
        assert "comic-dl -u" in capsys.readouterr().out

    def test_list_source_filter(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = _seed(tmp_path, root)
        lib.open()
        lib.upsert_series(
            "webtoons.com:1", title="Webtoon", source="https://www.webtoons.com/x",
            source_site="webtoons.com", relative_path="Webtoon",
        )
        lib.close()
        assert run_library_command("list", ["-o", str(root), "--source", "e-hentai.org"]) == 0
        out = capsys.readouterr().out
        assert "Alpha" in out
        assert "Webtoon" not in out

    def test_list_source_no_match_hint(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("list", ["-o", str(root), "--source", "webtoons.com"]) == 0
        out = capsys.readouterr().out
        assert "No series from 'webtoons.com'" in out

    def test_list_source_typo_suggestion(self, tmp_path, monkeypatch, capsys):
        from comic_dl.scrapers.registry import SourceEntry

        entries = [
            SourceEntry(
                instance=None, domain="e-hentai.org", capabilities=frozenset(),
                name="t", version="0", builtin=True,
            ),
            SourceEntry(
                instance=None, domain="webtoons.com", capabilities=frozenset(),
                name="t", version="0", builtin=True,
            ),
        ]
        monkeypatch.setattr("comic_dl.cli.library.list_sources", lambda: entries)
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("list", ["-o", str(root), "--source", "e-hentaiorg"]) == 0
        assert "Did you mean: e-hentai.org" in capsys.readouterr().err

    def test_list_latest_suggests_sibling_command(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("list", ["-o", str(root), "--latest"]) == 2
        err = capsys.readouterr().err
        assert "separate command" in err
        assert "comic-dl latest" in err


class TestInfo:
    def test_empty_library_not_found(self, tmp_path, capsys):
        root = tmp_path / "dl"
        assert run_library_command("info", ["-o", str(root), "Alpha"]) == 2
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower()

    def test_info_by_title(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("info", ["-o", str(root), "alpha"]) == 0
        out = capsys.readouterr().out
        assert "Alpha" in out
        assert "e-hentai.org:aaa" in out
        assert "Ch 1" in out
        assert "Ch 2" in out

    def test_info_marks_missing_cbz(self, tmp_path, capsys):
        from comic_dl.ui import glyphs

        root = tmp_path / "dl"
        _seed(tmp_path, root)
        (root / "Alpha" / "2.cbz").unlink()
        assert run_library_command("info", ["-o", str(root), "Alpha"]) == 0
        assert glyphs().fail in capsys.readouterr().out

    def test_info_not_found(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("info", ["-o", str(root), "Nope"]) == 2
        assert "not found" in capsys.readouterr().err.lower()

    def test_info_by_url(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        lib.upsert_series(
            "webtoons.com:10482", title="Lodoss",
            source="https://www.webtoons.com/en/action/list?title_no=10482",
            source_site="webtoons.com", relative_path="Lodoss",
        )
        lib.close()
        assert run_library_command(
            "info", ["-o", str(root), "https://www.webtoons.com/en/action/list/?title_no=10482"]
        ) == 0
        out = capsys.readouterr().out
        assert "Lodoss" in out
        assert "webtoons.com:10482" in out

    def test_info_url_not_found_hints_root(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "info", ["-o", str(root), "https://example.com/missing"]
        ) == 2
        assert "matches that URL" in capsys.readouterr().err

    def test_info_ambiguous(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        lib.upsert_series("x:1", title="Same", relative_path="A")
        lib.upsert_series("x:2", title="Same", relative_path="B")
        lib.close()
        assert run_library_command("info", ["-o", str(root), "same"]) == 2
        captured = capsys.readouterr()
        assert "matches multiple" in captured.err.lower()
        assert "x:1" in captured.err
        assert "x:2" in captured.err

    def test_info_never_downloaded_shows_dash(self, tmp_path, capsys):
        from comic_dl.ui import glyphs

        root = tmp_path / "dl"
        lib = _seed(tmp_path, root)
        lib.open()
        lib.upsert_chapter(
            "e-hentai.org:aaa", url="https://e-hentai.org/g/aaa/3/",
            chapter_no="3", title="Ch 3",
        )
        lib.close()
        assert run_library_command("info", ["-o", str(root), "Alpha"]) == 0
        out = capsys.readouterr().out
        assert "Verified" in out
        assert glyphs().ok in out
        assert glyphs().ndash in out

    def test_info_explains_failures_by_default(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        (root / "Alpha" / "2.cbz").unlink()
        assert run_library_command("info", ["-o", str(root), "Alpha"]) == 0
        err = capsys.readouterr().err
        assert "not verified" in err
        assert "cbz not found" in err

    def test_info_rejects_old_verbose_flag(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("info", ["-o", str(root), "Alpha", "-v"]) == 2

    def test_info_domain_suggestion(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("comic_dl.cli.library.list_sources", lambda: [])
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("info", ["-o", str(root), "gedecomix.com"]) == 2
        err = capsys.readouterr().err
        assert "source domain" in err
        assert "not a series title" in err

    def test_info_registered_source_suggestion(self, tmp_path, monkeypatch, capsys):
        from comic_dl.scrapers.registry import SourceEntry

        entries = [
            SourceEntry(
                instance=None, domain="gedecomix.com", capabilities=frozenset(),
                name="t", version="0", builtin=True,
            ),
        ]
        monkeypatch.setattr("comic_dl.cli.library.list_sources", lambda: entries)
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("info", ["-o", str(root), "gedecomix.com"]) == 2
        err = capsys.readouterr().err
        assert "looks like a source, not a series" in err
        assert "--source gedecomix.com" in err

    def test_info_title_typo_suggestion(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("info", ["-o", str(root), "Alpa"]) == 2
        err = capsys.readouterr().err
        assert "Did you mean: Alpha" in err


class TestLatest:
    def test_no_db(self, tmp_path, capsys):
        root = tmp_path / "dl"
        root.mkdir()
        db = root / ".comic-dl" / "library.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"this is not a sqlite database")
        assert run_library_command("latest", ["-o", str(root)]) == 1
        assert "no library database" in capsys.readouterr().err.lower()

    def test_nothing_in_window(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = _seed(tmp_path, root)
        lib.open()
        with lib._conn:
            lib._conn.execute("UPDATE chapters SET downloaded_at = '2020-01-01T00:00:00'")
        lib.close()
        assert run_library_command("latest", ["-o", str(root)]) == 0
        assert "no chapters" in capsys.readouterr().out.lower()

    def test_lists_recent_chapters(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = _seed(tmp_path, root)
        lib.open()
        with lib._conn:
            lib._conn.execute(
                "UPDATE chapters SET downloaded_at = ?",
                (datetime.now(UTC).isoformat(timespec="seconds"),),
            )
        lib.close()
        assert run_library_command("latest", ["-o", str(root)]) == 0
        out = capsys.readouterr().out
        assert "Alpha" in out
        assert "Ch 1" in out
        assert "Ch 2" in out

    def test_days_limit(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = _seed(tmp_path, root)
        lib.open()
        with lib._conn:
            lib._conn.execute(
                "UPDATE chapters SET downloaded_at = ?",
                (datetime.now(UTC).isoformat(timespec="seconds"),),
            )
        lib.close()
        assert run_library_command("latest", ["-o", str(root), "--days", "1"]) == 0
        assert "Ch 1" in capsys.readouterr().out

    def test_invalid_days(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("latest", ["-o", str(root), "-n", "0"]) == 2

    def test_latest_source_filter(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = _seed(tmp_path, root)
        lib.open()
        with lib._conn:
            lib._conn.execute(
                "UPDATE chapters SET downloaded_at = ?",
                (datetime.now(UTC).isoformat(timespec="seconds"),),
            )
        lib.close()
        assert run_library_command(
            "latest", ["-o", str(root), "--days", "1", "--source", "e-hentai.org"]
        ) == 0
        assert "Ch 1" in capsys.readouterr().out

    def test_latest_source_no_match_hint(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = _seed(tmp_path, root)
        lib.open()
        with lib._conn:
            lib._conn.execute(
                "UPDATE chapters SET downloaded_at = ?",
                (datetime.now(UTC).isoformat(timespec="seconds"),),
            )
        lib.close()
        assert run_library_command(
            "latest", ["-o", str(root), "--days", "1", "--source", "webtoons.com"]
        ) == 0
        assert "No chapters from 'webtoons.com'" in capsys.readouterr().out


class TestRemove:
    def test_empty_library_not_found(self, tmp_path, capsys):
        root = tmp_path / "dl"
        assert run_library_command("remove", ["-o", str(root), "Alpha"]) == 2
        assert "not found" in capsys.readouterr().err.lower()

    def test_remove_moves_to_trash(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("remove", ["-o", str(root), "Alpha", "-y"]) == 0
        out = capsys.readouterr().out
        assert "moved to trash" in out.lower()
        trash = root / ".comic-dl" / "trash"
        entries = list(trash.iterdir())
        dirs = [e for e in entries if e.is_dir()]
        assert len(dirs) == 1
        assert (dirs[0] / "1.cbz").exists()
        assert not (root / "Alpha").exists()
        sidecars = [e for e in entries if e.is_file()]
        assert len(sidecars) == 1
        assert sidecars[0].name.endswith(".restore.json")

    def test_remove_deletes_db_row(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("remove", ["-o", str(root), "Alpha", "-y"]) == 0
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        assert lib.get_series("e-hentai.org:aaa") is None
        assert lib.get_chapters("e-hentai.org:aaa") == []
        assert lib.get_series("e-hentai.org:bbb") is not None
        lib.close()

    def test_remove_missing_dir_only_db(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        shutil.rmtree(root / "Alpha")
        assert run_library_command("remove", ["-o", str(root), "Alpha", "-y"]) == 0
        captured = capsys.readouterr()
        assert "directory not found" in captured.err.lower()
        assert "removed from library" in captured.out.lower()

    def test_remove_declined(self, tmp_path, capsys, monkeypatch):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        monkeypatch.setattr("comic_dl.cli.library.Prompt.ask", lambda *a, **k: "n")
        assert run_library_command("remove", ["-o", str(root), "Alpha"]) == 0
        assert "cancelled" in capsys.readouterr().err.lower()
        assert (root / "Alpha").is_dir()
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        assert lib.get_series("e-hentai.org:aaa") is not None
        lib.close()

    def test_remove_ambiguous(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        lib.upsert_series("x:1", title="Same", relative_path="A")
        lib.upsert_series("x:2", title="Same", relative_path="B")
        lib.close()
        assert run_library_command("remove", ["-o", str(root), "same", "-y"]) == 2
        assert "matches multiple" in capsys.readouterr().err.lower()

    def test_remove_escapes_root(self, tmp_path, capsys):
        root = tmp_path / "dl"
        root.mkdir()
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        lib.upsert_series(
            "x:1", title="Evil", source_site="x",
            relative_path=os.path.join("..", "elsewhere"),
        )
        lib.close()
        (tmp_path / "elsewhere").mkdir(exist_ok=True)
        assert run_library_command("remove", ["-o", str(root), "Evil", "-y"]) == 1
        assert "refusing" in capsys.readouterr().err.lower()

    def test_remove_dry_run_changes_nothing(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "--dry-run"]
        ) == 0
        assert "dry run" in capsys.readouterr().err.lower()
        assert (root / "Alpha").is_dir()
        assert not (root / ".comic-dl" / "trash").exists()
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        assert lib.get_series("e-hentai.org:aaa") is not None
        lib.close()

    def test_remove_json_requires_yes(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "--json"]
        ) == 130
        captured = capsys.readouterr()
        assert "requires confirmation" in (captured.out + captured.err).lower()
        assert (root / "Alpha").is_dir()

    def test_remove_json_emits_result(self, tmp_path, capsys):
        import json
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "--json", "-y"]
        ) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 1
        assert payload["series_id"] == "e-hentai.org:aaa"
        assert payload["title"] == "Alpha"
        assert payload["directory_moved"] is True
        assert payload["chapter_count"] == 2
        assert payload["size_bytes"] == 300
        assert "trash" in payload["trashed_to"]
        assert not (root / "Alpha").exists()

    def test_remove_json_dry_run(self, tmp_path, capsys):
        import json
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "--json", "--dry-run"]
        ) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert payload["series_id"] == "e-hentai.org:aaa"
        assert (root / "Alpha").is_dir()


class TestListJson:
    def _read_json(self, capsys):
        import json
        return json.loads(capsys.readouterr().out)

    def test_empty_library_emits_empty_list(self, tmp_path, capsys):
        root = tmp_path / "dl"
        root.mkdir()
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        lib.close()
        assert run_library_command("list", ["-o", str(root), "--json"]) == 0
        assert self._read_json(capsys) == {
            "schema_version": 1,
            "series": [],
        }

    def test_no_db_exits_error(self, tmp_path):
        root = tmp_path / "dl"
        root.mkdir()
        db = root / ".comic-dl" / "library.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"not sqlite")
        assert run_library_command("list", ["-o", str(root), "--json"]) == 1

    def test_payload_fields(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("list", ["-o", str(root), "--json"]) == 0
        payload = self._read_json(capsys)
        assert payload["schema_version"] == 1
        series = payload["series"]
        assert len(series) == 2
        alpha = next(s for s in series if s["title"] == "Alpha")
        assert alpha["series_id"] == "e-hentai.org:aaa"
        assert alpha["source_site"] == "e-hentai.org"
        assert alpha["chapter_count"] == 2
        assert alpha["total_size"] == 300
        assert alpha["directory"].endswith("Alpha")


class TestInfoJson:
    def _read_json(self, capsys):
        import json
        return json.loads(capsys.readouterr().out)

    def test_payload_fields(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "info", ["-o", str(root), "alpha", "--json"]
        ) == 0
        payload = self._read_json(capsys)
        assert payload["schema_version"] == 1
        assert payload["title"] == "Alpha"
        assert payload["chapter_count"] == 2
        assert [c["chapter_no"] for c in payload["chapters"]] == ["1", "2"]
        assert all(c["ok"] for c in payload["chapters"])

    def test_missing_cbz_ok_false(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        (root / "Alpha" / "2.cbz").unlink()
        assert run_library_command(
            "info", ["-o", str(root), "Alpha", "--json"]
        ) == 0
        payload = self._read_json(capsys)
        assert payload["chapters"][0]["ok"] is True
        assert payload["chapters"][1]["ok"] is False

    def test_not_found_exits_usage(self, tmp_path):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "info", ["-o", str(root), "Nope", "--json"]
        ) == 2


class TestLatestJson:
    def _read_json(self, capsys):
        import json
        return json.loads(capsys.readouterr().out)

    def test_payload_fields(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = _seed(tmp_path, root)
        lib.open()
        with lib._conn:
            lib._conn.execute(
                "UPDATE chapters SET downloaded_at = ?",
                (datetime.now(UTC).isoformat(timespec="seconds"),),
            )
        lib.close()
        assert run_library_command("latest", ["-o", str(root), "--json"]) == 0
        payload = self._read_json(capsys)
        assert payload["schema_version"] == 1
        chapters = payload["chapters"]
        assert len(chapters) == 2
        first = chapters[0]
        assert first["series_title"] == "Alpha"
        assert first["size_bytes"] in (100, 200)

    def test_empty_window_emits_empty_list(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = _seed(tmp_path, root)
        lib.open()
        with lib._conn:
            lib._conn.execute("UPDATE chapters SET downloaded_at = '2020-01-01T00:00:00'")
        lib.close()
        assert run_library_command("latest", ["-o", str(root), "--json"]) == 0
        assert self._read_json(capsys)["chapters"] == []


class TestRestore:
    def test_remove_then_restore_round_trips(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "-y"]
        ) == 0
        capsys.readouterr()
        assert not (root / "Alpha").exists()

        assert run_library_command("restore", ["-o", str(root), "Alpha"]) == 0
        out = capsys.readouterr().out
        assert "restored directory" in out.lower()

        assert (root / "Alpha" / "1.cbz").exists()
        assert (root / "Alpha" / "2.cbz").exists()
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        series = lib.get_series("e-hentai.org:aaa")
        assert series is not None
        assert series["title"] == "Alpha"
        chapters = lib.get_chapters("e-hentai.org:aaa")
        assert len(chapters) == 2
        assert {c["chapter_no"] for c in chapters} == {"1", "2"}
        lib.close()
        trash = root / ".comic-dl" / "trash"
        assert not list(trash.iterdir()) if trash.is_dir() else True

    def test_restore_preserves_timestamps(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = _seed(tmp_path, root)
        lib.open()
        with lib._conn:
            lib._conn.execute(
                "UPDATE series SET last_updated = '2021-03-04T05:06:07',"
                " created_at = '2021-01-01T00:00:00'"
            )
            lib._conn.execute(
                "UPDATE chapters SET downloaded_at = '2021-02-02T00:00:00'"
            )
        lib.close()
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "-y"]
        ) == 0
        capsys.readouterr()
        assert run_library_command("restore", ["-o", str(root), "Alpha"]) == 0
        capsys.readouterr()
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        series = lib.get_series("e-hentai.org:aaa")
        assert series["last_updated"] == "2021-03-04T05:06:07"
        assert series["created_at"] == "2021-01-01T00:00:00"
        chapters = lib.get_chapters("e-hentai.org:aaa")
        assert {c["downloaded_at"] for c in chapters} == {"2021-02-02T00:00:00"}
        lib.close()

    def test_restore_by_url(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "-y"]
        ) == 0
        capsys.readouterr()
        assert run_library_command(
            "restore", ["-o", str(root), "https://x/"]
        ) == 0
        assert "restored to library" in capsys.readouterr().out.lower()

    def test_restore_by_id(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "-y"]
        ) == 0
        capsys.readouterr()
        assert run_library_command(
            "restore", ["-o", str(root), "e-hentai.org:aaa"]
        ) == 0
        assert "restored to library" in capsys.readouterr().out.lower()

    def test_restore_not_found(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "restore", ["-o", str(root), "Nope"]
        ) == 2
        assert "no trashed series matches" in capsys.readouterr().err.lower()

    def test_restore_ambiguous(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        lib.upsert_series("x:1", title="Same", source_site="x", relative_path="A")
        lib.upsert_series("x:2", title="Same", source_site="x", relative_path="B")
        lib.close()
        for sdir, sid in (("A", "x:1"), ("B", "x:2")):
            (root / sdir).mkdir(parents=True)
            assert run_library_command(
                "remove", ["-o", str(root), sid, "-y"]
            ) == 0
            capsys.readouterr()
        assert run_library_command(
            "restore", ["-o", str(root), "same"]
        ) == 2
        assert "matches multiple" in capsys.readouterr().err.lower()

    def test_restore_dry_run_changes_nothing(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "-y"]
        ) == 0
        capsys.readouterr()
        assert run_library_command(
            "restore", ["-o", str(root), "Alpha", "--dry-run"]
        ) == 0
        assert "dry run" in capsys.readouterr().err.lower()
        assert not (root / "Alpha").exists()
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        assert lib.get_series("e-hentai.org:aaa") is None
        lib.close()

    def test_restore_json_round_trip(self, tmp_path, capsys):
        import json
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("remove", ["-o", str(root), "Alpha", "-y"]) == 0
        capsys.readouterr()
        assert run_library_command(
            "restore", ["-o", str(root), "Alpha", "--json"]
        ) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 1
        assert payload["series_id"] == "e-hentai.org:aaa"
        assert payload["title"] == "Alpha"
        assert payload["directory_restored"] is True
        assert (root / "Alpha").is_dir()

    def test_restore_json_dry_run(self, tmp_path, capsys):
        import json
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command("remove", ["-o", str(root), "Alpha", "-y"]) == 0
        capsys.readouterr()
        assert run_library_command(
            "restore", ["-o", str(root), "Alpha", "--json", "--dry-run"]
        ) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert not (root / "Alpha").exists()

    def test_restore_refuses_existing_library_entry(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "-y"]
        ) == 0
        capsys.readouterr()
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        lib.upsert_series(
            "e-hentai.org:aaa", title="Alpha", source_site="e-hentai.org",
            relative_path="Alpha",
        )
        lib.close()
        assert run_library_command(
            "restore", ["-o", str(root), "Alpha"]
        ) == 1
        assert "already in the library" in capsys.readouterr().err.lower()

    def test_restore_refuses_existing_directory(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "-y"]
        ) == 0
        capsys.readouterr()
        (root / "Alpha").mkdir(parents=True)
        assert run_library_command(
            "restore", ["-o", str(root), "Alpha"]
        ) == 1
        assert "refusing to restore" in capsys.readouterr().err.lower()

    def test_restore_without_directory_restores_db_only(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "-y"]
        ) == 0
        capsys.readouterr()
        import shutil as _shutil
        for entry in (root / ".comic-dl" / "trash").iterdir():
            if entry.is_dir():
                _shutil.rmtree(entry)
        assert run_library_command(
            "restore", ["-o", str(root), "Alpha"]
        ) == 0
        captured = capsys.readouterr()
        assert "directory not found" in captured.err.lower()
        assert "restored to library" in captured.out.lower()
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        assert lib.get_series("e-hentai.org:aaa") is not None
        assert len(lib.get_chapters("e-hentai.org:aaa")) == 2
        lib.close()

    def test_restore_escapes_root(self, tmp_path, capsys):
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        assert run_library_command(
            "remove", ["-o", str(root), "Alpha", "-y"]
        ) == 0
        capsys.readouterr()
        # Tamper the sidecar so restore resolves outside the root.
        import json
        trash = root / ".comic-dl" / "trash"
        sidecar = next(trash.glob("*.restore.json"))
        meta = json.loads(sidecar.read_text())
        meta["series"]["relative_path"] = os.path.join("..", "elsewhere")
        sidecar.write_text(json.dumps(meta))
        assert run_library_command(
            "restore", ["-o", str(root), "Alpha"]
        ) == 1
        assert "refusing" in capsys.readouterr().err.lower()


class TestTrashPurge:
    def test_purges_old_entries(self, tmp_path):
        root = tmp_path / "dl"
        trash = root / ".comic-dl" / "trash"
        old = trash / "old"
        fresh = trash / "fresh"
        old.mkdir(parents=True)
        fresh.mkdir(parents=True)
        (old / "x.cbz").write_bytes(b"x")
        (fresh / "x.cbz").write_bytes(b"x")
        cutoff = datetime.now().timestamp() - (TRASH_TTL_DAYS + 1) * 86400
        os.utime(old, (cutoff, cutoff))
        _purge_trash(root)
        assert not old.exists()
        assert fresh.exists()

    def test_no_trash_dir(self, tmp_path):
        root = tmp_path / "dl"
        _purge_trash(root)
        assert not (root / ".comic-dl" / "trash").exists()

    def test_purges_old_sidecars(self, tmp_path):
        root = tmp_path / "dl"
        trash = root / ".comic-dl" / "trash"
        old = trash / "old.restore.json"
        fresh = trash / "fresh.restore.json"
        old.parent.mkdir(parents=True)
        old.write_text("{}")
        fresh.write_text("{}")
        cutoff = datetime.now().timestamp() - (TRASH_TTL_DAYS + 1) * 86400
        os.utime(old, (cutoff, cutoff))
        _purge_trash(root)
        assert not old.exists()
        assert fresh.exists()

    def test_trash_entry_stamped_with_removal_time(self, tmp_path):
        """Trashed dirs keep their original mtime unless re-stamped; the TTL
        must count from removal time, not the series' creation date."""
        root = tmp_path / "dl"
        _seed(tmp_path, root)
        series_dir = root / "Alpha"
        old = datetime.now().timestamp() - 400 * 86400
        os.utime(series_dir, (old, old))
        assert run_library_command("remove", ["-o", str(root), "Alpha", "-y"]) == 0
        trash = root / ".comic-dl" / "trash"
        entry = next(e for e in trash.iterdir() if e.is_dir())
        assert entry.stat().st_mtime > old + 3600


class TestDispatch:
    def test_unknown_command_returns_2(self, tmp_path):
        assert run_library_command("frobnicate", ["-o", str(tmp_path)]) == 2

    def test_unknown_command_has_no_side_effects(self, tmp_path):
        """Unknown commands must not open the DB or create trash."""
        root = tmp_path / "dl"
        root.mkdir()
        assert run_library_command("frobnicate", ["-o", str(root)]) == 2
        assert not (root / ".comic-dl" / "library.db").exists()
        assert not (root / ".comic-dl" / "trash").exists()

    def test_markup_in_titles_does_not_crash(self, tmp_path, capsys):
        root = tmp_path / "dl"
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        lib.upsert_series(
            "x:1", title="Evil[/] Title", source_site="x", relative_path="Evil"
        )
        lib.upsert_chapter(
            "x:1", url="https://x/1", title="Ch 1 [/]",
            chapter_no="1", cbz="1.cbz", size_bytes=10, page_count=2,
        )
        lib.close()
        (root / "Evil").mkdir(parents=True)
        (root / "Evil" / "1.cbz").write_bytes(b"x")

        assert run_library_command("list", ["-o", str(root)]) == 0
        out = capsys.readouterr().out
        assert "Evil[/] Title" in out

        assert run_library_command("info", ["-o", str(root), "Evil"]) == 0
        out = capsys.readouterr().out
        assert "Ch 1 [/]" in out

        assert run_library_command("latest", ["-o", str(root)]) == 0
        out = capsys.readouterr().out
        assert "Evil[/] Title" in out


class TestUpdate:
    def _lib(self, root: Path) -> Library:
        lib = Library(root / ".comic-dl" / "library.db")
        lib.open()
        return lib

    def _seed_webtoon(self, root: Path, sid: str = "webtoons.com:s1") -> None:
        lib = self._lib(root)
        lib.upsert_series(
            sid, title="Comet", source="https://www.webtoons.com/en/action/s/list?title_no=1",
            source_site="webtoons.com", relative_path="Comet",
        )
        lib.close()

    def test_empty_library(self, tmp_path, capsys):
        import asyncio

        from comic_dl.cli import _run_update
        assert asyncio.run(_run_update(["-o", str(tmp_path), "all"])) == 0
        assert "nothing to update" in capsys.readouterr().err.lower()

    def test_target_forwards_to_series_processor(self, tmp_path, capsys, monkeypatch):
        import asyncio

        from comic_dl import cli
        self._seed_webtoon(tmp_path)
        called: list[str] = []

        async def stub(*args, **kwargs):
            called.append(kwargs["url"])
            return True

        monkeypatch.setattr(cli, "_process_series", stub)
        code = asyncio.run(cli._run_update(["-o", str(tmp_path), "Comet"]))
        assert code == 0
        assert called == ["https://www.webtoons.com/en/action/s/list?title_no=1"]
        out = capsys.readouterr().out
        assert "Checked 1 series" in out

    def test_json_summary(self, tmp_path, capsys, monkeypatch):
        import asyncio
        import json

        from comic_dl import cli
        self._seed_webtoon(tmp_path)

        async def stub(*args, **kwargs):
            return True

        monkeypatch.setattr(cli, "_process_series", stub)
        code = asyncio.run(cli._run_update(["-o", str(tmp_path), "Comet", "--json"]))
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 1
        assert payload["checked"] == 1
        assert payload["failed"] == []
        assert payload["series"] == [{
            "series_id": "webtoons.com:s1",
            "title": "Comet",
            "status": "unchanged",
        }]

    def test_all_skips_non_series_source(self, tmp_path, capsys, monkeypatch):
        import asyncio

        from comic_dl import cli
        lib = self._lib(tmp_path)
        lib.upsert_series(
            "webtoons.com:s1", title="Comet", source_site="webtoons.com",
            source="https://www.webtoons.com/en/action/s/list?title_no=1",
            relative_path="Comet",
        )
        lib.upsert_series(
            "e-hentai.org:aaa", title="Gallery", source_site="e-hentai.org",
            source="https://e-hentai.org/g/aaa/1/", relative_path="Gallery",
        )
        lib.close()
        called: list[str] = []

        async def stub(*args, **kwargs):
            called.append(kwargs["url"])
            return True

        monkeypatch.setattr(cli, "_process_series", stub)
        code = asyncio.run(cli._run_update(["-o", str(tmp_path), "all"]))
        assert code == 0
        # Only the webtoon series is re-scraped; e-hentai has no series endpoint.
        assert called == ["https://www.webtoons.com/en/action/s/list?title_no=1"]
        assert "Skipped 1 series" in capsys.readouterr().err

    def test_missing_source_is_skipped(self, tmp_path, capsys, monkeypatch):
        import asyncio

        from comic_dl import cli
        lib = self._lib(tmp_path)
        lib.upsert_series(
            "webtoons.com:s1", title="Comet", source_site="webtoons.com",
            source="", relative_path="Comet",
        )
        lib.close()
        called: list[str] = []

        async def stub(*args, **kwargs):
            called.append(kwargs["url"])
            return True

        monkeypatch.setattr(cli, "_process_series", stub)
        code = asyncio.run(cli._run_update(["-o", str(tmp_path), "all"]))
        assert code == 0
        assert called == []
        captured = capsys.readouterr()
        assert "No source URL recorded" in captured.err

    def test_changed_counts_new_chapters(self, tmp_path, capsys, monkeypatch):
        import asyncio

        from comic_dl import cli
        self._seed_webtoon(tmp_path)

        async def stub(*args, output_dir, **kwargs):
            lib = Library(Path(output_dir) / ".comic-dl" / "library.db")
            lib.open()
            lib.upsert_chapter(
                "webtoons.com:s1", url="https://webtoons.com/ep/n",
                chapter_no="n", title="New", cbz="new.cbz",
            )
            lib.close()
            return True

        monkeypatch.setattr(cli, "_process_series", stub)
        code = asyncio.run(cli._run_update(["-o", str(tmp_path), "Comet"]))
        assert code == 0
        out = capsys.readouterr().out
        assert "1 had new chapters" in out

    def test_target_not_found_exits_usage(self, tmp_path, capsys):
        import asyncio

        from comic_dl.cli import _run_update
        self._seed_webtoon(tmp_path)
        assert asyncio.run(_run_update(["-o", str(tmp_path), "Nope"])) == 2
        assert "not found" in capsys.readouterr().err.lower()
