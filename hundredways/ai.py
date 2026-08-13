"""AI module: LLM-powered diff summaries and port review.

Wraps an LLM (OpenAI-compatible chat completions) to:

  * summarize what a Hermes commit actually changes in plain language;
  * review a ported diff for correctness and branding compliance;
  * classify whether a commit is "safe to port" vs "needs human eyes".

The module is optional: when no API key / provider is configured, the CLI
falls back to deterministic summaries built from commit metadata.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .analyzer import GapReport
from .verify import VerifyReport


@dataclass
class AIConfig:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout: int = 60


def ai_config_from_env() -> AIConfig:
    return AIConfig(
        base_url=os.getenv("SYNCBRIDGE_AI_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.getenv("SYNCBRIDGE_AI_API_KEY", ""),
        model=os.getenv("SYNCBRIDGE_AI_MODEL", "gpt-4o-mini"),
    )


class AIEngine:
    def __init__(self, cfg: AIConfig | None = None):
        self.cfg = cfg or ai_config_from_env()

    @property
    def available(self) -> bool:
        return bool(self.cfg.api_key)

    def _chat(self, system: str, user: str) -> str:
        import httpx

        resp = httpx.post(
            f"{self.cfg.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.cfg.api_key}"},
            json={
                "model": self.cfg.model,
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

    # -- deterministic fallbacks --------------------------------------------

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
