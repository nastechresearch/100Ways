from hundredways.commit_stream import TELEGRAM_SAFETY_FOOTER
from hundredways.operation_safety import (
    TELEGRAM_MAX_CHARS,
    bound_telegram_text,
    format_release_readiness_status,
    notification_fingerprint,
    should_send_notification,
)


def test_telegram_text_redacts_credentials_preserves_footer_and_bounds_length():
    text = "token ghp_abcdefghijklmnopqrstuvwxyz1234567890\n" + ("x" * 5000)

    rendered = bound_telegram_text(text)

    assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890" not in rendered
    assert "[REDACTED]" in rendered
    assert TELEGRAM_SAFETY_FOOTER in rendered
    assert len(rendered) <= TELEGRAM_MAX_CHARS


def test_notification_deduplication_tracks_delivery_significant_state_only():
    payload = {
        "status": "warming",
        "pending_commits": 22,
        "threshold": 50,
        "baseline_sha": "a" * 40,
        "upstream_sha": "b" * 40,
    }
    fingerprint = notification_fingerprint(payload)

    assert should_send_notification(previous_fingerprint=fingerprint, payload=payload) is False
    assert should_send_notification(
        previous_fingerprint=fingerprint,
        payload=payload | {"pending_commits": 23},
    ) is True


def test_release_readiness_messages_never_authorize_automatic_release():
    ready = format_release_readiness_status({"gate": "READY", "upstream_tag": "v2026.8.13"})
    blocked = format_release_readiness_status(
        {"gate": "BLOCKED", "upstream_tag": "v2026.8.13", "issues": [{"code": "tag"}]}
    )

    assert "human release review" in ready
    assert "no tag or release is authorized" in blocked
    assert TELEGRAM_SAFETY_FOOTER in ready
    assert TELEGRAM_SAFETY_FOOTER in blocked
