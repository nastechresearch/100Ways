"""The verbs that carry the shipped capabilities, and what each one refuses to do.

`narrative`, `pubconfig`, `why-red`, `telegram` and `lintsim` sit thin over modules the
pipeline already uses, so these tests are about the surface: the answer a commander or a
CI job gets must be the library's own answer, unchanged.  Two properties matter more than
coverage -- a command that predicts a red never weakens the gate it is reporting on, and a
command that speaks to Telegram only speaks when it was given a chat to speak to.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hundredways import cli as cli_module
from hundredways import pubconfig as pc
from hundredways import red_classifier as rc
from hundredways.cli import Cli, _inventory_from_json, build_parser
from hundredways.forklint import FINDING, ForkLintIssue
from hundredways.narrative import title_from_manifest
from hundredways.telegram_commands import COMMANDS

MANIFEST = {
    "upstream_sha": "b" * 40,
    "gate": True,
    "commit_subjects": [
        "feat: add scheduled workspace sync",
        "fix: stop retry loop on expired session",
    ],
    "verify": {"total": 4200, "passed": 4200, "locked": 12, "failed": [], "pass_ratio": 1.0},
    "brand": {"total": 4212, "renamed": 80, "rewritten": 140, "errors": 0},
    "fork": {"violations": 0, "preserved_paths": []},
    "source_delta": {"counts": {"added": 3, "modified": 9, "deleted": 1, "renamed": 2}},
    "source_provenance": {"acquisition": "fresh-direct-clone"},
}

ACTIONS = TELEGRAM = ["--repo", "/nowhere", "telegram"]


NARR = ["narrative", "--manifest", "manifest.json", "--summary", "summary.md"]
ACTIONS = TELEGRAM = ["--repo", "/nowhere", "telegram"]


def unreadable(reason: str):
    """Raise the way the module does when the settings cannot be read at all."""
    raise pc.PublisherConfigurationError(reason)


def invoke(argv: list[str]) -> Cli:
    """Build the command the way ``main()`` does, without a process boundary."""
    return Cli(build_parser().parse_args(argv))


# -- the surface exists ------------------------------------------------------


@pytest.mark.parametrize(
    "argv,func",
    [
        (["narrative", "--manifest", "m.json", "--summary", "s.md"], "cmd_narrative"),
        (["pubconfig", "--tree", ".", "--inventory", "i.json"], "cmd_pubconfig"),
        (["why-red", "--runs", "r.json"], "cmd_why_red"),
        (["telegram", "menu"], "cmd_telegram"),
        (["lintsim", "--candidate", "tree"], "cmd_lintsim"),
    ],
)
def test_each_new_verb_dispatches_to_a_real_method(argv, func):
    assert build_parser().parse_args(argv).func == func
    assert callable(getattr(Cli, func))


def test_every_verb_that_parses_is_documented_in_the_help():
    help_text = build_parser().format_help()
    for verb in ("narrative", "pubconfig", "why-red", "telegram", "lintsim"):
        assert f"  {verb}" in help_text, f"{verb} parses but --help does not advertise it"
        assert f"\n{verb:<10}" in cli_module.__doc__, f"{verb} is missing from the command list"


# -- narrative ---------------------------------------------------------------


@pytest.fixture
def manifest_tree(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (tmp_path / "summary.md").write_text("## Verification\nall gates green\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_narrative_writes_the_title_the_engine_computed(manifest_tree):
    cli = invoke(
        [
            "narrative",
            "--manifest",
            "manifest.json",
            "--summary",
            "summary.md",
            "--title-out",
            "title.txt",
            "--body-out",
            "body.md",
        ]
    )

    cli.cmd_narrative()

    title = (manifest_tree / "title.txt").read_text(encoding="utf-8").strip()
    assert title == title_from_manifest(MANIFEST)
    body = (manifest_tree / "body.md").read_text(encoding="utf-8")
    assert "all gates green" in body
    assert "Editorial note" not in body


def test_narrative_says_so_when_the_model_has_nothing_to_add(manifest_tree, monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "garnish", lambda manifest, *, engine=None: "")

    invoke(NARR + ["--ai"]).cmd_narrative()

    assert "stays the engine's own" in capsys.readouterr().err


def test_narrative_appends_only_the_paragraph_a_reachable_model_offered(manifest_tree, monkeypatch):
    monkeypatch.setattr(
        cli_module, "garnish", lambda manifest, *, engine=None: "The run moved on schedule."
    )

    invoke(
        [
            "narrative",
            "--manifest",
            "manifest.json",
            "--summary",
            "summary.md",
            "--body-out",
            "body.md",
            "--ai",
        ]
    ).cmd_narrative()

    body = (manifest_tree / "body.md").read_text(encoding="utf-8")
    assert "The run moved on schedule." in body
    assert "Editorial note" in body


# -- pubconfig ---------------------------------------------------------------


def _snapshot(tmp_path) -> str:
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(
            {
                "repo_secrets": ["APPLE_KEY_ID"],
                "repo_vars": ["APPLE_STORE_ID"],
                "environments": {
                    "release-signing": {"secrets": ["AZURE_TENANT_ID"], "vars": ["APPLE_ISSUER"]}
                },
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def test_the_snapshot_loader_reads_the_names_back_exactly_as_the_dataclass_holds_them(tmp_path):
    inventory = _inventory_from_json(_snapshot(tmp_path))

    assert inventory.provides("secrets", "APPLE_KEY_ID")
    assert inventory.provides("vars", "APPLE_STORE_ID")
    assert inventory.provides("secrets", "AZURE_TENANT_ID", "release-signing")
    assert inventory.provides("vars", "APPLE_ISSUER", "release-signing")
    assert not inventory.provides("secrets", "NOT_PROVIDED")
    assert inventory.total_names == 4


def _workflows(tmp_path, body: str) -> Path:
    root = tmp_path / "fork"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "release.yml").write_text(body, encoding="utf-8")
    return root


UNREADABLE_LANE = """name: release
on:
  workflow_dispatch:
jobs:
  bundle:
    environment: release-signing
    steps:
      - name: build
        env:
          APPLE_SECRET: ${{ secrets.NOT_PROVIDED }}
        run: build
"""


def test_pubconfig_exits_nonzero_when_a_run_can_still_read_an_empty_string(tmp_path, capsys):
    root = _workflows(tmp_path, UNREADABLE_LANE)
    cli = invoke(
        ["pubconfig", "--tree", str(root), "--inventory", _snapshot(tmp_path),
         "--project", "o/r"]
    )

    with pytest.raises(SystemExit) as stop:
        cli.cmd_pubconfig()

    assert stop.value.code == 1
    out = capsys.readouterr().out
    assert "live-missing" in out
    assert "NOT_PROVIDED" in out
    assert "release-signing" in out


def test_pubconfig_reports_a_clean_repository_and_exits_zero(tmp_path, capsys):
    root = _workflows(
        tmp_path,
        UNREADABLE_LANE.replace("NOT_PROVIDED", "APPLE_KEY_ID"),
    )

    invoke(["pubconfig", "--tree", str(root), "--inventory", _snapshot(tmp_path)]).cmd_pubconfig()

    assert "every credential name" in capsys.readouterr().out


def test_pubconfig_refuses_to_guess_where_the_credentials_are(tmp_path):
    root = _workflows(tmp_path, "jobs: {}\n")
    cli = invoke(["pubconfig", "--tree", str(root)])

    with pytest.raises(SystemExit) as stop:
        cli.cmd_pubconfig()

    assert stop.value.code == 2


def test_pubconfig_writes_the_machine_form_and_still_names_no_value(tmp_path, capsys):
    root = _workflows(tmp_path, UNREADABLE_LANE)
    cli = invoke(
        [
            "pubconfig",
            "--tree",
            str(root),
            "--inventory",
            _snapshot(tmp_path),
            "--json-out",
            str(tmp_path / "pub.json"),
        ]
    )

    with pytest.raises(SystemExit) as stop:
        cli.cmd_pubconfig()

    assert stop.value.code == 1, "an actionable verdict must not look like a pass"
    payload = json.loads((tmp_path / "pub.json").read_text(encoding="utf-8"))
    assert payload["actionable"] == 1
    assert payload["findings"][0]["name"] == "NOT_PROVIDED"
    assert set(payload) == {"gate", "repo", "actionable", "findings"}
    assert "APPLE_SECRET" not in json.dumps(payload), "a value's line must not be echoed"


def test_pubconfig_stops_when_the_settings_cannot_be_read(tmp_path, monkeypatch, capsys):
    root = _workflows(tmp_path, "jobs: {}\n")
    monkeypatch.setattr(
        cli_module,
        "fetch_inventory",
        lambda repo, **kwargs: unreadable("gh is not here"),
    )
    cli = invoke(["pubconfig", "--tree", str(root), "--fetch", "--project", "o/r"])

    with pytest.raises(SystemExit) as stop:
        cli.cmd_pubconfig()

    assert stop.value.code == 1
    assert "gh is not here" in capsys.readouterr().err


# -- why-red -----------------------------------------------------------------


def _red_payload() -> dict:
    return {
        "total_count": 1,
        "check_runs": [
            {
                "id": 501,
                "name": "Python tests (3.13)",
                "conclusion": "failure",
                "runner_name": "ubuntu-latest",
                "output": {
                    "title": "Process completed with exit code 1.",
                    "text": ">       assert max(gaps) < (HOLD_S / 3)\n"
                    "E       AssertionError: assert 0.78 < 0.5\n"
                    "FAILED tests/gateway/test_api_admission_off_loop.py::test_admission[chat]\n",
                },
            }
        ],
    }


def test_why_red_prints_exactly_what_the_classifier_concluded(tmp_path, monkeypatch, capsys):
    (tmp_path / "runs.json").write_text(json.dumps(_red_payload()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    expected = rc.render(rc.classify_runs(_red_payload()))

    invoke(["why-red", "--runs", "runs.json"]).cmd_why_red()

    assert expected.strip()
    assert capsys.readouterr().out.strip() == expected.strip()


def test_why_red_names_a_payload_with_nothing_red_in_it(tmp_path, monkeypatch, capsys):
    (tmp_path / "runs.json").write_text(
        json.dumps({"total_count": 1, "check_runs": [{"name": "lint", "conclusion": "success"}]}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    invoke(["why-red", "--runs", "runs.json"]).cmd_why_red()

    assert "no red check" in capsys.readouterr().out


def test_why_red_forwards_the_upstream_side_it_was_handed(tmp_path, monkeypatch):
    (tmp_path / "runs.json").write_text(json.dumps(_red_payload()), encoding="utf-8")
    (tmp_path / "up.json").write_text(
        json.dumps(
            {
                "total_count": 1,
                "check_runs": [
                    {
                        "name": "Python tests (3.13)",
                        "conclusion": "success",
                        "labels": ["blacksmith-32vcpu"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "delta.txt").write_text("# comment only\ngateway/router.py\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    seen: dict = {}

    def fake(payload, **kwargs):
        seen.update(kwargs)
        return ()

    monkeypatch.setattr(cli_module, "classify_runs", fake)

    invoke(
        [
            "why-red",
            "--runs",
            "runs.json",
            "--upstream-runs",
            "up.json",
            "--changed-files",
            "delta.txt",
            "--baseline",
            "a" * 40,
            "--current",
            "b" * 40,
            "--checkout",
            "tree",
        ]
    ).cmd_why_red()

    assert seen["upstream_changed_files"] == ["gateway/router.py"]
    assert seen["baseline_sha"] == "a" * 40
    assert seen["current_sha"] == "b" * 40
    assert seen["checkout_path"] == "tree"
    assert seen["upstream_runs"]["check_runs"][0]["labels"] == ["blacksmith-32vcpu"]


# -- telegram ----------------------------------------------------------------


def test_the_menu_advertises_every_registered_command_and_nothing_else(capsys):
    invoke(ACTIONS + ["menu"]).cmd_telegram()

    out = capsys.readouterr().out
    for command in COMMANDS:
        assert command.name in out


def test_check_reports_the_trust_a_verb_needs_without_running_anything(capsys):
    invoke(TELEGRAM + ["check", "--message", "/edit rules.py widen the cap"]).cmd_telegram()

    out = capsys.readouterr().out
    assert "needs: edit" in out
    assert "operate no" in out
    assert "edit yes" in out


def test_check_says_aloud_when_a_line_names_nothing(capsys):
    invoke(TELEGRAM + ["check", "--message", "please be green"]).cmd_telegram()

    assert "model" in capsys.readouterr().out


def test_test_verb_sends_nothing_without_a_chat_to_send_to(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    sent: list = []
    monkeypatch.setattr(cli_module, "send_telegram", lambda *a, **k: sent.append(a))
    cli = invoke(TELEGRAM + ["test", "--message", "probe"])

    with pytest.raises(SystemExit) as stop:
        cli.cmd_telegram()

    assert stop.value.code == 1
    assert sent == []
    assert "chat id" in capsys.readouterr().err


def test_test_verb_delivers_and_reports_a_refusal_without_raising_it(monkeypatch, capsys):
    from hundredways.telegram_transport import TelegramDeliveryError

    sent: list = []
    monkeypatch.setattr(
        cli_module, "send_telegram", lambda token, chat, text, **k: sent.append((token, chat, text))
    )

    invoke(
        TELEGRAM + ["test", "--token", "t", "--chat-id", "42", "--message", "probe"]
    ).cmd_telegram()

    assert sent == [("t", "42", "probe")]
    assert "delivered" in capsys.readouterr().out

    def refuse(*a, **k):
        raise TelegramDeliveryError("telegram refused")

    monkeypatch.setattr(cli_module, "send_telegram", refuse)
    cli = invoke(TELEGRAM + ["test", "--token", "t", "--chat-id", "42"])
    with pytest.raises(SystemExit) as stop:
        cli.cmd_telegram()

    assert stop.value.code == 1
    assert "not delivered" in capsys.readouterr().err


# -- lintsim -----------------------------------------------------------------


def _issue(blocking: bool = True) -> ForkLintIssue:
    return ForkLintIssue(
        script="scripts/ci/check_agents_md_size.py",
        path="AGENTS.md",
        detail="chain over the cap by 40 chars",
        blocking=blocking,
        status=FINDING,
    )


def test_lintsim_exits_on_a_blocking_finding(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "run_fork_lint", lambda fork, candidate, upstream: [_issue()])
    cli = invoke(["lintsim", "--candidate", str(tmp_path), "--fork", str(tmp_path)])

    with pytest.raises(SystemExit) as stop:
        cli.cmd_lintsim()

    assert stop.value.code == 1
    assert "BLOCKING" in capsys.readouterr().out


def test_lintsim_passes_when_the_fork_contract_is_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "run_fork_lint", lambda fork, candidate, upstream: [])

    invoke(["lintsim", "--candidate", str(tmp_path), "--fork", str(tmp_path)]).cmd_lintsim()

    assert "fork-lint PASS" in capsys.readouterr().out


def test_lintsim_lints_the_repo_it_was_given_when_no_fork_is_named(tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        cli_module,
        "run_fork_lint",
        lambda fork, candidate, upstream: seen.update(fork=fork) or [],
    )

    invoke(["--repo", str(tmp_path), "lintsim", "--candidate", str(tmp_path)]).cmd_lintsim()

    assert seen["fork"] == str(tmp_path)
