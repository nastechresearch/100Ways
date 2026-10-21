from __future__ import annotations

import json
import os
from pathlib import Path

from hundredways import tree_index
from hundredways.integrity import tree_digest
from hundredways.tree_index import FileIndex


def _tree(root: Path) -> Path:
    """Build the filesystem shapes the wired loops have to agree about."""
    (root / "readme.md").write_text("# NasTech\n", encoding="utf-8")
    (root / "empty.py").write_text("", encoding="utf-8")
    (root / "package-lock.json").write_text(json.dumps({"name": "nastech"}), encoding="utf-8")
    (root / "uv.lock").write_text('name = "urllib3"\nversion = "2.0.1"\n', encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "logo.png").write_bytes(b"\x89PNG\xff\xfe not utf-8\n")
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "empty-dir").mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep").mkdir()
    (root / "node_modules" / "dep" / "index.js").write_text("hermes\n", encoding="utf-8")
    (root / "app" / "node_modules").mkdir()
    (root / "app" / "node_modules" / "inner.txt").write_text("nous\n", encoding="utf-8")
    (root / "linked.md").symlink_to(root / "readme.md")
    (root / "linked-app").symlink_to(root / "app")
    (root / "broken.md").symlink_to(root / "gone.md")
    worktree = root / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /nowhere\n", encoding="utf-8")
    return root


def _weekly_walk(root: Path) -> list[str]:
    """The path list the five weekly ``rglob`` loops used to build for themselves."""
    return sorted(
        str(path.relative_to(root))
        for path in Path(root).rglob("*")
        if path.is_file() and ".git" not in path.parts and "node_modules" not in path.parts
    )


def _bare_walk(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix() for path in Path(root).rglob("*") if path.is_file()
    )


def test_files_view_matches_the_weekly_loop_it_replaced(tmp_path):
    root = _tree(Path(tmp_path))

    assert [entry.rel for entry in FileIndex(root).files()] == _weekly_walk(root)


def test_entries_view_keeps_every_path_a_bare_rglob_saw(tmp_path):
    root = _tree(Path(tmp_path))

    assert [entry.rel for entry in FileIndex(root).entries] == _bare_walk(root)
    assert "node_modules/dep/index.js" in [entry.rel for entry in FileIndex(root).entries]
    assert ".git/config" in [entry.rel for entry in FileIndex(root).entries]


def test_symlinked_and_unreadable_shares_are_viewed_like_is_file(tmp_path):
    root = _tree(Path(tmp_path))
    index = FileIndex(root)
    rels = [entry.rel for entry in index.files()]

    assert "linked.md" in rels
    assert "broken.md" not in rels
    assert "linked-app/main.py" not in rels
    assert "empty-dir" not in rels
    assert index.entry("missing.md") is None
    assert index.entry("worktree/.git") is not None
    assert "worktree/.git" not in rels


def test_digest_matches_integrity_tree_digest(tmp_path):
    root = _tree(Path(tmp_path))

    assert FileIndex(root).digest() == tree_digest(root)


def test_digest_binds_file_modes_and_content(tmp_path):
    root = _tree(Path(tmp_path))
    before = FileIndex(root).digest()

    (root / "readme.md").write_text("# NasTech changed\n", encoding="utf-8")

    assert FileIndex(root).digest() != before


def test_text_matches_read_text_for_every_viewed_path(tmp_path):
    root = _tree(Path(tmp_path))
    index = FileIndex(root)

    for entry in index.entries:
        expected = None
        try:
            expected = entry.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            expected = None
        assert index.text(entry.rel) == expected
    assert index.text("missing.md") is None


def test_binary_and_unreadable_files_read_as_none(tmp_path):
    root = _tree(Path(tmp_path))
    blocked = root / "locked.txt"
    blocked.write_text("hermes\n", encoding="utf-8")
    blocked.chmod(0)
    index = FileIndex(root)

    assert index.text("assets/logo.png") is None
    if os.geteuid() != 0:
        assert index.text("locked.txt") is None
        assert "locked.txt" in [entry.rel for entry in index.files()]


def test_cached_text_is_read_from_disk_only_once(tmp_path, monkeypatch):
    root = _tree(Path(tmp_path))
    index = FileIndex(root)
    expected = (root / "readme.md").read_text(encoding="utf-8")
    reads: list[str] = []
    real_read_text = Path.read_text

    def counting(self, *args, **kwargs):
        reads.append(str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)

    assert index.text("readme.md") == expected
    assert index.text("readme.md") == expected
    assert index.text("missing.md") is None
    assert reads == [str(root / "readme.md")]


def test_one_walk_serves_every_repeated_view(tmp_path, monkeypatch):
    root = _tree(Path(tmp_path))
    directories = len(list(os.walk(root)))
    scans: list[str] = []
    real_scandir = os.scandir

    def counting(path):
        scans.append(os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr(tree_index.os, "scandir", counting)
    index = FileIndex(root)

    assert len([scan for scan in scans if Path(scan) == root or scan.startswith(str(root) + "/")]) \
        == directories
    scans.clear()
    digest = index.digest()
    for _ in range(3):
        assert index.files()
        assert index.files(matching="uv.lock")
        assert index.entries
        assert index.text("readme.md")
    assert index.digest() == digest
    assert scans == []


def test_content_cache_stays_bounded(tmp_path, monkeypatch):
    root = _tree(Path(tmp_path))
    monkeypatch.setattr(tree_index, "_CACHE_MAX_ENTRIES", 2)
    index = FileIndex(root)
    reads: list[str] = []
    real_read_text = Path.read_text

    def counting(self, *args, **kwargs):
        reads.append(self.name)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    for rel in ("readme.md", "empty.py", "uv.lock"):
        index.text(rel)
    assert reads == ["readme.md", "empty.py", "uv.lock"]

    index.text("readme.md")
    index.text("uv.lock")

    assert reads == ["readme.md", "empty.py", "uv.lock", "readme.md"]


def test_matching_narrows_a_view_to_one_basename(tmp_path):
    root = _tree(Path(tmp_path))
    root_child = root / "nested"
    root_child.mkdir()
    (root_child / "package-lock.json").write_text("{}", encoding="utf-8")
    index = FileIndex(root)

    assert [entry.rel for entry in index.files(matching="package-lock.json")] == [
        "nested/package-lock.json",
        "package-lock.json",
    ]
    assert [entry.rel for entry in index.files(matching="uv.lock")] == ["uv.lock"]


def test_index_is_a_snapshot_not_a_live_view(tmp_path):
    root = _tree(Path(tmp_path))
    index = FileIndex(root)
    assert index.text("readme.md") == "# NasTech\n"

    (root / "written-after.md").write_text("Hermes\n", encoding="utf-8")
    (root / "readme.md").write_text("# rewritten\n", encoding="utf-8")

    assert index.entry("written-after.md") is None
    assert "written-after.md" not in [entry.rel for entry in index.files()]
    assert index.text("readme.md") == "# NasTech\n"
    assert FileIndex(root).text("readme.md") == "# rewritten\n"
