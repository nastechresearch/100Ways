from __future__ import annotations

import hashlib

import pytest

import hundredways.release_replay as replay
from hundredways.python_replay import merge_python_members

REL = "tests/scripts/test_release_artifacts.py"
BASE = "VALUE = 0\n"
OURS = BASE + "UPSTREAM = True\n"
THEIRS = BASE + "FORK = True\n"
MERGED = OURS + "\nFORK = True\n"


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        replay.REVIEWED_MEMBER_MERGES,
        REL,
        tuple(
            hashlib.sha256(text.encode("utf-8")).hexdigest()
            for text in (BASE, OURS, THEIRS, MERGED)
        ),
    )


def test_public_entry_accepts_exact_merge_and_fixed_point(pinned: None) -> None:
    assert replay.replay_release_module(REL, BASE, OURS, THEIRS) == MERGED
    assert replay.replay_release_module(REL, BASE, MERGED, THEIRS) == MERGED


@pytest.mark.parametrize("position", range(3))
def test_each_changed_complete_input_is_rejected(pinned: None, position: int) -> None:
    inputs = [BASE, OURS, THEIRS]
    inputs[position] += "# semantically unchanged but unreviewed bytes\n"
    assert replay.replay_release_module(REL, *inputs) is None


def test_changed_output_is_rejected(pinned: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(replay, "merge_python_members", lambda *_args: MERGED + "# unreviewed\n")
    assert replay.replay_release_module(REL, BASE, OURS, THEIRS) is None


def test_inline_validation_comments_stay_with_imports() -> None:
    first = "from fixtures import fixture as fixture  # noqa: PLC0414\n"
    added = "from other import store as store  # noqa: PLC0414\n"
    base = first + "\nVALUE = 0\n"
    ours = first + "\nVALUE = 1\n"
    theirs = added + base
    merged = merge_python_members(base, ours, theirs)
    assert merged is not None
    assert first in merged
    assert added in merged
    assert not any(line.lstrip().startswith("# noqa:") for line in merged.splitlines())
