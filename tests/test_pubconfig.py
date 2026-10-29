"""Publisher configuration: the credential a fork job will ask for and not have.

``pubconfig`` exists because the fourth fork-CI class is the only one with no file
to look at -- the missing thing is a repository *setting*.  These tests pin the two
things that make the answer trustworthy:

* **scope resolution the way GitHub does it.**  A job with an environment sees the
  repository's settings too, so a credential configured once is not "missing"
  because some other job pins an environment.  A report that got this wrong would
  send the owner to duplicate secrets that already work.
* **the difference between a documented skip and a live hole.**  The fork publishes
  from Linux and Termux while the signing accounts are unconfigured, and the engine
  says so with an all-or-nothing gate.  Absent Apple credentials are therefore not
  a red; an absent ``APP_PRIVATE_KEY`` in a lane that runs is.  The verdicts that
  draw that line are derived from the engine's own tables, and one test below mutates
  the gate -- by wiring it, and by adding the omitted name -- to prove the line moves
  with the gate rather than with a list written here.

The fixtures are synthetic: invented names, an unreachable URL, and a
credential-shaped value placed in the workflow *text* to prove no value can be
echoed out.  The final test class reads the real fork only when
``NASTECH_AGENT_TREE`` points at one.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from hundredways import pubconfig
from hundredways.pubconfig import (
    ActionsInventory,
    CredentialGate,
    Finding,
    PublisherConfigurationError,
    analyze,
    analyze_root,
    credential_gates,
    fetch_inventory,
    load_workflows,
    render,
    to_json,
    workflow_references,
)
from hundredways.updates import SIGNING_CREDENTIAL_CONTEXT

REAL_TREE = os.environ.get("NASTECH_AGENT_TREE")

WORKFLOW = """\
name: desktop release
on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      release-phase:
        type: string
jobs:
  build-darwin:
    runs-on: macos-latest
    environment: release-signing
    steps:
      - run: build
        env:
          CERT: ${{ secrets.CSC_LINK }}
          PASS: ${{ secrets.CSC_KEY_PASSWORD }}
          # - run: retired
          #   env: { TOKEN: ${{ secrets.RETIRED_ONLY }} }
  store:
    if: inputs.release-phase == 'publish' && inputs.skip-store != true
    environment:
      name: release-signing
      url: https://signing.invalid
    steps:
      - run: msstore reconfigure --sellerId "$MS_STORE_SELLER_ID"
        env:
          TENANT: ${{ secrets.MS_STORE_TENANT_ID }}
          CLIENT: ${{ secrets.MS_STORE_CLIENT_ID }}
          SECRET: ${{ secrets.MS_STORE_CLIENT_SECRET }}
          PRODUCT: ${{ vars.MS_STORE_PRODUCT_ID }}
          SELLER: ${{ secrets.MS_STORE_SELLER_ID }}
          LEAKED: AKIAIOSFODNN7EXAMPLE
  automate:
    if: github.repository == 'NastechResearch/nastech-agent'
    environment: trusted-automation
    steps:
      - run: gh api user
        env:
          APP: ${{ secrets.APP_PRIVATE_KEY }}
          ID: ${{ vars.APP_CLIENT_ID }}
          SCOPE: ${{ secrets.GITHUB_TOKEN }}
"""

APPLE = CredentialGate(
    "macOS arm64", frozenset({"CSC_LINK", "CSC_KEY_PASSWORD"}), admitted_in_workflow=True
)
STORE = CredentialGate(
    "Microsoft Store submission",
    frozenset(
        {
            "MS_STORE_TENANT_ID",
            "MS_STORE_CLIENT_ID",
            "MS_STORE_CLIENT_SECRET",
            "MS_STORE_PRODUCT_ID",
        }
    ),
    admitted_in_workflow=False,
)
GATES = (APPLE, STORE)

EMPTY = ActionsInventory()
CONFIGURED_APPLE = ActionsInventory(
    repo_secrets=frozenset({"CSC_LINK", "CSC_KEY_PASSWORD"}),
    repo_vars=frozenset(),
)


def references():
    return workflow_references(WORKFLOW, workflow="release.yml")


def verdicts(found, *only):
    """Names mapped to verdicts, all of them unless a verdict is named."""
    return {item.name: item.verdict for item in found if not only or item.verdict in only}


def test_trigger_keys_are_not_jobs():
    """Only text under ``jobs:`` is a job, so ``push`` cannot own a credential."""
    names = {reference.job for reference in references()}
    assert names == {"build-darwin", "store", "automate"}


def test_a_name_is_attributed_to_the_job_that_reads_it():
    """The environment that decides a verdict is the reading job's own."""
    by_name = {reference.name: reference for reference in references()}
    assert by_name["CSC_LINK"].job == "build-darwin"
    assert by_name["CSC_LINK"].environment == "release-signing"
    assert by_name["APP_PRIVATE_KEY"].environment == "trusted-automation"


def test_the_environment_mapping_form_is_read_as_well_as_the_inline_form():
    assert {r.environment for r in references() if r.job == "store"} == {"release-signing"}


def test_a_commented_out_step_reads_nothing():
    """A disabled step consumes no credential, so it must not become a finding."""
    assert "RETIRED_ONLY" not in {reference.name for reference in references()}


def test_github_token_is_never_a_finding():
    """The runner mints it per job; carrying it would make every report noisy forever."""
    names = {reference.name for reference in references()}
    assert "GITHUB_TOKEN" not in names


def test_the_repeated_name_keeps_the_earliest_line():
    once = [r for r in references() if r.name == "MS_STORE_TENANT_ID"]
    assert len(once) == 1
    assert once[0].line == WORKFLOW[: WORKFLOW.index("TENANT:")].count("\n") + 1


def test_repo_scope_satisfies_a_job_that_pins_an_environment():
    """GitHub resolves repository settings into an environment job, so one is enough."""
    inventory = ActionsInventory(repo_secrets=frozenset({"APP_PRIVATE_KEY"}))
    assert inventory.provides("secrets", "APP_PRIVATE_KEY", "trusted-automation")


def test_an_environment_secret_satisfies_only_its_own_scope_reads():
    inventory = ActionsInventory(
        environments={"release-signing": (frozenset({"CSC_LINK"}), frozenset())}
    )
    assert inventory.provides("secrets", "CSC_LINK", "release-signing")
    assert not inventory.provides("secrets", "CSC_LINK", "trusted-automation")
    assert not inventory.provides("secrets", "CSC_LINK")


def test_a_missing_var_is_read_from_the_vars_context_not_the_secrets_one():
    found = {item.name: item for item in analyze(references(), EMPTY, gates=GATES)}
    assert found["MS_STORE_PRODUCT_ID"].kind == "vars"
    assert found["MS_STORE_TENANT_ID"].kind == "secrets"
    # Setting the two secret names satisfies exactly those two: a var is never
    # satisfied by a secret of the same name, and vice versa.
    satisfied = analyze(
        references(),
        ActionsInventory(
            repo_secrets=frozenset({"MS_STORE_TENANT_ID", "MS_STORE_CLIENT_ID",
                                    "MS_STORE_CLIENT_SECRET"}),
            environments={"release-signing": (frozenset(), frozenset())},
        ),
        gates=GATES,
    )
    assert "MS_STORE_PRODUCT_ID" in {item.name for item in satisfied}
    assert "MS_STORE_TENANT_ID" not in {item.name for item in satisfied}


def test_a_fully_unconfigured_wired_gate_is_a_declared_skip_not_a_red():
    """Apple credentials absent is the fork's documented state, not a finding to fix."""
    found = verdicts(analyze(references(), EMPTY, gates=GATES), pubconfig.DECLARED_SKIP)
    assert found == {"CSC_LINK": pubconfig.DECLARED_SKIP,
                     "CSC_KEY_PASSWORD": pubconfig.DECLARED_SKIP}


def test_a_partially_configured_gate_names_what_is_already_set():
    inventory = ActionsInventory(repo_secrets=frozenset({"CSC_LINK"}))
    item = next(
        f for f in analyze(references(), inventory, gates=GATES)
        if f.name == "CSC_KEY_PASSWORD"
    )
    assert item.verdict == pubconfig.PARTIAL_GATE
    assert "CSC_LINK" in item.note
    assert not item.actionable


def test_a_gate_that_is_not_wired_into_the_workflow_cannot_excuse_a_lane():
    """The Store gate lives in ``store.py``; the workflow job is flag-gated, so it runs.

    Without this distinction the four Store names would read as a declared skip and
    the report would hide a lane that any publish-phase release executes.
    """
    found = verdicts(analyze(references(), EMPTY, gates=GATES), pubconfig.LIVE_MISSING)
    assert set(found) >= {
        "MS_STORE_TENANT_ID",
        "MS_STORE_CLIENT_ID",
        "MS_STORE_CLIENT_SECRET",
        "MS_STORE_PRODUCT_ID",
        "APP_PRIVATE_KEY",
        "APP_CLIENT_ID",
    }


def test_a_name_the_gate_omits_is_ungated_consumed():
    """``MS_STORE_SELLER_ID`` is consumed by the lane the gate decides, and is not in it."""
    item = next(
        f for f in analyze(references(), EMPTY, gates=GATES)
        if f.name == "MS_STORE_SELLER_ID"
    )
    assert item.verdict == pubconfig.UNGATED_CONSUMED
    assert item.gate == STORE.label
    assert "MS_STORE_TENANT_ID" in item.note


def test_wiring_the_gate_moves_the_four_names_but_not_the_omitted_one():
    """The two gate flags are load-bearing, in opposite directions.

    Admitting the gate into the workflow turns its four listed names into a declared
    skip.  The fifth stays ``ungated-consumed``, because wiring a set that does not
    contain a name cannot cover it -- which is the whole hazard this verdict exists for.
    """
    wired = (APPLE, CredentialGate(STORE.label, STORE.names, admitted_in_workflow=True))
    found = verdicts(analyze(references(), EMPTY, gates=wired))
    assert found["MS_STORE_TENANT_ID"] == pubconfig.DECLARED_SKIP
    assert found["MS_STORE_SELLER_ID"] == pubconfig.UNGATED_CONSUMED


def test_adding_the_omitted_name_to_the_gate_makes_it_a_declared_skip():
    """The fix the verdict asks for, shown to change the answer."""
    complete = CredentialGate(
        STORE.label, STORE.names | {"MS_STORE_SELLER_ID"}, admitted_in_workflow=True
    )
    found = verdicts(analyze(references(), EMPTY, gates=(APPLE, complete)))
    assert found["MS_STORE_SELLER_ID"] == pubconfig.DECLARED_SKIP


def test_one_shared_name_does_not_make_a_job_part_of_a_lane():
    """A majority of the gate, not a coincidence beside it.

    A job that reads one of a four-name lane's credentials and an unrelated name is
    not doing that lane's work; calling it drift would point the owner at a gate they
    have no reason to widen.
    """
    text = """\
jobs:
  unrelated:
    runs-on: ubuntu-latest
    steps:
      - run: build
        env:
          ONE: ${{ secrets.CSC_LINK }}
          EXTRA: ${{ secrets.UNRELATED_NAME }}
"""
    gate = CredentialGate("macOS arm64", APPLE.names | {"APPLE_API_KEY_P8", "APPLE_API_ISSUER"})
    found = {item.name: item for item in analyze(
        workflow_references(text, workflow="other.yml"), EMPTY, gates=(gate,)
    )}
    assert found["UNRELATED_NAME"].verdict == pubconfig.LIVE_MISSING
    assert found["UNRELATED_NAME"].gate == ""


def test_a_live_lane_quotes_the_guard_that_runs_it():
    item = next(
        f for f in analyze(references(), EMPTY, gates=GATES)
        if f.name == "APP_PRIVATE_KEY"
    )
    assert "github.repository ==" in item.note


def test_the_store_lane_guard_reaches_the_report_of_the_store_names():
    item = next(
        f for f in analyze(references(), EMPTY, gates=GATES)
        if f.name == "MS_STORE_CLIENT_ID"
    )
    assert "inputs.skip-store" in item.note


def test_a_configured_lane_disappears_from_the_report_entirely():
    """A lane that is not a hole is not printed at all, so the eye lands on the holes."""
    found = analyze(references(), CONFIGURED_APPLE, gates=GATES)
    assert "CSC_LINK" not in {item.name for item in found}
    assert "CSC_KEY_PASSWORD" not in {item.name for item in found}
    assert "MS_STORE_SELLER_ID" in {item.name for item in found}


def test_only_the_two_verdicts_that_burn_are_actionable():
    found = analyze(references(), EMPTY, gates=GATES)
    actionable = {item.name for item in found if item.actionable}
    assert actionable <= {
        item.name for item in found if item.verdict in (pubconfig.LIVE_MISSING,
                                                        pubconfig.UNGATED_CONSUMED)
    }
    assert "CSC_LINK" not in actionable


def test_configured_credentials_leave_no_finding_at_all():
    inventory = ActionsInventory(
        repo_secrets=frozenset(
            {"CSC_LINK", "CSC_KEY_PASSWORD", "MS_STORE_TENANT_ID", "MS_STORE_CLIENT_ID",
             "MS_STORE_CLIENT_SECRET", "MS_STORE_SELLER_ID", "APP_PRIVATE_KEY"}
        ),
        repo_vars=frozenset({"MS_STORE_PRODUCT_ID", "APP_CLIENT_ID"}),
    )
    assert analyze(references(), inventory, gates=GATES) == ()
    assert "every credential name" in render((), repo="x", inventory=inventory)


def test_gates_come_from_the_engine_tables_and_the_wired_flag_is_measured():
    gates = credential_gates()
    assert gates
    for gate in gates:
        assert gate.names
        assert gate.admitted_in_workflow == bool(
            gate.names & frozenset(SIGNING_CREDENTIAL_CONTEXT)
        )
    store = next(g for g in gates if g.label == "Microsoft Store submission")
    assert not store.admitted_in_workflow


def test_lanes_that_share_a_credential_set_share_one_gate():
    """Two Windows legs, one decision -- the engine folds them into one admit output."""
    labels = [g.label for g in credential_gates() if "AZURE_SIGN_ENDPOINT" in g.names]
    assert len(labels) == 1
    assert labels[0].count("Windows") >= 2


def test_the_store_gate_lists_the_names_the_forks_own_lane_reads():
    """The Store gate's set is the lane's consumed set, measured, not hand-listed.

    ``tests/fixtures/store_lane`` transcribes the fork's ``stable-store`` job
    (``origin/main``, ``desktop-bundled-release.yml`` measured 2026-10-25: job at 2477,
    the credential ``env`` at 2487-2497, ``msstore reconfigure --sellerId`` at 2513), so
    the gate is compared against the lane rather than against a copy of itself -- the
    four-name gate that left ``MS_STORE_SELLER_ID`` ``ungated-consumed`` fails here, and
    so does an engine that stops folding ``STORE_CREDENTIALS`` into the gate at all.
    """
    lane = os.path.join(
        os.path.dirname(__file__),
        "fixtures",
        "store_lane",
        ".github",
        "workflows",
        "desktop-bundled-release.yml",
    )
    references = workflow_references(
        open(lane, encoding="utf-8").read(),
        workflow="desktop-bundled-release.yml",
    )
    consumed = frozenset(r.name for r in references if r.name.startswith("MS_STORE_"))
    gate = next(g for g in credential_gates() if g.label == "Microsoft Store submission")

    assert consumed == gate.names
    assert "MS_STORE_SELLER_ID" in gate.names
    found = analyze(references, EMPTY, gates=credential_gates())
    assert not [
        item
        for item in found
        if item.verdict == pubconfig.UNGATED_CONSUMED and item.name.startswith("MS_STORE_")
    ]
    assert "MS_STORE_SELLER_ID" in {
        item.name for item in found if item.verdict == pubconfig.LIVE_MISSING
    }
    # The lane's one foreign name is a different lane's gap and stays attributed
    # to it, so the Store gate's fix is not a licence to widen the guard: with the
    # fork's real inventory, which does provide it, nothing is ungated at all.
    r2 = [item for item in found if item.name == "CLOUDFLARE_R2_PUBLIC_URL"]
    assert r2 and r2[0].verdict == pubconfig.UNGATED_CONSUMED
    provided = ActionsInventory(repo_vars=frozenset({"CLOUDFLARE_R2_PUBLIC_URL"}))
    still_ungated = [
        item.name
        for item in analyze(references, provided, gates=credential_gates())
        if item.verdict == pubconfig.UNGATED_CONSUMED
    ]
    assert not still_ungated


def test_render_is_plain_text_and_carries_the_names_the_owner_must_type():
    found = analyze(references(), EMPTY, gates=GATES)
    text = render(found, repo="nastechresearch/nastech-agent", inventory=EMPTY)
    assert "**" not in text and "`" not in text
    assert text.splitlines()[0] == "Publisher configuration: nastechresearch/nastech-agent"
    assert "secrets.MS_STORE_SELLER_ID" in text
    assert "[release-signing]" in text
    assert "in job store" in text


def test_a_value_written_into_the_workflow_is_never_a_reference():
    """The parser captures the identifier after ``secrets.``/``vars.``, never a value."""
    token = "AKIAIOSFODNN7EXAMPLE"
    assert token in WORKFLOW
    assert not any(name.startswith("AKIA") for name in {r.name for r in references()})
    found = analyze(references(), EMPTY, gates=GATES)
    assert token not in render(found, inventory=EMPTY)
    assert token not in to_json(found)


def test_a_credential_shaped_value_in_any_field_is_masked_line_by_line():
    """Even a hand-built finding cannot leak: every rendered line passes ``redact``."""
    token = "AKIAIOSFODNN7EXAMPLE"
    leaked = Finding(
        name="APP_PRIVATE_KEY",
        kind="secrets",
        verdict=pubconfig.LIVE_MISSING,
        scopes=(),
        references=references()[:1],
        note=f"the log showed {token} here",
    )
    text = render([leaked], inventory=EMPTY)
    assert token not in text
    assert "[REDACTED]" in text


def test_json_reports_the_same_answer_with_the_same_absence():
    found = analyze(references(), EMPTY, gates=GATES)
    payload = json.loads(to_json(found, repo="r"))
    assert payload["actionable"] == sum(1 for item in found if item.actionable)
    assert len(payload["findings"]) == len(found)
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(payload)
    entry = next(f for f in payload["findings"] if f["name"] == "MS_STORE_SELLER_ID")
    assert entry["verdict"] == pubconfig.UNGATED_CONSUMED
    assert entry["evidence"]


def test_an_unreadable_inventory_refuses_to_answer():
    """A silent fetch failure would read as "everything configured" -- so it is fatal."""

    def failing(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="HTTP 403: Must have read access")

    with pytest.raises(PublisherConfigurationError, match="no configuration verdict"):
        fetch_inventory("NousResearch/hermes-agent", run=failing)


class FakeGh:
    """Stands in for ``gh api`` and records the exact argv each call used."""

    def __init__(self, pages):
        self.pages = pages
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout=self.pages[argv[3]], stderr="")


def test_fetch_inventory_reads_names_and_never_a_value():
    pages = {
        "repos/r/actions/secrets?per_page=100": "CSC_LINK\nnot a name\n",
        "repos/r/actions/variables?per_page=100": "AZURE_CLIENT_ID\n",
        "repos/r/environments?per_page=100": "release-signing\nProduction – docs\n",
        "repos/r/environments/release-signing/secrets?per_page=100": "APP_PRIVATE_KEY\n",
        "repos/r/environments/release-signing/variables?per_page=100": "",
        "repos/r/environments/Production%20%E2%80%93%20docs/secrets?per_page=100": "",
        "repos/r/environments/Production%20%E2%80%93%20docs/variables?per_page=100": "R2_BUCKET\n",
    }
    run = FakeGh(pages)
    inventory = fetch_inventory("r", run=run)
    assert inventory.repo_secrets == frozenset({"CSC_LINK"})
    assert inventory.environments["Production – docs"][1] == frozenset({"R2_BUCKET"})
    assert inventory.total_names == 4
    for call in run.calls:
        assert call[:3] == ["gh", "api", "--paginate"]
        assert call[-2:] == ["--jq", call[-1]]
        assert "[].name" in call[-1]


def test_load_workflows_refuses_a_tree_without_them(tmp_path):
    with pytest.raises(PublisherConfigurationError):
        load_workflows(tmp_path)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    with pytest.raises(PublisherConfigurationError, match="holds no workflows"):
        load_workflows(tmp_path)


def test_analyze_root_walks_the_tree_in_one_call(tmp_path):
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "release.yml").write_text(WORKFLOW, encoding="utf-8")
    found = analyze_root(tmp_path, EMPTY)
    assert {item.name for item in found} == {item.name for item in analyze(references(), EMPTY)}


@pytest.mark.skipif(not REAL_TREE, reason="set NASTECH_AGENT_TREE to a fork checkout")
def test_the_real_fork_reports_the_measured_gap_and_nothing_else_as_urgent():
    """The acceptance test: reproduce the release-signing inventory from the tree itself."""
    inventory = fetch_inventory("nastechresearch/nastech-agent")
    found = analyze_root(REAL_TREE, inventory)
    by_verdict = {
        verdict: sorted(item.name for item in found if item.verdict == verdict)
        for verdict in pubconfig.VERDICT_ORDER
    }
    # ``MS_STORE_SELLER_ID`` is now in the Store gate, so the lane no longer
    # consumes a credential the gate omits: it moves out of ungated-consumed (the
    # hole that let a run be admitted and fail mid-publish) and, the fork not
    # having set it, lands in live-missing -- still actionable, so ``pubconfig``
    # refuses the run up front exactly as before, just for the honest reason.
    assert by_verdict[pubconfig.UNGATED_CONSUMED] == []
    assert "MS_STORE_SELLER_ID" in by_verdict[pubconfig.LIVE_MISSING]
    assert {"APP_PRIVATE_KEY", "APP_CLIENT_ID"} <= set(by_verdict[pubconfig.LIVE_MISSING])
    assert {"CSC_LINK", "CSC_KEY_PASSWORD", "APPLE_API_KEY_P8"} <= set(
        by_verdict[pubconfig.DECLARED_SKIP]
    )
    assert not set(by_verdict[pubconfig.DECLARED_SKIP]) & {
        "APP_PRIVATE_KEY",
        "MS_STORE_SELLER_ID",
    }
    text = render(found, repo="nastechresearch/nastech-agent", inventory=inventory)
    assert f"{pubconfig.LIVE_MISSING} ({len(by_verdict[pubconfig.LIVE_MISSING])})" in text
    assert "AKIAIOSFODNN7EXAMPLE" not in text and "-----BEGIN" not in text
