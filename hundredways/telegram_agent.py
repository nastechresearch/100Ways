"""Bounded Telegram status agent for trusted 100Ways Actions runs.

The agent is informational only. It can explain status, errors, remaining gates,
and PR state, but it cannot authorize or execute merges, tags, releases, or
 deployments. State is deliberately bounded and persisted by the workflow cache.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .commit_stream import CommitStreamDecision, format_telegram_status
from .operation_safety import bound_telegram_text, redact_telegram_text

BUTTONS = (
    ("Status", "Progress"),
    ("Errors", "Remaining"),
    ("PR status", "Help"),
)
MAX_MEMORY_EVENTS = 40
MAX_POLL_SECONDS = 1_200
TELEGRAM_TIMEOUT_SECONDS = 20
OLLAMA_URL = "https://ollama.com/v1/chat/completions"
DEFAULT_OLLAMA_MODEL = "gemma4:31b-cloud"


def reply_keyboard() -> dict[str, Any]:
    """Return a stable, compact reply keyboard for the Telegram chat."""
    return {
        "keyboard": [list(row) for row in BUTTONS],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def load_memory(path: str | Path) -> dict[str, Any]:
    """Load bounded interaction memory; corrupt state fails safe to empty."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"offset": 0, "events": []}
    if not isinstance(data, dict):
        return {"offset": 0, "events": []}
    events = data.get("events", [])
    return {
        "offset": int(data.get("offset", 0) or 0),
        "events": events[-MAX_MEMORY_EVENTS:] if isinstance(events, list) else [],
    }


def save_memory(path: str | Path, memory: dict[str, Any]) -> None:
    """Persist only bounded, redacted interaction state."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    safe = {
        "offset": int(memory.get("offset", 0) or 0),
        "events": memory.get("events", [])[-MAX_MEMORY_EVENTS:],
    }
    target.write_text(json.dumps(safe, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _record(memory: dict[str, Any], kind: str, text: str) -> None:
    memory.setdefault("events", []).append(
        {"at": int(time.time()), "kind": kind, "text": redact_telegram_text(text)[:800]}
    )
    memory["events"] = memory["events"][-MAX_MEMORY_EVENTS:]


def _api(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=TELEGRAM_TIMEOUT_SECONDS) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed")
    return result


def send_message(token: str, chat_id: str, text: str, *, keyboard: bool = True) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": bound_telegram_text(text),
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = reply_keyboard()
    _api(token, "sendMessage", payload)


def _status_from_file(path: str | Path) -> CommitStreamDecision:
    return CommitStreamDecision(**json.loads(Path(path).read_text(encoding="utf-8")))


def format_progress(decision: CommitStreamDecision) -> str:
    subjects = decision.subjects[:5]
    lines = [
        "NASTECH / 100WAYS",
        "UPSTREAM PROGRESS",
        "────────────────────",
        f"Pending: {decision.pending_commits}/{decision.threshold}",
        f"Remaining: {max(0, decision.threshold - decision.pending_commits)}",
        f"Baseline: {decision.merged_baseline_sha[:12]}",
        f"Upstream: {decision.upstream_sha[:12]}",
        "",
        "Latest detected commits:",
    ]
    lines.extend(f"• {subject}" for subject in subjects or ["No new commits detected."])
    return "\n".join(lines)


def format_remaining(decision: CommitStreamDecision, context: dict[str, Any]) -> str:
    gates = context.get("gates", [])
    lines = [
        "NASTECH / 100WAYS",
        "REMAINING WORK",
        "────────────────────",
        f"Threshold: {decision.pending_commits}/{decision.threshold}",
        f"Full sync: {'ENABLED' if decision.trigger_sync else 'ON HOLD'}",
        "",
        "Publication boundary:",
        "All verification gates must PASS before #344 may create/update a PR.",
        "Automatic merge, tag, release, and deployment are forbidden.",
    ]
    if gates:
        lines.extend(("", "Gate status:", *(f"• {g}" for g in gates[:12])))
    return "\n".join(lines)


def format_errors(context: dict[str, Any]) -> str:
    error = str(context.get("last_error") or "No recorded error in this run.")
    step = str(context.get("failed_step") or "Unknown step")
    return "\n".join(
        (
            "NASTECH / 100WAYS",
            "ERROR REPORT",
            "────────────────────",
            f"Step: {step}",
            f"Error: {error[:1500]}",
            "",
            "The failure blocks publication until the next verified run passes.",
        )
    )


def format_pr_status(context: dict[str, Any]) -> str:
    return "\n".join(
        (
            "NASTECH / 100WAYS",
            "PULL REQUEST STATUS",
            "────────────────────",
            f"PR: {context.get('pr_url') or 'Not created'}",
            f"State: {context.get('pr_state') or 'Not recorded'}",
            "Publication mode: review PR only.",
            "No automatic merge, tag, release, or deployment.",
        )
    )


def format_help() -> str:
    return "\n".join(
        (
            "NASTECH / 100WAYS",
            "TELEGRAM COMMANDS",
            "────────────────────",
            "Status — current state and real refs",
            "Progress — pending commits and recent subjects",
            "Errors — latest failed step and reason",
            "Remaining — threshold and gate work",
            "PR status — current review PR state",
            "Help — this menu",
            "",
            "AI answers are informational only; merge, tag, release, and deployment "
            "actions remain human-only.",
        )
    )


def _ollama_answer(question: str, context: dict[str, Any], api_key: str) -> str:
    model = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    system = (
        "You are the read-only 100Ways Telegram assistant. Explain current sync status, "
        "errors, remaining gates, commit refs, and PR state clearly. Never claim to have "
        "run an action. Never authorize or suggest bypassing gates, merging, tagging, "
        "releasing, or deploying. Keep answers under 1200 characters."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"Context:\n{json.dumps(context, sort_keys=True)}\nQuestion: {question}",
            },
        ],
        "max_tokens": 700,
    }
    request = Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=45) as response:
        result = json.loads(response.read().decode("utf-8"))
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    return str(content).strip() or "AI returned no answer. Use Status or Help instead."


def response_for(text: str, decision: CommitStreamDecision, context: dict[str, Any]) -> str:
    command = " ".join(text.casefold().strip().split())
    if command == "status":
        return format_telegram_status(decision)
    if command == "progress":
        return format_progress(decision)
    if command == "errors":
        return format_errors(context)
    if command == "remaining":
        return format_remaining(decision, context)
    if command == "pr status":
        return format_pr_status(context)
    if command == "help":
        return format_help()
    return ""


def poll(
    token: str,
    chat_id: str,
    decision: CommitStreamDecision,
    context: dict[str, Any],
    state_path: str | Path,
    *,
    seconds: int = 900,
    ollama_key: str = "",
) -> None:
    """Poll Telegram for a bounded window and answer informational requests."""
    deadline = time.time() + min(max(seconds, 0), MAX_POLL_SECONDS)
    memory = load_memory(state_path)
    while time.time() < deadline:
        result = _api(
            token,
            "getUpdates",
            {"offset": memory["offset"], "timeout": 10, "allowed_updates": ["message"]},
        )
        for update in result.get("result", []):
            memory["offset"] = int(update.get("update_id", 0)) + 1
            message = update.get("message", {})
            if str(message.get("chat", {}).get("id")) != str(chat_id):
                continue
            text = str(message.get("text", "")).strip()
            if not text:
                continue
            _record(memory, "user", text)
            answer = response_for(text, decision, context)
            if not answer and ollama_key:
                answer = _ollama_answer(text, context, ollama_key)
            if not answer:
                answer = "I can answer status questions. Use Help to see the available commands."
            _record(memory, "assistant", answer)
            send_message(token, chat_id, answer)
            save_memory(state_path, memory)
        save_memory(state_path, memory)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded 100Ways Telegram status agent")
    parser.add_argument("--token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    parser.add_argument("--chat-id", default=os.environ.get("TELEGRAM_CHAT_ID", ""))
    parser.add_argument("--decision", required=True)
    parser.add_argument("--context", default="")
    parser.add_argument("--state", default="telegram-state/interaction.json")
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--ollama-key", default=os.environ.get("OLLAMA_API_KEY", ""))
    args = parser.parse_args()
    if not args.token or not args.chat_id:
        return 0
    decision = _status_from_file(args.decision)
    context = {}
    if args.context:
        context = _load_context(args.context)
    poll(
        args.token,
        args.chat_id,
        decision,
        context,
        args.state,
        seconds=args.seconds,
        ollama_key=args.ollama_key,
    )
    return 0


def _load_context(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
