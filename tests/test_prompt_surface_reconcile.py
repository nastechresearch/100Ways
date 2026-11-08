import base64
import hashlib
import io
import tarfile
import zlib
from pathlib import Path

import pytest

from hundredways import prompt_surface, updates
from hundredways.prompt_surface import SNAPSHOT_REL, reconcile_prompt_surface


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def put(root: Path, files: dict[str, bytes]) -> Path:
    snapshot = root / SNAPSHOT_REL
    for rel, data in files.items():
        path = snapshot / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return snapshot


def payload(files: dict[str, bytes]) -> str:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for rel, data in sorted(files.items()):
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return base64.b85encode(zlib.compress(raw.getvalue(), 9)).decode()


def source_inputs(root: Path) -> dict[str, bytes]:
    """Minimal representative renderer inputs in every protected category."""
    files = {
        "run_agent.py": b"agent = 'reviewed'\n",
        "tools/example.py": b"TOOL = 'reviewed'\n",
        "agent/prompt.py": b"PROMPT = 'reviewed'\n",
        "nastech_cli/config.py": b"CONFIG = 'reviewed'\n",
        "nastech_time.py": b"NOW = 'reviewed'\n",
        "scripts/ci/prompt_surface.py": b"RENDER = 'reviewed'\n",
        "skills/demo/SKILL.md": b"# reviewed skill\n",
        "config/renderer.json": b'{"reviewed": true}\n',
        "pyproject.toml": b"[project]\nname = 'reviewed'\n",
    }
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return files


@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    before = {"prompts/cli.txt": b"old prompt\n", "tools/old.json": b"{}\n"}
    after = {
        "prompts/cli.txt": b"new prompt\n",
        "tools/new.json": b'{"name": "new"}\n',
        "surfaces.json": b'{"cli": {}}\n',
    }
    source_inputs(tmp_path)
    expected = prompt_surface.actual_source_manifest(str(tmp_path))
    assert expected
    old_root = put(tmp_path / "fixture" / "old", before)
    new_root = put(tmp_path / "fixture" / "new", after)
    monkeypatch.setattr(prompt_surface, "REVIEWED_STALE_TREE", tree_digest(old_root))
    monkeypatch.setattr(prompt_surface, "REVIEWED_RENDERED_TREE", tree_digest(new_root))
    monkeypatch.setattr(prompt_surface, "_PAYLOAD_B85", payload(after))
    monkeypatch.setattr(prompt_surface, "_reviewed_input_manifest", lambda: expected)
    return "legacy-provenance", before, after, expected


def test_reconciles_complete_reviewed_tree_and_accounts_each_file(tmp_path, reviewed):
    source, before, after, _ = reviewed
    snapshot = put(tmp_path, before)
    unresolved = []

    assert reconcile_prompt_surface(
        str(tmp_path), source_fingerprint=source, unresolved=unresolved
    ) == [
        f"{SNAPSHOT_REL}/prompts/cli.txt",
        f"{SNAPSHOT_REL}/surfaces.json",
        f"{SNAPSHOT_REL}/tools/new.json",
        f"{SNAPSHOT_REL}/tools/old.json",
    ]
    assert unresolved == []
    assert tree_digest(snapshot) == tree_digest(put(tmp_path / "expected", after))
    assert not (snapshot / "tools/old.json").exists()
    assert (
        reconcile_prompt_surface(str(tmp_path), source_fingerprint=source, unresolved=unresolved)
        == []
    )
    assert unresolved == []


def test_missing_snapshot_and_wrong_actual_source_fail_closed(tmp_path, reviewed):
    source, _, _, _ = reviewed
    unresolved = []
    assert (
        reconcile_prompt_surface(str(tmp_path), source_fingerprint=source, unresolved=unresolved)
        == []
    )
    assert unresolved == [SNAPSHOT_REL]
    assert not (tmp_path / SNAPSHOT_REL).exists()

    snapshot = put(tmp_path, {"prompts/cli.txt": b"original\n"})
    original = tree_digest(snapshot)
    (tmp_path / "run_agent.py").write_bytes(b"mutated actual source\n")
    unresolved.clear()
    assert (
        reconcile_prompt_surface(str(tmp_path), source_fingerprint=source, unresolved=unresolved)
        == []
    )
    assert unresolved == [SNAPSHOT_REL]
    assert tree_digest(snapshot) == original


def test_unverified_provenance_cannot_override_actual_input_mutation(tmp_path, reviewed):
    source, before, _, _ = reviewed
    snapshot = put(tmp_path, before)
    original = tree_digest(snapshot)
    # The legacy f408 tree value is deliberately passed, but a changed real tool
    # source must still stop the replacement before it touches the snapshot.
    (tmp_path / "tools/example.py").write_bytes(b"TOOL = 'tampered'\n")
    unresolved = []

    assert (
        reconcile_prompt_surface(
            str(tmp_path),
            source_fingerprint=prompt_surface.REVIEWED_SOURCE_FINGERPRINT,
            unresolved=unresolved,
        )
        == []
    )
    assert unresolved == [SNAPSHOT_REL]
    assert tree_digest(snapshot) == original
    assert prompt_surface.derive_source_fingerprint(str(tmp_path)) is None


def test_added_runtime_input_is_rejected_as_well_as_mutated_input(tmp_path, reviewed):
    source, before, _, _ = reviewed
    snapshot = put(tmp_path, before)
    original = tree_digest(snapshot)
    added = tmp_path / "agent" / "new_renderer_hook.py"
    added.write_bytes(b"PROMPT = 'unreviewed'\n")

    assert reconcile_prompt_surface(str(tmp_path), source_fingerprint=source) == []
    assert tree_digest(snapshot) == original


def test_unknown_snapshot_mutation_preserves_every_original_byte(tmp_path, reviewed):
    source, before, _, _ = reviewed
    snapshot = put(tmp_path, before)
    (snapshot / "prompts/cli.txt").write_bytes(b"future prompt\n")
    (snapshot / "unexpected.txt").write_bytes(b"new input\n")
    original = {
        path.relative_to(snapshot).as_posix(): path.read_bytes()
        for path in snapshot.rglob("*")
        if path.is_file()
    }
    unresolved = []

    assert (
        reconcile_prompt_surface(str(tmp_path), source_fingerprint=source, unresolved=unresolved)
        == []
    )
    assert unresolved == [SNAPSHOT_REL]
    assert {
        path.relative_to(snapshot).as_posix(): path.read_bytes()
        for path in snapshot.rglob("*")
        if path.is_file()
    } == original


def test_payload_full_file_accounting_rejects_tampered_or_incomplete_payload(
    tmp_path, reviewed, monkeypatch
):
    source, before, _, _ = reviewed
    snapshot = put(tmp_path, before)
    monkeypatch.setattr(
        prompt_surface, "_PAYLOAD_B85", payload({"prompts/cli.txt": b"new prompt\n"})
    )
    original = tree_digest(snapshot)
    unresolved = []

    assert (
        reconcile_prompt_surface(str(tmp_path), source_fingerprint=source, unresolved=unresolved)
        == []
    )
    assert unresolved == [SNAPSHOT_REL]
    assert tree_digest(snapshot) == original


def test_actual_source_fingerprint_is_derived_from_verified_content(tmp_path, reviewed):
    _, _, _, expected = reviewed
    fingerprint = prompt_surface.derive_source_fingerprint(str(tmp_path))
    assert (
        fingerprint
        == hashlib.sha256(
            __import__("json").dumps(expected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_symlinked_snapshot_is_rejected_without_touching_target(tmp_path, reviewed):
    source, before, _, _ = reviewed
    put(tmp_path / "holder", before)
    target = tmp_path / "holder" / SNAPSHOT_REL
    link = tmp_path / SNAPSHOT_REL
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    original = tree_digest(target)
    unresolved = []

    assert (
        reconcile_prompt_surface(str(tmp_path), source_fingerprint=source, unresolved=unresolved)
        == []
    )
    assert unresolved == [SNAPSHOT_REL]
    assert link.is_symlink()
    assert tree_digest(target) == original


def test_actual_source_manifest_rejects_symlinked_relevant_root(tmp_path):
    target = tmp_path / "outside-agent"
    target.mkdir()
    (target / "prompt.py").write_text("PROMPT = 'unreviewed'\n", encoding="utf-8")
    (tmp_path / "agent").symlink_to(target, target_is_directory=True)

    assert prompt_surface.actual_source_manifest(str(tmp_path)) is None


def test_actual_source_manifest_ignores_unrelated_large_trees(tmp_path):
    source_inputs(tmp_path)
    expected = prompt_surface.actual_source_manifest(str(tmp_path))
    ignored = tmp_path / "node_modules" / "nested" / "noise.py"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("not a renderer input\n", encoding="utf-8")
    (tmp_path / ".audit-core-env" / "lib").mkdir(parents=True)
    (tmp_path / ".audit-core-env" / "lib" / "noise.py").write_text(
        "not a renderer input\n", encoding="utf-8"
    )

    assert prompt_surface.actual_source_manifest(str(tmp_path)) == expected


def test_updates_reconcile_tree_dispatches_reviewed_prompt_surface_once(
    tmp_path, reviewed, monkeypatch
):
    _, before, after, _ = reviewed
    snapshot = put(tmp_path, before)
    # The integration target is the dispatch and accounting seam. These two
    # unrelated end-of-pipeline checks require a complete branded checkout.
    monkeypatch.setattr(updates, "_reconcile_prompt_surface_snapshot", lambda _dst: [])
    monkeypatch.setattr(updates, "_reconcile_store_gate_covers_the_lane", lambda _dst: None)

    result = updates.reconcile_tree(str(tmp_path))

    expected = [
        f"{SNAPSHOT_REL}/prompts/cli.txt",
        f"{SNAPSHOT_REL}/surfaces.json",
        f"{SNAPSHOT_REL}/tools/new.json",
        f"{SNAPSHOT_REL}/tools/old.json",
    ]
    assert result.fixed == expected
    assert result.unresolved == []
    assert tree_digest(snapshot) == tree_digest(put(tmp_path / "expected", after))
    assert updates.reconcile_tree(str(tmp_path)).fixed == []


def test_updates_reconcile_tree_preserves_unknown_runtime_snapshot_conflict(
    tmp_path, reviewed, monkeypatch
):
    _, before, _, _ = reviewed
    snapshot = put(tmp_path, before)
    original = {
        path.relative_to(snapshot).as_posix(): path.read_bytes()
        for path in snapshot.rglob("*")
        if path.is_file()
    }
    (tmp_path / "agent" / "unreviewed_runtime.py").write_bytes(b"PROMPT = 'unreviewed'\n")
    monkeypatch.setattr(updates, "_reconcile_prompt_surface_snapshot", lambda _dst: [])
    monkeypatch.setattr(updates, "_reconcile_store_gate_covers_the_lane", lambda _dst: None)

    result = updates.reconcile_tree(str(tmp_path))

    assert result.fixed == []
    assert result.unresolved == [SNAPSHOT_REL]
    assert {
        path.relative_to(snapshot).as_posix(): path.read_bytes()
        for path in snapshot.rglob("*")
        if path.is_file()
    } == original
