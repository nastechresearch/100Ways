"""Notifications: Telegram bot + opencode agent hook.

Sends sync events (new commits, gaps, brand violations, port results) to a
Telegram chat and/or to the opencode agent CLI so Nastech can react.  All
delivery is best-effort: a notifier failure never crashes a sync run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass
class NotifyConfig:
    telegram_token: str = ""
    telegram_chat_id: str = ""
    agent_command: str = ""          # e.g. "opencode run"
    enabled_telegram: bool = True
    enabled_agent: bool = True


@dataclass
class Notification:
    title: str
    body: str
    level: str = "info"  # info | warn | error
    kind: str = "sync"   # sync | gap | violation | port | watch

    def render(self) -> str:
        icon = {"info": "ℹ️", "warn": "⚠️", "error": "🚨"}.get(self.level, "ℹ️")
        return f"{icon} *{self.title}*\n\n{self.body}"


class Notifier:
    def __init__(self, cfg: NotifyConfig):
        self.cfg = cfg

    def notify(self, n: Notification) -> None:
        if self.cfg.enabled_telegram and self.cfg.telegram_token:
            try:
                self._telegram(n)
            except Exception as exc:  # pragma: no cover - best effort
                print(f"[notifier] telegram failed: {exc}")
        if self.cfg.enabled_agent and self.cfg.agent_command:
            try:
                self._agent(n)
            except Exception as exc:  # pragma: no cover - best effort
                print(f"[notifier] agent hook failed: {exc}")

    # -- Telegram -----------------------------------------------------------

    def _telegram(self, n: Notification) -> None:
        import httpx

        token = self.cfg.telegram_token
        chat_id = self.cfg.telegram_chat_id
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        httpx.post(
            url,
            json={
                "chat_id": chat_id,
                "text": n.render(),
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )

    def send_telegram(self, text: str, parse_mode: str = "Markdown") -> None:
        import httpx

        httpx.post(
            f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage",
            json={
                "chat_id": self.cfg.telegram_chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )

    # -- opencode agent ------------------------------------------------------

    def _agent(self, n: Notification) -> None:
        """Send the notification into the opencode agent as a prompt."""
        if not shutil.which("opencode"):
            raise RuntimeError("opencode CLI not on PATH")
        prompt = (
            f"SyncBridge event ({n.kind}/{n.level}): {n.title}\n\n{n.body}\n\n"
            "Review this sync report. If it describes work you should perform "
            "(porting commits, fixing brand violations, approving gaps), say so "
            "and tell the user the next concrete step."
        )
        subprocess.run(
            [self.cfg.agent_command, prompt],
            capture_output=True,
            text=True,
            timeout=300,
        )


def notifier_from_env() -> Notifier:
    return Notifier(
        NotifyConfig(
            telegram_token=os.getenv("HUNDREDWAYS_TELEGRAM_TOKEN") or os.getenv("SYNCBRIDGE_TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.getenv("HUNDREDWAYS_TELEGRAM_CHAT_ID") or os.getenv("SYNCBRIDGE_TELEGRAM_CHAT_ID", ""),
            agent_command=os.getenv("HUNDREDWAYS_AGENT_COMMAND") or os.getenv("SYNCBRIDGE_AGENT_COMMAND", "opencode run"),
        )
    )
