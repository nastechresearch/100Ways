"""Tests for the release verifier and admin password compiler."""

import pytest

from hundredways.release import (
    release_check_table,
    release_summary,
    release_verify_incoming,
)
from hundredways.security import verify_token


# -- security / operator token ------------------------------------------------

def test_verify_requires_exact_operator_token():
    assert verify_token("operator-secret", "operator-secret")
    assert not verify_token("wrong-pass", "operator-secret")
    assert not verify_token("operator-secret", None)
    assert not verify_token("operator-secret", "")
    assert not verify_token("", "operator-secret")


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
