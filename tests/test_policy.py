"""The policy subset reader, cross-checked against a real YAML parser.

Why a reader at all: ``pyyaml`` is a *dev* extra (``pyproject.toml``), so the
engine's runtime interpreter has no YAML library, while the policy files that
drive the evidence surface are YAML.  Adding a core dependency for four files
would change what a base install pulls in, so ``policy.parse`` reads the narrow
block-style shape our own policy files use and refuses everything else.

Refusing is the safety property: a construct we did not read must never be
silently ignored, because a silently-ignored line in a policy file is a policy
that is not enforced.  These tests therefore prove three things — the subset
parses, anything outside it raises, and where the subset *is* legal the reader
agrees with ``yaml.safe_load`` (the oracle, not a self-assertion).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hundredways.policy import PolicyError, load, parse, require

REPO = Path(__file__).resolve().parents[1]

URL_POLICY_SCHEMA = {
    "version": "int",
    "allowed_hosts": "list",
    "upstream_hosts": "list",
    "placeholder_hosts": "list",
    "ignore_paths": "list",
    "probe.timeout_seconds": "int",
    "probe.max_urls": "int",
    "probe.wall_budget_seconds": "int",
}


def test_flat_scalars_and_comments():
    doc = parse(
        "# leading comment\n"
        "name: url-policy\n"
        "max_urls: 200\n"
        'quoted: "has: colon"\n'
        "single: 'apos'\n"
        'empty: ""\n'
    )
    assert doc == {
        "name": "url-policy",
        "max_urls": 200,
        "quoted": "has: colon",
        "single": "apos",
        "empty": "",
    }


def test_string_lists_at_either_indent():
    doc = parse(
        "allowed_hosts:\n"
        "  - nastechresearch.com\n"
        "  - 'nastech.github.io'\n"
        "ignore:\n"
        "- schemas\n"
        "- example.com\n"
    )
    assert doc == {
        "allowed_hosts": ["nastechresearch.com", "nastech.github.io"],
        "ignore": ["schemas", "example.com"],
    }


def test_nested_mappings_to_any_depth():
    doc = parse(
        "probe:\n"
        "  timeout_seconds: 5\n"
        "  budget:\n"
        "    max_urls: 300\n"
        "    hosts:\n"
        "      - a.test\n"
        "tail: done\n"
    )
    assert doc == {
        "probe": {
            "timeout_seconds": 5,
            "budget": {"max_urls": 300, "hosts": ["a.test"]},
        },
        "tail": "done",
    }


@pytest.mark.parametrize(
    "text, fragment",
    [
        pytest.param("hosts: [a, b]\n", "flow", id="flow-sequence"),
        pytest.param("hosts: {a: 1}\n", "flow", id="flow-mapping"),
        pytest.param("base: &anchor value\n", "anchor", id="anchor"),
        pytest.param("a: *ref\n", "alias", id="alias"),
        pytest.param("a: !!str x\n", "tag", id="tag"),
        pytest.param("a: |\n  block\n", "block scalar", id="literal-block-scalar"),
        pytest.param("a: >\n  folded\n", "block scalar", id="folded-block-scalar"),
        pytest.param("---\na: 1\n", "document", id="document-start"),
        pytest.param("a: 1\n...\n", "document", id="document-end"),
        pytest.param("\ta: 1\n", "tab", id="tab-indent"),
        pytest.param("a: 1\na: 2\n", "duplicate", id="duplicate-key"),
        pytest.param("  a: 1\n", "indent", id="first-line-indented"),
        pytest.param("- top level list\n", "sequence", id="top-level-sequence"),
        pytest.param("scalar\n", "no key", id="bare-scalar-no-key"),
        pytest.param("a: 1\n  b: 2\n", "continue", id="continuation-line"),
        pytest.param("a: 1\n b: 2\n", "indent", id="odd-indent"),
        pytest.param("a: null\n", "null", id="null-token"),
        pytest.param("a: ~\n", "null", id="tilde-null"),
        pytest.param("a: yes\n", "truth word", id="unquoted-truth-word"),
        pytest.param("hosts:\n  - no\n", "truth word", id="unquoted-truth-item"),
        pytest.param("a: '\"unterminated\n", "unterminated", id="unterminated-quote"),
        pytest.param("a: x: y\n", "key", id="value-with-colon-not-a-key"),
        pytest.param("a: 07\n", "quote", id="leading-zero-int"),
        pytest.param("a: 1e3\n", "quote", id="exponent-number"),
        pytest.param("a: 0x1f\n", "quote", id="hex-number"),
        pytest.param("a: 1:30\n", "quote", id="sexagesimal"),
        pytest.param("a: .5\n", "quote", id="bare-fraction"),
        pytest.param("a: 1.\n", "quote", id="trailing-dot-number"),
    ],
)
def test_anything_outside_the_subset_raises(text, fragment):
    with pytest.raises(PolicyError) as excinfo:
        parse(text)
    assert fragment in str(excinfo.value).lower()


def test_parse_reports_the_offending_line_number():
    with pytest.raises(PolicyError) as excinfo:
        parse("a: 1\nb: 2\nc: [3]\n")
    assert "line 3" in str(excinfo.value)


def test_require_accepts_a_conforming_document():
    doc = parse("name: p\ncount: 3\nhosts:\n  - a\nnested:\n  on: 'true'\n")
    require(
        doc,
        {"name": "str", "count": "int", "hosts": "list", "nested.on": "bool"},
        source="p.yml",
    )


def test_require_names_every_violation_in_order():
    doc = parse("count: many\nhosts: here\nmissing_deep: x\n")
    with pytest.raises(PolicyError) as excinfo:
        require(
            doc,
            {"count": "int", "hosts": "list", "nested.on": "bool", "absent": "str"},
            source="p.yml",
        )
    message = str(excinfo.value)
    assert "p.yml" in message
    for key in ("count", "hosts", "nested.on", "absent"):
        assert key in message
    assert message.index("count") < message.index("hosts") < message.index("nested.on")


@pytest.mark.parametrize(
    "kind, value",
    [
        pytest.param("int", "5", id="int-as-a-string"),
        pytest.param("bool", "maybe", id="bool-not-a-bool"),
        pytest.param("list", "scalar", id="list-is-scalar"),
        pytest.param("str", 4, id="str-not-a-string"),
    ],
)
def test_require_kinds_are_strict(kind, value):
    with pytest.raises(PolicyError):
        require({"k": value}, {"k": kind}, source="p.yml")


def test_a_number_is_read_as_a_number_and_a_truth_word_as_a_boolean():
    doc = parse('count: 3\nratio: 1.5\non: "true"\noff: "false"\nnegative: -1\n')
    require(
        doc,
        {"count": "int", "on": "bool", "off": "bool", "negative": "int"},
        source="p.yml",
    )
    assert doc["count"] == 3 and isinstance(doc["count"], int)
    assert doc["negative"] == -1
    assert doc["on"] is True and doc["off"] is False
    # A float is not a str: the schema is what says which kind a field is.
    with pytest.raises(PolicyError):
        require(doc, {"ratio": "str"}, source="p.yml")


def test_load_reads_a_real_file_from_disk(tmp_path):
    path = tmp_path / "url-policy.yml"
    path.write_text("allowed_hosts:\n  - nastechresearch.com\n", encoding="utf-8")
    assert load(str(path)) == {"allowed_hosts": ["nastechresearch.com"]}


def test_load_raises_on_a_missing_file(tmp_path):
    with pytest.raises(PolicyError, match="no such policy"):
        load(str(tmp_path / "absent.yml"))


def test_load_validates_against_the_schema_it_was_given(tmp_path):
    path = tmp_path / "url-policy.yml"
    path.write_text("version: one\n", encoding="utf-8")
    with pytest.raises(PolicyError) as excinfo:
        load(str(path), {"version": "int", "allowed_hosts": "list"})
    assert "version" in str(excinfo.value)
    assert "allowed_hosts" in str(excinfo.value)


def test_shipped_policies_parse_and_match_their_schema():
    policy = load(str(REPO / "config" / "url-policy.yml"), URL_POLICY_SCHEMA)
    assert "nastechresearch.com" in policy["allowed_hosts"]
    assert isinstance(policy["probe"]["timeout_seconds"], int)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("a: 1\n", id="scalar"),
        pytest.param('a: "x: y"\n', id="double-quoted"),
        pytest.param("a: 'x'\n", id="single-quoted"),
        pytest.param("a:\n- x\n- y\n", id="list-zero-indent"),
        pytest.param("a:\n  b:\n    - x\n    - y\n", id="deep-list"),
        pytest.param("a: 1\n\n# comment\nb: 2\n", id="comment-and-blank"),
        pytest.param("a: 1\nb:\n  c: 2\n", id="nested-map"),
        pytest.param('a: "on"\nb: "false"\n', id="quoted-booleans"),
        pytest.param("a: '# not a comment'\n", id="quoted-hash"),
        pytest.param("a: x # trailing\n", id="trailing-comment"),
        pytest.param("a: 3.11\n", id="float"),
        pytest.param("a: -12\n", id="negative-int"),
        pytest.param("a: .env\n", id="dotted-name"),
        pytest.param("a: 127.0.0.1\n", id="ip-address-is-a-string"),
        pytest.param("a: 1.2.3\n", id="version-is-a-string"),
        pytest.param("hosts:\n  - 3.11\n  - a\n", id="numeric-list-item"),
    ],
)
def test_the_reader_agrees_with_the_real_yaml_parser(text):
    # The oracle: pyyaml ships in the `dev` extra, so engine CI runs this; on a
    # base install it is skipped rather than silently passing.
    yaml = pytest.importorskip("yaml", reason="pyyaml is a dev extra (base installs have none)")
    assert parse(text) == yaml.safe_load(text)


def test_the_shipped_policy_file_loads_under_the_real_parser():
    yaml = pytest.importorskip(
        "yaml", reason="pyyaml is a dev extra (base installs have none)"
    )
    text = (REPO / "config" / "url-policy.yml").read_text(encoding="utf-8")
    assert parse(text) == yaml.safe_load(text)
