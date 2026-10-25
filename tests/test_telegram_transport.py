"""Transport tests: retries, dispatch rights, renewal budgets and the landing order."""
from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from hundredways import telegram_transport as transport


class Response:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def telegram_error(code, payload):
    return HTTPError(
        "https://api.telegram.org/bot/x/method",
        code,
        "error",
        {},
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


class Opener:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome if isinstance(outcome, Response) else Response(outcome)


# --- delivery --------------------------------------------------------------


def test_a_rate_limited_send_waits_for_the_hint_it_was_given():
    slept = []
    opener = Opener(
        telegram_error(429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                             "parameters": {"retry_after": 2}}),
        {"ok": True, "result": {"message_id": 7}},
    )

    result = transport.call_api("token", "sendMessage", {"chat_id": "1"}, opener=opener, sleeper=slept.append)

    assert result["result"]["message_id"] == 7
    assert slept == [2.0]
    assert len(opener.requests) == 2


def test_a_retry_hint_longer_than_the_ceiling_is_clamped():
    slept = []
    opener = Opener(
        telegram_error(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 600}}),
        {"ok": True},
    )

    transport.call_api("token", "sendMessage", {}, opener=opener, sleeper=slept.append)

    assert slept == [transport.MAX_RETRY_AFTER_SECONDS]


def test_a_hintless_server_error_uses_a_small_default_wait():
    slept = []
    opener = Opener(telegram_error(502, {"ok": False, "error_code": 502}), {"ok": True})

    transport.call_api("token", "getUpdates", {}, opener=opener, sleeper=slept.append)

    assert slept == [transport.DEFAULT_RETRY_AFTER_SECONDS]


def test_a_rejected_request_is_not_hammered():
    opener = Opener(telegram_error(400, {"ok": False, "error_code": 400, "description": "chat not found"}))

    with pytest.raises(transport.TelegramDeliveryError) as raised:
        transport.call_api("token", "sendMessage", {}, opener=opener, sleeper=lambda seconds: None)

    assert len(opener.requests) == 1
    assert "chat not found" in str(raised.value)


def test_a_network_blip_is_retried_then_reported_without_leaking_the_token():
    secret = "1234567890:AAExampleTokenThatMustNeverBeEchoed"
    opener = Opener(URLError("connection reset"), URLError("connection reset"), URLError("connection reset"))

    with pytest.raises(transport.TelegramDeliveryError) as raised:
        transport.call_api(secret, "getUpdates", {}, opener=opener, sleeper=lambda seconds: None)

    assert len(opener.requests) == 3
    assert secret not in str(raised.value)


def test_a_payload_that_never_succeeds_raises_after_the_budget_not_forever():
    opener = Opener(*[telegram_error(429, {"ok": False, "error_code": 429})] * transport.MAX_SEND_ATTEMPTS)

    with pytest.raises(transport.TelegramDeliveryError):
        transport.call_api("token", "sendMessage", {}, opener=opener, sleeper=lambda seconds: None)

    assert len(opener.requests) == transport.MAX_SEND_ATTEMPTS


# --- dispatch rights -------------------------------------------------------


def test_dispatch_sends_string_inputs_and_reports_the_queued_status():
    opener = Opener(Response({"ignored": 1}, status=204))

    status = transport.dispatch_workflow(
        token="runner-token",
        repo="nastechresearch/100Ways",
        workflow="stage-telegram-window.yml",
        ref="main",
        inputs={"hop": 2, "seconds": 900.5, "reason": "demand"},
        opener=opener,
    )

    assert status == 204
    body = json.loads(opener.requests[0].data.decode("utf-8"))
    assert body["ref"] == "main"
    assert body["inputs"] == {"hop": "2", "seconds": "900.5", "reason": "demand"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"repo": "../other", "workflow": "stage.yml", "ref": "main"},
        {"repo": "nastechresearch/100Ways", "workflow": "../../x.yml", "ref": "main"},
        {"repo": "nastechresearch/100Ways", "workflow": "stage.yml", "ref": "main; rm -rf"},
        {"repo": "a/b/c", "workflow": "stage.yml", "ref": "main"},
    ],
)
def test_a_workflow_target_that_is_not_a_plain_identifier_is_refused(kwargs):
    with pytest.raises(ValueError):
        transport.dispatch_workflow(
            token="t", workflow=kwargs["workflow"], repo=kwargs["repo"], ref=kwargs["ref"],
            inputs={}, opener=Opener(Response({"ok": 1}, status=204)),
        )


def test_dispatch_without_a_token_never_touches_the_network():
    opener = Opener(Response({"ok": 1}, status=204))

    with pytest.raises(ValueError):
        transport.dispatch_workflow(
            token="", repo="nastechresearch/100Ways", workflow="stage.yml", ref="main",
            inputs={}, opener=opener,
        )

    assert opener.requests == []


def test_a_dispatch_that_was_not_queued_is_an_error_not_a_maybe():
    with pytest.raises(RuntimeError):
        transport.dispatch_workflow(
            token="t", repo="nastechresearch/100Ways", workflow="stage.yml", ref="main",
            inputs={}, opener=Opener(Response({"message": "Not Found"}, status=404)),
        )


# --- window budgets --------------------------------------------------------


def test_a_quiet_window_closes_the_chain_instead_of_burning_runners():
    plan = transport.plan_renewal(hop=0, elapsed_seconds=60, window_seconds=900, saw_traffic=False)

    assert plan.renewal is None
    assert "quiet" in plan.reason


def test_demand_renews_with_the_next_hop_and_a_bounded_window():
    plan = transport.plan_renewal(hop=2, elapsed_seconds=100, window_seconds=900, saw_traffic=True)

    assert plan.renewal.hop == 3
    assert plan.renewal.inputs == {"hop": "3", "seconds": "900"}


def test_an_operator_can_renew_a_window_that_stayed_quiet():
    plan = transport.plan_renewal(
        hop=0, elapsed_seconds=10, window_seconds=900, saw_traffic=False, requested_seconds=600
    )

    assert plan.renewal.inputs["seconds"] == "600"


def test_a_requested_window_cannot_exceed_one_window():
    plan = transport.plan_renewal(
        hop=0, elapsed_seconds=10, window_seconds=900, saw_traffic=True, requested_seconds=999_999
    )

    assert plan.renewal.inputs["seconds"] == str(transport.MAX_WINDOW_SECONDS)


def test_the_hop_budget_stops_a_chain_that_would_run_forever():
    plan = transport.plan_renewal(
        hop=transport.MAX_CHAIN_HOPS, elapsed_seconds=60, window_seconds=900, saw_traffic=True
    )

    assert plan.renewal is None
    assert "chain budget" in plan.reason


def test_the_wall_budget_stops_it_even_with_plenty_of_hops_left():
    plan = transport.plan_renewal(
        hop=1, elapsed_seconds=transport.MAX_CHAIN_SECONDS, window_seconds=900, saw_traffic=True
    )

    assert plan.renewal is None
    assert "wall budget" in plan.reason


# --- what may be edited ----------------------------------------------------


def test_rule_modules_are_editable():
    current = "CAP = 30\n"
    assert transport.edit_rejection("hundredways/rules.py", current, "CAP = 40\n") == ""


@pytest.mark.parametrize(
    ("path", "clue"),
    [
        ("hundredways/verify.py", "judges the pipeline"),
        ("hundredways/operation_safety.py", "judges the pipeline"),
        ("hundredways/ci_policy.py", "judges the pipeline"),
        ("tests/test_verify.py", "hundredways/*.py"),
        (".github/workflows/ci.yml", "hundredways/*.py"),
        ("hundredways/assets/icon.png", "hundredways/*.py"),
        ("hundredways/deep/nested.py", "hundredways/*.py"),
        ("/etc/passwd", "relative"),
        ("hundredways/../.github/workflows/ci.yml", "relative"),
    ],
)
def test_paths_outside_rule_code_are_refused_by_name(path, clue):
    assert clue in transport.edit_rejection(path, "x\n", "y\n")


def test_an_edit_that_changes_nothing_is_not_a_proposal():
    assert "changed nothing" in transport.edit_rejection("hundredways/rules.py", "CAP = 30\n", "CAP = 30\n")
    assert "no content" in transport.edit_rejection("hundredways/rules.py", "CAP = 30\n", "   ")


def test_an_oversized_file_is_not_stored():
    big = "z" * (transport.MAX_EDIT_BYTES + 1)
    assert "exceeds" in transport.edit_rejection("hundredways/rules.py", "small", big)


def test_a_credential_in_a_proposed_edit_refuses_the_write_rather_than_redacting_it():
    rejection = transport.edit_rejection(
        "hundredways/rules.py", "TOKEN = ''\n", "TOKEN = 'ghp_abcdefghijklmnopqrstuvwxyz'\n"
    )
    assert "credential-shaped" in rejection


# --- the literal block -----------------------------------------------------


def test_a_block_survives_the_fence_a_model_wraps_it_in():
    raw = "```\n<<<<<<<\nCAP = 30\n-------\nCAP = 40\n>>>>>>>\n```"

    block = transport.parse_block(raw)

    assert block is not None
    assert block.search == "CAP = 30"
    assert block.replace == "CAP = 40"


def test_prose_instead_of_a_block_is_not_an_edit():
    assert transport.parse_block("Sure! I would raise the cap to 40.") is None


def test_the_search_text_must_match_exactly_once():
    assert transport.apply_block("CAP = 30\nCAP = 30\n", transport.Block("CAP = 30", "CAP = 40")) is None
    assert transport.apply_block("CAP = 30\n", transport.Block("", "CAP = 40")) is None
    assert transport.apply_block("A\nCAP = 30\nB\n", transport.Block("CAP = 30", "CAP = 40")) == "A\nCAP = 40\nB\n"


def test_the_excerpt_is_the_whole_file_when_it_fits_and_a_bounded_window_when_it_does_not():
    short = "one\ntwo\n"
    assert transport.context_excerpt(short, "two") == short

    lines = [f"line_{index} = {index}" for index in range(4_000)]
    lines[2_000] = "MAX_WORKERS = 8  # the cap the operator means"
    text = "\n".join(lines)

    excerpt = transport.context_excerpt(text, "raise MAX_WORKERS cap", limit=2_000)

    assert len(excerpt) <= 2_000 + 200
    assert "MAX_WORKERS = 8" in excerpt
    assert "line_0 = 0" not in excerpt


def test_the_preview_is_a_real_diff_and_is_bounded():
    diff = transport.preview_diff("hundredways/rules.py", "CAP = 30\n", "CAP = 40\n")
    assert "-CAP = 30" in diff and "+CAP = 40" in diff

    huge = transport.preview_diff("hundredways/rules.py", "\n".join(str(i) for i in range(5_000)), "one line")
    assert len(huge) <= transport.MAX_DIFF_CHARS + 40
    assert "truncated" in huge


# --- proposals -------------------------------------------------------------


def propose(tmp_path, *, search="CAP = 30", replace="CAP = 40", author="9"):
    target = tmp_path / "hundredways"
    target.mkdir(parents=True, exist_ok=True)
    path = target / "rules.py"
    path.write_text(search + "\n", encoding="utf-8")
    block = transport.Block(search=search, replace=replace)
    current = path.read_text(encoding="utf-8")
    return transport.new_proposal(
        path="hundredways/rules.py",
        instruction="raise the cap",
        block=block,
        current=current,
        proposed=transport.apply_block(current, block),
        author=author,
    )


class Runner:
    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    def __call__(self, command):
        self.calls.append(list(command))
        for needle, outcome in self.results.items():
            if needle in " ".join(command):
                return outcome
        return (0, "https://github.com/nastechresearch/100Ways/pull/99")


def test_the_store_keeps_only_the_newest_proposals_and_round_trips_through_json(tmp_path):
    store = transport.ProposalStore()
    for index in range(transport.MAX_PROPOSALS + 4):
        proposal = propose(tmp_path, author=str(index))
        proposal.id = f"id{index}"
        proposal.created_at = index
        store.add(proposal)

    raw = json.loads(json.dumps(store.to_dict()))
    restored = transport.ProposalStore(raw)

    assert len(store.items) == transport.MAX_PROPOSALS
    assert [item.id for item in restored.items] == [item.id for item in store.items]
    assert restored.get("id2") is None and restored.get(store.items[-1].id) is not None


def test_a_store_that_cannot_be_parsed_starts_empty(tmp_path):
    assert transport.ProposalStore({"items": ["nonsense", {"nope": 1}]}).items == []


def test_landing_writes_tests_then_pushes_in_that_order(tmp_path):
    proposal = propose(tmp_path)
    runner = Runner()

    outcome = transport.land_proposal(
        proposal=proposal,
        workspace=tmp_path,
        runner=runner,
        repo="nastechresearch/100Ways",
        test_command=("python3", "-m", "pytest", "tests", "-q"),
        current_text=(tmp_path / "hundredways" / "rules.py").read_text(encoding="utf-8"),
    )

    assert outcome.ok
    joined = [" ".join(call) for call in runner.calls]
    assert joined[0].startswith("python3 -m pytest")
    assert "git checkout -b telegram-edit/" in joined[1]
    assert "git push origin telegram-edit/" in joined[-2]
    assert joined[-1].startswith("gh pr create")
    assert "pull/99" in outcome.text
    assert (tmp_path / "hundredways" / "rules.py").read_text(encoding="utf-8") == "CAP = 40\n"


def test_no_command_this_function_can_build_merges_forces_or_targets_main(tmp_path):
    proposal = propose(tmp_path)
    runner = Runner()

    transport.land_proposal(
        proposal=proposal,
        workspace=tmp_path,
        runner=runner,
        repo="nastechresearch/100Ways",
        test_command=("python3", "-m", "pytest"),
        current_text=(tmp_path / "hundredways" / "rules.py").read_text(encoding="utf-8"),
    )

    for call in runner.calls:
        text = " ".join(call).casefold()
        assert " merge" not in text
        assert "--force" not in text
        assert "push origin main" not in text


def test_a_red_check_restores_the_file_and_never_pushes(tmp_path):
    proposal = propose(tmp_path)
    runner = Runner({"pytest": (1, "2 failed\nassert CAP == 30")})

    outcome = transport.land_proposal(
        proposal=proposal,
        workspace=tmp_path,
        runner=runner,
        repo="nastechresearch/100Ways",
        test_command=("python3", "-m", "pytest"),
        current_text=(tmp_path / "hundredways" / "rules.py").read_text(encoding="utf-8"),
    )

    assert not outcome.ok
    assert "checks failed" in outcome.text
    assert runner.calls == [["python3", "-m", "pytest"]]
    assert (tmp_path / "hundredways" / "rules.py").read_text(encoding="utf-8") == "CAP = 30\n"


def test_a_file_that_moved_under_the_proposal_is_not_edited(tmp_path):
    proposal = propose(tmp_path)
    runner = Runner()
    (tmp_path / "hundredways" / "rules.py").write_text("CAP = 99\n", encoding="utf-8")

    outcome = transport.land_proposal(
        proposal=proposal,
        workspace=tmp_path,
        runner=runner,
        repo="nastechresearch/100Ways",
        test_command=("python3", "-m", "pytest"),
        current_text="CAP = 99\n",
    )

    assert not outcome.ok
    assert "no longer contains that exact text" in outcome.text
    assert runner.calls == []


def test_a_check_command_carrying_a_forbidden_verb_is_refused_before_it_runs(tmp_path):
    proposal = propose(tmp_path)
    runner = Runner()

    outcome = transport.land_proposal(
        proposal=proposal,
        workspace=tmp_path,
        runner=runner,
        repo="nastechresearch/100Ways",
        test_command=("git", "merge", "origin/main"),
        current_text=(tmp_path / "hundredways" / "rules.py").read_text(encoding="utf-8"),
    )

    assert not outcome.ok
    assert "'merge'" in outcome.text
    assert runner.calls == []


def test_an_edit_that_does_not_compile_stops_before_the_checks(tmp_path):
    proposal = propose(tmp_path, replace="CAP = ((((")
    proposal.search = "CAP = 30"
    runner = Runner()

    outcome = transport.land_proposal(
        proposal=proposal,
        workspace=tmp_path,
        runner=runner,
        repo="nastechresearch/100Ways",
        test_command=("python3", "-m", "pytest"),
        current_text="CAP = 30\n",
    )

    assert not outcome.ok
    assert "does not compile" in outcome.text
    assert runner.calls == []


def test_a_push_that_fails_reports_the_branch_and_not_a_pr(tmp_path):
    proposal = propose(tmp_path)
    runner = Runner({"push": (1, "remote: permission denied")})

    outcome = transport.land_proposal(
        proposal=proposal,
        workspace=tmp_path,
        runner=runner,
        repo="nastechresearch/100Ways",
        test_command=("python3", "-m", "pytest"),
        current_text="CAP = 30\n",
    )

    assert not outcome.ok
    assert "could not be pushed" in outcome.text


def test_the_pr_body_carries_the_evidence_a_reviewer_needs(tmp_path):
    proposal = propose(tmp_path)
    body = transport._pull_request_body(proposal, "telegram-edit/abc")

    assert "raise the cap" in body
    assert "```diff" in body
    assert proposal.digest in body
    assert "does not authorize a merge" in body
