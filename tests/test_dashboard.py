"""Tests for the admin surface: rule overrides, achievements, dashboard API."""

import json
import os
import threading
from http.client import HTTPConnection

import pytest

from hundredways.achievements import Achievements
from hundredways.dashboard import DashboardHandler, DashboardState, _HTML
from hundredways.rules import BrandingRules, tokens_from_overrides
from hundredways.security import compile_token


# -- rules overrides ----------------------------------------------------------

def test_overrides_are_additive_and_dedupe(tmp_path):
    override = tmp_path / "rules_override.json"
    override.write_text(json.dumps({"tokens": [{"match": "Foo", "replace": "Bar"}]}))
    tokens = tokens_from_overrides(str(override))
    assert "Foo" in [t.match for t in tokens]
    matches = [t.match for t in tokens]
    assert len(matches) == len(set(matches))  # base defaults not duplicated
    assert len(tokens) == len(BrandingRules().tokens) + 1


def test_overrides_missing_file_yields_defaults(tmp_path):
    tokens = tokens_from_overrides(str(tmp_path / "nope.json"))
    assert len(tokens) == len(BrandingRules().tokens)


def test_override_rule_applies_in_rules(tmp_path):
    override = tmp_path / "rules_override.json"
    override.write_text(json.dumps({"tokens": [{"match": "Acme", "replace": "Widget"}]}))
    rules = BrandingRules(tokens=tokens_from_overrides(str(override)))
    assert "Widget" in rules.transform_text("Acme product")


# -- achievements -------------------------------------------------------------

def test_achievements_unlock_and_persist(tmp_path):
    home = str(tmp_path)
    ach = Achievements(home)
    unlocked = ach.apply_event("pull")
    assert "first_pull" in unlocked
    # persisted state survives a fresh instance
    ach2 = Achievements(home)
    assert ach2.is_unlocked("first_pull")
    assert ach2.count("pulls") == 1


def test_admin_edit_achievement(tmp_path):
    ach = Achievements(str(tmp_path))
    assert "admin_edit" in ach.apply_event("admin_edit")


# -- dashboard ----------------------------------------------------------------

ADMIN_COMPILED = compile_token("Nastech@Pass")


def _request(handler_cls, method, path, body=None, token=None):
    handler = object.__new__(handler_cls)
    handler.state = DashboardState()
    handler.repo = "/tmp"
    handler.home = tmp_home
    handler.admin_token = ADMIN_COMPILED
    handler.rules_override = os.path.join(tmp_home, "config", "rules_override.json")
    send = _CaptureSend(handler)
    handler._send = send
    handler._json = lambda payload, code=200: send(code, json.dumps(payload).encode())
    handler.headers = _FakeHeaders(token)
    handler.path = path
    handler.rfile = type("R", (), {"read": lambda self, n=-1: (body or b"").encode()})()
    getattr(handler, f"do_{method}")()
    return send

tmp_home = None

class _CaptureSend:
    def __init__(self, handler):
        self.code = None
        self.body = b""
        self.handler = handler
    def __call__(self, code, body, ctype="application/json"):
        self.code = code
        self.body = body
        self.handler._captured = self

class _FakeHeaders:
    def __init__(self, token=None):
        self.token = token
    def get(self, key, default=""):
        if key == "Authorization":
            return f"Bearer {self.token}" if self.token else ""
        return default

@pytest.fixture(autouse=True)
def _set_home(tmp_path, monkeypatch):
    global tmp_home
    tmp_home = str(tmp_path)


def test_dashboard_rules_endpoint_no_auth(tmp_path):
    res = _request(DashboardHandler, "POST", "/api/rules", body=json.dumps({"action": "add", "match": "X", "replace": "Y"}), token=None)
    assert res.code == 403


def test_dashboard_rules_add_persists(tmp_path):
    res = _request(DashboardHandler, "POST", "/api/rules",
                   body=json.dumps({"action": "add", "match": "Acme", "replace": "Widget"}), token="Nastech@Pass")
    assert res.code == 200
    assert json.loads(res.body)["ok"] is True
    saved = json.loads((tmp_path / "config" / "rules_override.json").read_text())
    assert {"match": "Acme", "replace": "Widget", "anchored": False} in saved["tokens"]


def test_dashboard_admin_accepts_compiled_token(tmp_path):
    """The compiled system token also grants access."""
    res = _request(DashboardHandler, "POST", "/api/rules",
                   body=json.dumps({"action": "add", "match": "Acme2", "replace": "W2"}), token=ADMIN_COMPILED)
    assert res.code == 200


def test_dashboard_admin_denies_wrong_password(tmp_path):
    res = _request(DashboardHandler, "POST", "/api/rules",
                   body=json.dumps({"action": "add", "match": "X", "replace": "Y"}), token="wrong-pass")
    assert res.code == 403


def test_dashboard_rules_remove(tmp_path):
    rules_file = tmp_path / "config" / "rules_override.json"
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(json.dumps({"tokens": [{"match": "Acme", "replace": "Widget"}]}))
    res = _request(DashboardHandler, "POST", "/api/rules", body=json.dumps({"action": "remove", "match": "Acme"}), token="Nastech@Pass")
    assert json.loads(res.body)["ok"] is True
    saved = json.loads(rules_file.read_text())
    assert all(t["match"] != "Acme" for t in saved["tokens"])


def test_dashboard_html_is_servable():
    assert _HTML.startswith("<!doctype html>")
    assert "/api/state" in _HTML and "/api/rules" in _HTML
