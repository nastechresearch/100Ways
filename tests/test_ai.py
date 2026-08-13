"""Tests for the AI module: ollama-cloud provider resolution, per-stage
models, and the 15 stage-aware reviews."""

import os

import pytest

from hundredways.ai import (
    AIConfig,
    AIEngine,
    OLLAMA_CLOUD_BASE_URL,
    OLLAMA_CLOUD_DEFAULT_MODEL,
    ai_config_from_env,
)
from hundredways.updates import STAGES


# -- env-driven config -------------------------------------------------------

def test_ai_config_defaults_to_openai():
    cfg = AIConfig()
    assert cfg.provider == "openai"
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model == "gpt-4o-mini"


def test_ai_config_from_env_provider_ollama_cloud(monkeypatch):
    monkeypatch.setenv("SYNCBRIDGE_AI_PROVIDER", "ollama-cloud")
    monkeypatch.delenv("SYNCBRIDGE_AI_MODEL", raising=False)
    monkeypatch.delenv("SYNCBRIDGE_AI_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_API_KEY", "sk-ollama")
    cfg = ai_config_from_env()
    assert cfg.provider == "ollama-cloud"
    assert cfg.base_url == OLLAMA_CLOUD_BASE_URL
    assert cfg.model == OLLAMA_CLOUD_DEFAULT_MODEL
    assert cfg.api_key == "sk-ollama"


def test_ai_config_from_env_ollama_com_base_url_shortcut(monkeypatch):
    monkeypatch.delenv("SYNCBRIDGE_AI_PROVIDER", raising=False)
    monkeypatch.setenv("SYNCBRIDGE_AI_BASE_URL", "https://ollama.com/v1")
    monkeypatch.delenv("SYNCBRIDGE_AI_MODEL", raising=False)
    cfg = ai_config_from_env()
    assert cfg.provider == "ollama-cloud"
    assert cfg.model == OLLAMA_CLOUD_DEFAULT_MODEL


def test_ai_config_from_env_model_override_wins(monkeypatch):
    monkeypatch.setenv("SYNCBRIDGE_AI_PROVIDER", "ollama-cloud")
    monkeypatch.setenv("SYNCBRIDGE_AI_MODEL", "deepseek-v4:cloud")
    cfg = ai_config_from_env()
    assert cfg.model == "deepseek-v4:cloud"


def test_ollama_cloud_default_model_is_gemma4_31b():
    assert OLLAMA_CLOUD_DEFAULT_MODEL == "gemma4:31b-cloud"
    assert OLLAMA_CLOUD_BASE_URL == "https://ollama.com/v1"


# -- per-stage models --------------------------------------------------------

def test_model_for_stage_uses_override_then_default():
    cfg = AIConfig(model="main-model", stage_models={"gate": "cheap-model"})
    engine = AIEngine(cfg)
    assert engine.model_for_stage("gate") == "cheap-model"
    assert engine.model_for_stage("pull") == "main-model"
    assert engine.model_for_stage("brand") == "main-model"


def test_model_for_stage_no_override_map():
    engine = AIEngine(AIConfig(model="m"))
    assert engine.model_for_stage("verify") == "m"


# -- stage-aware review ------------------------------------------------------

def test_stages_are_all_15():
    assert len(STAGES) == 15
    expected = [
        "pull", "census", "plan", "brand", "scan", "compare", "verify",
        "report", "package", "manifest", "record", "notify", "gate", "summary", "release",
    ]
    assert STAGES == expected


def test_review_stage_rejects_unknown_stage():
    engine = AIEngine(AIConfig(api_key=""))
    with pytest.raises(ValueError):
        engine.review_stage("not-a-stage", "ctx")


def test_review_stage_fallback_without_key():
    engine = AIEngine(AIConfig(api_key=""))
    out = engine.review_stage("gate", "0 failures")
    assert "gate" in out and "0 failures" in out


@pytest.mark.parametrize("stage", STAGES)
def test_review_stage_accepts_every_stage(stage):
    engine = AIEngine(AIConfig(api_key=""))
    out = engine.review_stage(stage, "sample context")
    assert out.startswith(f"Stage '{stage}':")


def test_review_stage_uses_stage_model_for_request(monkeypatch):
    calls = {}

    def fake_chat(self, system, user, model=None):
        calls["model"] = model
        return "reviewed"

    monkeypatch.setattr(AIEngine, "_chat", fake_chat)
    cfg = AIConfig(api_key="sk", model="main", stage_models={"gate": "gemma4:31b-cloud"})
    engine = AIEngine(cfg)
    engine.review_stage("gate", "ctx")
    assert calls["model"] == "gemma4:31b-cloud"


def test_review_stage_falls_back_on_api_error(monkeypatch):
    def boom(self, system, user, model=None):
        raise RuntimeError("down")

    monkeypatch.setattr(AIEngine, "_chat", boom)
    engine = AIEngine(AIConfig(api_key="sk"))
    out = engine.review_stage("verify", "x")
    assert "[AI unavailable" in out
