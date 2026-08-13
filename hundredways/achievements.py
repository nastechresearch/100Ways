"""Achievements: gamified proof the pipeline works.

Every meaningful milestone unlocks an achievement.  Unlocks are recorded in
``config/achievements.json`` and surfaced by ``100ways achievements`` and the
dashboard.  The list is a contract about behavior (a gate passed, a violation
caught), not a snapshot - so it is safe to grow forever.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class Achievement:
    name: str
    description: str
    emoji: str = "🏆"


ACHIEVEMENTS: dict[str, Achievement] = {
    "first_gate_pass": Achievement("First Pass", "The parity gate passed for the first time.", "✅"),
    "gate_pass_10": Achievement("Double Digits", "The parity gate passed 10 times.", "🔟"),
    "gate_pass_100": Achievement("Century", "The parity gate passed 100 times.", "💯"),
    "first_violation": Achievement("Watchdog", "Caught a brand-rule violation (error 82).", "🐕"),
    "first_missing": Achievement("Lost Article", "Found an upstream file missing from Nastech (error 404).", "🗺️"),
    "first_extra": Achievement("Explainable", "Every extra file got an explanation (error 84).", "🧾"),
    "port_1": Achievement("First Port", "Ported the first upstream commit.", "🚢"),
    "first_pull": Achievement("First Pull", "Ran the first upstream pull + report.", "🔽"),
    "pulls": Achievement("Puller", "Cumulative upstream pulls.", "🔁"),
    "port_10": Achievement("Squadron", "Ported 10 upstream commits.", "⛵"),
    "port_100": Achievement("Fleet", "Ported 100 upstream commits.", "⚓"),
    "scan_1000": Achievement("Deep Scan", "Verified 1000+ files in a single run.", "🔍"),
    "admin_edit": Achievement("Sysadmin", "An admin edited a branding rule in the dashboard.", "👑"),
    "polyglot": Achievement("Polyglot", "Generated reports in 3 or more languages.", "🌐"),
    "first_report": Achievement("Genesis", "Wrote the first markdown report.", "📜"),
    "shipped_pr": Achievement("Courier", "Shipped a verified update as a pull request.", "📦"),
}


def _state_path(home: str) -> str:
    return os.path.join(home, "config", "achievements.json")


class Achievements:
    def __init__(self, home: str):
        self.home = home
        self.path = _state_path(home)
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {"unlocked": [], "counters": {}}
        data.setdefault("unlocked", [])
        data.setdefault("counters", {})
        return data

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)

    def unlocked(self) -> list[str]:
        return list(self._data["unlocked"])

    def is_unlocked(self, name: str) -> bool:
        return name in self._data["unlocked"]

    def count(self, name: str) -> int:
        return int(self._data["counters"].get(name, 0))

    def bump(self, name: str, amount: int = 1) -> list[str]:
        """Increment a counter and unlock any threshold achievements that
        it now crosses.  Returns the newly unlocked names."""
        self._data["counters"][name] = self._data["counters"].get(name, 0) + amount
        total = self._data["counters"][name]
        threshold_map = {
            "gate_passes": {"gate_pass_10": 10, "gate_pass_100": 100},
            "ports": {"port_10": 10, "port_100": 100},
        }
        new = []
        for ach_name, threshold in threshold_map.get(name, {}).items():
            if total >= threshold and ach_name not in self._data["unlocked"]:
                self._data["unlocked"].append(ach_name)
                new.append(ach_name)
        self._save()
        return new

    def unlock(self, name: str, reason: str = "") -> bool:
        """Unlock an achievement (no-op if already unlocked).  Returns True
        when this call performed the unlock."""
        if name not in ACHIEVEMENTS:
            return False
        if name in self._data["unlocked"]:
            return False
        self._data["unlocked"].append(name)
        if reason:
            self._data.setdefault("reasons", {})[name] = reason
        self._save()
        return True

    def list_all(self) -> list[tuple[str, Achievement, bool]]:
        return [(k, v, k in self._data["unlocked"]) for k, v in ACHIEVEMENTS.items()]

    def trophies(self) -> list[Achievement]:
        """The unlocked achievements, in catalog order, as a trophy shelf.
        A trophy is an achievement that has been earned - the README and the
        dashboard both render this shelf."""
        return [meta for _k, meta, unlocked in self.list_all() if unlocked]

    def apply_event(self, event: str) -> list[str]:
        """Map a pipeline event to achievement unlocks; returns new names."""
        unlocked = []
        if event == "gate_pass":
            unlocked.extend(self.bump("gate_passes"))
            if self.unlock("first_gate_pass", "first passing gate run"):
                unlocked.append("first_gate_pass")
        elif event == "violation":
            if self.unlock("first_violation", "brand rule violated in an article"):
                unlocked.append("first_violation")
        elif event == "missing":
            if self.unlock("first_missing", "an upstream article is missing here"):
                unlocked.append("first_missing")
        elif event == "extra_explained":
            if self.unlock("first_extra", "every extra file carries an explanation"):
                unlocked.append("first_extra")
        elif event == "pull":
            unlocked.extend(self.bump("pulls"))
            if self.unlock("first_pull", "first upstream pull + report run"):
                unlocked.append("first_pull")
        elif event == "port":
            unlocked.extend(self.bump("ports"))
            if self.unlock("port_1", "first upstream commit ported"):
                unlocked.append("port_1")
        elif event == "scan_1000":
            if self.unlock("scan_1000", "1000+ files verified in one run"):
                unlocked.append("scan_1000")
        elif event == "admin_edit":
            if self.unlock("admin_edit", "rules edited with admin permission"):
                unlocked.append("admin_edit")
        elif event == "polyglot":
            if self.unlock("polyglot", "reports in 3+ languages"):
                unlocked.append("polyglot")
        elif event == "report":
            if self.unlock("first_report", "first markdown report written"):
                unlocked.append("first_report")
        elif event == "shipped_pr":
            if self.unlock("shipped_pr", "verified update shipped as a PR"):
                unlocked.append("shipped_pr")
        return unlocked
