"""Tests for the public GitHub Pages status payload builder.

The Pages payload is the only thing 100Ways publishes to the public
internet.  These tests pin the schema, the safety boundary (no secrets,
chat IDs, or raw logs), and the state-machine rendering.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.build_pages_status import build


SCHEMA = "100ways.public-status.v1"


def _decision(**overrides):
    base = {
        "baseline_sha": "a" * 40,
        "merged_baseline_sha": "a" * 40,
        "candidate_baseline_sha": "b" * 40,
        "upstream_sha": "c" * 40,
        "pending_commits": 23,
        "threshold": 50,
        "status": "warming",
        "trigger_sync": False,
    }
    base.update(overrides)
    return base


def _context(**overrides):
    base = {"verified": "Latest monitor run", "runUrl": "https://example/runs/1"}
    base.update(overrides)
    return base


def test_payload_schema_is_pinned():
    payload = build(_decision(), _context())
    assert payload["schema"] == SCHEMA


def test_payload_never_contains_secret_tokens():
    """No tokens, chat IDs, raw log paths, or env-style secrets leak.

    The privacy notice uses the literal word "secrets" by design — that's
    the published boundary, not a leaked credential.  This test only blocks
    credential-shaped substrings.
    """
    payload = build(_decision(), _context())
    serialized = json.dumps(payload, sort_keys=True)
    forbidden = (
        "ghp_",
        "github_pat_",
        "gho_",
        "glpat-",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "OLLAMA_API_KEY",
        "/home/runner/work/_temp",
    )
    for needle in forbidden:
        assert needle not in serialized, f"{needle!r} must never appear in payload"


def test_payload_never_contains_private_log_filenames():
    payload = build(_decision(), _context())
    serialized = json.dumps(payload)
    # These are the names the analyzer writes; never published.
    forbidden_logs = ("source-fetch.log", "commit-stream.log")
    for log_name in forbidden_logs:
        assert log_name not in serialized


def test_payload_shortens_shas_to_twelve_chars():
    """All SHAs in the payload are truncated to 12 hex chars regardless of input length."""
    payload = build(
        _decision(
            upstream_sha="d" * 40,
            baseline_sha="e" * 40,
            merged_baseline_sha="e" * 40,
            candidate_baseline_sha="f" * 40,
        ),
        _context(),
    )
    assert payload["upstreamSha"] == "d" * 12
    assert payload["baselineSha"] == "e" * 12
    assert payload["candidateSha"] == "f" * 12


def test_payload_handles_missing_candidate_sha():
    payload = build(_decision(candidate_baseline_sha=""), _context())
    assert payload["candidateSha"] == "—"


def test_payload_renders_threshold_reached_state():
    payload = build(_decision(status="threshold-reached", pending_commits=75), _context())
    assert payload["state"] == "THRESHOLD REACHED"
    assert "full verification chain" in payload["stateDetail"].lower()


def test_payload_renders_current_state():
    payload = build(_decision(status="current", pending_commits=0), _context())
    assert payload["state"] == "CURRENT"
    assert "no new upstream commits" in payload["stateDetail"].lower()


def test_payload_renders_awaiting_review_state():
    payload = build(_decision(status="awaiting-review"), _context())
    assert payload["state"] == "AWAITING REVIEW"
    assert "open candidate" in payload["stateDetail"].lower()


def test_payload_remaining_clamps_at_zero_when_pending_exceeds_threshold():
    payload = build(_decision(pending_commits=200, threshold=50), _context())
    assert payload["remaining"] == 0


def test_payload_sync_state_reflects_trigger():
    payload = build(_decision(trigger_sync=True), _context())
    assert payload["syncState"] == "ENABLED"
    payload = build(_decision(trigger_sync=False), _context())
    assert payload["syncState"] == "ON HOLD"


def test_payload_gates_default_when_context_omitted():
    payload = build(_decision(), {})
    assert isinstance(payload["gates"], list)
    assert len(payload["gates"]) >= 4
    assert all(isinstance(item, list) and len(item) == 2 for item in payload["gates"])


def test_payload_caps_gates_at_twelve_entries():
    gates = [["gate"] * 2] * 25  # 25 entries, each [name, status]
    payload = build(_decision(), {"gates": gates})
    assert len(payload["gates"]) == 12


def test_payload_caps_history_at_twelve_entries():
    history = [{"title": f"event-{i}", "detail": "x"} for i in range(30)]
    payload = build(_decision(), {"history": history})
    assert len(payload["history"]) == 12


def test_payload_handles_garbage_history_gracefully():
    history = [
        {"title": "ok", "detail": "fine"},
        "not-a-dict",
        42,
        {"title": "also ok"},
    ]
    payload = build(_decision(), {"history": history})
    assert len(payload["history"]) == 2  # only dict entries survive


def test_payload_includes_publication_and_privacy_boundaries():
    payload = build(_decision(), _context())
    assert "Public sanitized evidence only" in payload["privacy"]
    assert "No automatic merge, tag, release, or deployment" in payload["publication"]


def test_payload_handles_non_int_pending():
    payload = build(_decision(pending_commits="not-a-number"), _context())
    assert payload["pending"] == 0  # coerced to 0 via int(0 or 0)


def test_payload_default_run_url_is_repo_actions_page():
    payload = build(_decision(), {})
    assert payload["runUrl"].startswith("https://github.com/")


def test_payload_is_json_serializable_roundtrip():
    payload = build(_decision(), _context())
    roundtripped = json.loads(json.dumps(payload, sort_keys=True))
    assert roundtripped == payload


def test_payload_generated_at_is_iso8601_utc():
    payload = build(_decision(), _context())
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", payload["generatedAt"])
    assert "+00:00" in payload["generatedAt"] or "Z" in payload["generatedAt"]


@pytest.mark.parametrize(
    "raw_state, expected",
    [
        ("warming", "WARMING"),
        ("threshold-reached", "THRESHOLD REACHED"),
        ("current", "CURRENT"),
        ("awaiting-review", "AWAITING REVIEW"),
        ("weird-state", "WEIRD STATE"),
    ],
)
def test_payload_state_uppercases_and_replaces_dashes(raw_state, expected):
    payload = build(_decision(status=raw_state), _context())
    assert payload["state"] == expected
