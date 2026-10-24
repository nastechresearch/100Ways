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
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.request import Request, urlopen

from .commit_stream import CommitStreamDecision, format_telegram_status
from .delivery import SUGGESTIONS, TABLE_KINDS, Plan, plan, plan_table
from .errorfeed import KINDS as FEED_KINDS
from .operation_safety import bound_telegram_text, redact_telegram_text
from .telegram_tables import CHAT_LIMIT, TableError, answer
from .telegram_commands import (
    EDIT,
    LEVELS,
    OPERATE,
    READ,
    REGISTRY,
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
from .telegram_transport import (
    MAX_CHAIN_HOPS,
    MAX_WINDOW_SECONDS,
    ProposalStore,
    apply_block,
    call_api,
    context_excerpt,
    dispatch_workflow,
    edit_rejection,
    land_proposal,
    new_proposal,
    normalize_path,
    parse_block,
    path_rejection,
    plan_renewal,
    py_compiles,
    TelegramDeliveryError,
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
        "window_key": str(data.get("window_key", ""))[:64],
    }


def save_memory(path: str | Path, memory: dict[str, Any]) -> None:
    """Persist only bounded, redacted interaction state."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    safe = {
        "offset": int(memory.get("offset", 0) or 0),
        "events": memory.get("events", [])[-MAX_MEMORY_EVENTS:],
        "window_key": str(memory.get("window_key", ""))[:64],
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
    return call_api(token, method, payload)


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
    lines = [
        "NASTECH / 100WAYS",
        "ERROR REPORT",
        "────────────────────",
        f"Step: {step}",
        f"Error: {error[:1500]}",
    ]
    feed = context.get("error_feed")
    if feed is None:
        lines.append(
            "The failure blocks publication until the next verified run passes. "
            "No cycle comparison was recorded, so nothing here is claimed about what "
            "cleared -- run 100ways errors --runs ... --state ... for that."
        )
        return "\n".join(lines)
    moved = ", ".join(f"{kind} {feed.count(kind)}" for kind in FEED_KINDS if feed.count(kind))
    lines.append("")
    lines.append(f"CYCLE COMPARISON: {len(feed.events)} movement(s) ({moved or 'none'})")
    for event in feed.events[:MAX_LOG_LINES]:
        lines.append(f"• {event.lane}: {event.kind} [{event.cls}]")
    if len(feed.events) > MAX_LOG_LINES:
        lines.append(f"• {len(feed.events) - MAX_LOG_LINES} further movements in the feed.")
    if not feed.complete:
        lines.append(f"• this read was partial: {feed.partial_reason}")
    if feed.unowned:
        lines.append(f"• {len(feed.unowned)} lane(s) still have no owner.")
    lines.append(BOUNDARY_LINE)
    return "\n".join(lines)


def _delivery_view(context: dict[str, Any]) -> Plan:
    """The plan for whatever this run actually measured -- and only that.

    Every family is read from the context by name, so a run that classified reds but never
    scanned links gets a plan whose ``skipped`` says exactly that.  A chat answer built from
    it cannot read as a clean repository, which is the whole reason this goes through the
    planner instead of formatting the context directly.
    """
    return plan(
        verdicts=context.get("red_verdicts"),
        urls=context.get("url_findings"),
        deltas=context.get("surface_deltas"),
        credentials=context.get("credential_findings"),
        orphans=context.get("orphans"),
        feed=context.get("error_feed"),
    )


def format_suggestions(context: dict[str, Any]) -> str:
    result = _delivery_view(context)
    lines = ["NASTECH / 100WAYS", "NEXT ACTIONS", "────────────────────"]
    if not result.suggestions:
        lines.append(
            "Nothing to suggest from what was measured."
            if not result.skipped
            else f"Not measured this run: {', '.join(result.skipped)}."
        )
        lines.append(BOUNDARY_LINE)
        return "\n".join(lines)
    lines.append(f"{len(result.suggestions)} action(s) from: {', '.join(result.measured)}")
    lines.extend(
        f"{i}. [{tip.kind}] {tip.text[:200]} -- {tip.owner[:80]}"
        for i, tip in enumerate(result.suggestions[:MAX_LOG_LINES], start=1)
    )
    if len(result.suggestions) > MAX_LOG_LINES:
        lines.append(
            f"• {len(result.suggestions) - MAX_LOG_LINES} further actions: 100ways suggest."
        )
    if result.skipped:
        lines.append(f"not measured, so nothing suggested there: {', '.join(result.skipped)}")
    lines.append(BOUNDARY_LINE)
    return "\n".join(lines)


def format_issues(context: dict[str, Any]) -> str:
    result = _delivery_view(context)
    lines = ["NASTECH / 100WAYS", "ISSUES THIS WOULD OPEN", "────────────────────"]
    if not result.issues:
        lines.append("No drafted issue: nothing measured earned one.")
    for draft in result.issues[:MAX_LOG_LINES]:
        lines.append(f"• [{draft.severity}] {draft.title[:180]}")
    if len(result.issues) > MAX_LOG_LINES:
        lines.append(f"• {len(result.issues) - MAX_LOG_LINES} further drafts in the plan.")
    lines.append(
        f"measured: {', '.join(result.measured) or 'nothing'}; "
        f"not measured: {', '.join(result.skipped) or 'nothing'}"
    )
    lines.append(
        "Nothing was created. An operator runs 100ways issues --apply --project owner/name "
        "to open them, and only then."
    )
    lines.append(BOUNDARY_LINE)
    return "\n".join(lines)


def format_discuss(context: dict[str, Any]) -> str:
    result = _delivery_view(context)
    lines = ["NASTECH / 100WAYS", "QUESTIONS THIS WOULD ASK", "────────────────────"]
    if not result.discussions:
        lines.append("No drafted discussion: the measured families raised no open question.")
    for draft in result.discussions[:MAX_LOG_LINES]:
        lines.append(f"• {draft.title[:180]}")
    lines.append(
        "A discussion is asked when the engine cannot choose: adopting an upstream name, "
        "or keeping a fork-only file, is a decision, not a finding."
    )
    lines.append(
        "Nothing was created. An operator runs 100ways discuss --apply --project owner/name."
    )
    lines.append(BOUNDARY_LINE)
    return "\n".join(lines)


def format_table(context: dict[str, Any], kind: str) -> str:
    """One page of the table, sized so the transport's footer still fits."""
    chosen = kind.strip().casefold() or SUGGESTIONS
    if chosen not in TABLE_KINDS:
        return usage_for(REGISTRY["table"])
    lines = ["NASTECH / 100WAYS", "TABLE", "────────────────────"]
    result = _delivery_view(context)
    try:
        page = answer(plan_table(result, chosen), limit=CHAT_LIMIT, max_pages=1)[0]
    except TableError as exc:
        return "\n".join([*lines, str(exc), BOUNDARY_LINE])
    lines.append(page)
    lines.append(BOUNDARY_LINE)
    return "\n".join(lines)


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


def _proposal_file(state_path: str) -> Path:
    return Path(state_path).with_name("proposals.json") if state_path else Path("proposals.json")


def load_proposals(path: str | Path) -> ProposalStore:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ProposalStore()
    return ProposalStore(raw if isinstance(raw, dict) else None)


def save_proposals(path: str | Path, store: ProposalStore) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(store.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _header(title: str) -> list[str]:
    return ["NASTECH / 100WAYS", title, "────────────────────"]


def format_pending(store: ProposalStore) -> str:
    lines = _header("PROPOSED EDITS")
    waiting = store.open()
    if not waiting:
        return "\n".join([*lines, "Nothing is waiting. Edit proposes, apply lands it as a PR."])
    for proposal in waiting:
        lines.append(f"• {proposal.id} {proposal.path} ({len(proposal.search)}-line anchor)")
    lines.append("")
    lines.append("apply <id> verifies it and opens a PR; cancel <id> drops it.")
    return "\n".join(lines)


def handle_edit(call: Call) -> str:
    path, _, instruction = _first(call.args).partition(" ")
    if not instruction.strip():
        return usage_for(REGISTRY["edit"])
    clean = normalize_path(path)
    blocked = path_rejection(clean)
    if blocked:
        return "\n".join([*_header("EDIT REFUSED"), blocked])
    target = Path(call.runtime.workspace) / clean if call.runtime.workspace else None
    if target is None or not target.is_file():
        return "\n".join([*_header("EDIT REFUSED"), f"{clean} is not a file in this engine checkout."])
    try:
        current = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return "\n".join([*_header("EDIT REFUSED"), f"Could not read {clean}: {type(error).__name__}"])
    engine = call.runtime.model()
    if engine is None or not getattr(engine, "available", False):
        return "\n".join(
            [
                *_header("EDIT REFUSED"),
                "No OLLAMA_API_KEY is set for this run, so no change was proposed.",
                "A proposal is drafted by the model and verified by the checks; neither is faked.",
            ]
        )
    block = parse_block(engine.edit_block(clean, context_excerpt(current, instruction), instruction))
    if block is None:
        return "\n".join(
            [*_header("EDIT REFUSED"), "The model returned no SEARCH/REPLACE block; nothing was proposed."]
        )
    proposed = apply_block(current, block)
    if proposed is None:
        return "\n".join(
            [
                *_header("EDIT REFUSED"),
                f"That search text does not appear exactly once in {clean}, so it was not applied.",
                "Quote the current lines verbatim, or run Status to see what the tree holds now.",
            ]
        )
    rejection = edit_rejection(clean, current, proposed)
    if rejection:
        return "\n".join([*_header("EDIT REFUSED"), rejection])
    if not py_compiles(clean, proposed):
        return "\n".join([*_header("EDIT REFUSED"), "The proposed edit does not compile; nothing was stored."])
    proposal = new_proposal(
        path=clean,
        instruction=instruction,
        block=block,
        current=current,
        proposed=proposed,
        author=call.request.user_id,
    )
    call.runtime.proposals.add(proposal)
    save_proposals(_proposal_file(call.runtime.state_path), call.runtime.proposals)
    return "\n".join(
        (
            *_header(f"PROPOSED EDIT {proposal.id}"),
            f"{clean} by user {proposal.author or 'unknown'}",
            "",
            proposal.preview,
            "",
            f"Nothing has been written. Send: apply {proposal.id}",
            BOUNDARY_LINE,
        )
    )


def handle_apply(call: Call) -> str:
    proposal = call.runtime.proposals.get(_first(call.args))
    if proposal is None:
        return "No proposed edit with that id. Send pending to list them."
    if proposal.status != "proposed":
        return f"{proposal.id} is already {proposal.status}."
    if proposal.author and proposal.author != call.request.user_id:
        return f"Only the commander who proposed {proposal.id} can land it."
    if not call.runtime.workspace or not call.runtime.test_command:
        return "This run has no engine checkout or check command, so an edit cannot be verified."
    target = Path(call.runtime.workspace) / proposal.path
    try:
        current = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f"{proposal.path} is no longer readable in the checkout; the edit was not applied."
    outcome = land_proposal(
        proposal=proposal,
        workspace=Path(call.runtime.workspace),
        runner=call.runtime.runner,
        repo=call.runtime.repo,
        test_command=call.runtime.test_command,
        current_text=current,
    )
    call.runtime.proposals.mark(proposal.id, "opened" if outcome.ok else "refused", note=outcome.text)
    save_proposals(_proposal_file(call.runtime.state_path), call.runtime.proposals)
    return "\n".join(
        (
            *_header(f"EDIT {'OPENED FOR REVIEW' if outcome.ok else 'REFUSED'}"),
            outcome.text,
            BOUNDARY_LINE,
        )
    )


def handle_cancel(call: Call) -> str:
    proposal = call.runtime.proposals.mark(_first(call.args), "cancelled")
    if proposal is None:
        return "No proposed edit with that id. Send pending to list them."
    save_proposals(_proposal_file(call.runtime.state_path), call.runtime.proposals)
    return f"Cancelled {proposal.id}: {proposal.path} was not written and no branch was pushed."


def handle_stay(call: Call) -> str:
    try:
        requested = int(_first(call.args))
    except ValueError:
        return usage_for(REGISTRY["stay"])
    requested = min(max(requested, 0), MAX_WINDOW_SECONDS)
    if not requested:
        return "stay needs a positive number of seconds, up to " + str(MAX_WINDOW_SECONDS) + "."
    call.runtime.requested_seconds = requested
    call.runtime.extra_seconds = requested
    return (
        f"Listening again for {requested}s even if this window is quiet "
        f"(hop {call.runtime.hop + 1}, ceiling {MAX_CHAIN_HOPS} hops)."
    )


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


def _subprocess_runner(command: Sequence[str]) -> tuple[int, str]:
    """Run a git/gh command for a landing step and return its exit code and output."""
    completed = subprocess.run(
        list(command), capture_output=True, text=True, timeout=900, check=False
    )
    return completed.returncode, (completed.stdout + completed.stderr)[-4_000:]


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
    proposals: ProposalStore = field(default_factory=ProposalStore)
    state_path: str = ""
    workspace: str = ""
    repo: str = ""
    workflow: str = ""
    ref: str = "main"
    github_token: str = ""
    runner: Callable[[Sequence[str]], tuple[int, str]] = _subprocess_runner
    test_command: tuple[str, ...] = ()
    engine: Any = None
    hop: int = 0
    window_seconds: int = 900
    started_at: float = 0.0
    traffic: int = 0
    requested_seconds: int = 0
    extra_seconds: int = 0
    last_window_key: str = ""

    def reload(self) -> str:
        reloaded: list[str] = []
        if self.decision_path and Path(self.decision_path).is_file():
            self.decision = _status_from_file(self.decision_path)
            reloaded.append("decision")
        if self.context_path and Path(self.context_path).is_file():
            self.context = _load_context(self.context_path)
            reloaded.append("context")
        return ", ".join(reloaded) if reloaded else "no on-disk state to reload"

    @property
    def elapsed(self) -> float:
        return max(0.0, time.time() - (self.started_at or time.time()))

    def model(self) -> Any:
        """The configured AI engine, or None when there is nothing to ask.

        Built on first use because importing and validating the provider is a
        per-deployment concern: a status-only deployment never needs it.
        """
        if self.engine is None:
            try:
                from .ai import AIEngine

                self.engine = AIEngine()
            except Exception:
                return None
        return self.engine


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
    "suggest": lambda call: format_suggestions(call.context),
    "table": lambda call: format_table(call.context, _first(call.args)),
    "issues": lambda call: format_issues(call.context),
    "discuss": lambda call: format_discuss(call.context),
    "pr status": lambda call: format_pr_status(call.context),
    "commit": lambda call: format_commit(call.decision, _first(call.args)),
    "search": lambda call: format_search(call.decision, call.args),
    "explain": lambda call: format_explain(_first(call.args)),
    "log": lambda call: format_log(call.runtime.memory, _first(call.args)),
    "refresh": lambda call: f"Reloaded {call.runtime.reload()}.\n{format_progress(call.decision)}",
    "stay": handle_stay,
    "pending": lambda call: format_pending(call.runtime.proposals),
    "edit": handle_edit,
    "apply": handle_apply,
    "cancel": handle_cancel,
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
    hop: int = 0,
    workspace: str = "",
    repo: str = "",
    workflow: str = "",
    ref: str = "main",
    github_token: str = "",
    test_command: Sequence[str] = (),
    runner: Callable[[Sequence[str]], tuple[int, str]] | None = None,
) -> None:
    """Poll Telegram for a bounded window and answer the chats this deployment trusts."""
    window = min(max(seconds, 0), MAX_POLL_SECONDS)
    started = time.time()
    deadline = started + window
    memory = load_memory(state_path)
    granted = access if access is not None else Access.from_env(default_chat=chat_id)
    runtime = Runtime(
        decision=decision,
        context=context,
        memory=memory,
        access=granted,
        decision_path=decision_path,
        context_path=context_path,
        proposals=load_proposals(_proposal_file(str(state_path))),
        state_path=str(state_path),
        workspace=workspace,
        repo=repo,
        workflow=workflow,
        ref=ref,
        github_token=github_token,
        runner=runner or _subprocess_runner,
        test_command=tuple(test_command),
        hop=max(int(hop), 0),
        window_seconds=window,
        started_at=started,
        last_window_key=str(memory.get("window_key", "")),
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
            runtime.traffic += 1
            _record(memory, "user", request.raw, who=request.user_id, level=level, command=request.name)
            answer = dispatch(request, runtime, level=level)
            if runtime.extra_seconds:
                deadline = min(deadline + runtime.extra_seconds, started + MAX_POLL_SECONDS)
                runtime.extra_seconds = 0
            if not answer and ollama_key:
                answer = _ollama_answer(request.raw, runtime.context, ollama_key)
            if not answer:
                answer = "I can answer status questions. Send Help for the commands."
            _record(memory, "assistant", answer, who=request.user_id, level=level, command=request.name)
            try:
                send_message(token, request.chat_id, answer)
            except TelegramDeliveryError as error:
                # The answer stays in the audit even when Telegram will not take it;
                # failing the job over a delivery refusal would lose the window too.
                _record(memory, "system", f"not delivered: {error}", who=request.user_id, level=level)
            save_memory(state_path, memory)
        save_memory(state_path, memory)
    _renew(runtime, token, chat_id, memory, state_path)


def _tell(token: str, chat_id: str, text: str) -> None:
    """Say something to the owning chat, if this deployment has one to talk to."""
    if not (token and chat_id):
        return
    try:
        send_message(token, chat_id, text)
    except TelegramDeliveryError:
        pass


def _renew(runtime: Runtime, token: str, chat_id: str, memory: dict[str, Any], state_path: str | Path) -> None:
    """Queue the next window only when the chat is actually using these.

    An always-on poll loop would spend the repository's free runner minutes to keep
    a chat open that nobody is reading, so a quiet window closes the chain and the
    scheduled monitor takes over.  The key is recorded in the cached state: a second
    dispatch for the same hop in the same minute is a duplicate, not a retry.
    """
    if not (runtime.workflow and runtime.repo and runtime.github_token):
        # A deployment that did not grant dispatch rights gets no renewal chatter at
        # all; the scheduled monitor is its only window and that is the honest answer.
        return
    plan = plan_renewal(
        hop=runtime.hop,
        elapsed_seconds=runtime.elapsed,
        window_seconds=runtime.window_seconds,
        saw_traffic=runtime.traffic > 0,
        requested_seconds=runtime.requested_seconds,
    )
    if plan.renewal is None:
        if runtime.traffic:
            _tell(token, chat_id, f"Listening window closed: {plan.reason}.")
        return
    key = f"{plan.renewal.hop}-{int(time.time() // 60)}"
    if key == runtime.last_window_key:
        _tell(token, chat_id, f"Window {plan.renewal.hop} is already queued for this minute; not queueing twice.")
        return
    inputs = {**plan.renewal.inputs, "key": key, "reason": plan.reason}
    try:
        dispatch_workflow(
            token=runtime.github_token,
            repo=runtime.repo,
            workflow=runtime.workflow,
            ref=runtime.ref,
            inputs=inputs,
        )
    except (ValueError, RuntimeError, OSError) as error:
        _tell(
            token,
            chat_id,
            f"Could not queue the next window: {redact_telegram_text(str(error))[:200]}",
        )
        return
    runtime.last_window_key = key
    memory["window_key"] = key
    save_memory(state_path, memory)
    _tell(
        token,
        chat_id,
        f"Window {plan.renewal.hop} queued for {inputs['seconds']}s ({plan.reason}). Send anything and I will answer.",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded 100Ways Telegram agent")
    parser.add_argument("--token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    parser.add_argument("--chat-id", default=os.environ.get("TELEGRAM_CHAT_ID", ""))
    parser.add_argument("--decision", required=True)
    parser.add_argument("--context", default="")
    parser.add_argument("--state", default="telegram-state/interaction.json")
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--ollama-key", default=os.environ.get("OLLAMA_API_KEY", ""))
    parser.add_argument("--hop", type=int, default=int(os.environ.get("HUNDREDWAYS_WINDOW_HOP", "0") or 0))
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--repo", default=os.environ.get("HUNDREDWAYS_REPO", "nastechresearch/100Ways"))
    parser.add_argument("--workflow", default=os.environ.get("HUNDREDWAYS_WINDOW_WORKFLOW", ""))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF_NAME", "main"))
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--test-command", default=os.environ.get("HUNDREDWAYS_TEST_COMMAND", ""))
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
        hop=args.hop,
        workspace=args.workspace,
        repo=args.repo,
        workflow=args.workflow,
        ref=args.ref,
        github_token=args.github_token,
        test_command=tuple(shlex.split(args.test_command)) if args.test_command else (),
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
