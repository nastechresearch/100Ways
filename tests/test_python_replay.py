from __future__ import annotations

from hundredways.python_replay import member_fingerprint, merge_python_members


def test_carries_independent_import_and_helper_additions() -> None:
    ancestor = "import os\n\ndef run():\n    return os.name\n"
    ours = "import os\nimport desktop_targets\n\ndef run():\n    return os.name\n"
    theirs = "import os\n\ndef publish():\n    return 'fork'\n\ndef run():\n    return os.name\n"

    assert merge_python_members(ancestor, ours, theirs) == (
        "import os\nimport desktop_targets\n\n"
        "def publish():\n    return 'fork'\n\n"
        "def run():\n    return os.name\n"
    )


def test_preserves_ours_bytes_when_semantically_unchanged() -> None:
    ancestor = "def work(x):\n    return x + 1\n"
    ours = "def work( x ) :\n    return x+1  # keep my format\n"

    assert merge_python_members(ancestor, ours, ancestor) == ours


def test_adopts_upstream_removal_when_fork_member_is_unchanged() -> None:
    ancestor = "NATIVE_LEGS = ('linux',)\n\ndef run():\n    return 1\n"
    ours = "from targets import NATIVE_LEGS\n\ndef run():\n    return 1\n"
    theirs = "def run():\n    return 1\n"

    assert merge_python_members(ancestor, ours, theirs) == ours


def test_modified_deleted_assignment_fails_closed() -> None:
    ancestor = "NATIVE_LEGS = ('linux',)\n"
    ours = "from targets import NATIVE_LEGS\n"
    theirs = "NATIVE_LEGS = ('linux', 'win32')\n"

    assert merge_python_members(ancestor, ours, theirs) is None


def test_merges_independent_adjacent_function_edits() -> None:
    ancestor = "def first():\n    return 1\n\ndef second():\n    return 2\n"
    ours = "def first():\n    return 10\n\ndef second():\n    return 2\n"
    theirs = "def first():\n    return 1\n\ndef second():\n    return 20\n"

    assert merge_python_members(ancestor, ours, theirs) == (
        "def first():\n    return 10\n\ndef second():\n    return 20\n"
    )


def test_true_same_line_collision_fails_closed() -> None:
    ancestor = "def build():\n    return 'base'\n"
    ours = "def build():\n    return 'upstream'\n"
    theirs = "def build():\n    return 'fork'\n"

    assert merge_python_members(ancestor, ours, theirs) is None


def test_rejects_duplicate_member_keys() -> None:
    duplicate = "def same():\n    return 1\n\ndef same():\n    return 2\n"

    assert merge_python_members(duplicate, duplicate, duplicate) is None


def test_rejects_overlapping_import_binding() -> None:
    source = "import release_artifacts\nfrom bundles import release_artifacts\n"

    assert merge_python_members(source, source, source) is None


def test_rejects_syntax_error() -> None:
    assert (
        merge_python_members(
            "def ok():\n    return 1\n", "def nope(:\n", "def ok():\n    return 1\n"
        )
        is None
    )


def test_rejects_unrecognized_top_level_statement() -> None:
    source = "for item in []:\n    pass\n"

    assert merge_python_members(source, source, source) is None


def test_keeps_docstring_and_future_import_at_top() -> None:
    ancestor = (
        '"""module docs"""\nfrom __future__ import annotations\n\ndef task():\n    return 1\n'
    )
    ours = ancestor
    theirs = (
        '"""module docs"""\nfrom __future__ import annotations\n\n'
        "def helper():\n    return 2\n\ndef task():\n    return 1\n"
    )

    merged = merge_python_members(ancestor, ours, theirs)

    assert merged == theirs
    compile(merged or "", "<test>", "exec")


def test_keeps_decorator_and_leading_comment_attached() -> None:
    ancestor = (
        "def before():\n    return 1\n\n# explain decorated\n@cache\ndef task():\n    return 2\n"
    )
    ours = ancestor
    theirs = (
        "def before():\n    return 1\n\n# explain decorated\n@cache\ndef task():\n    return 3\n"
    )

    merged = merge_python_members(ancestor, ours, theirs)

    assert "# explain decorated\n@cache\ndef task():\n    return 3" in (merged or "")


def test_inserts_new_members_before_main_guard() -> None:
    ancestor = "def run():\n    return 1\n\nif __name__ == '__main__':\n    run()\n"
    ours = ancestor
    theirs = (
        "def run():\n    return 1\n\n"
        "def report():\n    return 2\n\n"
        "if __name__ == '__main__':\n    run()\n"
    )

    merged = merge_python_members(ancestor, ours, theirs)

    assert merged is not None
    assert merged.index("def report") < merged.index("if __name__")
    assert merged.rstrip().endswith("run()")


def test_is_idempotent() -> None:
    ancestor = "import os\n\ndef run():\n    return os.name\n"
    ours = "import os\nimport sys\n\ndef run():\n    return os.name\n"
    theirs = "import os\n\ndef fork_only():\n    return 3\n\ndef run():\n    return os.name\n"

    merged = merge_python_members(ancestor, ours, theirs)

    assert merged is not None
    assert merge_python_members(merged, merged, merged) == merged


def test_member_fingerprint_ignores_formatting_but_tracks_ast_changes() -> None:
    assert member_fingerprint("def task(x):\n    return x + 1\n") == member_fingerprint(
        "def task( x ):\n    return x+1\n"
    )
    assert member_fingerprint("def task():\n    return 1\n") != member_fingerprint(
        "def task():\n    return 2\n"
    )


def test_resolver_is_not_called_without_member_conflicts() -> None:
    calls: list[tuple[str, ...]] = []
    ancestor = "def first():\n    return 1\n\ndef second():\n    return 2\n"
    ours = "def first():\n    return 10\n\ndef second():\n    return 2\n"
    theirs = "def first():\n    return 1\n\ndef second():\n    return 20\n"

    merged = merge_python_members(
        ancestor,
        ours,
        theirs,
        resolve=lambda key, base, current, fork: calls.append(key) or current,
    )

    assert merged is not None
    assert calls == []


def test_resolver_unknown_conflict_choice_fails_closed() -> None:
    calls: list[tuple[str, ...]] = []
    ancestor = "def build():\n    return 'base'\n"
    ours = "def build():\n    return 'current'\n"
    theirs = "def build():\n    return 'fork'\n"

    assert (
        merge_python_members(
            ancestor,
            ours,
            theirs,
            resolve=lambda key, base, current, fork: calls.append(key) or None,
        )
        is None
    )
    assert calls == [("member:build",)]


def test_resolver_rejects_wrong_name_or_multiple_member_injection() -> None:
    ancestor = "def build():\n    return 'base'\n"
    ours = "def build():\n    return 'current'\n"
    theirs = "def build():\n    return 'fork'\n"

    assert (
        merge_python_members(
            ancestor,
            ours,
            theirs,
            resolve=lambda key, base, current, fork: "def injected():\n    return 1\n",
        )
        is None
    )
    assert (
        merge_python_members(
            ancestor,
            ours,
            theirs,
            resolve=lambda key, base, current, fork: current + "\ndef helper():\n    return 2\n",
        )
        is None
    )


def test_resolver_accepts_an_exact_supported_choice() -> None:
    ancestor = "def build():\n    return 'base'\n"
    ours = "def build():\n    return 'current'\n"
    theirs = "def build():\n    return 'fork'\n"
    calls: list[tuple[str, ...]] = []

    merged = merge_python_members(
        ancestor,
        ours,
        theirs,
        resolve=lambda key, base, current, fork: calls.append(key) or current,
    )

    assert merged == ours
    assert calls == [("member:build",)]


def test_resolver_errors_fail_closed() -> None:
    ancestor = "def build():\n    return 'base'\n"
    ours = "def build():\n    return 'current'\n"
    theirs = "def build():\n    return 'fork'\n"

    def broken_resolver(
        key: tuple[str, ...], base: str | None, current: str | None, fork: str | None
    ) -> str | None:
        raise RuntimeError("policy bug")

    assert merge_python_members(ancestor, ours, theirs, resolve=broken_resolver) is None
