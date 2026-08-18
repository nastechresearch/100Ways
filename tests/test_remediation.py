from hundredways.remediation import classify_failure


def test_shallow_history_is_the_only_auto_recoverable_sync_failure():
    decision = classify_failure(
        "effective NasTech baseline abc is not an ancestor of upstream def; "
        "repository is shallow"
    )

    assert decision.category == "source_history"
    assert decision.disposition == "auto_recover"
    assert decision.action == "complete_shallow_history"
    assert decision.hard is False


def test_transient_transport_uses_existing_bounded_fetch_retry_only():
    decision = classify_failure("error: RPC failed; HTTP 429 curl 22")

    assert decision.category == "transport"
    assert decision.disposition == "auto_recover"
    assert decision.action == "bounded_direct_fetch_retry"
    assert decision.hard is False


def test_branding_or_integrity_failure_is_hard_skipped():
    decision = classify_failure(
        "ValueError: candidate integrity checks failed: "
        "archive-case-collision:nastech-agent/contributors/emails/example"
    )

    assert decision.category == "candidate_integrity"
    assert decision.disposition == "hard_skip"
    assert decision.action == "none"
    assert decision.hard is True


def test_runtime_test_failure_is_hard_skipped():
    decision = classify_failure(
        "FAILED tests/gateway/test_loop_command.py::test_gateway_loop_goal_note_when_goal_active"
    )

    assert decision.category == "fork_runtime"
    assert decision.disposition == "hard_skip"
    assert decision.action == "none"


def test_unknown_failure_is_skipped_for_human_review():
    decision = classify_failure("unexpected parser condition")

    assert decision.category == "unknown"
    assert decision.disposition == "manual_skip"
    assert decision.action == "none"
    assert decision.hard is True


def test_sensitive_values_are_redacted_from_report_evidence():
    decision = classify_failure("token=ghp_0123456789abcdefghijklmnopqrstuv HTTP 429")

    assert "ghp_" not in decision.evidence
    assert "[REDACTED]" in decision.evidence
