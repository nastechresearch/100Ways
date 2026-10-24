"""Delivery: an issue is a defect with an owner, a discussion is a question with none.

The planner is where engine measurements become something a person can act on, so the
two properties these tests hold are the two ways this module could mislead: it must not
decide a question that belongs to a human (adoption, fork-only files), and it must not
write -- or appear to write -- anything an operator did not ask for.  Every write test
here runs against a fake ``run``, because the engine's one issue path is
:func:`create_issues` and this suite never talks to GitHub.
"""

from __future__ import annotations

import json

import pytest

from hundredways import delivery as dv
from hundredways import errorfeed as ef
from hundredways.adapters import Orphan, SurfaceDelta
from hundredways.errorfeed import observe
from hundredways.pubconfig import LIVE_MISSING, Finding
from hundredways.red_classifier import OWNERS, RedClass, Verdict
from hundredways.telegram_tables import render as render_table
from hundredways.urlcheck import BRAND_VIOLATION, BROKEN, OK, UNREACHABLE, Link, UrlFinding

A_TOKEN = "ghp_" + "A" * 36


def link(url, path="README.md", line=1) -> UrlFinding:
    return UrlFinding(Link(url, path, line, 1, ""), OK, "fine", "", True)


def judged(url, status, path="README.md", line=1, suggestion="") -> UrlFinding:
    return UrlFinding(Link(url, path, line, 1, ""), status, f"{status} at {line}", suggestion)


def verdict(name, kind=RedClass.UNKNOWN, action="read the log") -> Verdict:
    return Verdict(name, kind, (f"{name} gave no fact",), str(OWNERS[kind]), action)


def credential(name="MS_STORE_SELLER_ID", verdict_kind=LIVE_MISSING) -> Finding:
    return Finding(name, "secret", verdict_kind, ("release",), (), gate="release", note="")


def feed_payload(*names, total=None):
    return {
        "total_count": len(names) if total is None else total,
        "check_runs": [
            {
                "id": 1,
                "name": lane,
                "conclusion": "failure",
                "runner_name": "ubuntu-latest",
                "output": {"title": "", "text": ""},
                "updated_at": "2026-10-21T03:04:05Z",
            }
            for lane in names
        ],
    }


def recorded(*names, previous=None, total=None):
    return observe(feed_payload(*names, total=total), previous=previous)


def measure_all() -> dv.Plan:
    return dv.plan(
        verdicts=[verdict("python-tests"), verdict("js-lint", RedClass.RUNNER_CAPACITY, "rerun")],
        urls=[
            judged("https://nowhere.example/x", BROKEN, suggestion="replace it"),
            judged("https://hermes-agent.nousresearch.com/docs", BRAND_VIOLATION),
            link("https://nastechresearch.org"),
        ],
        deltas=[
            SurfaceDelta("removed", "cli-verb", "sync", ""),
            SurfaceDelta("added", "cli-verb", "", "doctor"),
        ],
        credentials=[credential()],
        orphans=[
            Orphan("agent/stale.py", "deleted-twin", "upstream file gone", "remove or explain"),
            Orphan("nastech/only.py", "unclassified", "no upstream brands here", "confirm"),
        ],
        feed=recorded("python-tests"),
    )


# --- the split the module is named for ---------------------------------------


def test_a_defect_with_one_next_action_is_an_issue_and_a_choice_is_a_question():
    result = measure_all()
    issues = {draft.key for draft in result.issues}
    questions = {draft.key for draft in result.discussions}

    assert "surface-rule-needed" in issues, "a removal the transform must absorb is a defect"
    assert "surface-adopt-or-refuse" in questions, "adopting a new verb is the owner's call"
    assert "orphans-deleted-twin" in issues
    assert "orphans-unexplained" in questions
    assert not questions & issues, "one finding may not be asked for in both places"


def test_the_issue_a_class_earns_is_addressed_to_the_owner_that_class_already_names():
    result = dv.plan(verdicts=[verdict("js-lint", RedClass.RUNNER_CAPACITY)])
    draft = result.issues[0]

    assert str(OWNERS[RedClass.RUNNER_CAPACITY]) in draft.title
    assert "js-lint" in draft.body


def test_only_a_judged_link_is_a_finding():
    result = dv.plan(
        urls=[
            judged("https://a.example", BROKEN),
            judged("https://b.example", BRAND_VIOLATION),
            judged("https://c.example", UNREACHABLE),
            link("https://d.example"),
        ]
    )
    keys = {draft.key for draft in result.issues}

    assert keys == {"urls-brand-violation", "urls-broken"}
    assert all("https://c.example" not in draft.body for draft in result.issues)
    assert all("https://d.example" not in draft.body for draft in result.issues)


def test_a_credential_a_live_job_reads_as_empty_is_blocking():
    result = dv.plan(credentials=[credential()])

    assert [draft.severity for draft in result.issues] == ["blocking"]
    assert "MS_STORE_SELLER_ID" in result.issues[0].body


def test_a_credential_every_reader_has_earns_no_draft():
    configured = Finding("TOKEN", "secret", "configured", ("release",), (), gate="release")

    assert dv.plan(credentials=[configured]).issues == ()


# --- honesty about what was measured ----------------------------------------


def test_a_family_that_was_not_measured_is_recorded_as_skipped_not_clean():
    result = dv.plan()

    assert result.measured == ()
    assert result.skipped == (dv.CI, dv.URLS, dv.SURFACE, dv.CREDENTIALS, dv.ORPHANS, dv.FEED)
    assert result.actionable == 0
    assert "Not measured: ci, urls" in dv.render_suggestions(result)


def test_measured_and_finding_nothing_is_a_different_answer():
    result = dv.plan(verdicts=[], urls=[], deltas=[], credentials=[], orphans=[])

    assert result.measured == (dv.CI, dv.URLS, dv.SURFACE, dv.CREDENTIALS, dv.ORPHANS)
    assert result.skipped == (dv.FEED,)
    assert result.actionable == 0
    assert dv.render_suggestions(result).startswith("Suggestions: none, from the families")


def test_an_unmeasured_family_contributes_no_suggestion():
    handed = dv.plan(verdicts=[verdict("python-tests")])

    assert {tip.kind for tip in handed.suggestions} == {dv.CI}
    assert dv.render_suggestions(handed).rstrip().endswith(
        "not measured, so nothing to suggest there: urls, surface, credentials, orphans, feed"
    )


def test_one_lane_is_never_asked_for_twice():
    feed = recorded("python-tests")

    assert "feed-lanes-without-owner" not in {
        draft.key for draft in dv.plan(verdicts=[verdict("python-tests")], feed=feed).issues
    }
    alone = {draft.key for draft in dv.plan(feed=feed).issues}
    assert "feed-lanes-without-owner" in alone, "with no verdicts handed, the feed owes the ask"


def test_a_partial_feed_read_becomes_an_issue_about_the_read():
    feed = recorded("python-tests", "js-lint", previous={"gone-lane": "UNKNOWN"}, total=9)
    draft = next(item for item in dv.plan(feed=feed).issues
                 if item.key == "feed-unproved-clearances")

    assert feed.complete is False and feed.count(ef.UNPROVED) == 1
    assert "gone-lane" in draft.body
    assert "not evidence that they went green" in draft.body


# --- the dedupe invariant ----------------------------------------------------


def test_every_draft_carries_a_marker_that_reads_back_its_own_key():
    result = measure_all()
    drafts = (*result.issues, *result.discussions)

    assert len(drafts) == result.actionable >= 6
    for draft in drafts:
        assert dv.MARKER.search(draft.body).group(1) == draft.key
        assert dv.FOOTER in draft.body


def test_a_key_is_a_slug_the_marker_can_match_whole():
    for kind in RedClass:
        key = dv._slug(f"ci-{kind.value}")
        assert dv.MARKER.fullmatch(f"100ways:issue:{key}"), key


def test_deduplicate_drops_only_what_was_already_asked_for():
    result = measure_all()
    trimmed, dropped = dv.deduplicate(result, {"ci-unknown", "orphans-unexplained"})

    assert dropped == ("ci-unknown", "orphans-unexplained")
    assert "ci-unknown" not in {draft.key for draft in trimmed.issues}
    assert "orphans-unexplained" not in {draft.key for draft in trimmed.discussions}
    assert len(trimmed.suggestions) == len(result.suggestions)
    assert trimmed.measured == result.measured


def test_the_open_list_is_read_as_bodies_and_parsed_for_markers():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Result(0, "<!-- 100ways:issue:ci-unknown -->\nno marker here", "")

    assert dv.open_issue_keys("o/r", run=fake_run) == frozenset({"ci-unknown"})
    joined = " ".join(calls[0])
    assert "--paginate" in joined and "state=open" in joined
    assert "pull_request" in joined, "a pull request body is not an open issue"


def test_the_discussion_list_is_read_the_same_way():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Result(0, "<!-- 100ways:issue:orphans-unexplained -->", "")

    assert dv.open_discussion_keys("o/r", run=fake_run) == frozenset({"orphans-unexplained"})
    assert calls[0][2] == "--paginate"
    assert "repos/o/r/discussions?per_page=100" in calls[0][3]


def test_an_unreadable_open_list_is_a_failure_not_an_empty_one():
    def failing(cmd, **kwargs):
        return _Result(1, "", "bad token")

    with pytest.raises(dv.DeliveryError, match="no deduplication may be claimed"):
        dv.open_issue_keys("o/r", run=failing)
    with pytest.raises(dv.DeliveryError, match="no deduplication may be claimed"):
        dv.open_discussion_keys("o/r", run=failing)


def test_no_gh_means_no_claim_about_what_is_already_open(monkeypatch):
    monkeypatch.setattr(dv.shutil, "which", lambda _: None)

    with pytest.raises(dv.DeliveryError, match="not on PATH"):
        dv.open_issue_keys("o/r")
    with pytest.raises(dv.DeliveryError, match="not on PATH"):
        dv.open_discussion_keys("o/r")


# --- tables and documents agree ---------------------------------------------


def test_a_table_row_for_every_draft_the_same_plan_drafted():
    result = measure_all()

    for kind, expected in (
        (dv.ISSUES, len(result.issues)),
        (dv.DISCUSSIONS, len(result.discussions)),
        (dv.SUGGESTIONS, len(result.suggestions)),
    ):
        table = dv.plan_table(result, kind)
        assert table.row_count == expected
        assert f"{expected} row(s)" in render_table(table)


def test_a_table_names_the_families_that_were_measured():
    notes = dv.plan_table(dv.plan(verdicts=[verdict("x")])).notes

    assert any("not: urls, surface, credentials, orphans, feed" in note for note in notes)


def test_a_family_that_is_not_a_delivery_family_raises_rather_than_printing_nothing():
    with pytest.raises(dv.TableError, match="no delivery family"):
        dv.plan_table(measure_all(), "wikis")


def test_the_rendered_document_and_the_machine_form_count_the_same_things():
    result = measure_all()
    payload = dv.plan_json(result)
    rendered = dv.render_plan(result)

    assert payload["actionable"] == result.actionable == len(result.issues) + len(
        result.discussions
    )
    assert f"- issues: {len(result.issues)}" in rendered
    assert f"- discussions: {len(result.discussions)}" in rendered
    for draft in (*result.issues, *result.discussions):
        assert draft.title in rendered
    assert rendered.rstrip().endswith(dv.FOOTER)


def test_a_document_never_repeats_a_credential_that_reached_a_finding():
    result = dv.plan(
        urls=[judged(f"https://x.example/{A_TOKEN}", BROKEN)],
        credentials=[credential(name=A_TOKEN)],
    )
    text = dv.render_plan(result) + json.dumps(dv.plan_json(result))

    assert A_TOKEN not in text
    assert "[REDACTED]" in text


def test_suggestions_are_ordered_by_family_then_key_and_named_with_their_owner():
    result = measure_all()
    body = dv.render_suggestions(result)

    assert result.suggestions == tuple(
        sorted(result.suggestions, key=lambda tip: (tip.kind, tip.key))
    )
    assert "configure the name, or gate the lane that reads it" in body
    assert "the repo owner" in body


def test_a_limit_reports_what_it_withheld():
    result = measure_all()
    body = dv.render_suggestions(result, limit=2)

    assert f"showing 2 of {len(result.suggestions)}" in body
    assert "withheld by --limit" in body


# --- writing ----------------------------------------------------------------


def test_a_dry_run_touches_nothing_and_reports_itself_as_skipped():
    result = measure_all()
    outcome = dv.deliver("o/r", result)

    assert outcome["apply"] is False
    assert outcome["planned"] == result.actionable
    assert all(record["action"] == "skipped" for record in outcome["issues"])
    assert all(record["action"] == "skipped" for record in outcome["discussions"])
    assert outcome["errors"] == []


def test_asking_for_issues_does_not_open_discussions_as_a_side_effect(monkeypatch):
    seen = []

    def only_issues(repo, drafts, apply=False):
        seen.append((repo, len(drafts), apply))
        return [{"key": draft.key} for draft in drafts]

    monkeypatch.setattr(dv, "create_issues", only_issues)
    result = measure_all()
    outcome = dv.deliver("o/r", result, True, kinds=("issues",), run=_never)

    assert seen == [("o/r", len(result.issues), True)]
    assert outcome["discussions"] == []
    assert outcome["planned"] == len(result.issues)


def test_the_one_issue_path_is_the_one_deliver_uses(monkeypatch):
    called = []
    def the_one_path(repo, drafts, apply=False):
        called.append(apply)
        return []

    monkeypatch.setattr(dv, "create_issues", the_one_path)

    dv.deliver("o/r", measure_all(), True, kinds=("issues",), run=_never)

    assert called == [True], "issues are written through brandaudit and nowhere else"


def test_a_family_that_does_not_exist_is_refused_before_anything_is_written(monkeypatch):
    monkeypatch.setattr(dv, "create_issues", lambda *a, **k: [])  # noqa: E731

    with pytest.raises(dv.DeliveryError, match="no family to deliver"):
        dv.deliver("o/r", measure_all(), True, kinds=("issues", "wikis"))


def test_a_discussion_is_created_from_a_json_body_on_stdin_never_a_shell_string():
    calls = []
    drafts = [dv._discussion("q-odd", "Ask about `backquote` and $HOME", ["line one"], "general")]

    records = dv.create_discussions("o/r", drafts, True, run=_capture(calls))

    assert records == [{"key": "q-odd", "title": drafts[0].title, "url": "https://x/7"}]
    command, kwargs = calls[0]
    assert command == ["gh", "api", "repos/o/r/discussions", "-X", "POST", "--input", "-"]
    assert json.loads(kwargs["input"]) == {
        "title": drafts[0].title,
        "body": drafts[0].body,
        "category": "general",
    }
    assert not any(shell_metachar in part for part in command for shell_metachar in "`$")


def test_a_category_is_sent_only_when_the_operator_named_one():
    calls = []
    dv.create_discussions(
        "o/r", [dv._discussion("q", "Ask", ["body"])], True, run=_capture(calls)
    )
    dv.create_discussions(
        "o/r", [dv._discussion("q", "Ask", ["body"], "general")], True, run=_capture(calls)
    )

    assert "category" not in json.loads(calls[0][1]["input"])
    assert json.loads(calls[1][1]["input"])["category"] == "general"


def test_a_failed_create_comes_back_as_a_record_so_a_partial_delivery_is_visible():
    def failing(cmd, **kwargs):
        return _Result(1, "", "403 not allowed")
    drafts = [dv._discussion("q", "Ask", ["body"])]

    assert dv.create_discussions("o/r", drafts, True, run=failing)[0]["error"] == "403 not allowed"
    outcome = dv.deliver(
        "o/r", dv.plan(orphans=[Orphan("a.py", "unclassified", "e", "s")]), True,
        kinds=("discussions",), run=failing,
    )
    assert [record["error"] for record in outcome["errors"]] == ["403 not allowed"]


def test_a_write_without_gh_reports_itself_instead_of_looking_silent(monkeypatch):
    monkeypatch.setattr(dv.shutil, "which", lambda _: None)

    assert dv.create_discussions(
        "o/r", [dv._discussion("q", "Ask", ["body"])], True
    ) == [{"key": "q", "title": "Ask", "error": "gh CLI not on PATH"}]


def test_a_response_that_is_not_json_is_reported_as_such():
    def odd(cmd, **kwargs):
        return _Result(0, "created", "")

    records = dv.create_discussions("o/r", [dv._discussion("q", "Ask", ["body"])], True, run=odd)

    assert records[0]["url"] == "created; the response was not JSON"


class _Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _capture(sink):
    def run(cmd, **kwargs):
        sink.append((cmd, kwargs))
        return _Result(0, '{"html_url":"https://x/7"}', "")

    return run


def _never(*args, **kwargs):
    raise AssertionError("no process may be started for a write this test did not authorize")
