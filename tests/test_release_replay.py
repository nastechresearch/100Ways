from __future__ import annotations

import hashlib

import pytest

from hundredways.release_replay import (
    KNOWN_CONFLICTS,
    REVIEWED_LEGACY_MERGES,
    _fingerprint,
    replay_release_module,
)

REL = "scripts/releases/channel_releases.py"
BASE = "def publish_release(target):\n    return target\n"
OURS = "def publish_release(target):\n    return ('github', target)\n"
THEIRS = "def publish_release(target):\n    return ('upstream', target)\n"


def test_unknown_path_and_future_conflict_fail_closed() -> None:
    assert replay_release_module("future.py", BASE, OURS, THEIRS) is None
    assert replay_release_module(REL, BASE, OURS, THEIRS) is None


@pytest.mark.parametrize(
    "rel",
    [
        "future.py",
        "scripts/releases/future.py",
        "scripts/bundles/future.py",
        "tests/scripts/future.py",
        "tests/scripts/test_release_entrypoint.py",
        "scripts/releases/channel_releases.py",
        "scripts/bundles/channel_artifacts.py",
        "scripts/bundles/release_artifacts.py",
        "scripts/releases/darwin.py",
        "scripts/releases/versioning.py",
    ],
)
def test_unreviewed_path_cannot_merge_even_without_member_collisions(rel: str) -> None:
    base = "VALUE = 1\n"
    ours = base + "\ndef fork_only():\n    return 'fork'\n"
    theirs = base + "\ndef upstream_only():\n    return 'upstream'\n"
    assert replay_release_module(rel, base, ours, theirs) is None


def test_only_frozen_triple_resolves_and_preserves_current_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = (REL, "member:publish_release")
    triple = (_fingerprint(BASE), _fingerprint(OURS), _fingerprint(THEIRS))
    monkeypatch.setitem(KNOWN_CONFLICTS, key, {triple: "ours"})
    monkeypatch.setitem(
        REVIEWED_LEGACY_MERGES,
        REL,
        tuple(hashlib.sha256(text.encode()).hexdigest() for text in (BASE, OURS, THEIRS, OURS)),
    )
    merged = replay_release_module(REL, BASE, OURS, THEIRS)
    assert merged == OURS
    assert "target" in merged and "github" in merged
    changed = THEIRS.replace("upstream", "new-upstream")
    assert replay_release_module(REL, BASE, OURS, changed) is None


def test_known_policy_does_not_become_general_merge_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = (REL, "member:publish_release")
    monkeypatch.setitem(
        KNOWN_CONFLICTS,
        key,
        {(_fingerprint(BASE), _fingerprint(OURS), _fingerprint(THEIRS)): "ours"},
    )
    base = BASE + "\ndef unrelated():\n    return 'base'\n"
    ours = OURS + "\ndef unrelated():\n    return 'ours'\n"
    theirs = THEIRS + "\ndef unrelated():\n    return 'theirs'\n"
    assert replay_release_module(REL, base, ours, theirs) is None


@pytest.fixture
def complete_legacy_pin(monkeypatch: pytest.MonkeyPatch) -> tuple[str, ...]:
    triple = (_fingerprint(BASE), _fingerprint(OURS), _fingerprint(THEIRS))
    monkeypatch.setitem(KNOWN_CONFLICTS, (REL, "member:publish_release"), {triple: "ours"})
    frozen = tuple(hashlib.sha256(text.encode()).hexdigest() for text in (BASE, OURS, THEIRS, OURS))
    monkeypatch.setitem(REVIEWED_LEGACY_MERGES, REL, frozen)
    return frozen


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("addition", ["\n", "\nUNREVIEWED = True\n"])
def test_complete_legacy_pin_rejects_changed_inputs(
    complete_legacy_pin: tuple[str, ...], index: int, addition: str
) -> None:
    inputs = [BASE, OURS, THEIRS]
    inputs[index] += addition
    assert replay_release_module(REL, *inputs) is None


def test_complete_legacy_pin_rejects_unreviewed_output(
    monkeypatch: pytest.MonkeyPatch, complete_legacy_pin: tuple[str, ...]
) -> None:
    monkeypatch.setitem(REVIEWED_LEGACY_MERGES, REL, (*complete_legacy_pin[:3], "0" * 64))
    assert replay_release_module(REL, BASE, OURS, THEIRS) is None
