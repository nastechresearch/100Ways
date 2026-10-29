"""The fork-lint pre-flight: the fork's own rules, run before the fork's CI can be the
one to say no.

The fixtures mirror the live ``nastechresearch/nastech-agent`` contract this module
reads instead of remembers: six blocking ``run: python scripts/...`` steps in job
``windows-footguns`` of ``lint.yml``, caps owned by
``scripts/ci/check_agents_md_size.py`` and ``agent/subdirectory_hints.py``, and ruff
configured by the checkout's own config file.  Where a fixture has to replace a real
checker (the fork's scripts read a 290 MB tree), it replaces it with the same
exit-shape, never with a weaker rule.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from hundredways import forklint, prepublish
from hundredways.forklint import (
    AGENTS_SIZE_CHECKER,
    BRAND_INFLATION,
    FINDING,
    FINDINGS_CAP_PER_SCRIPT,
    FORK_CHECK_SCRIPTS,
    FORK_LINT_WORKFLOW,
    GENUINE_DIVERGENCE,
    RUNNER_CAPACITY,
    UNAVAILABLE,
    ForkLintIssue,
    predict_brand_inflation,
    read_fork_ruff_config,
    report_lines,
    run_fork_lint,
    run_fork_ruff,
    summarize,
)

# ---------------------------------------------------------------------------
# The trees: a fork checkout, a branded candidate, the upstream it came from
# ---------------------------------------------------------------------------

_PASS = "import sys\nsys.exit(0)\n"

# One fork checker's real output shape: a finding line with an indented rule/fix block.
_FOOTGUN_OUT = (
    "nastech_cli/gateway.py:12: subprocess.run without check=\n"
    "  rule: PLW1510\n"
    "  fix: pass check=True\n"
)
_FOOTGUN_FAIL = f"import sys\nprint({_FOOTGUN_OUT!r}, end='')\nsys.exit(1)\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _lint_yml(footguns: str = "blocking") -> str:
    """The real job shape: six blocking ``run: python ...`` steps in the fork's order.

    ``footguns`` demos the two demotion spellings: ``advisory-under-name`` is how the
    fork writes ``continue-on-error`` today (under the ``- name:`` that opened the
    step), ``advisory-first-key`` is the other order GitHub accepts.
    """
    footgun_step = {
        "blocking": """      - name: Run footgun checker
        run: python scripts/check-windows-footguns.py --all
""",
        "advisory-under-name": """      - name: Run footgun checker
        if: github.event_name == 'pull_request'
        continue-on-error: true
        run: python scripts/check-windows-footguns.py --all
""",
        "advisory-first-key": """      - continue-on-error: true
        name: Run footgun checker
        run: python scripts/check-windows-footguns.py --all
""",
    }[footguns]
    return f"""name: lint
on: pull_request
jobs:
  windows-footguns:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Checkout code
        uses: actions/checkout@f548e57e544e1ff5a4c46bf1e1b8685f8e4a348a # v7.0.1

{footgun_step}\
      - name: Require portable Bash shebangs
        run: python scripts/check_bash_shebangs.py

      - name: Forbid literal /tmp paths outside the baseline
        run: python scripts/check_no_tmp_literals.py

      - name: Forbid config.yaml writers
        run: python scripts/check_config_yaml_writers.py

      - name: Forbid unmarked macOS fakes in tests
        run: python scripts/ci/check_os_marker_fakes.py

      - name: Keep every AGENTS.md under its truncation cap
        run: python scripts/ci/check_agents_md_size.py

      - name: Profile-scope patterns on added lines (advisory)
        if: github.event_name == 'pull_request'
        continue-on-error: true
        run: |
          python scripts/check_profile_scope_patterns.py --base origin/main --head HEAD
"""


def _fork(
    tmp_path: Path,
    *,
    caps: tuple[int, int, int] = (12_000, 30_000, 32_000),
    workflow: str | None = None,
    scripts: dict[str, str] | None = None,
    ruff_select: tuple[str, ...] = ("F401",),
    per_file_ignores: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> Path:
    """A fork checkout: lint.yml, the six checkers, the cap-owning files, ruff.toml.

    The AGENTS-size checker body carries the three caps as assignments -- that file and
    ``agent/subdirectory_hints.py`` are the sources the module must read numbers from; a
    fixture that restated them somewhere else would hide the drift this gate exists to
    catch.  ``ruff.toml`` is written as TOML ruff itself accepts (double quotes), because
    the module hands its text verbatim to ``ruff --config``.
    """
    fork = tmp_path / "fork"
    root_max, chain_max, hint_max = caps
    _write(
        fork / AGENTS_SIZE_CHECKER,
        f"ROOT_MAX_CHARS = {root_max}\nCHAIN_MAX_CHARS = {chain_max}\n{_PASS}",
    )
    _write(fork / "agent/subdirectory_hints.py", f"_MAX_HINT_CHARS = {hint_max}\n{_PASS}")
    for rel in FORK_CHECK_SCRIPTS:
        if rel != AGENTS_SIZE_CHECKER:
            _write(fork / rel, _PASS)
    for rel, body in (scripts or {}).items():
        _write(fork / rel, body)
    _write(fork / FORK_LINT_WORKFLOW, workflow if workflow is not None else _lint_yml())
    ruff = f"[lint]\nselect = {json.dumps(list(ruff_select))}\n"
    if per_file_ignores:
        ruff += "\n[lint.per-file-ignores]\n"
        ruff += "".join(
            f"{json.dumps(pattern)} = {json.dumps(list(codes))}\n"
            for pattern, codes in per_file_ignores
        )
    _write(fork / "ruff.toml", ruff)
    return fork


# The live overrun in miniature: upstream keeps its chain 44 chars under the cap and
# branding (``hermes`` -> ``nastech``, +1 char per token) spends the slack.
_ROOT_UP = "hermes " * 120  # 840 chars, 120 brand tokens
_AREA_UP = "hermes gateway docs\n" * 40  # 800 chars, 40 brand tokens
_UP_CHAIN = len(_ROOT_UP) + len(_AREA_UP)  # 1640
_BRAND_CAPS = (2_048, _UP_CHAIN + 44, 1_600)  # chain cap 1684: green upstream, red fork


def _brand_pair(tmp_path: Path) -> tuple[Path, Path]:
    """An upstream tree 44 chars under the chain cap and its branded candidate."""
    upstream = tmp_path / "upstream"
    _write(upstream / "AGENTS.md", _ROOT_UP)
    _write(upstream / "gateway/AGENTS.md", _AREA_UP)
    candidate = tmp_path / "candidate"
    _write(candidate / "AGENTS.md", _ROOT_UP.replace("hermes", "nastech"))
    _write(candidate / "gateway/AGENTS.md", _AREA_UP.replace("hermes", "nastech"))
    return candidate, upstream


def _digest_tree(root: Path) -> dict[str, tuple[str, int]]:
    """Every file's bytes-hash and mode, keyed by relative path: the mutation witness."""
    seen = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            absolute = Path(dirpath) / name
            data = absolute.read_bytes()
            rel = str(absolute.relative_to(root)).replace(os.sep, "/")
            seen[rel] = (hashlib.sha256(data).hexdigest(), os.stat(absolute).st_mode)
    return seen


# ---------------------------------------------------------------------------
# (a) brand inflation: upstream green, branded red, the cap read from the enforcing file
# ---------------------------------------------------------------------------

def test_predicts_the_chain_the_brand_alone_pushes_over(tmp_path):
    """Upstream's 44-chars-under chain is a fork red, and the module says so pre-release.

    This is the incident the engine keeps paying an hour of human CI per: identical
    AGENTS.md content, green upstream, red fork, because ``hermes`` -> ``nastech`` adds
    one character per renamed token and the chain cap is the context an agent is
    truncated at -- the fix is the transform or the fork-owned text, never the cap.
    """
    fork = _fork(tmp_path, caps=_BRAND_CAPS)
    candidate, upstream = _brand_pair(tmp_path)

    issues = predict_brand_inflation(str(fork), str(candidate), str(upstream))

    assert len(issues) == 1, issues  # each file fits on its own; only the chain does not
    (chain,) = issues
    assert chain.script == AGENTS_SIZE_CHECKER
    assert chain.path == "AGENTS.md + gateway/AGENTS.md"
    assert chain.blocking and chain.status == FINDING
    assert chain.classification == BRAND_INFLATION
    assert "OVER CAP" in chain.detail
    assert "upstream 1640 -> branded 1800 chars (+160 from the brand transform)" in chain.detail
    assert "against the cap 1684" in chain.detail


def test_the_cap_is_the_one_the_fork_checker_names_not_one_copied(tmp_path):
    """Same branded tree, three different numbers in the enforcing file, three verdicts.

    ``CHAIN_MAX_CHARS`` lives in ``scripts/ci/check_agents_md_size.py``; if the predictor
    remembered it, moving the fork's number would silently move what this gate calls red
    away from what CI calls red.  The 10-chars-under case is also the margin contract:
    advisory, never blocking.
    """
    candidate, upstream = _brand_pair(tmp_path)

    roomy = _fork(tmp_path / "roomy", caps=(2_048, 30_000, 1_600))
    assert predict_brand_inflation(str(roomy), str(candidate), str(upstream)) == []

    near = _fork(tmp_path / "near", caps=(2_048, 1_810, 1_600))
    (advisory,) = predict_brand_inflation(str(near), str(candidate), str(upstream))
    assert not advisory.blocking and "inside the margin" in advisory.detail
    assert advisory.classification == BRAND_INFLATION
    assert f"prediction margin {forklint.BRAND_MARGIN_CHARS} chars" in advisory.detail
    assert summarize([advisory]).verdict == "PASS"

    moved = _fork(tmp_path / "moved", caps=(2_048, 1_700, 1_600))
    (over,) = predict_brand_inflation(str(moved), str(candidate), str(upstream))
    assert over.blocking

    _write(roomy / AGENTS_SIZE_CHECKER, f"ROOT_MAX_CHARS = 2048\n{_PASS}")
    (lost,) = predict_brand_inflation(str(roomy), str(candidate), str(upstream))
    assert lost.status == UNAVAILABLE and lost.blocking
    assert "no CHAIN_MAX_CHARS assignment" in lost.detail


def test_no_agents_md_means_nothing_to_predict(tmp_path):
    """A candidate without AGENTS.md files has no chain to budget: no issues, no crash."""
    fork = _fork(tmp_path, caps=_BRAND_CAPS)
    candidate = tmp_path / "candidate"
    _write(candidate / "README.md", "no agent context files here\n")
    assert predict_brand_inflation(str(fork), str(candidate)) == []


# ---------------------------------------------------------------------------
# (b) a clean tree is not flagged
# ---------------------------------------------------------------------------

def test_a_clean_branded_tree_is_not_flagged(tmp_path):
    """Nothing to report when the fork's six pass, ruff passes, and no unit nears a cap."""
    fork = _fork(tmp_path)
    candidate = tmp_path / "candidate"
    _write(candidate / "AGENTS.md", "nastech project context.\n" * 20)  # 500 chars
    _write(candidate / "gateway/AGENTS.md", "nastech gateway notes.\n" * 20)  # 460
    _write(candidate / "nastech_cli/app.py", "import sys\nprint(sys.argv)\n")

    issues = run_fork_lint(str(fork), str(candidate))

    if forklint._ruff_executable():
        assert issues == []
        assert summarize(issues).verdict == "PASS"
    else:
        # A missing ruff is blocking by contract -- a simulation that quietly skips is
        # indistinguishable from a green fork -- and that outcome is asserted outright.
        assert [issue.script for issue in issues] == ["ruff"]
        assert issues[0].status == UNAVAILABLE and issues[0].blocking


def test_a_demoted_step_reports_advisory_and_cannot_be_demoted_here(tmp_path):
    """The fork demoting a check (``continue-on-error: true``) is the ONLY demotion here.

    A finding from an advisory step reports as non-blocking with the same detail a
    blocking one would carry -- and the only way a blocking step becomes advisory is the
    fork editing its own workflow, never a flag in this module.
    """
    blocking_fork = _fork(tmp_path / "blocking",
                          scripts={"scripts/check-windows-footguns.py": _FOOTGUN_FAIL})
    advisory_fork = _fork(tmp_path / "advisory",
                          workflow=_lint_yml(footguns="advisory-under-name"),
                          scripts={"scripts/check-windows-footguns.py": _FOOTGUN_FAIL})
    candidate, _ = _brand_pair(tmp_path)

    def footgun_issue(fork: Path) -> ForkLintIssue:
        (issue,) = [found for found in run_fork_lint(str(fork), str(candidate))
                    if found.script == "scripts/check-windows-footguns.py"]
        return issue

    demoted = footgun_issue(advisory_fork)
    assert demoted.status == FINDING and not demoted.blocking
    assert demoted.classification == GENUINE_DIVERGENCE
    assert "line 12: subprocess.run without check=" in demoted.detail
    assert "| rule: PLW1510" in demoted.detail  # the indented block rides along
    assert footgun_issue(blocking_fork).blocking  # same finding, undemoted, still gates


# ---------------------------------------------------------------------------
# (c) read-only: the audit never mutates the tree it audits
# ---------------------------------------------------------------------------

def test_the_audit_never_touches_the_trees_it_audits(tmp_path):
    """Staging, installing the fork's checker, and ruff must all be read-only.

    The candidate is the tree about to be packaged and pushed; a lint run that edited it
    (writing through a hardlink, leaving ``.git`` or ``.ruff_cache`` behind) would ship
    the mutation itself as the fork PR.  The candidate deliberately carries its own copy
    of the checker the fork overwrites in staging -- the only way to prove the install
    cannot write through the hardlink into it.
    """
    fork = _fork(tmp_path, caps=_BRAND_CAPS,
                 scripts={"scripts/check-windows-footguns.py": _FOOTGUN_FAIL})
    candidate, upstream = _brand_pair(tmp_path)
    _write(candidate / AGENTS_SIZE_CHECKER, "ROOT_MAX_CHARS = 1\nCHAIN_MAX_CHARS = 2\n")
    before_candidate, before_fork = _digest_tree(candidate), _digest_tree(fork)

    issues = run_fork_lint(str(fork), str(candidate), str(upstream))

    assert _digest_tree(candidate) == before_candidate
    assert _digest_tree(fork) == before_fork
    assert not (candidate / ".git").exists()
    assert not (candidate / ".ruff_cache").exists()
    assert issues  # the run found things; it just was not allowed to touch anything


# ---------------------------------------------------------------------------
# the fork's lint.yml is the contract: args, demotion, drift
# ---------------------------------------------------------------------------

def test_workflow_step_reads_args_and_advisory_from_the_fork(tmp_path):
    """Both orders GitHub accepts, the ``--all`` the footgun step actually passes, and the
    absence of a step the workflow no longer runs."""
    plain = forklint._workflow_step(_lint_yml(), "scripts/check-windows-footguns.py")
    assert plain is not None and plain.args == ("--all",) and plain.advisory is False

    for shape in ("advisory-under-name", "advisory-first-key"):
        step = forklint._workflow_step(
            _lint_yml(footguns=shape), "scripts/check-windows-footguns.py"
        )
        assert step is not None and step.advisory is True, shape

    assert forklint._workflow_step(_lint_yml(), "scripts/ci/check_public_surface.py") is None


def test_checker_findings_are_genuine_divergence_and_block(tmp_path):
    """The fork's own checker fired on the candidate: class 3, blocking, no cap to blame."""
    fork = _fork(tmp_path, scripts={"scripts/check-windows-footguns.py": _FOOTGUN_FAIL})
    candidate, _ = _brand_pair(tmp_path)
    workflow = forklint._read(str(fork / FORK_LINT_WORKFLOW))

    with forklint._staged_checkout(str(candidate)) as tree:
        issues = forklint._run_checker(
            str(fork), "scripts/check-windows-footguns.py", workflow, tree
        )

    (finding,) = issues
    assert finding.blocking and finding.status == FINDING
    assert finding.classification == GENUINE_DIVERGENCE
    assert finding.path == "nastech_cli/gateway.py"


def test_findings_past_the_report_cap_are_counted_not_dropped():
    """A checker with 45 findings reports 45 findings' worth, capped for the log."""
    output = "".join(
        f"nastech_cli/f{i}.py:{i}: hit {i}\n" for i in range(FINDINGS_CAP_PER_SCRIPT + 5)
    )

    issues = forklint._findings("scripts/check_no_tmp_literals.py", output, advisory=False)

    assert len(issues) == FINDINGS_CAP_PER_SCRIPT + 1
    assert all(issue.classification == GENUINE_DIVERGENCE for issue in issues)
    assert issues[-1].detail == (
        f"5 further finding line(s) beyond the {FINDINGS_CAP_PER_SCRIPT}-finding cap"
    )
    assert issues[-1].blocking


# ---------------------------------------------------------------------------
# unavailable is blocking: the simulation never gets to skip
# ---------------------------------------------------------------------------

def test_unavailable_outcomes_block_and_never_claim_a_triage_class(tmp_path):
    """A workflow that runs none of the six, no fork, no candidate: all blocking, all
    classless.

    An unavailable is not a fork red, so it carries no triage class -- the first fix is
    making the check run; guessing a class for a check that did not execute is how a red
    gets blamed on the wrong owner.
    """
    fork = _fork(tmp_path)
    _write(fork / FORK_LINT_WORKFLOW, "name: lint\njobs: {}\n")  # invokes none of the six
    candidate = tmp_path / "candidate"
    _write(candidate / "AGENTS.md", "context\n")

    issues = run_fork_lint(str(fork), str(candidate))
    missing_step = [i for i in issues if i.status == UNAVAILABLE and "invokes no" in i.detail]
    assert len(missing_step) == len(FORK_CHECK_SCRIPTS)
    assert all(i.blocking and i.classification == "" for i in missing_step)
    assert summarize(issues).verdict == "FAIL"

    (nowhere,) = run_fork_lint(str(tmp_path / "absent-fork"), str(candidate))
    assert nowhere.blocking and "no fork checkout" in nowhere.detail

    (nocand,) = run_fork_lint(str(fork), str(tmp_path / "absent-candidate"))
    assert nocand.blocking and "no candidate tree to lint" in nocand.detail


def test_a_checker_that_did_not_answer_documented_is_unavailable(tmp_path):
    """A traceback and an undocumented exit code are both unavailable, never a finding.

    Exit code 1 means "I found something" for these scripts; a crash also leaves exit 1,
    and reading it as findings would publish a tree the check never actually judged.
    """
    crash = _fork(tmp_path / "crash", scripts={
        "scripts/check_bash_shebangs.py": 'raise RuntimeError("boom")\n',
        "scripts/check_no_tmp_literals.py": "raise SystemExit(2)\n",
    })
    candidate, _ = _brand_pair(tmp_path)

    with forklint._staged_checkout(str(candidate)) as tree:
        broken = forklint._run_checker(
            str(crash), "scripts/check_bash_shebangs.py", _lint_yml(), tree)
        weird = forklint._run_checker(
            str(crash), "scripts/check_no_tmp_literals.py", _lint_yml(), tree)

    (traceback_issue,) = broken
    assert traceback_issue.status == UNAVAILABLE and traceback_issue.blocking
    assert "crashed (exit 1)" in traceback_issue.detail
    (code_issue,) = weird
    assert code_issue.status == UNAVAILABLE and code_issue.blocking
    assert "outside the checker's documented 0 and 1" in code_issue.detail
    assert traceback_issue.classification == code_issue.classification == ""


def test_a_missing_checker_in_the_fork_is_unavailable_not_silently_skipped(tmp_path):
    """The workflow still invokes the step, but the fork's tree has no script to run."""
    fork = _fork(tmp_path)
    _ = (fork / "scripts/ci/check_os_marker_fakes.py").unlink()
    candidate, _ = _brand_pair(tmp_path)

    issues = run_fork_lint(str(fork), str(candidate))

    script = "scripts/ci/check_os_marker_fakes.py"
    (missing,) = [issue for issue in issues if issue.script == script]
    assert missing.status == UNAVAILABLE and missing.blocking
    assert "the fork checkout has no" in missing.detail


# ---------------------------------------------------------------------------
# ruff, configured by the fork
# ---------------------------------------------------------------------------

def test_ruff_config_discovery_prefers_ruff_toml_and_falls_back(tmp_path):
    """ruff.toml wins over pyproject exactly as it wins for ``ruff check .`` -- and a fork
    with no ``select`` list raises rather than being called clean."""
    fork = _fork(tmp_path, ruff_select=("F401",),
                 per_file_ignores=(("nastech_cli/*", ("F401",)),))
    _write(fork / "pyproject.toml",
           '[tool.ruff.lint]\nselect = ["PLW1510", "TID251"]\n'
           '[tool.ruff.lint.per-file-ignores]\n"pm/**" = ["TID251"]\n')
    config = read_fork_ruff_config(str(fork))
    assert config.source == "ruff.toml"
    assert config.select == ("F401",)
    assert config.per_file_ignores == (("nastech_cli/*", ("F401",)),)
    assert '"nastech_cli/*"' in config.config_text

    _ = (fork / "ruff.toml").unlink()  # now the pyproject subtree is what ruff would read
    fallback = read_fork_ruff_config(str(fork))
    assert fallback.source == "pyproject.toml"
    assert fallback.select == ("PLW1510", "TID251")
    assert "TID251" in fallback.config_text

    _ = (fork / "pyproject.toml").write_text('[project]\nname = "x"\n')
    with pytest.raises(forklint.ForkLintError, match="no select list"):
        read_fork_ruff_config(str(fork))
    _ = (fork / "pyproject.toml").unlink()
    with pytest.raises(forklint.ForkLintError, match="has none of"):
        read_fork_ruff_config(str(fork))


@pytest.mark.skipif(
    not forklint._ruff_executable(), reason="the fork's ruff is not installed here"
)
def test_a_selected_rule_of_the_fork_fires_on_generated_code(tmp_path):
    """The PR #130 shape: the engine generates code the fork's ruff rejects, and this sees it.

    Upstream's identical tree is green because the engine text is not in it; the fix is
    the generated code, never the fork's ``select`` list -- and the fork's own
    per-file-ignores land as verbatim exemptions, not as judgement made here.
    """
    fork = _fork(tmp_path, per_file_ignores=(("website/*", ("F401",)),))
    candidate = tmp_path / "candidate"
    _write(candidate / "nastech_cli/generated.py", "import os\nprint('hi')\n")
    _write(candidate / "website/snip.py", "import os\nprint('hi')\n")

    issues = run_fork_ruff(str(fork), str(candidate))

    assert [issue.path for issue in issues] == ["nastech_cli/generated.py"]
    (hit,) = issues
    assert hit.blocking and hit.classification == GENUINE_DIVERGENCE
    assert "F401" in hit.detail


@pytest.mark.skipif(
    not forklint._ruff_executable(), reason="the fork's ruff is not installed here"
)
def test_the_forks_ruff_pin_is_eyed_not_assumed(tmp_path):
    """A run on a different ruff than the fork pins is reported as an advisory note that
    makes no triage claim -- the fork's CI installs the pinned version itself."""
    fork = _fork(tmp_path)
    _write(fork / "pyproject.toml",
           '[project]\nname = "x"\ndependencies = ["ruff==0.0.0.dev1"]\n')
    candidate = tmp_path / "candidate"
    _write(candidate / "ok.py", "print('ok')\n")

    issues = run_fork_ruff(str(fork), str(candidate))

    (note,) = issues
    assert not note.blocking and note.classification == ""
    assert "the fork pins ruff==0.0.0.dev1" in note.detail


# ---------------------------------------------------------------------------
# (d) classifications agree with the fork-ci-triage skill
# ---------------------------------------------------------------------------

def test_triage_vocabulary_is_the_skills_three_classes_verbatim():
    """The class names are the skill's, and a static pre-flight can only ever emit two.

    fork-ci-triage (SKILL.md step 2) splits fork reds into runner capacity, brand
    inflation and genuine divergence; a red is mispriced the moment it is blamed on the
    wrong owner, so the strings are pinned here.  The third class is unreachable by
    construction: capacity reds are wall-clock artefacts of the hosted image, invisible
    to a lint of a tree, and nothing this module reports may claim that owner.
    """
    assert (RUNNER_CAPACITY, BRAND_INFLATION, GENUINE_DIVERGENCE) == (
        "runner capacity",
        "brand inflation",
        "genuine divergence",
    )


def test_every_emitted_finding_carries_a_class_the_skill_knows(tmp_path):
    """One run, three kinds of report: inflation, divergence, unclassifiable -- never capacity."""
    fork = _fork(tmp_path, caps=_BRAND_CAPS,
                 scripts={"scripts/check-windows-footguns.py": _FOOTGUN_FAIL})
    candidate, upstream = _brand_pair(tmp_path)
    _ = (fork / "scripts/ci/check_os_marker_fakes.py").unlink()  # + an unavailable

    issues = run_fork_lint(str(fork), str(candidate), str(upstream))

    kinds = {issue.classification for issue in issues}
    assert kinds == {BRAND_INFLATION, GENUINE_DIVERGENCE, ""}
    assert RUNNER_CAPACITY not in kinds
    for issue in issues:
        if issue.status == UNAVAILABLE:
            assert issue.classification == "", issue.detail
        else:
            assert issue.classification in {BRAND_INFLATION, GENUINE_DIVERGENCE}


def test_an_upstream_that_is_red_too_is_not_the_brands_fault(tmp_path):
    """Class 2's tell is "upstream's identical tree is green"; when upstream is over the
    same cap itself the red is class 3 and a sync owns it, not the transform."""
    fork = _fork(tmp_path, caps=(2_048, 1_684, 1_600))
    candidate = tmp_path / "candidate"
    upstream = tmp_path / "upstream"
    _write(upstream / "AGENTS.md", "hermes " * 130)  # 910 chars
    _write(upstream / "gateway/AGENTS.md", "hermes gateway docs\n" * 45)  # 900: chain 1810
    _write(candidate / "AGENTS.md", "nastech " * 130)
    _write(candidate / "gateway/AGENTS.md", "nastech gateway docs\n" * 45)

    (chain,) = predict_brand_inflation(str(fork), str(candidate), str(upstream))

    assert chain.blocking and chain.classification == GENUINE_DIVERGENCE


def test_a_unit_upstream_does_not_ship_is_fork_owned_divergence(tmp_path):
    """A fork-only AGENTS.md over the hint cap: branding cannot have caused it (class 3)."""
    fork = _fork(tmp_path, caps=(2_048, 8_000, 1_500))
    candidate = tmp_path / "candidate"
    upstream = tmp_path / "upstream"
    _write(upstream / "AGENTS.md", _ROOT_UP)
    _write(candidate / "AGENTS.md", _ROOT_UP.replace("hermes", "nastech"))
    _write(candidate / "fork_only/AGENTS.md", "nastech owns this doc.\n" * 120)  # 2760

    issues = predict_brand_inflation(str(fork), str(candidate), str(upstream))

    (fork_owned,) = issues
    assert fork_owned.path == "fork_only/AGENTS.md"
    assert fork_owned.blocking and fork_owned.classification == GENUINE_DIVERGENCE


# ---------------------------------------------------------------------------
# (e) the prepublish gate actually calls this
# ---------------------------------------------------------------------------

def _git_repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True)
    return str(path)


def _snapshot(path: Path, sha: str) -> None:
    path.mkdir()
    (path / "manifest.json").write_text(json.dumps({
        "upstream_sha": sha,
        "source_provenance": {
            "remote_url": "https://github.com/NousResearch/hermes-agent.git",
            "fetched_at": "2026-08-16T00:00:00+00:00",
            "acquisition": "fresh-direct-clone",
        },
    }), encoding="utf-8")
    (path / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    runner = path / "scripts" / "run_tests.sh"
    runner.parent.mkdir()
    runner.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(runner, 0o755)


def _publishable(tmp_path: Path) -> tuple[Path, Path, str]:
    """A snapshot that passes every other readiness scan, so a fork-lint issue is the only
    variable these tests move."""
    upstream = Path(_git_repo(tmp_path / "upstream"))
    (upstream / "clean.py").write_text("value = 'clean'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
    subprocess.run(["git", "-C", str(upstream), "commit", "-q", "-m", "clean"], check=True)
    sha = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    snapshot = tmp_path / "snapshot"
    _snapshot(snapshot, sha)
    return snapshot, upstream, sha


def test_scan_snapshot_refuses_to_finish_without_the_fork_simulation(tmp_path):
    """Delete the wiring and this fails: a ready candidate must carry the fork-lint verdict.

    With no fork checkout the simulation cannot run, so it is an unavailable -- and
    unavailable blocks.  That is the incident's whole cost: the engine declared itself
    green and the fork's CI spent the human's hour.
    """
    snapshot, upstream, sha = _publishable(tmp_path)
    assert prepublish.scan_snapshot(snapshot, upstream, sha) == []  # every other scan clean

    issues = prepublish.scan_snapshot(snapshot, upstream, sha, str(tmp_path / "absent-fork"))

    assert [issue.code for issue in issues] == ["fork-lint"]
    (fork_lint,) = issues
    assert "no fork checkout" in fork_lint.detail
    assert prepublish.is_blocking(fork_lint)


def test_fork_lint_advisory_is_the_only_code_the_gate_lets_through():
    """Advisory exists only because the fork's lint.yml said continue-on-error; the gate
    has no authority to promote or demote anything else."""
    assert not prepublish.is_blocking(
        prepublish.ReadinessIssue("fork-lint-advisory", "p", "d")
    )
    for code in ("fork-lint", "credential-signal", "world-writable", "stale-upstream-path"):
        assert prepublish.is_blocking(prepublish.ReadinessIssue(code, "p", "d"))


def test_main_gates_on_fork_lint_blockers_and_reports_severity(tmp_path):
    """The CLI the pipeline runs: an unsimulated fork is a FAIL, not a PASS with a note."""
    snapshot, upstream, sha = _publishable(tmp_path)
    out = tmp_path / "readiness.json"

    code = prepublish.main([
        "--snapshot", str(snapshot), "--upstream", str(upstream),
        "--expected-upstream-sha", sha, "--fork-root", str(tmp_path / "absent-fork"),
        "--output", str(out),
    ])

    body = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1 and body["gate"] == "FAIL"
    (issue,) = [i for i in body["issues"] if i["code"] == "fork-lint"]
    assert issue["severity"] == prepublish.BLOCKER


def test_main_advisory_findings_do_not_fail_the_gate(tmp_path, monkeypatch):
    """A fork-declared advisory reaches the report but not the verdict -- and it only
    reaches the report because the scan asks for it, with the candidate and the upstream
    handed to the simulation."""
    snapshot, upstream, sha = _publishable(tmp_path)
    seen = {}

    def fake_run_fork_lint(fork_root, candidate_root, upstream_root):
        seen.update(
            fork_root=fork_root, candidate_root=candidate_root, upstream_root=upstream_root
        )
        return [ForkLintIssue(script=AGENTS_SIZE_CHECKER, path="chain",
                              detail="inside the margin", blocking=False)]

    monkeypatch.setattr(prepublish, "run_fork_lint", fake_run_fork_lint)
    out = tmp_path / "readiness.json"

    code = prepublish.main([
        "--snapshot", str(snapshot), "--upstream", str(upstream),
        "--expected-upstream-sha", sha, "--fork-root", str(tmp_path / "fork"),
        "--output", str(out),
    ])

    body = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0 and body["gate"] == "PASS"
    (issue,) = [i for i in body["issues"] if i["code"] == "fork-lint-advisory"]
    assert issue["severity"] == prepublish.ADVISORY
    assert seen["fork_root"] == str(tmp_path / "fork")
    assert seen["candidate_root"] == str(snapshot) and seen["upstream_root"] == str(upstream)


def test_main_requires_the_fork_root(tmp_path, capsys):
    """Fail closed at the CLI: a scan run that forgot --fork-root errors instead of
    publishing a tree nobody simulated."""
    with pytest.raises(SystemExit) as raised:
        prepublish.main(["--snapshot", "s", "--upstream", "u",
                         "--expected-upstream-sha", "x", "--output", "o"])

    assert raised.value.code == 2
    assert "--fork-root" in capsys.readouterr().err


def test_fork_lint_cli_reports_and_exits_nonzero(tmp_path, capsys):
    """``python -m hundredways.forklint`` is a usable pre-flight: FAIL prints and exits 1."""
    absent = str(tmp_path / "absent-fork")

    assert forklint.main(["--fork", absent, "--candidate", str(tmp_path)]) == 1

    printed = capsys.readouterr().out
    assert printed.startswith("fork-lint FAIL: 1 blocking, 0 advisory, 1 unavailable")
    assert report_lines([
        ForkLintIssue(script="s", path="p", detail="d", blocking=True)
    ]) == ["  [BLOCKING] s p: d"]
    assert report_lines([
        ForkLintIssue(script="s", path="", detail="d", blocking=False)
    ]) == ["  [advisory] s: d"]
