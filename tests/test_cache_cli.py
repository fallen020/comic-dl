"""Tests for the ``comic-dl cache`` CLI surface (status/clear/prune)."""

from __future__ import annotations

import json
import tempfile
import time

import pytest

from comic_dl import cache
from comic_dl.cli import _run_cache


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    cache.set_cache_dir(tmp_path / "http")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    yield
    cache.set_cache_dir(None)


URL = "https://kagane.to/series/foo"
PROFILE = "chrome146"


def _store(url=URL, body=b"x"):
    cache.store(
        url,
        profile=PROFILE,
        extra_headers={},
        status=200,
        headers={"Content-Type": "text/html"},
        body=body,
    )


def _make_stale(url):
    _store(url)
    path = cache._entry_path(url, PROFILE, {})
    entry = cache._read_entry(path)
    assert entry is not None
    entry["created"] = time.time() - cache.cache_ttl_hours() * 3600 - 1
    cache._write_entry(path, entry)


def _out(capsys):
    captured = capsys.readouterr()
    return (captured.out + captured.err).replace("\n", "")


def test_status_human_reports_enabled_and_split(capsys):
    _store()
    assert _run_cache(["status"]) == 0
    out = _out(capsys)
    assert "Enabled: yes" in out
    assert "Entries: 1/5000 (fresh 1, stale 0)" in out
    assert "Stored: 1 entry" in out


def test_status_json_shape(capsys):
    _store()
    assert _run_cache(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["entries"] == 1 and payload["fresh"] == 1
    assert payload["max_entries"] == 5000 and payload["scratch_dirs"] == 0


def test_clear_needs_confirmation_when_noninteractive(capsys):
    _store()
    assert _run_cache(["clear"]) == 130
    assert "requires confirmation" in _out(capsys)
    resp, _ = cache.lookup(URL, PROFILE, {})
    assert resp is not None


def test_clear_dry_run_deletes_nothing(capsys):
    _store()
    assert _run_cache(["clear", "--dry-run"]) == 0
    assert "Dry run: would clear 1 cached entry" in _out(capsys)
    resp, _ = cache.lookup(URL, PROFILE, {})
    assert resp is not None


def test_clear_yes_deletes_and_reports(capsys, tmp_path):
    _store()
    stray = tmp_path / "tmp" / "comic-dl-deadbeef"
    stray.mkdir()
    assert _run_cache(["clear", "-y"]) == 0
    out = _out(capsys)
    assert "Cleared 1 cached response(s)." in out
    assert "Removed 1 stray temp dir(s)." in out
    assert not stray.exists()


def test_clear_declined_interactive_aborts(monkeypatch, capsys):
    _store()
    monkeypatch.setattr("comic_dl.cli._is_interactive_output", lambda: True)
    monkeypatch.setattr("comic_dl.cli.Confirm.ask", lambda *a, **k: False)
    assert _run_cache(["clear"]) == 0
    assert "Aborted." in _out(capsys)
    resp, _ = cache.lookup(URL, PROFILE, {})
    assert resp is not None


def test_prune_needs_confirmation_when_noninteractive(capsys):
    _make_stale("https://kagane.to/series/old")
    assert _run_cache(["prune"]) == 130
    assert "requires confirmation" in _out(capsys)


def test_prune_yes_keeps_fresh(capsys):
    _store()
    _make_stale("https://kagane.to/series/old")
    assert _run_cache(["prune", "-y"]) == 0
    assert "Pruned 1 file(s)" in _out(capsys)
    resp, _ = cache.lookup(URL, PROFILE, {})
    assert resp is not None


def test_prune_dry_run_deletes_nothing(capsys):
    _make_stale("https://kagane.to/series/old")
    assert _run_cache(["prune", "--dry-run"]) == 0
    assert "Dry run: would remove 1 stale entry." in _out(capsys)
    _, stale = cache.lookup("https://kagane.to/series/old", PROFILE, {})
    assert stale is not None
