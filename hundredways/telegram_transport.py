"""Transport, windows and self-editing proposals for the Telegram agent.

Three jobs the command registry must not own.

Delivery: Telegram answers a bot that talks too fast with ``429`` and a retry hint,
and a status message that is dropped is worse than one that arrives a second late.
Every send goes through :func:`call_api`, which obeys the hint and never repeats a
failure that would only add load.

Availability: a scheduled Actions window is minutes long, so an operator who wants
the bot to keep listening needs a *bounded, demand-driven* chain of self-dispatches.
:func:`plan_renewal` decides that in one place: quiet windows end the chain, an
explicit request extends it, and hop and wall budgets stop it dead.

Self-editing: ``edit`` and ``apply`` are the only way this engine changes its own
source, and they are built so the strongest thing a commander can ask for is a pull
request a human has to read.  A proposal is a literal SEARCH/REPLACE block that must
match exactly once, it can only touch rule code, the suite has to pass before a
branch is pushed, and no command in this file can merge, tag, release or deploy.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .operation_safety import redact_telegram_text, secret_shaped

API_TIMEOUT_SECONDS = 20
MAX_SEND_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30
DEFAULT_RETRY_AFTER_SECONDS = 3

MAX_WINDOW_SECONDS = 1_200
MAX_CHAIN_HOPS = 8
MAX_CHAIN_SECONDS = 5_400

MAX_PROPOSALS = 6
MAX_EDIT_BYTES = 60_000
MAX_DIFF_CHARS = 4_000
MAX_INSTRUCTION_CHARS = 400
MAX_EXCERPT_CHARS = 3_000

EDITABLE_ROOT = "hundredways"
GUARD_FILES = frozenset(
    {
        "ci_policy.py",
        "integrity.py",
        "operation_safety.py",
        "prepublish.py",
        "prompt_gate.py",
        "receipts.py",
        "verify.py",
    }
)
FORBIDDEN_ARGUMENTS = ("merge", "rebase", "--admin", "--force", "--no-verify")

_REPO = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}\Z")
_WORKFLOW = re.compile(r"[A-Za-z0-9._-]{1,80}\.ya?ml\Z")
_REF = re.compile(r"[A-Za-z0-9._/-]{1,255}\Z")


class TelegramDeliveryError(RuntimeError):
    """A send that Telegram refused; the message never repeats the token."""


@dataclass(frozen=True)
class Block:
    """One literal edit: text that must appear exactly once, and its replacement."""

    search: str
    replace: str


@dataclass(frozen=True)
class Renewal:
    hop: int
    inputs: dict[str, str]


@dataclass(frozen=True)
class Plan:
    """Whether to open another window, and the honest reason when not."""

    renewal: Renewal | None
    reason: str


@dataclass
class Proposal:
    id: str
    path: str
    instruction: str
    search: str
    replace: str
    preview: str
    digest: str
    author: str
    created_at: int
    status: str = "proposed"
    note: str = ""
    pr_url: str = ""


@dataclass(frozen=True)
class Outcome:
    ok: bool
    text: str


def call_api(
    token: str,
    method: str,
    payload: dict[str, Any],
    *,
    timeout: int = API_TIMEOUT_SECONDS,
    attempts: int = MAX_SEND_ATTEMPTS,
    sleeper: Callable[[float], None] = time.sleep,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """POST one Telegram method call, honouring ``retry_after`` on a rate limit.

    Only a throttled or server-side failure is retried: a rejected chat id or a
    malformed payload will fail the same way every time, and hammering it makes the
    outage worse.
    """
    body = json.dumps(payload).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/{method}"
    status = 0
    description = ""
    for attempt in range(1, max(1, attempts) + 1):
        result = {}
        try:
            request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            with opener(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            result = _error_body(error)
            status = int(error.code)
        except (URLError, OSError, ValueError) as error:
            status = 0
            description = type(error).__name__
        if result.get("ok"):
            return result
        status = int(result.get("error_code") or status)
        description = str(result.get("description") or description)[:200]
        throttled = status == 0 or status == 429 or status >= 500
        if throttled and attempt < max(1, attempts):
            sleeper(_retry_after(result))
            continue
        break
    raise TelegramDeliveryError(
        f"Telegram {method} failed with {status or 'no response'}: {redact_telegram_text(description)}"[:600]
    )


def _error_body(error: HTTPError) -> dict[str, Any]:
    try:
        parsed = json.loads(error.read().decode("utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _retry_after(result: dict[str, Any]) -> float:
    parameters = result.get("parameters")
    raw = parameters.get("retry_after") if isinstance(parameters, dict) else 0
    try:
        seconds = float(raw or DEFAULT_RETRY_AFTER_SECONDS)
    except (TypeError, ValueError):
        seconds = float(DEFAULT_RETRY_AFTER_SECONDS)
    return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))


def dispatch_workflow(
    *,
    token: str,
    repo: str,
    workflow: str,
    ref: str,
    inputs: dict[str, str],
    opener: Callable[..., Any] = urlopen,
    timeout: int = API_TIMEOUT_SECONDS,
) -> int:
    """Queue a ``workflow_dispatch`` run and return its HTTP status.

    Every path component is validated before it is placed in a URL: this call is
    made with a runner token that can queue work, so a stray ``..`` or an injected
    second path segment is a privilege escalation, not a typo.
    """
    if not (_REPO.fullmatch(repo) and _WORKFLOW.fullmatch(workflow) and _REF.fullmatch(ref)):
        raise ValueError("refusing to dispatch an unvalidated workflow target")
    if any(part in ("", ".", "..") for part in (*repo.split("/"), *ref.split("/"))):
        raise ValueError("refusing to dispatch a path that walks outside the repository")
    if not token:
        raise ValueError("workflow dispatch needs a token")
    safe = {str(key)[:60]: str(value)[:300] for key, value in inputs.items()}
    body = json.dumps({"ref": ref, "inputs": safe}).encode("utf-8")
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "100ways",
    }
    with opener(Request(url, data=body, headers=headers, method="POST"), timeout=timeout) as response:
        status = int(getattr(response, "status", 0) or response.getcode())
    if status != 204:
        raise RuntimeError(f"workflow dispatch returned HTTP {status}")
    return status


def plan_renewal(
    *,
    hop: int,
    elapsed_seconds: float,
    window_seconds: int,
    saw_traffic: bool,
    requested_seconds: int = 0,
) -> Plan:
    """Decide whether the next window is worth a runner slot.

    A quiet window ends the chain: the scheduled monitor will answer within its own
    cadence, and an idle poll job costs minutes nobody asked for.  Demand keeps it
    alive -- traffic in the tail, or an operator who asked -- and hop and wall budgets
    stop a chain that would otherwise run forever.
    """
    if hop >= MAX_CHAIN_HOPS:
        return Plan(None, f"chain budget used ({MAX_CHAIN_HOPS} hops)")
    if elapsed_seconds >= MAX_CHAIN_SECONDS:
        return Plan(None, f"wall budget used ({MAX_CHAIN_SECONDS // 60} minutes)")
    if not saw_traffic and requested_seconds <= 0:
        return Plan(None, "quiet window closed; the scheduled monitor resumes")
    if requested_seconds > 0:
        granted = min(int(requested_seconds), MAX_WINDOW_SECONDS)
    else:
        granted = min(max(int(window_seconds), 1), MAX_WINDOW_SECONDS)
    return Plan(
        Renewal(hop=hop + 1, inputs={"hop": str(hop + 1), "seconds": str(granted)}),
        f"renewing for {granted}s",
    )


# --- proposals -------------------------------------------------------------


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def normalize_path(path: str) -> str:
    return "/".join(part for part in str(path).replace("\\", "/").split("/") if part not in ("", "."))


def path_rejection(path: str) -> str:
    """Return why this path may never be edited from chat, or "" when it may.

    Checked before the file is read or a model is consulted: rule code that decides
    whether a run passes is not something a chat message can rewrite, and guard
    source should not be shipped to a provider on the chance that it might be.
    """
    raw = str(path).replace("\\", "/")
    clean = normalize_path(raw)
    parts = clean.split("/")
    if raw.startswith("/") or ".." in parts:
        return "path must be relative and inside the engine checkout"
    if parts[0] != EDITABLE_ROOT or len(parts) != 2 or not clean.endswith(".py"):
        return f"only {EDITABLE_ROOT}/*.py rule modules can be edited from chat"
    if parts[-1] in GUARD_FILES:
        return f"{parts[-1]} judges the pipeline and is not editable from chat"
    return ""


def edit_rejection(path: str, current: str, proposed: str) -> str:
    """Return why this edit may not be written, or "" when it may."""
    rejection = path_rejection(path)
    if rejection:
        return rejection
    if not proposed.strip():
        return "the edit produced no content"
    if proposed == current:
        return "the edit changed nothing"
    if len(proposed.encode("utf-8")) > MAX_EDIT_BYTES:
        return f"edited file exceeds {MAX_EDIT_BYTES} bytes"
    if secret_shaped(proposed):
        return "the edit contains credential-shaped text and will not be written"
    return ""


def parse_block(text: str) -> Block | None:
    """Read one SEARCH/REPLACE block, tolerating the code fences models add."""
    body = text.strip()
    fence = re.search(r"```[a-zA-Z]*\n(.*?)\n?```", body, re.DOTALL)
    if fence:
        body = fence.group(1).strip()
    match = re.match(r"(?s)^\s*<<<<<<< *\n(.*?)\n?-------\n(.*?)\n?>>>>>>>", body)
    if not match:
        return None
    return Block(search=match.group(1), replace=match.group(2))


def render_block(search: str, replace: str) -> str:
    return f"<<<<<<<\n{search}\n-------\n{replace}\n>>>>>>>"


def apply_block(current: str, block: Block) -> str | None:
    """Return the edited text, or None unless the search text is unambiguous.

    An exact single match is the whole point: it means what lands is the lines the
    commander read in the preview, not the nearest thing a model meant.
    """
    if not block.search:
        return None
    if current.count(block.search) != 1:
        return None
    return current.replace(block.search, block.replace, 1)


def preview_diff(path: str, current: str, proposed: str) -> str:
    lines = list(
        difflib.unified_diff(
            current.splitlines(),
            proposed.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    text = "\n".join(lines)
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS].rstrip() + "\n… diff truncated"
    return text or "no textual change"


def context_excerpt(text: str, instruction: str, *, limit: int = MAX_EXCERPT_CHARS) -> str:
    """Give a model the lines its edit must land in, never the whole module.

    The context sent to a provider is bounded; handing it a truncated file would
    produce an edit that matches nothing.  Choosing the window around the words the
    commander used keeps the excerpt both small and relevant.
    """
    lines = text.splitlines()
    if len(text) <= limit:
        return text
    wanted = {word.casefold() for word in re.findall(r"[A-Za-z_]{4,}", instruction)} - {
        "the", "this", "that", "with", "from", "should", "would", "please"
    }
    best, score = 0, 0
    for index, line in enumerate(lines):
        hits = sum(1 for word in wanted if word in line.casefold())
        if hits > score:
            best, score = index, hits
    start = max(0, best - 15)
    window: list[str] = []
    used = 0
    for line in lines[start:]:
        used += len(line) + 1
        if used > limit:
            window.append(f"… {len(lines) - start - len(window)} further lines not shown")
            break
        window.append(line)
    return "\n".join(window)


def new_proposal(
    *,
    path: str,
    instruction: str,
    block: Block,
    current: str,
    proposed: str,
    author: str,
    now: int | None = None,
) -> Proposal:
    created = int(time.time()) if now is None else int(now)
    stamp = f"{author}|{path}|{created}|{digest(proposed)}"
    return Proposal(
        id=digest(stamp)[:8],
        path=normalize_path(path),
        instruction=instruction[:MAX_INSTRUCTION_CHARS],
        search=block.search[:MAX_EDIT_BYTES],
        replace=block.replace[:MAX_EDIT_BYTES],
        preview=preview_diff(path, current, proposed),
        digest=digest(proposed),
        author=str(author)[:32],
        created_at=created,
    )


class ProposalStore:
    """The edits this chat proposed and has not yet landed, bounded and redacted."""

    def __init__(self, raw: dict[str, Any] | None = None):
        self.items: list[Proposal] = []
        for entry in (raw or {}).get("items", []):
            if isinstance(entry, dict) and entry.get("id"):
                self.items.append(_proposal_from(entry))
        self.items = self.items[-MAX_PROPOSALS:]

    def add(self, proposal: Proposal) -> None:
        self.items = [item for item in self.items if item.id != proposal.id]
        self.items.append(proposal)
        self.items = self.items[-MAX_PROPOSALS:]

    def get(self, identifier: str) -> Proposal | None:
        wanted = str(identifier).strip()
        for item in self.items:
            if item.id == wanted:
                return item
        return None

    def open(self) -> list[Proposal]:
        return [item for item in self.items if item.status == "proposed"]

    def mark(self, identifier: str, status: str, *, note: str = "", pr_url: str = "") -> Proposal | None:
        proposal = self.get(identifier)
        if proposal is None:
            return None
        proposal.status = status
        if note:
            proposal.note = redact_telegram_text(note)[:400]
        if pr_url:
            proposal.pr_url = pr_url[:300]
        return proposal

    def to_dict(self) -> dict[str, Any]:
        return {"items": [asdict(item) for item in self.items[-MAX_PROPOSALS:]]}


def _proposal_from(entry: dict[str, Any]) -> Proposal:
    known = {key: entry[key] for key in Proposal.__dataclass_fields__ if key in entry}
    return Proposal(**known)  # type: ignore[arg-type]


# --- landing ---------------------------------------------------------------

Runner = Callable[[Sequence[str]], tuple[int, str]]


def _safe_arguments(command: Sequence[str]) -> str:
    """Refuse a runner command that could do more than propose.

    The test command arrives from the job's environment, so it is checked the same
    way an untrusted argument would be: nothing in it may merge, rebase, force or
    skip a hook.
    """
    for argument in command:
        lowered = str(argument).casefold()
        for token in FORBIDDEN_ARGUMENTS:
            if token in lowered:
                return token
    return ""


def py_compiles(path: str, text: str) -> bool:
    """True when the edited module still parses; a syntax error never reaches a PR."""
    try:
        compile(text, path, "exec")
    except SyntaxError:
        return False
    return True


def land_proposal(
    *,
    proposal: Proposal,
    workspace: Path,
    runner: Runner,
    repo: str,
    test_command: Sequence[str],
    current_text: str,
    branch_prefix: str = "telegram-edit",
) -> Outcome:
    """Write one verified edit to a branch and open it for review. Nothing else.

    The order is the safety property: match, compile, test, *then* push.  A failure
    at any step returns before the first ``git`` command runs, so a red edit cannot
    reach a ref, and the only ref this function can ever write is a fresh branch.
    """
    target = workspace / proposal.path
    if not target.is_file():
        return Outcome(False, f"{proposal.path} is not in the checkout, so nothing was written.")
    forbidden = _safe_arguments(test_command)
    if forbidden:
        return Outcome(False, f"the configured check command contains {forbidden!r}; refusing to run it.")
    block = Block(search=proposal.search, replace=proposal.replace)
    proposed = apply_block(current_text, block)
    if proposed is None:
        return Outcome(
            False,
            f"{proposal.path} no longer contains that exact text once, so the edit was not applied. "
            "Propose it again against the current file.",
        )
    rejection = edit_rejection(proposal.path, current_text, proposed)
    if rejection:
        return Outcome(False, rejection)
    if not py_compiles(proposal.path, proposed):
        return Outcome(False, "the edit does not compile; nothing was written.")

    branch = f"{branch_prefix}/{proposal.id}"
    steps = (
        (["git", "checkout", "-b", branch], "could not create the branch"),
        (["git", "add", "--", proposal.path], "could not stage the edit"),
        (["git", "commit", "-m", f"Telegram edit: {proposal.path}"], "could not commit the edit"),
    )
    target.write_text(proposed, encoding="utf-8")
    code, output = runner(test_command)
    if code != 0:
        target.write_text(current_text, encoding="utf-8")
        return Outcome(
            False,
            "the checks failed, so nothing was pushed:\n" + redact_telegram_text(output)[-1_200:],
        )
    for command, failure in steps:
        code, output = runner(command)
        if code != 0:
            target.write_text(current_text, encoding="utf-8")
            return Outcome(False, f"{failure}: {redact_telegram_text(output)[:200]}")
    code, output = runner(["git", "push", "origin", branch])
    if code != 0:
        return Outcome(False, f"the branch could not be pushed: {redact_telegram_text(output)[:200]}")
    body = _pull_request_body(proposal, branch)
    body_path = workspace / f".telegram-edit-{proposal.id}.md"
    body_path.write_text(body, encoding="utf-8")
    code, output = runner(
        [
            "gh", "pr", "create",
            "--repo", repo,
            "--base", "main",
            "--head", branch,
            "--title", f"Telegram edit: {proposal.path}",
            "--body-file", str(body_path),
        ]
    )
    if code != 0:
        return Outcome(False, f"the branch is pushed but no PR was opened: {redact_telegram_text(output)[:200]}")
    url = output.strip().splitlines()[-1] if output.strip() else ""
    return Outcome(True, f"Opened {branch} for review." + (f"\n{url}" if url.startswith("http") else ""))


def _pull_request_body(proposal: Proposal, branch: str) -> str:
    return "\n".join(
        (
            f"## Telegram edit: `{proposal.path}`",
            "",
            f"Requested from chat by Telegram user id `{proposal.author}`.",
            "",
            "### Instruction",
            proposal.instruction or "(none recorded)",
            "",
            "### Diff",
            "",
            "```diff",
            proposal.preview,
            "```",
            "",
            f"Resulting file digest `{proposal.digest}` on branch `{branch}`.",
            "",
            "100Ways created this PR from a commanded edit and ran the engine checks "
            "before pushing. It does not authorize a merge, tag, release or deployment; "
            "a human reviews and merges.",
        )
    )
