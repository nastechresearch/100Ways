import ast
import hashlib
from pathlib import Path

import pytest

from hundredways import release_surface
from hundredways.release_surface import reconcile_release_surface


def approve_fixture(monkeypatch, rel: str, source: str, expected: str) -> None:
    """Approve only explicit test fixtures; production pins are never learned."""
    monkeypatch.setitem(
        release_surface._REVIEWED_FILES,
        rel,
        (
            hashlib.sha256(source.encode()).hexdigest(),
            hashlib.sha256(expected.encode()).hexdigest(),
        ),
    )


def put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_reconciles_only_the_literal_expressions_in_actual_function_shapes(tmp_path, monkeypatch):
    # These are the complete containing functions from f408, reduced only by
    # unrelated executable statements; their signatures and anchors are literal.
    put(
        tmp_path,
        "tests/scripts/test_release_darwin.py",
        """\
def test_finalize_publishes_the_feed_pointer_without_any_r2_configuration(release_store, tmp_path):
    legs, bytes_by_key = _inputs()
    staged = stage_github_handoff(tmp_path, "v0.28.0", legs, bytes_by_key)
    darwin.finalize(tag="v0.28.0", dir=str(staged))
    pointer = "releases/darwin/stable/stable-mac.yml"
    published = gh_transport.read_key(pointer)
    assert published == merge_mac_feeds(legs, "v0.28.0")["text"]
    feed = parse_mac_feed(published)
    assert len(feed["files"]) == 2
""",
    )
    put(
        tmp_path,
        "tests/scripts/test_stable_release.py",
        """\
def test_transitions_bind_all_arches_identity_version_and_archive():
    old = candidates("v1.2.3", "a" * 40, "1" * 64)
    new = candidates("v1.2.4", "b" * 40, "2" * 64, archive="rc.1-v1.2.4")
    windows = next(i for i, row in enumerate(new["packages"]) if row["platform"] == "windows")
    for suffix in ("../other.zip", "%2e%2e/other.zip", "%252e%252e/other.zip"):
        traversal = copy.deepcopy(new)
        traversal["packages"][windows]["artifact"]["url"] = (
            f"{BASE}/releases/tag/{new['tag']}/{suffix}"
        )
        with pytest.raises(ValueError, match="path encoding"):
            plan_transitions(old, traversal, BASE)

def test_complete_writes_no_final_tag_and_leaves_the_draft_on_the_attempt_ref(
    tmp_path, monkeypatch
):
    from scripts.releases import stable
    candidate = candidates("v1.2.3", "a" * 40, "c" * 64, archive="rc.1-v1.2.3")
    monkeypatch.setattr(stable, "read_candidate", lambda env: candidate)
    stable.complete({"RELEASE_CLAIM_TAG": "rc.1-v1.2.3"})
""",
    )
    put(
        tmp_path,
        "scripts/releases/lanes.py",
        """\
DEFAULT = frozenset(("docker", "termux"))
def claim_lanes(metadata):
    if metadata.get("includeMacosIntel"):
        raise ValueError("unsupported")
    lanes = set(NAMES) - {"termux"}
    if metadata.get("includeTermux"):
        lanes.add("termux")
    return frozenset(lanes)
""",
    )

    for rel in ("tests/scripts/test_release_darwin.py", "tests/scripts/test_stable_release.py"):
        source = (tmp_path / rel).read_text()
        expected = (
            source.replace(
                'assert len(feed["files"]) == 2',
                'assert len(feed["files"]) == len(darwin.ARCHES)',
            )
            .replace(
                'match="path encoding"',
                'match="outside its immutable tag archive: windows/arm64"',
            )
            .replace(
                "lambda env: candidate", "lambda env, *, tag, commit, base, archive: candidate"
            )
        )
        approve_fixture(monkeypatch, rel, source, expected)
    unresolved = []
    changed = reconcile_release_surface(str(tmp_path), unresolved=unresolved)

    assert unresolved == []
    assert changed == [
        "tests/scripts/test_release_darwin.py",
        "tests/scripts/test_stable_release.py",
    ]
    darwin = (tmp_path / "tests/scripts/test_release_darwin.py").read_text()
    stable = (tmp_path / "tests/scripts/test_stable_release.py").read_text()
    lanes = (tmp_path / "scripts/releases/lanes.py").read_text()
    assert 'assert len(feed["files"]) == len(darwin.ARCHES)' in darwin
    assert 'match="outside its immutable tag archive: windows/arm64"' in stable
    assert "lambda env, *, tag, commit, base, archive: candidate" in stable
    assert 'lanes = set(NAMES) - {"termux"}' in lanes
    for text in (darwin, stable, lanes):
        ast.parse(text)
    assert reconcile_release_surface(str(tmp_path)) == []


def test_unknown_shapes_and_existing_entrypoint_guard_are_untouched(tmp_path):
    unknown = 'def release():\n    assert len(feed["files"]) >= 2\n'
    entrypoint = "def release():\n    GITHUB_BODY_LIMIT = 125000\n"
    put(tmp_path, "tests/scripts/test_release_darwin.py", unknown)
    put(tmp_path, "scripts/releases/entrypoint.py", entrypoint)

    assert reconcile_release_surface(str(tmp_path)) == []
    assert (tmp_path / "tests/scripts/test_release_darwin.py").read_text() == unknown
    assert (tmp_path / "scripts/releases/entrypoint.py").read_text() == entrypoint


_SAMPLE_REL = "tests/scripts/test_release_darwin.py"
_SAMPLE = (
    "def test_finalize_publishes_the_feed_pointer_without_any_r2_configuration():\n"
    '    assert len(feed["files"]) == 2\n'
)
_EXPECTED = _SAMPLE.replace("== 2", "== len(darwin.ARCHES)")


@pytest.mark.parametrize("suffix", ["\n# future revision\n", "    audit_future_behavior()\n"])
def test_matching_anchor_in_unreviewed_revision_is_not_rewritten(tmp_path, monkeypatch, suffix):
    approve_fixture(monkeypatch, _SAMPLE_REL, _SAMPLE, _EXPECTED)
    future = _SAMPLE + suffix
    put(tmp_path, _SAMPLE_REL, future)
    unresolved = []
    assert reconcile_release_surface(str(tmp_path), unresolved=unresolved) == []
    assert unresolved == [_SAMPLE_REL]
    assert (tmp_path / _SAMPLE_REL).read_text() == future


def test_reviewed_input_applies_once_and_known_output_is_a_fixed_point(tmp_path, monkeypatch):
    approve_fixture(monkeypatch, _SAMPLE_REL, _SAMPLE, _EXPECTED)
    put(tmp_path, _SAMPLE_REL, _SAMPLE)
    unresolved = []
    assert reconcile_release_surface(str(tmp_path), unresolved=unresolved) == [_SAMPLE_REL]
    assert (tmp_path / _SAMPLE_REL).read_text() == _EXPECTED
    assert reconcile_release_surface(str(tmp_path), unresolved=unresolved) == []
    assert unresolved == []


def test_unknown_output_digest_prevents_all_writes(tmp_path, monkeypatch):
    approve_fixture(monkeypatch, _SAMPLE_REL, _SAMPLE, _EXPECTED + "# not reviewed\n")
    put(tmp_path, _SAMPLE_REL, _SAMPLE)
    unresolved = []
    assert reconcile_release_surface(str(tmp_path), unresolved=unresolved) == []
    assert unresolved == [_SAMPLE_REL]
    assert (tmp_path / _SAMPLE_REL).read_text() == _SAMPLE


def test_failed_later_anchor_does_not_partially_repair_a_file(tmp_path, monkeypatch):
    rel = "tests/scripts/test_stable_release.py"
    source = (
        "def test_transitions_bind_all_arches_identity_version_and_archive():\n"
        '    with pytest.raises(ValueError, match="path encoding"):\n'
        "        plan_transitions(old, traversal, BASE)\n"
    )
    expected = source.replace(
        'match="path encoding"',
        'match="outside its immutable tag archive: windows/arm64"',
    )
    approve_fixture(monkeypatch, rel, source, expected)
    put(tmp_path, rel, source)
    unresolved = []
    assert reconcile_release_surface(str(tmp_path), unresolved=unresolved) == []
    assert unresolved == [rel]
    assert (tmp_path / rel).read_text() == source


@pytest.fixture
def reviewed_workflow(tmp_path, monkeypatch):
    from hundredways import release_replay, updates

    rel = ".github/workflows/desktop-bundled-release.yml"
    source = "control-plane: github\nunsigned: required\nshared: anchor\n"
    put(tmp_path, rel, source)
    path = tmp_path / rel
    monkeypatch.setattr(
        updates,
        "_BUNDLED_WORKFLOW_EDITS",
        (("absent input", "absent output"), ("shared: anchor\n", "changed: shared: anchor\n")),
    )
    monkeypatch.setattr(
        updates,
        "_BUNDLED_LEG_MARKERS",
        (("store", "control-plane: github"), ("receipt", "unsigned: required")),
    )
    monkeypatch.setitem(
        release_replay.REVIEWED_SURFACES,
        rel,
        ("unused", hashlib.sha256(source.encode()).hexdigest(), "unused"),
    )
    return path, source, updates, release_replay, rel


def test_exact_reviewed_arm_workflow_is_already_reconciled(tmp_path, reviewed_workflow):
    path, source, updates, _, _ = reviewed_workflow
    assert updates._reconcile_desktop_release_leg_guards(str(tmp_path)) == []
    assert path.read_text() == source


@pytest.mark.parametrize("missing_marker", [False, True])
def test_reviewed_workflow_exemption_still_fails_closed(
    tmp_path, monkeypatch, reviewed_workflow, missing_marker
):
    path, source, updates, release_replay, rel = reviewed_workflow
    changed = source + "# future revision\n"
    if missing_marker:
        changed = source.replace("unsigned: required", "unsigned: unknown")
        monkeypatch.setitem(
            release_replay.REVIEWED_SURFACES,
            rel,
            ("unused", hashlib.sha256(changed.encode()).hexdigest(), "unused"),
        )
    path.write_text(changed)
    with pytest.raises(updates.DesktopReleaseLegAnchorError):
        updates._reconcile_desktop_release_leg_guards(str(tmp_path))
    assert path.read_text() == changed


def test_unsigned_fixture_is_pinned_too(tmp_path, monkeypatch):
    rel = "tests/ci/test_unsigned_desktop_legs.py"
    source = 'STABLE_RECEIPTS = ("darwin-arm64", "darwin-x64", "win32-bundle")\n'
    expected = 'STABLE_RECEIPTS = ("darwin-arm64", "win32-bundle")\n'
    approve_fixture(monkeypatch, rel, source, expected)
    future = source + "FUTURE_RECEIPT_CHECK = True\n"
    put(tmp_path, rel, future)
    unresolved = []
    assert reconcile_release_surface(str(tmp_path), unresolved=unresolved) == []
    assert unresolved == [rel]
    assert (tmp_path / rel).read_text() == future
    put(tmp_path, rel, source)
    unresolved.clear()
    assert reconcile_release_surface(str(tmp_path), unresolved=unresolved) == [rel]
    assert reconcile_release_surface(str(tmp_path), unresolved=unresolved) == []
    assert unresolved == []


def test_symlink_is_reported_without_modifying_its_target(tmp_path, monkeypatch):
    approve_fixture(monkeypatch, _SAMPLE_REL, _SAMPLE, _EXPECTED)
    outside = tmp_path / "outside.py"
    outside.write_text(_SAMPLE)
    path = tmp_path / _SAMPLE_REL
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)
    unresolved = []
    assert reconcile_release_surface(str(tmp_path), unresolved=unresolved) == []
    assert unresolved == [_SAMPLE_REL]
    assert outside.read_text() == _SAMPLE


def test_conflicts_are_not_duplicated(tmp_path):
    put(tmp_path, _SAMPLE_REL, _SAMPLE + "# unreviewed\n")
    unresolved = ["previous-conflict", _SAMPLE_REL]
    assert reconcile_release_surface(str(tmp_path), unresolved=unresolved) == []
    assert unresolved == ["previous-conflict", _SAMPLE_REL]
