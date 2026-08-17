from __future__ import annotations

import json
from pathlib import Path

from hundredways.commit_stream import CommitStreamDecision
from hundredways.telegram_agent import (
    format_help,
    format_progress,
    load_memory,
    reply_keyboard,
    response_for,
    save_memory,
)
from scripts.build_pages_status import build


def decision(**overrides):
    values = {
        "baseline_sha": "a" * 40,
        "merged_baseline_sha": "a" * 40,
        "candidate_baseline_sha": "b" * 40,
        "upstream_sha": "c" * 40,
        "pending_commits": 11,
        "threshold": 50,
        "status": "warming",
        "trigger_sync": False,
        "subjects": ["c123 fix desktop cleanup"],
    }
    values.update(overrides)
    return CommitStreamDecision(**values)


def test_telegram_status_commands_are_structured_and_safe():
    d = decision()
    assert "NASTECH / 100WAYS" in response_for("Status", d, {})
    assert "Pending: 11/50" in format_progress(d)
    assert "merge" in format_help().lower()
    assert reply_keyboard()["keyboard"] == [
        ["Status", "Progress"],
        ["Errors", "Remaining"],
        ["PR status", "Help"],
    ]


def test_memory_is_bounded_and_recoverable(tmp_path: Path):
    path = tmp_path / "telegram-state" / "interaction.json"
    memory = {"offset": 42, "events": [{"kind": "user", "text": str(i)} for i in range(100)]}
    save_memory(path, memory)
    loaded = load_memory(path)
    assert loaded["offset"] == 42
    assert len(loaded["events"]) == 40
    assert loaded["events"][0]["text"] == "60"


def test_pages_status_is_public_safe_and_uses_merged_baseline():
    payload = build(decision().to_dict(), {"pr_url": "https://github.com/example/1"})
    assert payload["schema"] == "100ways.public-status.v1"
    assert payload["baselineSha"] == "a" * 12
    assert payload["candidateSha"] == "b" * 12
    encoded = json.dumps(payload)
    assert "TELEGRAM_BOT_TOKEN" not in encoded
    assert "ghp_" not in encoded
    assert "github_pat_" not in encoded
    assert payload["publication"].startswith("Review PR only")


def test_pages_and_telegram_workflows_are_wired():
    root = Path(__file__).parents[1]
    pages = (root / ".github/workflows/pages.yml").read_text(encoding="utf-8")
    stream = (root / ".github/workflows/stage-commit-stream.yml").read_text(encoding="utf-8")
    assert "actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9" in pages
    assert "actions/deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128" in pages
    assert "python3 -m hundredways.telegram_agent" in stream
    assert "actions/cache/restore@1bd1e32a3bdc45362d1e726936510720a7c30a57" in stream
    assert "actions/cache/save@1bd1e32a3bdc45362d1e726936510720a7c30a57" in stream
    assert "OLLAMA_API_KEY" in stream
