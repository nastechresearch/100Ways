"""Live watcher: polls upstream, detects new commits, reports gaps.

Runs as a loop (``100ways watch``) or a one-shot check (``100ways
check``).  On each cycle it:

  1. fetches the upstream remote (unless ``--no-fetch``);
  2. computes commits since the last ported point;
  3. runs a gap analysis against our branch;
  4. notifies Telegram + agent when anything is new;
  5. records its last-seen state so it can detect *new* events.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .analyzer import GapReport, analyze
from .notifier import Notification, Notifier
from .rules import BrandingRules
from .verify import _git, _git_ok


@dataclass
class WatcherConfig:
    repo: str
    upstream: str = "upstream/main"
    branch: str = "sync-upstream"
    state_file: str = "config/state.json"
    fetch: bool = True
    interval: int = 300  # seconds between cycles in watch mode


@dataclass
class WatchEvent:
    kind: str  # new-commits | gap | violation | none
    title: str
    body: str
    commit_count: int = 0
    report: GapReport | None = None


class Watcher:
    def __init__(self, cfg: WatcherConfig, notifier: Notifier | None = None):
        self.cfg = cfg
        self.notifier = notifier or Notifier(NotifyConfig())
        self.state_path = Path(self.cfg.state_file)

    # -- state ---------------------------------------------------------------

    def _load_state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"last_upstream_head": "", "last_nastech_head": ""}

    def _save_state(self, upstream_head: str, nastech_head: str) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {"last_upstream_head": upstream_head, "last_nastech_head": nastech_head},
                indent=2,
            )
        )

    # -- cycle ---------------------------------------------------------------

    def cycle(self) -> WatchEvent | None:
        if self.cfg.fetch:
            _git(self.cfg.repo, "fetch", "upstream")

        upstream_head = _git_ok(self.cfg.repo, "rev-parse", self.cfg.upstream).strip()
        nastech_head = _git_ok(self.cfg.repo, "rev-parse", "HEAD").strip()
        state = self._load_state()

        new_commits = _git_ok(
            self.cfg.repo, "rev-list", "--reverse", f"HEAD..{self.cfg.upstream}"
        ).split()

        event = None
        if new_commits:
            subjects = []
            for sha in new_commits:
                subj = _git_ok(self.cfg.repo, "log", "-1", "--format=%s", sha).strip()
                subjects.append(f"  {sha[:8]} {subj}")
            is_new = upstream_head != state.get("last_upstream_head", "")
            title = f"{len(new_commits)} new upstream commit(s)"
            body = "\n".join(subjects)
            if is_new:
                event = WatchEvent(
                    kind="new-commits",
                    title=title,
                    body=body,
                    commit_count=len(new_commits),
                )
                self.notifier.notify(Notification(title, body, kind="watch"))

        # gap analysis runs regardless so reports stay fresh
        report = analyze(upstream_head, nastech_head, self.cfg.repo, BrandingRules())
        if report.violations() and report.upstream_commit != report.nastech_commit:
            paths = ", ".join(e.path for e in report.violations()[:10])
            violation_event = WatchEvent(
                kind="violation",
                title=f"{len(report.violations())} brand violations",
                body=f"Files with Hermes tokens left behind:\n{paths}",
                report=report,
            )
            if event is None:
                event = violation_event
            self.notifier.notify(
                Notification(violation_event.title, violation_event.body, level="warn", kind="violation")
            )

        self._save_state(upstream_head, nastech_head)
        return event

    # -- entry points ---------------------------------------------------------

    def check_once(self) -> WatchEvent | None:
        return self.cycle()

    def watch(self, max_cycles: int | None = None) -> None:
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            try:
                event = self.cycle()
                if event:
                    print(f"[watch] {event.kind}: {event.title}")
                else:
                    print("[watch] no new events")
            except Exception as exc:  # pragma: no cover
                print(f"[watch] cycle error: {exc}")
            cycles += 1
            if max_cycles is None or cycles < max_cycles:
                time.sleep(self.cfg.interval)
