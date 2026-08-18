from hundredways.prompt_gate import compare_prompt_resume


def test_prompt_resume_gate_accepts_byte_identical_prompt():
    evidence = compare_prompt_resume("stable prompt", "stable prompt")

    assert evidence.passed is True
    assert evidence.classification == "identical"
    assert evidence.first_difference is None
    assert evidence.first_digest == evidence.resumed_digest


def test_prompt_resume_gate_classifies_workspace_git_drift():
    first = "Guidance\n\nWorkspace:\nrecent commits: alpha"
    resumed = "Guidance\n\nWorkspace:\nrecent commits: beta"

    evidence = compare_prompt_resume(first, resumed)

    assert evidence.passed is False
    assert evidence.classification == "workspace-git-context"
    assert evidence.first_difference is not None
    assert evidence.first_digest != evidence.resumed_digest


def test_prompt_resume_gate_classifies_plugin_drift():
    first = "## Plugin Context: example.rules\noriginal bytes"
    resumed = "## Plugin Context: example.rules\nchanged bytes"

    evidence = compare_prompt_resume(first, resumed)

    assert evidence.classification == "plugin-section"
    assert "original" in evidence.first_context
    assert "changed" in evidence.resumed_context


def test_prompt_resume_gate_marks_unrecognized_drift_unknown():
    evidence = compare_prompt_resume("prefix alpha", "prefix bravo")

    assert evidence.passed is False
    assert evidence.classification == "unknown"
