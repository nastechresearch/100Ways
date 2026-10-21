"""The write verbs: proposing, landing and refusing a change from chat.

These tests exist because ``edit`` and ``apply`` are the only way this engine can
change itself.  What is asserted here is not the happy path -- it is that nothing is
written before a human says apply, that a guard module cannot be edited, that a red
check cannot reach a ref, and that a commander cannot escalate by guessing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hundredways import telegram_agent
from hundredways.commit_stream import CommitStreamDecision
from hundredways.telegram_agent import Access, Runtime, dispatch, parse, poll
from hundredways.telegram_commands import EDIT, OPERATE, READ, REGISTRY, usage_for
from hundredways.telegram_transport import ProposalStore

BLOCK = "<<<<<<<\nCAP = 30\n-------\nCAP = 40\n>>>>>>>"


class FakeEngine:
    def __init__(self, reply=BLOCK, available=True):
        self.reply = reply
        self.available = available
        self.prompts: list[tuple[str, str, str]] = []

    def edit_block(self, path, excerpt, instruction):
        self.prompts.append((path, excerpt, instruction))
        return self.reply


class FakeRunner:
    def __init__(self, outcome=(0, "https://github.com/nastechresearch/100Ways/pull/99")):
        self.outcome = outcome
        self.calls: list[list[str]] = []

    def __call__(self, command):
        self.calls.append(list(command))
        return self.outcome


def workspace(tmp_path: Path) -> str:
    module = tmp_path / "hundredways"
    module.mkdir(parents=True, exist_ok=True)
    (module / "rules.py").write_text("CAP = 30\n", encoding="utf-8")
    (module / "verify.py").write_text("if not parity:\n    raise GateFailure()\n", encoding="utf-8")
    return str(tmp_path)


def edit_runtime(tmp_path: Path, **overrides) -> Runtime:
    values: dict = {
        "decision": CommitStreamDecision(
            baseline_sha="a" * 40,
            merged_baseline_sha="a" * 40,
            candidate_baseline_sha="b" * 40,
            upstream_sha="c" * 40,
            pending_commits=11,
            threshold=50,
            status="warming",
            trigger_sync=False,
            subjects=["c123ab fix desktop cleanup race"],
        ),
        "context": {},
        "memory": {"offset": 1, "events": []},
        "access": Access(chats=frozenset({"42"}), operators=frozenset({"7"}), admins=frozenset({"9"})),
        "workspace": workspace(tmp_path),
        "state_path": str(tmp_path / "telegram-state" / "interaction.json"),
        "repo": "nastechresearch/100Ways",
        "workflow": "stage-telegram-window.yml",
        "github_token": "runner-token",
        "test_command": ("python3", "-m", "pytest", "tests", "-q"),
        "runner": FakeRunner(),
        "engine": FakeEngine(),
    }
    values.update(overrides)
    return Runtime(**values)


def ask(text: str, *, chat="42", user="9"):
    return parse(text, chat_id=chat, user_id=user)


def rule_file(tmp_path: Path) -> Path:
    return tmp_path / "hundredways" / "rules.py"


def first_id(runtime: Runtime) -> str:
    assert runtime.proposals.items, "no proposal was stored"
    return runtime.proposals.items[-1].id


# --- proposing --------------------------------------------------------------


def test_an_edit_is_proposed_with_a_diff_and_writes_nothing(tmp_path):
    runtime = edit_runtime(tmp_path)

    answer = dispatch(ask("edit hundredways/rules.py raise the cap to 40"), runtime, level=EDIT)

    assert "PROPOSED EDIT" in answer
    assert "-CAP = 30" in answer and "+CAP = 40" in answer
    assert "Nothing has been written" in answer
    assert rule_file(tmp_path).read_text(encoding="utf-8") == "CAP = 30\n"


def test_the_model_is_shown_an_excerpt_and_the_commanders_words(tmp_path):
    runtime = edit_runtime(tmp_path)

    dispatch(ask("edit hundredways/rules.py raise the cap to 40"), runtime, level=EDIT)

    path, excerpt, instruction = runtime.engine.prompts[-1]
    assert path == "hundredways/rules.py"
    assert "CAP = 30" in excerpt
    assert instruction == "raise the cap to 40"


def test_a_proposal_outlives_the_window_that_made_it(tmp_path):
    runtime = edit_runtime(tmp_path)
    answer = dispatch(ask("edit hundredways/rules.py raise the cap"), runtime, level=EDIT)

    stored = telegram_agent.load_proposals(telegram_agent._proposal_file(runtime.state_path))

    assert json.dumps(stored.to_dict())
    assert [item.id for item in stored.open()] == [answer.split("PROPOSED EDIT ")[1].split()[0]]
    assert stored.open()[0].author == "9"


def test_a_reader_cannot_propose_an_edit(tmp_path):
    runtime = edit_runtime(tmp_path)

    answer = dispatch(ask("edit hundredways/rules.py raise the cap", user="1"), runtime, level=READ)

    assert "PERMISSION REQUIRED" in answer
    assert "TELEGRAM_ADMINS" in answer
    assert runtime.proposals.items == []


@pytest.mark.parametrize(
    ("engine", "clue"),
    [
        (FakeEngine(available=False), "No OLLAMA_API_KEY"),
        (FakeEngine("Sure, I would raise the cap."), "no SEARCH/REPLACE block"),
        (FakeEngine("<<<<<<<\nCAP = 77\n-------\nCAP = 40\n>>>>>>>"), "does not appear exactly once"),
    ],
)
def test_a_proposal_needs_an_answer_it_can_be_held_to(tmp_path, engine, clue):
    runtime = edit_runtime(tmp_path, engine=engine)

    answer = dispatch(ask("edit hundredways/rules.py raise the cap"), runtime, level=EDIT)

    assert clue in answer
    assert runtime.proposals.items == []
    assert rule_file(tmp_path).read_text(encoding="utf-8") == "CAP = 30\n"


def test_the_modules_that_judge_the_pipeline_cannot_be_edited_from_chat(tmp_path):
    runtime = edit_runtime(tmp_path)

    answer = dispatch(ask("edit hundredways/verify.py drop the parity check"), runtime, level=EDIT)

    assert "judges the pipeline" in answer
    assert runtime.proposals.items == []
    assert runtime.engine.prompts == []


def test_an_edit_without_a_change_asked_for_gets_its_usage_line(tmp_path):
    runtime = edit_runtime(tmp_path)

    assert dispatch(ask("edit hundredways/rules.py"), runtime, level=EDIT) == usage_for(REGISTRY["edit"])


def test_a_path_that_is_not_in_the_checkout_is_refused_by_name(tmp_path):
    runtime = edit_runtime(tmp_path)

    answer = dispatch(ask("edit hundredways/nope.py change it"), runtime, level=EDIT)

    assert "not a file in this engine checkout" in answer


# --- landing ---------------------------------------------------------------


def test_apply_writes_verifies_and_opens_a_pr_for_review(tmp_path):
    runtime = edit_runtime(tmp_path)
    identifier = ""
    dispatch(ask("edit hundredways/rules.py raise the cap"), runtime, level=EDIT)
    identifier = first_id(runtime)

    answer = dispatch(ask(f"apply {identifier}"), runtime, level=EDIT)

    assert "OPENED FOR REVIEW" in answer
    assert "pull/99" in answer
    calls = runtime.runner.calls
    assert calls[0] == ["python3", "-m", "pytest", "tests", "-q"]
    assert any(call[0] == "gh" for call in calls)
    assert runtime.proposals.get(identifier).status == "opened"


def test_a_red_check_refuses_the_landing_and_says_why(tmp_path):
    runtime = edit_runtime(tmp_path, runner=FakeRunner((1, "FAILED tests/test_rules.py::cap\n1 failed")))
    dispatch(ask("edit hundredways/rules.py raise the cap"), runtime, level=EDIT)
    identifier = first_id(runtime)

    answer = dispatch(ask(f"apply {identifier}"), runtime, level=EDIT)

    assert "EDIT REFUSED" in answer
    assert "1 failed" in answer
    assert runtime.proposals.get(identifier).status == "refused"
    assert not any(call[0] in ("git", "gh") for call in runtime.runner.calls)
    assert rule_file(tmp_path).read_text(encoding="utf-8") == "CAP = 30\n"


def test_only_the_commander_who_proposed_an_edit_can_land_it(tmp_path):
    runtime = edit_runtime(tmp_path)
    dispatch(ask("edit hundredways/rules.py raise the cap"), runtime, level=EDIT)
    identifier = first_id(runtime)

    answer = dispatch(ask(f"apply {identifier}", user="8"), runtime, level=EDIT)

    assert "Only the commander who proposed" in answer
    assert runtime.runner.calls == []


def test_an_unknown_or_repeat_id_never_reaches_a_check(tmp_path):
    runtime = edit_runtime(tmp_path)

    assert "No proposed edit with that id" in dispatch(ask("apply nope1234"), runtime, level=EDIT)
    dispatch(ask("edit hundredways/rules.py raise the cap"), runtime, level=EDIT)
    identifier = first_id(runtime)
    dispatch(ask(f"apply {identifier}"), runtime, level=EDIT)

    assert "already opened" in dispatch(ask(f"apply {identifier}"), runtime, level=EDIT)


def test_cancel_drops_a_proposal_without_writing_anything(tmp_path):
    runtime = edit_runtime(tmp_path)
    dispatch(ask("edit hundredways/rules.py raise the cap"), runtime, level=EDIT)
    identifier = first_id(runtime)

    answer = dispatch(ask(f"cancel {identifier}"), runtime, level=EDIT)

    assert f"Cancelled {identifier}" in answer
    assert runtime.runner.calls == []
    assert rule_file(tmp_path).read_text(encoding="utf-8") == "CAP = 30\n"
    assert runtime.proposals.get(identifier).status == "cancelled"


def test_pending_lists_what_is_waiting_and_needs_operate_level(tmp_path):
    editor = edit_runtime(tmp_path)
    dispatch(ask("edit hundredways/rules.py raise the cap"), editor, level=EDIT)

    listed = dispatch(ask("pending", user="7"), editor, level=OPERATE)
    denied = dispatch(ask("pending", user="1"), editor, level=READ)

    assert first_id(editor) in listed
    assert "hundredways/rules.py" in listed
    assert "PERMISSION REQUIRED" in denied


def test_pending_is_honest_when_nothing_is_waiting(tmp_path):
    runtime = edit_runtime(tmp_path)

    assert "Nothing is waiting" in dispatch(ask("pending", user="7"), runtime, level=OPERATE)


# --- staying listening ------------------------------------------------------


def test_stay_asks_for_the_next_window_within_the_ceiling(tmp_path):
    runtime = edit_runtime(tmp_path)

    answer = dispatch(ask("stay 600", user="7"), runtime, level=OPERATE)

    assert "600s" in answer
    assert runtime.requested_seconds == 600
    assert runtime.extra_seconds == 600


def test_stay_cannot_be_used_to_ask_for_an_endless_window(tmp_path):
    runtime = edit_runtime(tmp_path)

    dispatch(ask(f"stay {999_999}", user="7"), runtime, level=OPERATE)

    assert runtime.requested_seconds == telegram_agent.MAX_WINDOW_SECONDS


def test_a_reader_cannot_keep_the_bot_open(tmp_path):
    runtime = edit_runtime(tmp_path)

    assert "PERMISSION REQUIRED" in dispatch(ask("stay 600", user="1"), runtime, level=READ)


def test_nonsense_seconds_get_the_usage_line(tmp_path):
    runtime = edit_runtime(tmp_path)

    assert dispatch(ask("stay soon", user="7"), runtime, level=OPERATE) == usage_for(REGISTRY["stay"])


# --- the poll loop's renewal ------------------------------------------------


class FakeApi:
    def __init__(self, updates):
        self.updates = updates

    def __call__(self, token, method, payload):
        if method == "getUpdates":
            batch, self.updates = self.updates, []
            return {"ok": True, "result": batch}
        raise AssertionError(f"unexpected method {method}")


class FakeDispatch:
    def __init__(self, error=None):
        self.calls: list[dict] = []
        self.error = error

    def __call__(self, *, token, repo, workflow, ref, inputs, **kwargs):
        self.calls.append({"token": token, "repo": repo, "workflow": workflow, "ref": ref, "inputs": dict(inputs)})
        if self.error is not None:
            raise self.error
        return 204


def update(update_id, text, *, chat="42", user="9"):
    return {
        "update_id": update_id,
        "message": {"chat": {"id": int(chat)}, "from": {"id": int(user), "first_name": "Nastech"}, "text": text},
    }


def run_window(monkeypatch, tmp_path, updates, *, dispatch_api=None, **overrides) -> tuple[list, FakeDispatch | None]:
    values: dict = {}
    values.update(overrides)
    runtime = edit_runtime(tmp_path, **values)
    sent: list[tuple[str, str]] = []
    api = FakeApi(updates)
    monkeypatch.setattr(telegram_agent, "_api", api)
    monkeypatch.setattr(
        telegram_agent, "send_message", lambda token, chat_id, text, **kwargs: sent.append((chat_id, text))
    )
    if dispatch_api is not None:
        monkeypatch.setattr(telegram_agent, "dispatch_workflow", dispatch_api)
    runtime_dict = runtime.__dict__
    state = Path(runtime.state_path)
    poll(
        "bot-token",
        "42",
        runtime.decision,
        runtime.context,
        state,
        seconds=1,
        access=runtime.access,
        workspace=runtime_dict["workspace"],
        repo=runtime_dict["repo"],
        workflow=runtime_dict["workflow"],
        github_token=runtime_dict["github_token"],
        hop=runtime_dict["hop"],
    )
    return sent, dispatch_api


def test_a_window_that_was_used_queues_the_next_one(tmp_path, monkeypatch):
    queued = FakeDispatch()

    sent, _ = run_window(monkeypatch, tmp_path, [update(1, "status")], dispatch_api=queued)

    assert [chat for chat, _ in sent] == ["42", "42"]
    assert "Window closed" not in sent[0][1]
    assert queued.calls[0]["inputs"]["hop"] == "1"
    assert queued.calls[0]["workflow"] == "stage-telegram-window.yml"
    assert "queued" in sent[-1][1]


def test_a_quiet_window_ends_the_chain_without_disturbing_anyone(tmp_path, monkeypatch):
    queued = FakeDispatch()

    sent, _ = run_window(monkeypatch, tmp_path, [], dispatch_api=queued)

    assert sent == []
    assert queued.calls == []


def test_the_renewal_key_is_recorded_so_a_hop_is_not_queued_twice(tmp_path, monkeypatch):
    queued = FakeDispatch()
    monkeypatch.setattr(telegram_agent, "dispatch_workflow", queued)
    runtime = edit_runtime(tmp_path)
    memory = {"offset": 9, "events": []}
    runtime.hop = 0
    runtime.traffic = 1
    runtime.window_seconds = 900

    telegram_agent._renew(runtime, "bot-token", "42", memory, Path(runtime.state_path))
    runtime.last_window_key = memory["window_key"]
    telegram_agent._renew(runtime, "bot-token", "42", memory, Path(runtime.state_path))

    assert len(queued.calls) == 1
    assert queued.calls[0]["inputs"]["hop"] == "1"
    assert memory["window_key"]
    assert json.loads(Path(runtime.state_path).read_text(encoding="utf-8"))["window_key"] == memory["window_key"]


def test_a_dispatch_that_fails_is_told_not_raised(tmp_path, monkeypatch):
    sent, _ = run_window(
        monkeypatch,
        tmp_path,
        [update(1, "status")],
        dispatch_api=FakeDispatch(error=RuntimeError("ghp_SecretValueThatMustNotBeEchoed123456")),
    )

    delivered = sent[-1][1]
    assert "Could not queue the next window" in delivered
    assert "ghp_SecretValueThatMustNotBeEchoed123456" not in delivered


def test_a_deployment_without_dispatch_rights_hears_nothing_about_windows(tmp_path, monkeypatch):
    queued = FakeDispatch()

    sent, _ = run_window(monkeypatch, tmp_path, [update(1, "status")], dispatch_api=queued, workflow="")

    assert queued.calls == []
    assert [text for _, text in sent] and "queued" not in sent[-1][1]
