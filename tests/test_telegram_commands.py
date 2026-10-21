"""The Telegram surface is a contract: what parses, who is trusted, what is answered.

These tests hold the registry, the allowlists and the dispatcher together, because a
command that is advertised but not handled, or an action reachable by a stranger who
guessed a chat id, is exactly the failure this agent must not have.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hundredways import telegram_agent
from hundredways.commit_stream import CommitStreamDecision
from hundredways.telegram_agent import Access, HANDLERS, Runtime, dispatch, parse, poll
from hundredways.telegram_commands import (
    COMMANDS,
    EDIT,
    LEVELS,
    MAX_ARG_CHARS,
    OPERATE,
    READ,
    REGISTRY,
    Request,
    bind,
    grants,
    lookup,
    menu,
    rank,
    usage_for,
)


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
        "subjects": [
            "c123ab fix desktop cleanup race",
            "c456cd feat: add scheduled workspace sync",
            "c789ef docs: refresh the settings guide",
        ],
    }
    values.update(overrides)
    return CommitStreamDecision(**values)


def runtime(**overrides) -> Runtime:
    values: dict = {
        "decision": decision(),
        "context": {
            "gates": ["brand parity: PASS", "forkcheck: PASS"],
            "last_error": "",
            "failed_step": "",
            "pr_url": "https://github.com/nastechresearch/nastech-agent/pull/162",
            "pr_state": "open",
        },
        "memory": {"offset": 1, "events": []},
        "access": Access(chats=frozenset({"42"}), operators=frozenset({"7"}), admins=frozenset({"9"})),
    }
    values.update(overrides)
    return Runtime(**values)


def ask(text: str, *, chat="42", user="1"):
    return parse(text, chat_id=chat, user_id=user)


# --- registry integrity -----------------------------------------------------


def test_every_registered_name_resolves_to_one_command():
    names = [command.name for command in COMMANDS]
    assert len(names) == len(set(names))
    for command in COMMANDS:
        for key in (command.name, *command.aliases):
            assert REGISTRY[key.casefold()] is command


def test_every_command_has_a_handler_and_every_handler_is_registered():
    registered = {command.name for command in COMMANDS}

    assert set(HANDLERS) == registered
    assert registered, "an empty registry would answer nothing at all"


def test_declared_arities_are_sane():
    for command in COMMANDS:
        low, high = command.arity
        assert 0 <= low <= high <= 4, command.name
        assert (high > 0) == bool(command.usage), f"{command.name} declares args without usage"


def test_the_menu_only_advertises_levels_the_caller_holds():
    reader = menu((READ,))
    operator = menu((READ, OPERATE))

    assert "- status - Gate, threshold" in reader
    assert "log" not in reader
    assert "log [count]" in operator
    assert "[operate]" in operator


# --- parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Status", "status"),
        ("/status", "status"),
        ("  STATUS   ", "status"),
        ("pr status", "pr status"),
        ("/PR_Status@NasTechBot", "pr status"),
        ("explain gate", "explain"),
        ("what-is receipt", "explain"),
    ],
)
def test_messages_normalize_to_a_registry_name(text, name):
    assert ask(text).name == name


def test_two_word_commands_do_not_swallow_the_first_argument():
    request = ask("pr status now")

    assert request.name == "pr status"
    assert request.words == ("now",)


def test_blank_messages_are_not_commands():
    assert ask("   ").is_blank
    assert dispatch(ask("   "), runtime()) == ""


def test_a_phrase_binding_keeps_one_argument_commands_whole():
    command = REGISTRY["commit"]

    assert bind(command, ask("commit stop retry loop")) == ("stop retry loop",)
    assert bind(REGISTRY["explain"], ask("explain")) == ()
    assert bind(REGISTRY["search"], ask("search a b c d e f"))[-1] == "d e f"


def test_arguments_are_bounded_before_a_handler_sees_them():
    long = "x" * (MAX_ARG_CHARS * 4)

    bound = bind(REGISTRY["search"], ask(f"search {long}"))

    assert all(len(argument) <= MAX_ARG_CHARS for argument in bound)


# --- permissions -----------------------------------------------------------


def test_levels_are_ordered_and_measured_by_rank():
    assert rank(READ) < rank(OPERATE) < rank(EDIT)
    assert grants(EDIT, OPERATE) and not grants(READ, OPERATE)
    assert grants(OPERATE, OPERATE)
    assert not grants(None, READ)


def test_no_allowlist_means_read_only_for_the_configured_chat():
    access = Access(chats=frozenset({"42"}))

    assert access.level_for("42", "7") == READ
    assert access.level_for("77", "7") is None


def test_a_chat_outside_the_allowlist_is_not_answered_at_all():
    access = Access(chats=frozenset({"42"}), admins=frozenset({"9"}))

    assert access.level_for("43", "9") is None


def test_operator_and_admin_lists_grant_exactly_what_they_name():
    access = Access(chats=frozenset({"42"}), operators=frozenset({"7"}), admins=frozenset({"9"}))

    assert access.level_for("42", "7") == OPERATE
    assert access.level_for("42", "9") == EDIT
    assert access.level_for("42", "1") == READ


def test_allowlists_ignore_anything_that_is_not_an_id():
    access = Access.from_env(
        default_chat="42",
        env={"TELEGRAM_OPERATORS": "7, 8, not-a-number, gh1234567890123456", "TELEGRAM_ADMINS": "9"},
    )

    assert access.operators == frozenset({"7", "8"})
    assert access.admins == frozenset({"9"})
    assert access.chats == frozenset({"42"})


def test_the_prefixed_alias_is_the_other_source_of_an_allowlist():
    access = Access.from_env(default_chat="", env={"HUNDREDWAYS_TELEGRAM_ADMINS": "9"})

    assert access.admins == frozenset({"9"})


def test_the_secret_name_this_environment_already_uses_also_widens_read():
    access = Access.from_env(default_chat="42", env={"TELEGRAM_ALLOWED_CHAT_IDS": "77, 88"})

    assert access.chats == frozenset({"42", "77", "88"})
    assert access.level_for("88", "1") == READ


def test_an_operate_command_denies_a_reader_with_a_callable_hint():
    answer = dispatch(ask("log 5"), runtime(), level=READ)

    assert "PERMISSION REQUIRED" in answer
    assert "TELEGRAM_OPERATORS" in answer
    assert "Whoami" in answer


def test_an_operate_command_answers_a_named_operator():
    answer = dispatch(ask("log 5"), runtime(), level=OPERATE)

    assert "COMMAND AUDIT" in answer


# --- dispatch --------------------------------------------------------------


def test_unknown_slash_commands_are_corrected_rather_than_sent_to_the_model():
    answer = dispatch(ask("/statuse"), runtime(), level=READ)

    assert "UNKNOWN COMMAND" in answer
    assert "- status" in answer


def test_plain_prose_is_left_for_the_model_to_answer():
    assert dispatch(ask("how close are we to shipping"), runtime(), level=READ) == ""


def test_wrong_argument_count_returns_usage_not_a_guess():
    assert "takes 1-2 argument" in dispatch(ask("explain"), runtime())
    assert "no arguments" in dispatch(ask("status extra words here"), runtime())


def test_read_commands_answer_from_the_run_state():
    assert "NASTECH / 100WAYS" in dispatch(ask("status"), runtime())
    assert "Pending: 11/50" in dispatch(ask("progress"), runtime())
    assert "brand parity: PASS" in dispatch(ask("gates"), runtime())
    assert "pull/162" in dispatch(ask("pr status"), runtime())
    assert "No recorded error" in dispatch(ask("errors"), runtime())


def test_commit_and_search_read_the_detected_subjects():
    assert "feat: add scheduled workspace sync" in dispatch(ask("search scheduled"), runtime())
    assert "Not among the commits" in dispatch(ask("commit c0ffee nonsense"), runtime())
    assert "c456cd" in dispatch(ask("commit c456"), runtime())


def test_explain_defines_the_words_the_pipeline_uses():
    assert "must be pending" in dispatch(ask("explain threshold"), runtime())
    assert "Known terms:" in dispatch(ask("explain zzz"), runtime())


def test_whoami_shows_the_ids_the_operator_needs_to_allowlist():
    answer = dispatch(ask("whoami", chat="42", user="7"), runtime(), level=OPERATE)

    assert "Your chat id: 42" in answer
    assert "Your user id: 7" in answer
    assert "Granted level: operate" in answer
    assert "never send a token" in answer


def test_the_audit_line_records_who_asked_and_at_what_level():
    memory = {"offset": 1, "events": []}
    agent_runtime = runtime(memory=memory)
    telegram_agent._record(memory, "user", "log 3", who="9", level=EDIT, command="log")

    assert memory["events"][0]["who"] == "9"
    assert memory["events"][0]["level"] == "edit"
    assert memory["events"][0]["command"] == "log"
    answer = dispatch(ask("log 3", user="9"), agent_runtime, level=EDIT)
    assert "9/edit log" in answer


def test_refresh_re_reads_the_run_files_instead_of_the_startup_snapshot(tmp_path):
    first = tmp_path / "decision.json"
    second = tmp_path / "context.json"
    first.write_text(json.dumps(decision(pending_commits=7).to_dict()), encoding="utf-8")
    second.write_text(json.dumps({"gates": ["late gate: PASS"]}), encoding="utf-8")
    agent_runtime = Runtime(
        decision=decision(pending_commits=11),
        context={},
        decision_path=str(first),
        context_path=str(second),
    )

    answer = dispatch(ask("refresh"), agent_runtime)

    assert "Reloaded decision, context" in answer
    assert "Pending: 7/50" in answer
    assert "late gate: PASS" in dispatch(ask("gates"), agent_runtime)


# --- the poll loop's authorization -----------------------------------------


class FakeApi:
    """Replaces the Telegram HTTP call with one canned batch of updates."""

    def __init__(self, updates):
        self.updates = updates
        self.sent = []

    def __call__(self, token, method, payload):
        if method == "getUpdates":
            batch, self.updates = self.updates, []
            return {"ok": True, "result": batch}
        raise AssertionError(f"unexpected method {method}")


def update(update_id, chat_id, user_id, text):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": int(chat_id)},
            "from": {"id": int(user_id), "first_name": "Nastech"},
            "text": text,
        },
    }


def run_poll(monkeypatch, tmp_path: Path, updates, access):
    api = FakeApi(updates)
    monkeypatch.setattr(telegram_agent, "_api", api)
    sent = []
    monkeypatch.setattr(
        telegram_agent,
        "send_message",
        lambda token, chat_id, text, **kwargs: sent.append((chat_id, text)),
    )
    state = tmp_path / "telegram-state" / "interaction.json"
    poll(
        "token",
        "42",
        decision(),
        {"gates": []},
        state,
        seconds=1,
        access=access,
    )
    return sent, state


def test_the_poll_loop_answers_only_allowed_chats(monkeypatch, tmp_path):
    api_updates = [
        update(1, 42, "1", "status"),
        update(2, 99, "1", "status"),
        update(3, 42, "1", "gates"),
    ]
    sent, state = run_poll(monkeypatch, tmp_path, api_updates, Access(chats=frozenset({"42"})))

    assert [chat for chat, _ in sent] == ["42", "42"]
    assert "NASTECH / 100WAYS" in sent[0][1]


def test_the_poll_loop_never_lets_a_chat_member_reach_an_operate_command(monkeypatch, tmp_path):
    sent, _ = run_poll(
        monkeypatch,
        tmp_path,
        [update(1, 42, "5", "log 5")],
        Access(chats=frozenset({"42"}), operators=frozenset({"7"})),
    )

    assert "PERMISSION REQUIRED" in sent[0][1]


def test_the_poll_loop_persists_a_bounded_redacted_audit(monkeypatch, tmp_path):
    secret = "ghp_" + "A" * 24
    sent, state = run_poll(
        monkeypatch,
        tmp_path,
        [update(1, 42, "7", f"explain threshold {secret}")],
        Access(chats=frozenset({"42"}), operators=frozenset({"7"})),
    )

    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["offset"] == 2
    kinds = [event["kind"] for event in saved["events"]]
    assert kinds == ["user", "assistant"]
    assert secret not in json.dumps(saved)
    assert saved["events"][0]["who"] == "7"


def test_response_for_still_answers_a_trusted_command_line():
    assert "Pending: 11/50" in telegram_agent.response_for("Progress", decision(), {})
