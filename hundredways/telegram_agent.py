"""Bounded Telegram agent for trusted 100Ways Actions runs.

Answering is cheap and open to the configured chat: status, progress, errors,
gates, commits and PR state are all read-only and come from the run's own files.
Commanding is not: a message only reaches an action when its Telegram user id is
named in ``TELEGRAM_OPERATORS`` or ``TELEGRAM_ADMINS``, and even then the engine
can only ever open work for review.  No message authorizes a merge, tag, release
or deployment, and nothing here reads a secret it does not need to reply.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from .commit_stream import CommitStreamDecision, format_telegram_status
from .operation_safety import bound_telegram_text, redact_telegram_text
from .telegram_commands import (
    EDIT,
    LEVELS,
    OPERATE,
    READ,
    Access,
    Command,
    Request as CommandRequest,
    bind,
    grants,
    lookup,
    menu,
    parse,
    usage_for,
)

BUTTONS = (
    ("Status", "Progress"),
    ("Errors", "Remaining"),
    ("Gates", "PR status"),
    ("Help", "Whoami"),
)
MAX_MEMORY_EVENTS = 40
MAX_LOG_LINES = 20
MAX_POLL_SECONDS = 1_200
TELEGRAM_TIMEOUT_SECONDS = 20
OLLAMA_URL = "https://ollama.com/v1/chat/completions"
DEFAULT_OLLAMA_MODEL = "gemma4:31b-cloud"
BOUNDARY_LINE = (
    "Read-only surface: no command here merges, tags, releases or deploys, "
    "and none can bypass a gate."
)


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


def _record(
    memory: dict[str, Any],
    kind: str,
    text: str,
    *,
    who: str = "",
    level: str = "",
    command: str = "",
) -> None:
    """Append one bounded audit line: who asked, what they were allowed, what answered."""
    event: dict[str, Any] = {
        "at": int(time.time()),
        "kind": kind,
        "text": redact_telegram_text(text)[:800],
    }
    if who:
        event["who"] = str(who)[:32]
    if level:
        event["level"] = level
    if command:
        event["command"] = str(command)[:40]
    memory.setdefault("events", []).append(event)
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


GLOSSARY: dict[str, str] = {
    "threshold": (
        "How many upstream commits must be pending before a full sync runs. The monitor "
        "counts them every cycle; below the threshold only the review branch moves."
    ),
    "gate": (
        "A verification step that must report PASS before the pipeline may open or "
        "update the review PR: provenance, branding, parity, fork consistency, "
        "publication readiness."
    ),
    "receipt": (
        "A signed record that a gate passed for one exact tree. Receipts allow a review PR "
        "to exist; they never allow a merge, tag, release or deployment."
    ),
    "baseline": (
        "The upstream commit the fork was last verified against. Pending commits are the "
        "ones between that baseline and the upstream head."
    ),
    "parity": (
        "Every file that is not deliberately fork-owned must be byte-identical to upstream "
        "after the branding transform. Parity failures block publication."
    ),
    "branding": (
        "The transform that rewrites upstream names into NasTech ones across paths and "
        "text. Locked assets are renamed but never rewritten."
    ),
    "review": (
        "The only publication mode: a pull request against the fork's main, reviewed and "
        "merged by a human."
    ),
}


def format_gates(context: dict[str, Any]) -> str:
    gates = context.get("gates", [])
    lines = [
        "NASTECH / 100WAYS",
        "VERIFICATION GATES",
        "────────────────────",
    ]
    if not gates:
        return "\n".join([*lines, "No gate results were recorded for this run."])
    lines.extend(f"• {gate}" for gate in gates[:MAX_LOG_LINES])
    if len(gates) > MAX_LOG_LINES:
        lines.append(f"• {len(gates) - MAX_LOG_LINES} further gates in the full report.")
    return "\n".join(lines)


def format_commit(decision: CommitStreamDecision, needle: str) -> str:
    want = needle.casefold().strip()
    matches = [subject for subject in decision.subjects if want in subject.casefold()]
    lines = [
        "NASTECH / 100WAYS",
        "COMMIT LOOKUP",
        "────────────────────",
        f"Query: {want[:120]}",
    ]
    if not matches:
        lines.append(
            "Not among the commits detected in this cycle. Progress lists what was."
        )
        return "\n".join(lines)
    lines.extend(f"• {subject[:200]}" for subject in matches[:5])
    if len(matches) > 5:
        lines.append(f"• {len(matches) - 5} further matches; narrow the query.")
    return "\n".join(lines)


def format_search(decision: CommitStreamDecision, words: tuple[str, ...]) -> str:
    phrase = " ".join(words).casefold().strip()
    matches = [subject for subject in decision.subjects if phrase in subject.casefold()]
    lines = [
        "NASTECH / 100WAYS",
        "COMMIT SEARCH",
        "────────────────────",
        f"Phrase: {phrase[:120]}",
        f"Matches: {len(matches)}/{len(decision.subjects)} detected subjects",
    ]
    lines.extend(f"• {subject[:200]}" for subject in matches[:MAX_LOG_LINES])
    if len(matches) > MAX_LOG_LINES:
        lines.append(f"• {len(matches) - MAX_LOG_LINES} more matches not shown.")
    return "\n".join(lines)


def format_explain(term: str) -> str:
    key = term.casefold().strip()
    lines = ["NASTECH / 100WAYS", "GLOSSARY", "────────────────────"]
    meaning = GLOSSARY.get(key)
    if meaning is None:
        lines.append(f"No entry for {key!r}.")
        lines.append("Known terms: " + ", ".join(sorted(GLOSSARY)))
        return "\n".join(lines)
    lines.append(f"{key}: {meaning}")
    return "\n".join(lines)


def format_log(memory: dict[str, Any], count: str = "5") -> str:
    try:
        wanted = max(1, min(int(count or 5), MAX_LOG_LINES))
    except (TypeError, ValueError):
        wanted = 5
    events = [event for event in memory.get("events", []) if isinstance(event, dict)][-wanted:]
    lines = [
        "NASTECH / 100WAYS",
        "COMMAND AUDIT",
        "────────────────────",
        f"Showing the last {len(events)} recorded event(s).",
    ]
    for event in events:
        stamp = time.strftime("%H:%M:%S", time.gmtime(int(event.get("at", 0) or 0)))
        who = str(event.get("who") or "system")
        level = str(event.get("level") or "read")
        command = str(event.get("command") or event.get("kind") or "event")
        lines.append(f"• {stamp} {who}/{level} {command}: {str(event.get('text', ''))[:120]}")
    if not events:
        lines.append("Nothing recorded yet in this chat's memory.")
    return "\n".join(lines)


def format_whoami(request: CommandRequest, access: Access, level: str | None) -> str:
    lines = [
        "NASTECH / 100WAYS",
        "IDENTITY AND RIGHTS",
        "────────────────────",
        f"Your chat id: {request.chat_id or 'not supplied'}",
        f"Your user id: {request.user_id or 'not supplied'}",
        f"Display name: {request.user_name[:80] or 'not supplied'}",
        f"Granted level: {level or 'none - this chat is not on the allowlist'}",
        "",
        "To raise your level, name your user id in the repository's Actions secrets:",
        "  TELEGRAM_OPERATORS - run checks and read the command audit",
        "  TELEGRAM_ADMINS    - additionally open engine change PRs for review",
        "Set the ids as a comma-separated list; never send a token through chat.",
    ]
    return "\n".join(lines)


def format_help() -> str:
    lines = ["NASTECH / 100WAYS", "TELEGRAM COMMANDS", "────────────────────", menu()]
    lines.extend([
        "",
        "Any other text is answered by the model from this run's context, and the answer",
        "is informational only.",
        BOUNDARY_LINE,
    ])
    return "\n".join(lines)


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


@dataclass
class Runtime:
    """Everything a command may answer from, plus the files to re-read on demand.

    A poll window can outlive the moment its decision was written, so a long-lived
    agent re-reads rather than answering from the snapshot it started with.
    """

    decision: CommitStreamDecision
    context: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    access: Access = field(default_factory=Access)
    decision_path: str = ""
    context_path: str = ""

    def reload(self) -> str:
        reloaded: list[str] = []
        if self.decision_path and Path(self.decision_path).is_file():
            self.decision = _status_from_file(self.decision_path)
            reloaded.append("decision")
        if self.context_path and Path(self.context_path).is_file():
            self.context = _load_context(self.context_path)
            reloaded.append("context")
        return ", ".join(reloaded) if reloaded else "no on-disk state to reload"


@dataclass(frozen=True)
class Call:
    request: CommandRequest
    args: tuple[str, ...]
    level: str | None
    runtime: Runtime

    @property
    def decision(self) -> CommitStreamDecision:
        return self.runtime.decision

    @property
    def context(self) -> dict[str, Any]:
        return self.runtime.context


def _first(args: tuple[str, ...]) -> str:
    return args[0] if args else ""


HANDLERS: dict[str, Callable[[Call], str]] = {
    "status": lambda call: format_telegram_status(call.decision),
    "progress": lambda call: format_progress(call.decision),
    "errors": lambda call: format_errors(call.context),
    "remaining": lambda call: format_remaining(call.decision, call.context),
    "gates": lambda call: format_gates(call.context),
    "pr status": lambda call: format_pr_status(call.context),
    "commit": lambda call: format_commit(call.decision, _first(call.args)),
    "search": lambda call: format_search(call.decision, call.args),
    "explain": lambda call: format_explain(_first(call.args)),
    "log": lambda call: format_log(call.runtime.memory, _first(call.args)),
    "refresh": lambda call: f"Reloaded {call.runtime.reload()}.\n{format_progress(call.decision)}",
    "whoami": lambda call: format_whoami(
        call.request,
        call.runtime.access,
        call.level,
    ),
    "help": lambda call: format_help(),
}


def _denial(command: Command, level: str | None) -> str:
    holder = "TELEGRAM_ADMINS" if command.level == EDIT else "TELEGRAM_OPERATORS"
    return "\n".join(
        (
            "NASTECH / 100WAYS",
            "PERMISSION REQUIRED",
            "────────────────────",
            f"{command.name} needs the {command.level} level; you hold {level or 'none'}.",
            f"An admin must add your user id to the {holder} allowlist.",
            "Send Whoami to see the id this bot was given.",
            BOUNDARY_LINE,
        )
    )


def dispatch(request: CommandRequest, runtime: Runtime, *, level: str | None = READ) -> str:
    """Answer one command, or "" when the message is prose for the model."""
    command = lookup(request)
    if command is None:
        if not request.raw.startswith("/"):
            return ""
        reachable = tuple(step for step in LEVELS if grants(level or "", step))
        return "\n".join(
            (
                "NASTECH / 100WAYS",
                "UNKNOWN COMMAND",
                "────────────────────",
                f"No command named {request.name!r}.",
                menu(reachable),
            )
        )
    if level is None:
        return "This chat is not on the allowlist, so nothing is answered here."
    args = bind(command, request)
    if command.misbinds(request):
        return usage_for(command)
    if not grants(level, command.level):
        return _denial(command, level)
    handler = HANDLERS.get(command.name)
    if handler is None:
        return usage_for(command)
    return handler(Call(request=request, args=args, level=level, runtime=runtime))


def response_for(text: str, decision: CommitStreamDecision, context: dict[str, Any]) -> str:
    """Answer one already-trusted command line; "" means the caller may ask the model."""
    return dispatch(parse(text), Runtime(decision=decision, context=context))


def poll(
    token: str,
    chat_id: str,
    decision: CommitStreamDecision,
    context: dict[str, Any],
    state_path: str | Path,
    *,
    seconds: int = 900,
    ollama_key: str = "",
    access: Access | None = None,
    decision_path: str = "",
    context_path: str = "",
) -> None:
    """Poll Telegram for a bounded window and answer the chats this deployment trusts."""
    deadline = time.time() + min(max(seconds, 0), MAX_POLL_SECONDS)
    memory = load_memory(state_path)
    granted = access if access is not None else Access.from_env(default_chat=chat_id)
    runtime = Runtime(
        decision=decision,
        context=context,
        memory=memory,
        access=granted,
        decision_path=decision_path,
        context_path=context_path,
    )
    while time.time() < deadline:
        result = _api(
            token,
            "getUpdates",
            {"offset": memory["offset"], "timeout": 10, "allowed_updates": ["message"]},
        )
        for update in result.get("result", []):
            memory["offset"] = int(update.get("update_id", 0)) + 1
            message = update.get("message", {})
            sender = message.get("from", {})
            request = parse(
                str(message.get("text", "")),
                chat_id=str(message.get("chat", {}).get("id", "")),
                user_id=str(sender.get("id", "")),
                user_name=str(sender.get("first_name") or sender.get("username") or ""),
            )
            level = granted.level_for(request.chat_id, request.user_id)
            if level is None or request.is_blank:
                # An unknown chat is answered by nobody, so the bot never advertises itself.
                save_memory(state_path, memory)
                continue
            _record(memory, "user", request.raw, who=request.user_id, level=level, command=request.name)
            answer = dispatch(request, runtime, level=level)
            if not answer and ollama_key:
                answer = _ollama_answer(request.raw, runtime.context, ollama_key)
            if not answer:
                answer = "I can answer status questions. Send Help for the commands."
            _record(memory, "assistant", answer, who=request.user_id, level=level, command=request.name)
            send_message(token, request.chat_id, answer)
            save_memory(state_path, memory)
        save_memory(state_path, memory)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded 100Ways Telegram agent")
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
    context = _load_context(args.context) if args.context else {}
    poll(
        args.token,
        args.chat_id,
        decision,
        context,
        args.state,
        seconds=args.seconds,
        ollama_key=args.ollama_key,
        decision_path=args.decision,
        context_path=args.context,
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
