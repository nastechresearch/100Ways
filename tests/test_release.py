"""Tests for the release verifier and admin password compiler."""

import pytest

from hundredways.release import (
    release_check_table,
    release_summary,
    release_verify_incoming,
)
from hundredways.security import (
    DEFAULT_ADMIN_PASS,
    compile_token,
    is_compiled,
    verify_token,
)


# -- security / admin pass ----------------------------------------------------

def test_compile_token_shape():
    token = compile_token("Nastech@Pass")
    assert is_compiled(token)
    assert len(token) > 15
    assert token.isascii()


def test_compile_token_deterministic():
    assert compile_token("Nastech@Pass") == compile_token("Nastech@Pass")
    assert compile_token("Nastech@Pass") != compile_token("Other@Pass")


def test_verify_accepts_password_and_compiled():
    stored = compile_token(DEFAULT_ADMIN_PASS)
    assert verify_token("Nastech@Pass", stored)
    assert verify_token(stored, stored)  # the compiled form works too
    assert not verify_token("wrong-pass", stored)
    assert not verify_token("", stored)
    assert not verify_token("Nastech@Pass", None)
    assert not verify_token("Nastech@Pass", "")


# -- release verifier ---------------------------------------------------------

def test_table_is_sound():
    assert release_check_table() == []


def test_verify_incoming_clean():
    payload = {"codes": [0, 82, 83, 84, 404, 1]}
    assert release_verify_incoming(payload) == []
    # entries form (list of records)
    payload2 = {"entries": [{"code": 82, "path": "a.py"}, {"code": 0, "path": "b.py"}]}
    assert release_verify_incoming(payload2) == []


def test_verify_incoming_catches_unknown():
    errors = release_verify_incoming({"codes": [0, 999]})
    assert any("unknown code 999" in e for e in errors)
    errors2 = release_verify_incoming([82, "not-a-code"])
    assert any("not a code" in e for e in errors2)


def test_verify_incoming_bad_shape():
    assert release_verify_incoming({"codes": 42})  # not a list


def test_release_summary():
    assert release_summary({"codes": [0, 0, 82]}) == "VIOLATION (82) - PASS=2  VIOLATION=1"
    assert release_summary({"codes": []}) == "clean"


def test_release_round_trip():
    """A release that passes verification also produces a sensible summary."""
    payload = {"codes": [0, 404, 82]}
    assert release_verify_incoming(payload) == []
    summary = release_summary(payload)
    assert "MISSING" in summary
