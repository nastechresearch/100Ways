"""Tests for the Ollama Cloud-only AI configuration and deterministic reviews."""

import pytest

from hundredways.ai import (
    OLLAMA_CLOUD_BASE_URL,
    OLLAMA_CLOUD_DEFAULT_MODEL,
    OLLAMA_CLOUD_PROVIDER,
    AIConfig,
    AIEngine,
    ai_config_from_env,
    sanitize_ai_context,
    validate_provider,
)
from hundredways.remediation import classify_failure
from hundredways.updates import STAGES

# -- env-driven configuration -------------------------------------------------


def test_ai_config_defaults_to_ollama_cloud():
    cfg = AIConfig()
    assert cfg.provider == OLLAMA_CLOUD_PROVIDER
    assert cfg.base_url == OLLAMA_CLOUD_BASE_URL
    assert cfg.model == OLLAMA_CLOUD_DEFAULT_MODEL


def test_ai_config_from_env_uses_hundredways_names(monkeypatch):
    monkeypatch.setenv("HUNDREDWAYS_AI_PROVIDER", "ollama-cloud")
    monkeypatch.setenv("HUNDREDWAYS_AI_MODEL", "deepseek-v4:cloud")
    monkeypatch.setenv("HUNDREDWAYS_AI_API_KEY", "sk-ollama")

    cfg = ai_config_from_env()

    assert cfg.provider == OLLAMA_CLOUD_PROVIDER
    assert cfg.base_url == OLLAMA_CLOUD_BASE_URL
    assert cfg.model == "deepseek-v4:cloud"
    assert cfg.api_key == "sk-ollama"


def test_ai_config_from_env_supports_legacy_aliases(monkeypatch):
    monkeypatch.setenv("SYNCBRIDGE_AI_PROVIDER", "ollama")
    monkeypatch.setenv("SYNCBRIDGE_AI_MODEL", "legacy-cloud-model")
    monkeypatch.setenv("OLLAMA_API_KEY", "sk-ollama")

    cfg = ai_config_from_env()

    assert cfg.provider == OLLAMA_CLOUD_PROVIDER
    assert cfg.model == "legacy-cloud-model"
    assert cfg.api_key == "sk-ollama"


def test_ai_config_from_env_rejects_other_provider(monkeypatch):
    monkeypatch.setenv("HUNDREDWAYS_AI_PROVIDER", "openai")

    with pytest.raises(ValueError, match="ollama-cloud"):
        ai_config_from_env()


def test_ai_config_from_env_rejects_noncanonical_endpoint(monkeypatch):
    monkeypatch.setenv("HUNDREDWAYS_AI_BASE_URL", "https://other.example/v1")

    with pytest.raises(ValueError, match="Ollama Cloud endpoint"):
        ai_config_from_env()


def test_validate_provider_rejects_unsafe_direct_configuration():
    with pytest.raises(ValueError, match="ollama-cloud"):
        validate_provider(AIConfig(provider="openai"))


def test_ollama_cloud_default_model_is_gemma4_31b():
    assert OLLAMA_CLOUD_DEFAULT_MODEL == "gemma4:31b-cloud"
    assert OLLAMA_CLOUD_BASE_URL == "https://ollama.com/v1"


def test_sanitize_ai_context_redacts_credentials_and_bounds_data():
    text = "token ghp_0123456789abcdefghijklmnopqrstuv bot 123456789:abcdefghijklmnopqrstuv"
    redacted = sanitize_ai_context(text, limit=80)

    assert "ghp_" not in redacted
    assert "123456789:" not in redacted
    assert redacted.count("[REDACTED]") == 2
    assert len(sanitize_ai_context("x" * 90, limit=80)) == 80


def test_remediation_advice_fallback_keeps_hard_skip_and_redaction():
    decision = classify_failure(
        "candidate integrity checks failed: archive-case-collision token=ghp_0123456789abcdefghijklmnopqrstuv"
    )

    advice = AIEngine(AIConfig(api_key="")).advise_remediation(decision)

    assert "hard_skip" in advice
    assert "Allowed action: none" in advice
    assert "ghp_" not in advice
    assert "publication" in advice


# -- per-stage models ---------------------------------------------------------


def test_model_for_stage_uses_override_then_default():
    cfg = AIConfig(model="main-model", stage_models={"gate": "cheap-model"})
    engine = AIEngine(cfg)
    assert engine.model_for_stage("gate") == "cheap-model"
    assert engine.model_for_stage("pull") == "main-model"
    assert engine.model_for_stage("brand") == "main-model"


def test_model_for_stage_no_override_map():
    engine = AIEngine(AIConfig(model="m"))
    assert engine.model_for_stage("verify") == "m"


# -- stage-aware review -------------------------------------------------------


def test_stages_ordered_and_unique():
    assert STAGES[0] == "pull"
    assert STAGES[-1] == "release"
    assert len(set(STAGES)) == len(STAGES)
    assert "brand" in STAGES and "verify" in STAGES


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
