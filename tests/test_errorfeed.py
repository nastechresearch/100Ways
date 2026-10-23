"""The feed reports movement, and refuses to invent it.

Everything here is about one risk: a check-runs payload that did not list every lane.
GitHub paginates that endpoint, so a truncated read looks like lanes went green -- the
most damaging thing this pipeline could say, because it looks like a fix landed.  These
tests hold the four properties that make the feed safe to interrupt for: novelty is not
claimed without a previous cycle, a clearance is only claimed on a provably complete
read, the answer is the classifier's own answer, and the text carries the payload's time
rather than a clock.
"""

from __future__ import annotations

import json

import pytest

from hundredways import errorfeed as ef
from hundredways.red_classifier import OWNERS, RedClass, classify_runs

A_TOKEN = "ghp_" + "A" * 36
STAMP = "2026-10-21T03:04:05Z"


def run(name, *, conclusion="failure", text="", image="ubuntu-latest", **extra) -> dict:
    payload = {
        "id": 501,
        "name": name,
        "conclusion": conclusion,
        "runner_name": image,
        "output": {"title": "Process completed with exit code 1.", "text": text},
        "updated_at": STAMP,
    }
    payload.update(extra)
    return payload


def payload(*runs, total=None) -> dict:
    """A complete read unless ``total`` says otherwise."""
    return {"total_count": len(runs) if total is None else total, "check_runs": list(runs)}


CYCLE = payload(run("python-tests"), run("js-lint"))


# --- novelty -----------------------------------------------------------------


def test_a_first_cycle_reports_first_and_never_new():
    feed = ef.observe(CYCLE)

    assert feed.compared is False
    assert feed.count(ef.FIRST) == 2
    assert feed.count(ef.NEW) == 0
    assert all("novelty is not claimed" in event.detail for event in feed.events)


def test_an_unchanged_cycle_moves_nothing():
    first = ef.observe(CYCLE)
    again = ef.observe(CYCLE, previous=first.reds)

    assert again.compared is True
    assert again.count(ef.ONGOING) == 2
    assert again.count(ef.NEW) == 0 and again.count(ef.RECLASSIFIED) == 0
    assert all(event.kind == ef.ONGOING for event in again.events)


def test_a_lane_the_evidence_now_places_elsewhere_is_reclassified():
    first = ef.observe(CYCLE)
    flipped = {lane: "RUNNER_CAPACITY" for lane in first.reds}
    moved = ef.observe(CYCLE, previous=flipped)

    assert moved.count(ef.RECLASSIFIED) == 2
    assert all("was RUNNER_CAPACITY" in event.detail for event in moved.events)


def test_a_lane_red_now_and_absent_before_is_new():
    previous = ef.observe(payload(run("python-tests"))).reds
    grown = ef.observe(CYCLE, previous=previous)

    assert grown.count(ef.NEW) == 1
    assert grown.count(ef.ONGOING) == 1
    new_lane = next(event for event in grown.events if event.kind == ef.NEW)
    assert new_lane.lane == "js-lint"


# --- completeness, the reason this module exists ------------------------------


def test_a_clearance_is_claimed_only_when_the_payload_listed_every_run():
    previous = ef.observe(CYCLE).reds
    cleared = ef.observe(payload(run("python-tests")), previous=previous)

    assert cleared.complete is True
    assert cleared.count(ef.RESOLVED) == 1
    resolved = next(event for event in cleared.events if event.kind == ef.RESOLVED)
    assert resolved.lane == "js-lint"
    assert resolved.owner == OWNERS[RedClass(resolved.cls)]


def test_a_truncated_read_claims_no_clearance_at_all():
    previous = ef.observe(CYCLE).reds
    truncated = ef.observe(payload(run("python-tests"), total=9), previous=previous)

    assert truncated.complete is False
    assert truncated.partial_reason == "payload lists 1 of 9 check run(s)"
    assert truncated.count(ef.RESOLVED) == 0
    assert truncated.count(ef.UNPROVED) == 1
    unproved = next(event for event in truncated.events if event.kind == ef.UNPROVED)
    assert "--paginate" in unproved.action
    assert unproved.stamp == "", "an unproved lane has no time; the payload never named it"


def test_a_payload_without_total_count_proves_nothing_about_a_gone_lane():
    previous = ef.observe(CYCLE).reds
    naked = {"check_runs": [run("python-tests")]}
    feed = ef.observe(naked, previous=previous)

    assert feed.complete is False
    assert "no total_count" in feed.partial_reason
    assert feed.count(ef.RESOLVED) == 0 and feed.count(ef.UNPROVED) == 1


def test_a_new_lane_on_a_partial_read_says_the_read_was_partial():
    previous = ef.observe(payload(run("python-tests"))).reds
    feed = ef.observe(payload(run("python-tests"), run("js-lint"), total=9), previous=previous)

    new_lane = next(event for event in feed.events if event.kind == ef.NEW)
    assert "this read is partial" in new_lane.detail


def test_the_feed_answers_the_same_question_why_red_answers():
    verdicts = classify_runs(CYCLE)

    assert ef.observe(CYCLE).verdicts == verdicts


# --- time comes from the payload ---------------------------------------------


def test_a_stamp_is_the_payloads_own_and_is_absent_rather_than_borrowed():
    feed = ef.observe(CYCLE)

    assert all(event.stamp == STAMP for event in feed.events)
    assert feed.untimed == 0


def test_a_run_with_no_timestamp_is_counted_and_said_so():
    silent = payload(run("python-tests", updated_at=None, completed_at=None))
    feed = ef.observe(silent)
    rendered = ef.render(feed)

    assert feed.untimed == 1
    assert "at: not in the payload" in rendered
    assert f"{feed.untimed} movement(s) carried no timestamp" in rendered


def test_completed_at_is_the_fallback_the_api_actually_sends():
    feed = ef.observe(payload(run("python-tests", updated_at=None, completed_at=STAMP)))

    assert feed.events[0].stamp == STAMP


# --- rendering ---------------------------------------------------------------


def test_rendering_is_reproducible_from_the_same_two_files():
    feed = ef.observe(CYCLE)

    assert ef.render(feed) == ef.render(feed)
    assert ef.to_json(feed) == ef.to_json(ef.observe(CYCLE))


def test_a_clean_cycle_says_so_without_inventing_movement():
    assert ef.render(ef.observe(payload())) == (
        "Fork CI error feed: no red lane this cycle and nothing carried over.\n"
        "previous cycle: not recorded\n"
    )


def test_an_unplaced_lane_is_readable_apart_from_the_counted_classes():
    feed = ef.observe(CYCLE)

    assert feed.unowned == ("python-tests", "js-lint")
    assert "still have no owner" in ef.render(feed)


def test_a_limit_withholds_rows_and_says_how_many():
    feed = ef.observe(CYCLE)
    rendered = ef.render(feed, limit=1)

    assert "1. js-lint" in rendered
    assert "python-tests\n" not in rendered.split("1. js-lint")[0]
    assert "showing 1 of 2 movement(s): 1 withheld by --limit" in rendered


def test_a_credential_shaped_lane_arrives_masked():
    feed = ef.observe(payload(run(A_TOKEN)))

    assert A_TOKEN not in ef.render(feed)
    assert "[REDACTED]" in ef.render(feed)


def test_json_counts_only_kinds_that_happened():
    counts = ef.to_json(ef.observe(CYCLE))["counts"]

    assert counts == {ef.FIRST: 2}
    assert ef.to_json(ef.observe(CYCLE))["schema"] == ef.SCHEMA


# --- the state file ----------------------------------------------------------


def test_state_round_trips_into_a_next_cycle_previous_map(tmp_path):
    feed = ef.observe(CYCLE)
    path = tmp_path / "feed.json"
    ef.save_state(feed, str(path))

    assert ef.load_state(str(path)) == dict(feed.reds)
    assert json.loads(path.read_text())["schema"] == ef.SCHEMA


def test_a_state_written_by_one_cycle_reads_as_ongoing_in_the_next(tmp_path):
    path = tmp_path / "feed.json"
    ef.save_state(ef.observe(CYCLE), str(path))
    following = ef.observe(CYCLE, previous=ef.load_state(str(path)))

    assert following.count(ef.ONGOING) == 2


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("{not json", "could not be read"),
        ("[]", "is not"),
        ('{"schema": "other", "reds": {}}', "is not"),
        ('{"schema": "100ways.errorfeed/v1", "reds": "nope"}', "not a lane"),
    ],
)
def test_a_state_file_that_is_not_this_schema_is_an_error(tmp_path, body, why):
    path = tmp_path / "feed.json"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ef.ErrorFeedError, match=why):
        ef.load_state(str(path))


def test_a_missing_state_file_is_an_error_not_an_empty_history(tmp_path):
    with pytest.raises(ef.ErrorFeedError, match="could not be read"):
        ef.load_state(str(tmp_path / "never-written.json"))
