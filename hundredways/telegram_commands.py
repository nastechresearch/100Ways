"""Command registry behind the Telegram agent: one place that says what a message may do.

The agent used to answer six literal strings and everything else went to the model.
This registry is the contract instead: a name, its aliases, the argument budget it
accepts, and the trust level it requires.  Parsing is deterministic and offline so a
malformed command gets a usage line rather than an AI guess, and the `help` text a
commander sees is generated from the same rows the dispatcher uses.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

READ = "read"
OPERATE = "operate"
EDIT = "edit"
LEVELS = (READ, OPERATE, EDIT)

MAX_ARGS = 4
MAX_ARG_CHARS = 200
MAX_WORDS = 40


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    level: str = READ
    aliases: tuple[str, ...] = ()
    arity: tuple[int, int] = (0, 0)
    usage: str = ""

    @property
    def takes_arguments(self) -> bool:
        return self.arity[1] > 0

    def needs(self, supplied: int) -> bool:
        """Only too *few* arguments can reach here; binding caps the most there are."""
        return supplied < self.arity[0]

    def misbinds(self, request: Request) -> bool:
        """True when the caller argued this command wrongly, so it gets a usage line.

        Trailing words are folded into the last slot on purpose -- ``commit stop
        retry loop`` is one subject, not three -- so too *many* is only an error for
        a command that declares no slots at all.  Silently dropping what someone
        typed is worse than telling them the command takes nothing.
        """
        if not self.takes_arguments:
            return bool(request.words)
        return self.needs(len(bind(self, request)))

    def usage_line(self) -> str:
        return f"{self.name} {self.usage}".strip()


@dataclass(frozen=True)
class Request:
    """A normalized incoming message; ``name`` is empty when nothing was addressed."""

    raw: str
    name: str
    words: tuple[str, ...] = ()
    chat_id: str = ""
    user_id: str = ""
    user_name: str = ""

    @property
    def is_blank(self) -> bool:
        return not self.name


COMMANDS: tuple[Command, ...] = (
    Command("status", "Gate, threshold and the real refs behind this run"),
    Command("progress", "Pending commits and the subjects detected this cycle"),
    Command("errors", "The latest failed step and why it failed"),
    Command("remaining", "What is left before a PR may be created or updated"),
    Command("gates", "Every verification gate and its recorded outcome"),
    Command("pr status", "The review PR this run produced", aliases=("pr", "pull-request")),
    Command("commit", "Explain one detected commit", arity=(1, 1), usage="<sha or words>"),
    Command("search", "Find commit subjects matching a phrase", arity=(1, 4), usage="<phrase>"),
    Command("explain", "What a pipeline term means in this fork", arity=(1, 2), usage="<term>",
            aliases=("what-is",)),
    Command("log", "The last commands this chat was given", arity=(0, 1), usage="[count]",
            level=OPERATE),
    Command("refresh", "Re-read the run state from disk before answering", aliases=("reload",)),
    Command("stay", "Keep listening for another bounded window", arity=(1, 1), usage="<seconds>",
            level=OPERATE, aliases=("extend",)),
    Command("pending", "The edits proposed from chat that have not landed", level=OPERATE,
            aliases=("proposals",)),
    Command("edit", "Propose a literal change to an engine rule module", level=EDIT,
            arity=(1, 1), usage="<module.py> <what to change>", aliases=("change",)),
    Command("apply", "Verify a proposed edit, push its branch and open it for review", level=EDIT,
            arity=(1, 1), usage="<id>", aliases=("land",)),
    Command("cancel", "Drop a proposed edit without writing anything", level=EDIT,
            arity=(1, 1), usage="<id>", aliases=("drop",)),
    Command("whoami", "The ids this bot sees and what you are allowed to do", aliases=("id",)),
    Command("help", "This menu", aliases=("commands", "start")),
)


def _index(commands: tuple[Command, ...]) -> dict[str, Command]:
    table: dict[str, Command] = {}
    for command in commands:
        keys = (command.name, *(alias for alias in command.aliases))
        for key in keys:
            normalized = " ".join(key.casefold().split())
            existing = table.get(normalized)
            if existing is not None:
                raise ValueError(f"duplicate telegram command {normalized!r}: {existing.name} vs {command.name}")
            table[normalized] = command
    return table


REGISTRY: dict[str, Command] = _index(COMMANDS)


def _message_tokens(body: str) -> list[str]:
    """Split a command line the way Telegram types it, keeping underscores as spaces.

    ``PR_Status`` and ``PR status`` arrive as the same request: a bot command cannot
    contain a space, so a two-word label is written with an underscore.  Each
    unfolded part remembers the token it came from so the argument words handed to
    ``bind`` keep their original spelling.
    """
    tokens = [token for token in body.casefold().split() if token]
    if tokens:
        tokens[0] = tokens[0].split("@", 1)[0]
    return [token for token in tokens if token]


def _probe(tokens: list[str]) -> list[tuple[str, int]]:
    return [(part, index) for index, token in enumerate(tokens) for part in token.split("_") if part]


def parse(raw: str, *, chat_id: str = "", user_id: str = "", user_name: str = "") -> Request:
    """Normalize an incoming message into a command name plus its remaining words.

    Telegram delivers ``/status``, ``/status@NasTechBot`` and, from the reply
    keyboard, the bare label ``Status`` -- all three name the same command, and the
    resolved name is the canonical one so an alias never reaches a handler or the
    audit log under its own spelling.  Binding to argument slots waits for ``bind``
    so a one-argument command can take a phrase, not just a word.
    """
    text = " ".join((raw or "").strip().split())
    tokens = _message_tokens(text[1:] if text.startswith("/") else text)
    probe = _probe(tokens)
    if not probe:
        return Request(raw=text, name="", chat_id=chat_id, user_id=user_id, user_name=user_name)
    pair = " ".join(part for part, _ in probe[:2])
    command = REGISTRY.get(pair) or REGISTRY.get(probe[0][0])
    if command is None:
        name, taken = tokens[0], 1
    else:
        name = command.name
        takes_two = len(probe) > 1 and pair in REGISTRY
        taken = probe[1][1] + 1 if takes_two else probe[0][1] + 1
    words = tokens[taken:]
    return Request(
        raw=text,
        name=name,
        words=tuple(" ".join(words)[:MAX_ARG_CHARS * MAX_ARGS].split()[:MAX_WORDS]),
        chat_id=chat_id,
        user_id=user_id,
        user_name=user_name,
    )


def bind(command: Command, request: Request) -> tuple[str, ...]:
    """Split a request's words into the argument slots the command declares."""
    high = command.arity[1]
    words = request.words
    if high == 0 or not words:
        return ()
    if high == 1:
        return (" ".join(words)[:MAX_ARG_CHARS],)
    if len(words) <= high:
        return tuple(word[:MAX_ARG_CHARS] for word in words)
    head, tail = words[: high - 1], " ".join(words[high - 1:])
    return tuple(head) + (tail[:MAX_ARG_CHARS],)


def lookup(request: Request) -> Command | None:
    return REGISTRY.get(request.name)


def usage_for(command: Command) -> str:
    low, high = command.arity
    if high == 0:
        return f"{command.name} takes no arguments."
    if low == high:
        return f"{command.name} takes {low} argument(s): {command.usage}."
    return f"{command.name} takes {low}-{high} argument(s): {command.usage}."


def menu(levels: tuple[str, ...] = LEVELS) -> str:
    """Render the menu from the registry so it can never advertise a dead command."""
    lines = ["COMMANDS"]
    for command in COMMANDS:
        if command.level not in levels:
            continue
        marker = "" if command.level == READ else f" [{command.level}]"
        lines.append(f"- {command.usage_line()} - {command.summary}{marker}")
    return "\n".join(lines)


def rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def grants(level: str | None, required: str) -> bool:
    """True when ``level`` meets the permission a command requires."""
    return rank(level or "") >= rank(required)


def _ids(value: str) -> frozenset[str]:
    """Parse an allowlist of Telegram ids; anything odd is dropped, not trusted."""
    kept = []
    for token in value.replace(";", ",").replace(" ", ",").split(","):
        entry = token.strip()
        if entry.isdigit() and len(entry) <= 32:
            kept.append(entry)
    return frozenset(kept)


@dataclass(frozen=True)
class Access:
    """Who this bot answers to, and how far it will go.

    The configured chat is the read surface, widened only by ``TELEGRAM_ALLOW_CHATS``.
    An empty operator or admin list means nobody holds that level, so a deployment
    that sets no allowlists is read-only rather than open.  Chat membership gates every
    level: a stranger sending your admin id to another chat still gets nothing.
    """

    chats: frozenset[str] = frozenset()
    operators: frozenset[str] = frozenset()
    admins: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls, *, default_chat: str = "", env: dict[str, str] | None = None) -> "Access":
        source = env if env is not None else os.environ
        chats = {str(default_chat).strip()} if str(default_chat).strip() else set()
        chats |= _allow(source, "TELEGRAM_ALLOW_CHATS")
        return cls(
            chats=frozenset(chats),
            operators=_allow(source, "TELEGRAM_OPERATORS"),
            admins=_allow(source, "TELEGRAM_ADMINS"),
        )

    def level_for(self, chat_id: str, user_id: str) -> str | None:
        if str(chat_id) not in self.chats:
            return None
        if str(user_id) and str(user_id) in self.admins:
            return EDIT
        if str(user_id) and str(user_id) in self.operators:
            return OPERATE
        return READ


def _allow(source: dict[str, str], name: str) -> frozenset[str]:
    return _ids(source.get(f"HUNDREDWAYS_{name}", "") or source.get(name, ""))
