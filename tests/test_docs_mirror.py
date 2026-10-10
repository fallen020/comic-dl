from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_MIRROR_FILE = REPO_ROOT / "scripts" / "check-docs-mirror.py"


def _load_mirror():
    spec = importlib.util.spec_from_file_location("test_docs_mirror", _MIRROR_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mirror = _load_mirror()


class TestDocsSourceMap:
    def test_known_renames(self):
        assert mirror._docs_source_map() == {
            "introduction": "index.md",
            "installation": "install.md",
            "usage/basic": "usage/download.md",
        }


class TestMirrorParity:
    def test_live_tree_has_no_orphans(self):
        assert mirror.check_mirror_parity() == []

    def test_missing_mirror_is_flagged(self, tmp_path, monkeypatch):
        docs = tmp_path / "docs"
        site = tmp_path / "site"
        docs.mkdir()
        site.mkdir()
        (docs / "orphan.md").write_text("# Orphan\n")
        (site / "stray.mdx").write_text("# Stray\n")
        monkeypatch.setattr(mirror, "DOCS", docs)
        monkeypatch.setattr(mirror, "WEBSITE", site)
        monkeypatch.setattr(mirror, "_docs_source_map", lambda: {})
        findings = mirror.check_mirror_parity()
        assert any("orphan.md" in f for f in findings)
        assert any("stray.mdx" in f for f in findings)
