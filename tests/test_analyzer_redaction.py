"""Redaction coverage for the Actions failure analyzer.

100Ways publishes sanitized failure reports to GitHub Pages and Telegram.
A leaked credential here becomes public. These tests pin the redaction
contract: every credential-shaped string MUST be replaced with
``[REDACTED]`` before it leaves the analyzer.
"""

from __future__ import annotations

from hundredways.actions_analyzer import redact


def test_redacts_classic_github_personal_access_token():
    text = "Authorization: token ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    out = redact(text)
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in out
    assert "[REDACTED]" in out


def test_redacts_fine_grained_github_pat():
    """Fine-grained PATs use the ``github_pat_`` prefix (not matched by the
    classic ``gh[pousr]_`` regex).  They must be redacted or they will leak
    through any log that lands in a published Pages payload."""
    text = "leaked: github_pat_11ABCDEFG0_1234567890abcdefghij"
    out = redact(text)
    assert "github_pat_11ABCDEFG0_1234567890abcdefghij" not in out
    assert "[REDACTED]" in out


def test_redacts_oauth_tokens():
    for prefix in ("gho_", "ghs_", "ghr_"):
        token = prefix + ("A" * 30)
        out = redact(f"token={token}")
        assert token not in out
        assert "[REDACTED]" in out


def test_redacts_gitlab_personal_access_token():
    text = "Authorization: Bearer glpat-abcdefghijklmnopqrstuvwx"
    out = redact(text)
    assert "glpat-abcdefghijklmnopqrstuvwx" not in out
    assert "[REDACTED]" in out


def test_redacts_basic_auth_in_url():
    text = "fatal: unable to access 'https://user:hunter2@github.com/foo/bar'"
    out = redact(text)
    assert "hunter2" not in out
    assert "[REDACTED]" in out


def test_redacts_env_style_secret_keyword():
    text = "ENV api_key=sk-abcdef0123456789abcdef0123456789"
    out = redact(text)
    assert "sk-abcdef0123456789abcdef0123456789" not in out
    assert "[REDACTED]" in out


def test_preserves_unrelated_text():
    """Regressions: ordinary log lines must pass through unchanged."""
    text = "Cloning into '/tmp/hermes-agent'...\nfatal: unable to find remote ref"
    out = redact(text)
    assert out == text


def test_handles_multiple_credentials_in_same_line():
    text = "got ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa then github_pat_11BBBBBBBBBB_cccccccccccccccccccccccccccccccc"
    out = redact(text)
    assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in out
    assert "github_pat_11BBBBBBBBBB_cccccccccccccccccccccccccccccccc" not in out
    assert out.count("[REDACTED]") == 2
