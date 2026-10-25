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


def local(tmp_path, *argv: str) -> Cli:
    """A verb run against a throwaway tree, the way a bare checkout would."""
    return invoke(["--repo", str(tmp_path), *argv])


# -- the surface exists ------------------------------------------------------


@pytest.mark.parametrize(
    "argv,func",
    [
        (["narrative", "--manifest", "m.json", "--summary", "s.md"], "cmd_narrative"),
        (["pubconfig", "--tree", ".", "--inventory", "i.json"], "cmd_pubconfig"),
        (["why-red", "--runs", "r.json"], "cmd_why_red"),
        (["telegram", "menu"], "cmd_telegram"),
        (["lintsim", "--candidate", "tree"], "cmd_lintsim"),
        (["urls", "--tree", "tree"], "cmd_urls"),
        (["surface", "--tree", "tree"], "cmd_surface"),
        (["verify-report", "--out", "v.md"], "cmd_verify_report"),
        (["errors", "--runs", "r.json"], "cmd_errors"),
        (["issues"], "cmd_issues"),
        (["discuss"], "cmd_discuss"),
        (["suggest"], "cmd_suggest"),
        (["table", "--source", "urls"], "cmd_table"),
    ],
)
def test_each_new_verb_dispatches_to_a_real_method(argv, func):
    assert build_parser().parse_args(argv).func == func
    assert callable(getattr(Cli, func))


def test_every_verb_that_parses_is_documented_in_the_help():
    help_text = build_parser().format_help()
    for verb in (
        "narrative",
        "pubconfig",
        "why-red",
        "telegram",
        "lintsim",
        "urls",
        "surface",
        "verify-report",
        "errors",
        "issues",
        "discuss",
        "suggest",
        "table",
    ):
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


# -- urls --------------------------------------------------------------------


def _link_tree(tmp_path, url: str) -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "README.md").write_text(f"See {url} for details.\n", encoding="utf-8")
    return root


def test_urls_fails_on_a_link_the_brand_map_still_rewrites(tmp_path, capsys):
    root = _link_tree(tmp_path, "https://hermes-agent.nousresearch.com/docs")
    cli = invoke(["--repo", str(tmp_path), "urls", "--tree", str(root)])

    with pytest.raises(SystemExit) as stop:
        cli.cmd_urls()

    assert stop.value.code == 1
    out = capsys.readouterr().out
    assert "BRAND_VIOLATION" in out
    assert "nastech-agent.nastechresearch.com" in out  # the fix it names


def test_urls_calls_an_offline_run_offline_instead_of_a_pass(tmp_path, capsys):
    root = _link_tree(tmp_path, "https://nastechresearch.com/store")

    invoke(["--repo", str(tmp_path), "urls", "--tree", str(root)]).cmd_urls()

    out = capsys.readouterr().out
    assert "not probed" in out
    assert "UNREACHABLE=1" in out  # named as not-judged, never as a pass
    assert "no finding" not in out


def test_urls_writes_the_machine_form_of_every_verdict(tmp_path):
    root = _link_tree(tmp_path, "https://example.com/docs")
    cli = local(tmp_path, "urls", "--tree", str(root), "--json-out", str(tmp_path / "u.json"))

    with pytest.raises(SystemExit) as stop:
        cli.cmd_urls()

    assert stop.value.code == 1, "a placeholder link is a verdict, not a shrug"
    payload = json.loads((tmp_path / "u.json").read_text(encoding="utf-8"))
    assert payload[0]["status"] == "PLACEHOLDER"
    assert payload[0]["probed"] is False
    assert payload[0]["path"] == "README.md"


def test_urls_refuses_to_invent_a_tree(tmp_path):
    cli = invoke(["--repo", str(tmp_path), "urls", "--tree", str(tmp_path / "absent")])

    with pytest.raises(SystemExit) as stop:
        cli.cmd_urls()

    assert stop.value.code == 2


# -- surface -----------------------------------------------------------------

VERB_APP = "import argparse\np = argparse.ArgumentParser()\np.add_subparsers().add_parser('run')\n"
ENV_APP = "import os\n\nKEY = os.environ['ORACLE_KEY']\n"


def test_surface_saves_a_baseline_and_finds_nothing_to_report(tmp_path, capsys):
    root = tmp_path / "tree"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "app.py").write_text(VERB_APP, encoding="utf-8")
    baseline = tmp_path / "surface.json"

    local(tmp_path, "surface", "--tree", str(root), "--save", str(baseline)).cmd_surface()

    assert baseline.is_file()
    assert "cli_verbs=1" in capsys.readouterr().out

    invoke(
        ["--repo", str(tmp_path), "surface", "--tree", str(root), "--baseline", str(baseline)]
    ).cmd_surface()

    assert "unchanged" in capsys.readouterr().out


def test_surface_exits_when_upstream_moved_something_the_fork_must_decide(tmp_path, capsys):
    root = tmp_path / "tree"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "app.py").write_text(VERB_APP, encoding="utf-8")
    baseline = tmp_path / "surface.json"
    invoke(
        ["--repo", str(tmp_path), "surface", "--tree", str(root), "--save", str(baseline)]
    ).cmd_surface()
    capsys.readouterr()
    (root / "pkg" / "env.py").write_text(ENV_APP, encoding="utf-8")
    cli = local(tmp_path, "surface", "--tree", str(root), "--baseline", str(baseline))

    with pytest.raises(SystemExit) as stop:
        cli.cmd_surface()

    assert stop.value.code == 1, "an env var is a decision, not an automatic adopt"
    out = capsys.readouterr().out
    assert "env-var added: ORACLE_KEY [rule]" in out


def test_surface_names_a_baseline_it_cannot_trust(tmp_path, capsys):
    root = tmp_path / "tree"
    root.mkdir()
    bad = tmp_path / "surface.json"
    bad.write_text('{"schema": "other/v9"}', encoding="utf-8")
    cli = invoke(["--repo", str(tmp_path), "surface", "--tree", str(root), "--baseline", str(bad)])

    with pytest.raises(SystemExit) as stop:
        cli.cmd_surface()

    assert stop.value.code == 2
    assert "schema" in capsys.readouterr().err


# -- verify-report -----------------------------------------------------------


def test_verify_report_writes_the_document_and_admits_what_it_skipped(tmp_path, capsys):
    root = tmp_path / "tree"
    root.mkdir()
    out = tmp_path / "verified.md"

    local(tmp_path, "verify-report", "--tree", str(root), "--out", str(out)).cmd_verify_report()

    text = out.read_text(encoding="utf-8")
    assert "## Gates" in text
    assert "## Receipts" in text
    assert "not verified: " in text
    spoken = capsys.readouterr().out
    assert "measured: Summary, URLs, Ways" in spoken
    assert "not verified: Delta, Gates" in spoken


def test_verify_report_gates_on_the_measurements_it_actually_made(tmp_path, capsys):
    root = _link_tree(tmp_path, "https://hermes-agent.nousresearch.com/docs")
    cli = invoke(["--repo", str(tmp_path), "verify-report", "--tree", str(root)])

    with pytest.raises(SystemExit) as stop:
        cli.cmd_verify_report()

    assert stop.value.code == 1
    assert "links" in capsys.readouterr().err


def test_verify_report_ignores_a_gate_it_was_not_asked_to_measure(tmp_path, capsys):
    root = tmp_path / "tree"
    root.mkdir()

    invoke(["--repo", str(tmp_path), "verify-report", "--tree", str(root)]).cmd_verify_report()

    out = capsys.readouterr().out
    assert "parity gate" not in capsys.readouterr().err
    assert "not verified: " in out


# -- delivery: errors, issues, discuss, suggest, table -----------------------


def _runs_file(tmp_path, *names, total=None) -> str:
    payload = {
        "total_count": len(names) if total is None else total,
        "check_runs": [
            {
                "id": 501,
                "name": lane,
                "conclusion": "failure",
                "runner_name": "ubuntu-latest",
                "output": {"title": "Process completed with exit code 1.", "text": ""},
                "updated_at": "2026-10-21T03:04:05Z",
            }
            for lane in names
        ],
    }
    path = tmp_path / "runs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _fork_tree(tmp_path):
    """A fork tree with one Nastech-only file and the upstream that does not brand it."""
    fork = tmp_path / "fork"
    fork.mkdir()
    (fork / "nastech-only.py").write_text("X = 1\n", encoding="utf-8")
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "keep.py").write_text("Y = 2\n", encoding="utf-8")
    return fork, upstream


def test_errors_is_a_report_not_a_gate(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    invoke(["errors", "--runs", _runs_file(tmp_path, "Python tests")]).cmd_errors()

    out = capsys.readouterr().out
    assert "first 1" in out and "1 movement(s)" in out
    assert "The feed reports what changed" in out


def test_errors_refuses_without_the_payload_of_this_cycle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as stop:
        invoke(["errors"]).cmd_errors()

    assert stop.value.code == 2


def test_errors_reports_a_lane_it_cannot_account_for(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "feed.json"
    state.write_text(
        json.dumps({"schema": "100ways.errorfeed/v1", "reds": {"gone-lane": "UNKNOWN"}}),
        encoding="utf-8",
    )

    invoke(["errors", "--runs", _runs_file(tmp_path, "Python tests", total=61),
            "--state", str(state)]).cmd_errors()

    out = capsys.readouterr().out
    assert "unproved 1" in out
    assert "resolved" not in out, "a truncated read may not claim a clearance"
    assert "--paginate" in out


def test_errors_records_a_cycle_the_next_cycle_can_compare_against(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    runs = _runs_file(tmp_path, "Python tests")
    state = tmp_path / "feed.json"

    invoke(["errors", "--runs", runs, "--record", "--state", str(state)]).cmd_errors()
    capsys.readouterr()
    invoke(["errors", "--runs", runs, "--state", str(state)]).cmd_errors()

    assert "ongoing 1" in capsys.readouterr().out
    assert json.loads(state.read_text(encoding="utf-8"))["schema"] == "100ways.errorfeed/v1"


def test_errors_needs_a_path_to_record_at(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as stop:
        invoke(["errors", "--runs", _runs_file(tmp_path, "Python tests"), "--record"]).cmd_errors()

    assert stop.value.code == 2


def test_errors_refuses_a_state_file_that_is_not_a_feed(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "other.json").write_text('{"schema": "something.else", "reds": {}}',
                                         encoding="utf-8")

    with pytest.raises(SystemExit) as stop:
        invoke(["errors", "--runs", _runs_file(tmp_path, "x"),
                "--state", "other.json"]).cmd_errors()

    assert stop.value.code == 2
    assert "nothing can be planned" not in capsys.readouterr().err


def test_errors_writes_the_machine_form_it_printed(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    invoke(["errors", "--runs", _runs_file(tmp_path, "Python tests"),
            "--json-out", "feed.json"]).cmd_errors()

    payload = json.loads((tmp_path / "feed.json").read_text(encoding="utf-8"))
    assert payload["counts"] == {"first": 1}
    assert payload["events"][0]["at"] == "2026-10-21T03:04:05Z"


def test_issues_drafts_from_a_link_the_policy_judged(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "deliver", lambda *a, **k: pytest.fail("no write"))
    root = _link_tree(tmp_path, "https://hermes-agent.nousresearch.com/docs")

    invoke(["--repo", str(tmp_path), "issues", "--tree", str(root)]).cmd_issues()

    out = capsys.readouterr().out
    assert "[blocking] urls-brand-violation:" in out
    assert "measured ci" not in out, "no check runs were handed, so ci stays unmeasured"
    assert "not measured: ci, surface, credentials, orphans, feed" in out


def test_issues_writes_the_document_it_offers_to_write(tmp_path, monkeypatch, capsys):
    root = _link_tree(tmp_path, "https://example.com/todo")
    monkeypatch.chdir(tmp_path)

    invoke(["--repo", str(tmp_path), "issues", "--tree", str(root),
            "--out", "plan.md", "--json-out", "plan.json"]).cmd_issues()

    document = Path("plan.md").read_text(encoding="utf-8")
    assert "## issue [blocking] Links: 1 placeholder link(s) to fix" in document
    payload = json.loads(Path("plan.json").read_text(encoding="utf-8"))
    assert [draft["key"] for draft in payload["issues"]] == ["urls-placeholder"]
    assert "-- 100ways:issue:urls-placeholder" in document


def test_issues_apply_needs_a_project(tmp_path, monkeypatch):
    root = _link_tree(tmp_path, "https://hermes-agent.nousresearch.com/docs")

    with pytest.raises(SystemExit) as stop:
        invoke(["--repo", str(tmp_path), "issues", "--tree", str(root),
                "--apply"]).cmd_issues()

    assert stop.value.code == 2


def test_issues_apply_asks_for_the_issue_family_only(tmp_path, monkeypatch):
    seen = {}

    def record(repo, result, apply=False, kinds=()):
        seen.update({"repo": repo, "apply": apply, "kinds": tuple(kinds),
                     "issues": len(result.issues)})
        return {"repo": repo, "apply": apply, "kinds": list(kinds), "planned": 1,
                "issues": [], "discussions": [], "errors": []}

    monkeypatch.setattr(cli_module, "deliver", record)
    root = _link_tree(tmp_path, "https://hermes-agent.nousresearch.com/docs")

    invoke(["--repo", str(tmp_path), "issues", "--tree", str(root),
            "--apply", "--project", "o/r"]).cmd_issues()

    assert seen == {"repo": "o/r", "apply": True, "kinds": ("issues",), "issues": 1}


def test_issues_dedupes_against_the_open_list_and_says_what_it_dropped(tmp_path, monkeypatch,
                                                                       capsys):
    monkeypatch.setattr(
        cli_module, "open_issue_keys", lambda repo: frozenset({"urls-brand-violation"})
    )
    root = _link_tree(tmp_path, "https://hermes-agent.nousresearch.com/docs")

    invoke(["--repo", str(tmp_path), "issues", "--tree", str(root), "--dedupe",
            "--project", "o/r"]).cmd_issues()

    out = capsys.readouterr().out
    assert "already asked for: urls-brand-violation" in out
    assert "planned: 0 issue(s)" in out


def test_dedupe_without_a_project_refuses_to_claim_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, "open_issue_keys",
                        lambda repo: pytest.fail("a dedupe must name the repo it read"))
    root = _link_tree(tmp_path, "https://hermes-agent.nousresearch.com/docs")

    with pytest.raises(SystemExit) as stop:
        invoke(["--repo", str(tmp_path), "issues", "--tree", str(root), "--dedupe"]).cmd_issues()

    assert stop.value.code == 2


def test_discuss_asks_the_question_the_engine_may_not_answer(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_module, "deliver", lambda *a, **k: pytest.fail("no write"))
    fork, upstream = _fork_tree(tmp_path)

    invoke(["--repo", str(tmp_path), "discuss", "--tree", str(fork),
            "--upstream", str(upstream)]).cmd_discuss()

    out = capsys.readouterr().out
    assert "[orphans-unexplained]" in out
    assert "Nothing was created" not in out, "the CLI says so in the document, not the summary"


def test_discuss_carries_the_category_the_operator_named(tmp_path, monkeypatch):
    fork, upstream = _fork_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    invoke(["--repo", str(tmp_path), "discuss", "--tree", str(fork), "--upstream", str(upstream),
            "--category", "surface-decisions", "--out", "ask.md"]).cmd_discuss()

    assert "- category: surface-decisions" in Path("ask.md").read_text(encoding="utf-8")


def test_discuss_says_so_when_the_measured_families_raised_no_question(tmp_path, monkeypatch,
                                                                       capsys):
    root = tmp_path / "quiet"
    root.mkdir()
    monkeypatch.chdir(tmp_path)

    invoke(["--repo", str(tmp_path), "discuss", "--tree", str(root)]).cmd_discuss()

    assert "no discussion to open" in capsys.readouterr().out


def test_a_delivery_verb_never_scans_a_tree_it_was_not_handed(tmp_path, monkeypatch, capsys):
    """--tree is opt-in: the default checkout is a fork of hundreds of thousands of files.

    Measuring links whenever ``--repo`` happens to be a directory made ``suggest`` read
    the whole fork -- minutes of work, and a family reported as measured that nobody
    asked for. A delivery verb reads only the families its flags name.
    """

    def no_scan(*args, **kwargs):  # pragma: no cover - the call itself is the failure
        raise AssertionError("a tree was scanned without --tree")

    monkeypatch.setattr(cli_module, "scan_url_tree", no_scan)
    runs = _runs_file(tmp_path, "Python tests (3.13)")

    invoke(["--repo", str(tmp_path), "suggest", "--runs", runs]).cmd_suggest()

    out = capsys.readouterr().out
    assert "not measured, so nothing to suggest there: urls" in out


def test_suggest_lists_only_actions_the_measurements_support(tmp_path, monkeypatch, capsys):
    root = _link_tree(tmp_path, "https://example.com/todo")

    invoke(["--repo", str(tmp_path), "suggest", "--tree", str(root)]).cmd_suggest()

    out = capsys.readouterr().out
    assert "next action(s)" in out
    assert "[urls]" in out
    assert "[ci]" not in out, "no check runs were read, so no CI action may be offered"


def test_suggest_can_answer_as_a_table(tmp_path, monkeypatch, capsys):
    root = _link_tree(tmp_path, "https://example.com/todo")

    invoke(["--repo", str(tmp_path), "suggest", "--tree", str(root), "--table",
            "--style", "grid"]).cmd_suggest()

    out = capsys.readouterr().out
    assert "100WAYS / NEXT ACTIONS" in out
    assert out.splitlines()[2].split() == ["kind", "action", "owner"]


def test_table_refuses_a_family_that_was_never_measured(tmp_path, monkeypatch, capsys):
    root = tmp_path / "tree"
    root.mkdir()

    with pytest.raises(SystemExit) as stop:
        invoke(["--repo", str(tmp_path), "table", "--source", "ci",
                "--tree", str(root)]).cmd_table()

    assert stop.value.code == 2
    assert "nothing was measured for it" in capsys.readouterr().err


def test_table_prints_a_measured_family(tmp_path, monkeypatch, capsys):
    invoke(["--repo", str(tmp_path), "table", "--source", "ci",
            "--runs", _runs_file(tmp_path, "Python tests")]).cmd_table()

    out = capsys.readouterr().out
    assert "100WAYS / CI REDS" in out
    assert "Python tests" in out


def test_table_stays_inside_the_messages_it_was_given(tmp_path, monkeypatch, capsys):
    root = _link_tree(tmp_path, "https://example.com/todo")

    invoke(["--repo", str(tmp_path), "table", "--source", "orphans", "--tree", str(root),
            "--upstream", str(tmp_path / "nowhere")]).cmd_table()

    pages = capsys.readouterr().out.split("\n")
    assert "100WAYS / ORPHANS" in pages[0]


def test_a_delivery_verb_reports_an_unreadable_input_rather_than_planning_on_air(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as stop:
        invoke(["issues", "--runs", "no-such-payload.json", "--tree", str(tmp_path)]).cmd_issues()

    assert stop.value.code == 2
    assert "nothing can be planned" in capsys.readouterr().err


# -- the README coverage gate -------------------------------------------------

def _shipped_readme() -> Path:
    return Path(cli_module.__file__).resolve().parents[1] / "README.md"


def test_the_readme_verb_exposes_the_coverage_gate():
    args = build_parser().parse_args(["--repo", "/nowhere", "readme", "--check-coverage"])

    assert args.check_coverage is True
    assert args.min == 0.7  # the bar the README is promised to clear


def test_check_coverage_scores_the_shipped_bytes_and_writes_nothing(tmp_path, capsys):
    """The gate measures the file that ships, not a copy the verb just made."""
    from hundredways.readme import measure_coverage, surface

    target = _shipped_readme()
    before = target.read_bytes()
    cov = measure_coverage(before.decode("utf-8"), surface())

    invoke(["--repo", str(tmp_path), "readme", "--check-coverage"]).cmd_readme()

    out = capsys.readouterr().out
    assert f"README coverage: {cov.documented}/{cov.total} of the command surface" in out
    assert "nothing was written" in out
    assert target.read_bytes() == before


def test_a_bar_the_shipped_readme_cannot_clear_is_a_red(tmp_path, capsys):
    before = _shipped_readme().read_bytes()

    with pytest.raises(SystemExit) as stop:
        argv = ["--repo", str(tmp_path), "readme", "--check-coverage", "--min", "1.01"]
        invoke(argv).cmd_readme()

    assert stop.value.code == 1
    assert "is under the 101% bar" in capsys.readouterr().err
    assert _shipped_readme().read_bytes() == before
