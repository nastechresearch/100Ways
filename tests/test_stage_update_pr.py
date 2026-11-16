"""The #344 candidate-publication contract (``stage-update-pr.yml``).

The candidate branch must accumulate, not churn: each update commits the
branded tree on top of the branch's existing head when that head still
descends from ``main``, pushes fast-forward only, and answers a rejected push
by re-reading the remote head and re-deriving on top of it -- never by
rewriting history.  A branch that stops being fast-forwardable fails the step
instead of being forced.  The commit author is the PAT's own identity, so the
accumulated history has a real owner, and the authorization receipt names
exactly what the run may do.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "stage-update-pr.yml"


def test_candidate_push_is_never_forced():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "git push --force" not in text
    assert not re.search(r"git push\s+--?\s*f\b", text), "no force flag on the candidate push"


def test_update_commit_accumulates_on_a_descending_head():
    text = WORKFLOW.read_text(encoding="utf-8")
    # The seed picks the branch head only while it still descends from main.
    assert "git merge-base --is-ancestor" in text
    assert "refs/factory/candidate" in text
    assert "refs/factory/base" in text


def test_a_rejected_push_rederives_instead_of_forcing():
    text = WORKFLOW.read_text(encoding="utf-8")
    # Three attempts, each re-deriving from a pristine copy of the overlay.
    assert "for ATTEMPT in 1 2 3" in text
    assert "branded-pristine" in text
    # And a non-fast-forwardable branch fails the step: fail closed.
    assert "PUSHED" in text and "exit 1" in text


def test_commit_author_is_the_pat_identity_not_the_machine_bot():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "gh api user" in text
    assert 'GIT_AUTHOR_EMAIL="${ACTOR_ID}+${ACTOR_LOGIN}@users.noreply.github.com"' in text
    # The push step no longer configures the bot identity for its commits.
    assert "git config user.email" not in text


def test_receipt_vocabulary_matches_the_workflow_contract():
    receipts = (ROOT / "hundredways" / "receipts.py").read_text(encoding="utf-8")
    assert "fast-forward-push-candidate-branch" in receipts
    assert "force-push-candidate-branch" not in receipts
    policy = (ROOT / "hundredways" / "ci_policy.py").read_text(encoding="utf-8")
    assert "force-pushes" not in policy
