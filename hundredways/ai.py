"""AI module: LLM-powered diff summaries and port review.

Wraps an LLM (OpenAI-compatible chat completions) to:

  * summarize what a Hermes commit actually changes in plain language;
  * review a ported diff for correctness and branding compliance;
  * classify whether a commit is "safe to port" vs "needs human eyes";
  * review any of the 15 pipeline stages by name (stage-aware AI).

The module is optional: when no API key / provider is configured, the CLI
falls back to deterministic summaries built from commit metadata.

Providers: OpenAI by default, or ``ollama-cloud`` (Ollama Cloud's
OpenAI-compatible ``https://ollama.com/v1`` endpoint).  Ollama Cloud's
default model is ``gemma4:31b-cloud`` — set ``SYNCBRIDGE_AI_MODEL`` to
switch (e.g. ``deepseek-v4:cloud``).  The ollama-cloud key comes from
``OLLAMA_API_KEY``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .analyzer import GapReport
from .updates import STAGES
from .verify import VerifyReport


@dataclass
class AIConfig:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    provider: str = "openai"
    timeout: int = 60
    # Per-stage model overrides: {"<stage>": "<model>"}.  A stage without an
    # override uses ``model``.  Lets each of the 15 pipeline stages pick a
    # cheaper/faster model while the main model stays frontier.
    stage_models: dict[str, str] = field(default_factory=dict)


# Ollama Cloud's OpenAI-compatible endpoint + default model.  gemma4:31b-cloud
# is a 31B open model; the id follows Ollama-style ``gemma4:<size>-cloud``
# naming (see agent/model_metadata.py "gemma4": 256000).
OLLAMA_CLOUD_BASE_URL = "https://ollama.com/v1"
OLLAMA_CLOUD_DEFAULT_MODEL = "gemma4:31b-cloud"


def ai_config_from_env() -> AIConfig:
    """Build AIConfig from env, with an ollama-cloud shortcut.

    ``SYNCBRIDGE_AI_PROVIDER=ollama-cloud`` (or leaving ``SYNCBRIDGE_AI_BASE_URL``
    pointing at ollama.com) switches to Ollama Cloud and reads
    ``OLLAMA_API_KEY``.  ``SYNCBRIDGE_AI_MODEL`` always wins for the model.
    """
    provider = os.getenv("SYNCBRIDGE_AI_PROVIDER", "").strip().lower() or "openai"
    base_url = os.getenv("SYNCBRIDGE_AI_BASE_URL", "")
    api_key = os.getenv("SYNCBRIDGE_AI_API_KEY", "")
    model = os.getenv("SYNCBRIDGE_AI_MODEL", "")

    if provider in {"ollama", "ollama-cloud", "ollama_cloud"} or "ollama.com" in base_url:
        if not base_url:
            base_url = OLLAMA_CLOUD_BASE_URL
        if not api_key:
            api_key = os.getenv("OLLAMA_API_KEY", "")
        if not model:
            model = OLLAMA_CLOUD_DEFAULT_MODEL
        provider = "ollama-cloud"

    if not model:
        model = "gpt-4o-mini"
    if not base_url:
        base_url = "https://api.openai.com/v1"

    return AIConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        provider=provider,
    )


class AIEngine:
    def __init__(self, cfg: AIConfig | None = None):
        self.cfg = cfg or ai_config_from_env()

    @property
    def available(self) -> bool:
        return bool(self.cfg.api_key)

    def model_for_stage(self, stage: str) -> str:
        """Resolve the model a pipeline stage should use (per-stage override
        first, then the configured default)."""
        return self.cfg.stage_models.get(stage, self.cfg.model)

    def _chat(self, system: str, user: str, model: str | None = None) -> str:
        import httpx

        resp = httpx.post(
            f"{self.cfg.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.cfg.api_key}"},
            json={
                "model": model or self.cfg.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
            },
            timeout=self.cfg.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

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
            return self._fallback_commit_summary(sha, subject, diff_preview) + f" [AI unavailable: {exc}]"

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
        add = sum(1 for ln in diff_preview.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        rem = sum(1 for ln in diff_preview.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        return f"{sha[:8]} {subject} — approx +{add}/-{rem} lines."

    @staticmethod
    def _fallback_gap_summary(report: GapReport) -> str:
        return (
            f"Gap {report.upstream_commit[:8]} vs {report.nastech_commit[:8]}: "
            f"{report.summary}."
        )
