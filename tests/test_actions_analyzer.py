from hundredways.actions_analyzer import analyze_failure, redact


def test_rate_limit_is_classified_and_remains_fail_closed():
    report = analyze_failure(
        "git clone https://github.com/NousResearch/hermes-agent.git\n"
        "error: RPC failed; HTTP 429 curl 22\n"
        "fatal: expected flush after ref listing"
    )
    assert report.status == "failure"
    assert report.safe_to_retry is False
    assert report.findings[0].category == "upstream_rate_limit"
    assert report.findings[0].retryable is True
    assert "429" in report.findings[0].evidence


def test_credentials_are_redacted_before_reporting():
    secret = "ghp_TEST_TOKEN_NOT_REAL_1234567890"
    assert redact(f"token={secret}") == "[REDACTED]"
    report = analyze_failure(f"fatal: token={secret}")
    assert secret not in report.to_dict()["findings"][0]["evidence"]


def test_incomplete_source_history_is_retryable_but_remains_fail_closed():
    report = analyze_failure(
        "effective NasTech baseline abc is not an ancestor of upstream def; "
        "manual reconciliation is required before threshold evaluation"
    )
    finding = report.findings[0]
    assert finding.category == "source_history"
    assert finding.retryable is True
    assert report.safe_to_retry is False
    assert "complete upstream history" in finding.recommendation


def test_permission_failure_is_not_retryable():
    report = analyze_failure("Resource not accessible by integration (HTTP 403)")
    finding = report.findings[0]
    assert finding.category == "permissions"
    assert finding.retryable is False


def test_unknown_failure_requires_manual_review():
    report = analyze_failure("pytest failed: assertion mismatch")
    finding = report.findings[0]
    assert finding.category == "unknown"
    assert finding.retryable is False


def test_rate_limit_budget_counts_http_429_in_log():
    from hundredways.actions_analyzer import count_rate_limit_signals

    log = (
        "Cloning into '/tmp/hermes-agent'...\n"
        "error: RPC failed; HTTP 429 curl 22 The requested URL returned error: 429\n"
        "fatal: expected flush after ref listing\n"
        "error: RPC failed; HTTP 429 curl 22 The requested URL returned error: 429\n"
        "fatal: expected flush after ref listing\n"
    )
    assert count_rate_limit_signals(log) == 2


def test_rate_limit_budget_exceeded_returns_true_only_above_budget():
    from hundredways.actions_analyzer import rate_limit_budget_exceeded

    log_clean = "fatal: unable to access 'https://example.com'\n"
    log_one = "error: RPC failed; HTTP 429\n"
    log_four = "\n".join(["error: RPC failed; HTTP 429"] * 4)

    assert rate_limit_budget_exceeded(log_clean) is False
    assert rate_limit_budget_exceeded(log_one) is False
    assert rate_limit_budget_exceeded(log_four, budget=3) is True
    # Custom budget overrides the default.
    assert rate_limit_budget_exceeded(log_one, budget=0) is True
