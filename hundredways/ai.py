"""Ollama Cloud-assisted diff summaries and port reviews.

AI is an optional, advisory feature. The production system permits only the
Ollama Cloud OpenAI-compatible endpoint; an absent key always falls back to
fully deterministic evidence summaries.  AI input is treated as untrusted
repository data, bounded, and redacted before it can leave the runner.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .analyzer import GapReport
from .updates import STAGES
from .verify import VerifyReport

# Ollama Cloud's OpenAI-compatible endpoint and approved default model.
OLLAMA_CLOUD_BASE_URL = "https://ollama.com/v1"
OLLAMA_CLOUD_DEFAULT_MODEL = "gemma4:31b-cloud"
OLLAMA_CLOUD_PROVIDER = "ollama-cloud"
_MAX_AI_CONTEXT_CHARS = 4_000
_SECRET_PATTERNS = (
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:sk|ollama|tg)_[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"),
)


@dataclass
class AIConfig:
    base_url: str = OLLAMA_CLOUD_BASE_URL
    api_key: str = ""
    model: str = OLLAMA_CLOUD_DEFAULT_MODEL
    provider: str = OLLAMA_CLOUD_PROVIDER
    timeout: int = 60
    # Per-stage model overrides intentionally remain provider-local.
    stage_models: dict[str, str] = field(default_factory=dict)


def _first_env(*names: str) -> str:
    """Return the first non-empty environment setting in priority order."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def validate_provider(cfg: AIConfig) -> None:
    """Reject every provider or endpoint other than canonical Ollama Cloud."""
    provider = cfg.provider.strip().lower().replace("_", "-")
    base_url = cfg.base_url.rstrip("/")
    if provider not in {"ollama", OLLAMA_CLOUD_PROVIDER}:
        raise ValueError("100Ways AI is locked to the ollama-cloud provider")
    if base_url != OLLAMA_CLOUD_BASE_URL:
        raise ValueError("100Ways AI must use the Ollama Cloud endpoint https://ollama.com/v1")


def sanitize_ai_context(value: str, *, limit: int = _MAX_AI_CONTEXT_CHARS) -> str:
    """Redact credential-shaped strings and bound untrusted repository evidence."""
    sanitized = value
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized[:limit]


def ai_config_from_env() -> AIConfig:
    """Build the only permitted provider config, retaining legacy aliases briefly.

    ``HUNDREDWAYS_AI_*`` takes precedence. ``SYNCBRIDGE_AI_*`` is read only as
    a compatibility alias and cannot be used to select another provider.
    """
    provider = (
        _first_env("HUNDREDWAYS_AI_PROVIDER", "SYNCBRIDGE_AI_PROVIDER")
        or OLLAMA_CLOUD_PROVIDER
    )
    base_url = (
        _first_env("HUNDREDWAYS_AI_BASE_URL", "SYNCBRIDGE_AI_BASE_URL")
        or OLLAMA_CLOUD_BASE_URL
    )
    api_key = _first_env(
        "HUNDREDWAYS_AI_API_KEY", "SYNCBRIDGE_AI_API_KEY", "OLLAMA_API_KEY"
    )
    model = (
        _first_env("HUNDREDWAYS_AI_MODEL", "SYNCBRIDGE_AI_MODEL")
        or OLLAMA_CLOUD_DEFAULT_MODEL
    )
    cfg = AIConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        provider=provider,
    )
    validate_provider(cfg)
    cfg.provider = OLLAMA_CLOUD_PROVIDER
    return cfg


class AIEngine:
    def __init__(self, cfg: AIConfig | None = None):
        self.cfg = cfg or ai_config_from_env()
        validate_provider(self.cfg)

    @property
    def available(self) -> bool:
        return bool(self.cfg.api_key)

    def model_for_stage(self, stage: str) -> str:
        """Resolve the model a pipeline stage should use (per-stage override
        first, then the configured default)."""
        return self.cfg.stage_models.get(stage, self.cfg.model)

    def _chat(self, system: str, user: str, model: str | None = None) -> str:
        """Request plain text only; Ollama Cloud structured output is unsupported."""
        import httpx

        validate_provider(self.cfg)
        resp = httpx.post(
            f"{self.cfg.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.cfg.api_key}"},
            json={
                "model": model or self.cfg.model,
                "messages": [
                    {"role": "system", "content": sanitize_ai_context(system)},
                    {"role": "user", "content": sanitize_ai_context(user)},
                ],
                "temperature": 0.2,
            },
            timeout=self.cfg.timeout,
        )
        resp.raise_for_status()
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(
                "Ollama Cloud response did not contain a plain-text completion"
            ) from exc
        if not isinstance(content, str):
            raise ValueError("Ollama Cloud completion must be plain text")
        return content.strip()

    # -- public API ----------------------------------------------------------

    def summarize_commit(self, repo: str, sha: str, subject: str, diff_preview: str) -> str:
        if not self.available:
            return self._fallback_commit_summary(sha, subject, diff_preview)
        system = (
            "You summarize git commits for a fork-sync pipeline. Be concise, "
            "one short paragraph, 2-4 sentences. Note anything risky to port "
            "(security, binaries, lockfiles, renames, brand-token changes)."
        )
        user = f"Commit {sha}: {subject}\n\nDiff preview (truncated):\n{diff_preview[:4000]}"
        try:
            return self._chat(system, user)
        except Exception as exc:
            fallback = self._fallback_commit_summary(sha, subject, diff_preview)
            return f"{fallback} [AI unavailable: {exc}]"

    def review_gap(self, report: GapReport, repo: str) -> str:
        if not self.available:
            return self._fallback_gap_summary(report)
        up_only = [e.path for e in report.upstream_only()]
        changed = [e.path for e in report.changed()]
        violations = [e.path for e in report.violations()]
        user = (
            f"Gap report:\n- upstream-only files: {len(up_only)}\n"
            f"- changed files: {len(changed)}\n- brand violations: {len(violations)}\n"
            f"Sample upstream-only: {up_only[:10]}\n"
            f"Sample changed: {changed[:10]}\n"
            f"Violations: {violations[:10]}"
        )
        try:
            return self._chat(
                "You are SyncBridge's gap reviewer for the Nastech fork of Hermes. "
                "Advise on what to port first, what to skip, and any branding hazards.",
                user,
            )
        except Exception as exc:
            return self._fallback_gap_summary(report) + f" [AI unavailable: {exc}]"

    def review_port(self, report: VerifyReport, sha: str) -> str:
        if not self.available:
            failed = ", ".join(f.path for f in report.failed[:5])
            if report.failed:
                return f"Port of {sha}: FAILED parity gate. Failing files: {failed or 'none'}."
            return f"Port of {sha}: PASSED ({report.summary()})."
        try:
            failed = ", ".join(f.path for f in report.failed[:10])
            return self._chat(
                "You review ported commits for a rebrand-safe sync pipeline. "
                "Confirm or flag: correctness, branding compliance, locked files.",
                f"Ported commit {sha}. Parity: {report.summary()}. Failed: {failed}.",
            )
        except Exception as exc:
            return f"Port of {sha}: {report.summary()} [AI review unavailable: {exc}]"

    def review_stage(self, stage: str, context: str) -> str:
        """Review one of the 15 pipeline stages with the per-stage model.

        ``context`` is stage-specific context (diff stats, violation list,
        gate results, ...).  Returns a plain-language assessment.  Uses
        ``model_for_stage(stage)`` so a per-stage override can back this
        stage with a cheaper model while the main model stays frontier.
        """
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
        if not self.available:
            return self._fallback_stage_review(stage, context)
        model = self.model_for_stage(stage)
        try:
            return self._chat(
                f"You are SyncBridge's {stage} stage reviewer for a rebrand-safe "
                "fork-sync pipeline. Assess the stage's output for correctness, "
                "branding compliance, and anything that should block the update.",
                f"Stage '{stage}' output:\n{context[:4000]}",
                model=model,
            )
        except Exception as exc:
            return self._fallback_stage_review(stage, context) + f" [AI unavailable: {exc}]"

    # -- deterministic fallbacks --------------------------------------------

    @staticmethod
    def _fallback_stage_review(stage: str, context: str) -> str:
        return f"Stage '{stage}': {context[:300] or 'no output'}."

    @staticmethod
    def _fallback_commit_summary(sha: str, subject: str, diff_preview: str) -> str:
        lines = diff_preview.splitlines()
        add = sum(
            1 for line in lines if line.startswith("+") and not line.startswith("+++")
        )
        rem = sum(
            1 for line in lines if line.startswith("-") and not line.startswith("---")
        )
        return f"{sha[:8]} {subject} — approx +{add}/-{rem} lines."

    @staticmethod
    def _fallback_gap_summary(report: GapReport) -> str:
        return (
            f"Gap {report.upstream_commit[:8]} vs {report.nastech_commit[:8]}: "
            f"{report.summary}."
        )
