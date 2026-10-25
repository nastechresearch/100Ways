"""Tests for :mod:`hundredways.uipack`.

Everything here is hermetic.  ``uipack`` reads three tarballs off
``registry.npmjs.org`` in production, and an autouse fixture replaces ``urlopen`` with a
guard that raises ``AssertionError``, so a test that forgets to stub a URL turns red
instead of reaching the network -- a suite that silently depends on npm's availability is a
suite that fails in a merge freeze.  The published shapes are reproduced as small synthetic
trees instead: the real pair is 352 paths and 3.2 MB, and the rules under test are about
bytes, not about size.

The fixtures carry the interesting cases on purpose: one span the human spelled two ways,
one morpheme with two observed spellings, a rename only content can justify, a residual
literal edit that is not a token rewrite, a path the pair proves untouched (with the one
brand word the human left in it), one fork path no rule can spell, and one ``<to>`` token
outside everything the pair proves.
"""

from __future__ import annotations

import io
import json
import tarfile
import urllib.error

import pytest

from hundredways import uipack
from hundredways.coverage import resolve_symbol
from hundredways.forklint import required_flags
from hundredways.uipack import (
    BrandStemSurvivorError,
    IdentityViolationError,
    OutputDirError,
    PatchAnchorError,
    PathSetError,
    TarballShapeError,
    Transform,
    UiPackError,
    UiTarballFetchError,
    UnmappableBrandTokenError,
    audit_stems,
    build_output,
    check_out_dir,
    fetch,
    package_name,
    parse_decisions,
    publish_command,
    read_tree,
    tarball_url,
)
from hundredways.ways import build_registry

# -- the network wall -------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_unstubbed_npm(monkeypatch):
    """Any npm request this file does not stub is a bug, not a retry."""

    def guard(url, *args, **kwargs):
        raise AssertionError(f"tests/test_uipack.py reached for the network: {url}")

    monkeypatch.setattr(uipack.urllib.request, "urlopen", guard)


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._payload


def _serve(monkeypatch, blobs: dict[str, bytes]):
    """Stand in for ``registry.npmjs.org`` for the duration of one test."""

    def fake_urlopen(url, *args, **kwargs):
        try:
            return _Response(blobs[str(url)])
        except KeyError:
            raise AssertionError(f"stubbed registry was asked for an unstored url: {url}") from None

    monkeypatch.setattr(uipack.urllib.request, "urlopen", fake_urlopen)


# -- the published pair, in miniature ---------------------------------------

HEADER = (
    b'import { Badge } from "@nous-research/ui";\n\n'
    b'export const title = "Hermes panel by Nous Research";\n'
)
BRANCHED_README = (
    b"# @nous-research/ui\n\n"
    b"Docs: https://portal.nousresearch.com/docs\n"
    b"Contact: team@nousresearch.example\n"
)
#: The one brand word the human chose to leave standing, in a file the pair proves was
#: otherwise untouched.  Its budget is 1 there and 0 everywhere else.
PLAIN = b'export const plain = "a component";\n// mirror hermes only\n'

UP_FROM: dict[str, bytes] = {
    "package.json": (
        b'{\n  "name": "@nous-research/ui",\n  "version": "0.18.2",\n  "scripts": {\n'
        b'    "dev": "storybook dev -p 6006"\n  }\n}\n'
    ),
    "src/ui/header.tsx": HEADER,
    "src/ui/nous-girl.tsx": b'export const nousGirl = "Nous";\n',
    "README.md": BRANCHED_README,
    "src/ui/plain.tsx": PLAIN,
}

FORK_FROM: dict[str, bytes] = {
    "package.json": b'{\n  "name": "@nastech-research/ui",\n  "version": "0.18.2"\n}\n',
    "src/ui/header.tsx": (
        b'import { Badge } from "@nastech-research/ui";\n'
        b'\nexport const title = "Nastech panel by Nastech Research";\n'
    ),
    "src/ui/nastech-girl.tsx": b'export const nastechGirl = "Nastech";\n',
    "README.md": (
        b"# @nastech-research/ui\n\n"
        b"Docs: https://portal.nastechai.com/docs\n"
        b"Contact: team@nastechairesearch.example\n"
    ),
    "src/ui/plain.tsx": PLAIN,
}

#: The fork README with the registry scope spelled the *other* way the human used, which is
#: what the published pair really does across README.md, header and CHANGELOG.md.
FORK_README_OTHER_SCOPE = (
    b"# @nastech/ui\n\n"
    b"Docs: https://portal.nastechai.com/docs\n"
    b"Contact: team@nastechairesearch.example\n"
)

#: Upstream's next publish: the ``<from>`` files with a new version plus one file the pair
#: never saw.
UP_TO: dict[str, bytes] = {
    **UP_FROM,
    "package.json": UP_FROM["package.json"].replace(b"0.18.2", b"0.19.1"),
    "src/ui/new-widget.tsx": (
        b'import { Badge } from "@nous-research/ui";\n\nexport const widget = "Hermes";\n'
    ),
}

#: A file new in ``<to>`` whose host carries the morpheme the pair spells two ways -- the
#: shape upstream 0.19.1's added ``blend-mode.test.*`` files have.
NEW_TOKEN_FILE = b"// see https://cdn.nousresearch.com/beta.js\n"
NEW_SCOPE_FILE = b'import { x } from "@nous-research/ui";\n'


def _tarball(files: dict[str, bytes], *, prefix: str = "package/") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(files):
            info = tarfile.TarInfo(f"{prefix}{path}")
            info.size = len(files[path])
            info.mtime = 0
            tar.addfile(info, io.BytesIO(files[path]))
    return buffer.getvalue()


def _derive() -> Transform:
    return Transform.derive(UP_FROM, FORK_FROM)


def _scope_split_pair() -> tuple[dict[str, bytes], dict[str, bytes]]:
    """The same pair, but with the registry scope rewritten two different ways."""
    up = dict(UP_FROM)
    fork = dict(FORK_FROM)
    fork["README.md"] = FORK_README_OTHER_SCOPE
    return up, fork


# -- derivation reads the pair ----------------------------------------------


def test_the_derived_rules_are_exactly_what_the_pair_shows():
    tf = _derive()
    assert tf.spans == {
        b"nous-research/ui": b"nastech-research/ui",
        b"Hermes": b"Nastech",
        b"Nous": b"Nastech",
        b"nousGirl": b"nastechGirl",
        b"https://portal.nousresearch.com/docs": b"https://portal.nastechai.com/docs",
        b"team@nousresearch.example": b"team@nastechairesearch.example",
    }
    assert tf.renames == {"src/ui/nous-girl.tsx": "src/ui/nastech-girl.tsx"}
    assert tf.stems == {b"nous", b"hermes", b"nousresearch"}
    assert tf.changed_paths == {
        "package.json",
        "src/ui/header.tsx",
        "src/ui/nous-girl.tsx",
        "README.md",
    }


def test_the_proof_reproduces_every_published_from_path():
    # derive() proves internally; saying it out per path makes the claim checkable and shows
    # the rename is reproduced by the content rules rather than copied from a table.
    tf = _derive()
    for path, data in UP_FROM.items():
        target = tf.map_path(path, proven=True)
        got, _ = tf.apply_path(path, data, proven=True)
        assert got == FORK_FROM[target], path


def test_no_brand_spelling_is_hard_coded():
    """Swap both brands and the same code derives the same shape of answer."""
    up = {
        "package.json": b'{\n  "name": "@glop-research/ui",\n  "version": "1.0.0"\n}\n',
        "src/t.js": b'import { w } from "@glop-research/ui";\n\nexport const t = "Glop Widget";\n',
    }
    fork = {
        "package.json": b'{\n  "name": "@zizz-research/ui",\n  "version": "1.0.0"\n}\n',
        "src/t.js": b'import { w } from "@zizz-research/ui";\n\nexport const t = "Zizz Widget";\n',
    }
    tf = Transform.derive(up, fork)
    assert tf.spans == {b"glop-research/ui": b"zizz-research/ui", b"Glop": b"Zizz"}
    assert tf.stems == {b"glop"}
    to = {
        "package.json": up["package.json"],
        "src/t.js": up["src/t.js"],
        "src/new.js": b"// Glop\n",
    }
    out, stats, blockers = build_output(tf, to)
    assert blockers == []
    assert out["src/t.js"] == fork["src/t.js"]
    assert out["src/new.js"] == b"// Zizz\n"
    assert stats["new_paths"] == ["src/new.js"]


def test_a_residual_literal_edit_is_recorded_as_a_patch_not_a_token_rule():
    # The fork's published package.json drops upstream's scripts block entirely; that is not
    # a rename of any readable unit, so it has to be carried as an anchored literal edit.
    tf = _derive()
    windows = [patch.old for patch in tf.patches.get("package.json", ())]
    assert windows, "the scripts-block deletion must survive as a residual edit"
    assert all(b"0.18.2" not in old for old in windows), windows
    out, stats, blockers = build_output(tf, UP_TO)
    assert not any(isinstance(block, PatchAnchorError) for block in blockers), blockers
    assert json.loads(out["package.json"]) == {"name": "@nastech-research/ui", "version": "0.19.1"}
    assert "package.json" in stats["rebranded"]


# -- tiers: global rule, per-path ledger, refusal ----------------------------


def test_a_span_rewritten_everywhere_is_trusted_on_a_file_that_does_not_exist_yet():
    tf = _derive()
    out, stats, blockers = build_output(tf, UP_TO)
    assert blockers == []
    assert out["src/ui/new-widget.tsx"] == (
        b'import { Badge } from "@nastech-research/ui";\n\nexport const widget = "Nastech";\n'
    )
    assert stats["outside_the_pair"] == ["src/ui/new-widget.tsx"], "a touched new path is named"


def test_a_span_the_human_spelled_two_ways_stays_a_per_path_ledger():
    up, fork = _scope_split_pair()
    tf = Transform.derive(up, fork)
    assert b"nous-research/ui" not in tf.spans, "two spellings must not fuse into one rule"
    assert tf.ledgers[("README.md", b"nous-research/ui")] == [b"nastech/ui"]
    assert tf.ledgers[("src/ui/header.tsx", b"nous-research/ui")] == [b"nastech-research/ui"]
    assert set(tf.ambiguous[b"nous-research/ui"]) == {b"nastech/ui", b"nastech-research/ui"}
    out, _, blockers = build_output(tf, up)
    assert blockers == []
    assert b'from "@nastech-research/ui"' in out["src/ui/header.tsx"]
    assert out["README.md"].startswith(b"# @nastech/ui\n")


def test_a_new_file_carrying_a_two_way_span_stops_and_shows_both_spellings():
    up, fork = _scope_split_pair()
    tf = Transform.derive(up, fork)
    to = dict(up)
    to["src/ui/blend-mode.test.tsx"] = NEW_SCOPE_FILE
    _, _, blockers = build_output(tf, to)
    assert [type(block) for block in blockers] == [UnmappableBrandTokenError]
    text = str(blockers[0])
    at = NEW_SCOPE_FILE.find(b"nous-research/ui")
    assert f"src/ui/blend-mode.test.tsx (offset {at})" in text, text
    assert "rewritten 2 different ways" in text, text
    assert "nastech/ui" in text and "nastech-research/ui" in text, text


def test_a_brand_morpheme_with_two_observed_spellings_is_refused_not_composed():
    tf = _derive()
    assert tf.morpheme_evidence[b"nousresearch"] == {b"nastechai": 1, b"nastechairesearch": 1}
    assert b"nousresearch" not in tf.morphemes
    to = dict(UP_TO)
    to["src/ui/blend-mode.test.tsx"] = NEW_TOKEN_FILE
    _, _, blockers = build_output(tf, to)
    assert [type(block) for block in blockers] == [UnmappableBrandTokenError]
    text = str(blockers[0])
    at = NEW_TOKEN_FILE.find(b"https://cdn")
    assert f"src/ui/blend-mode.test.tsx (offset {at})" in text, text
    assert "cdn.nousresearch.com/beta.js" in text, text
    assert "nastechai 1x, nastechairesearch 1x" in text, text
    assert "--map" in text, "the stop has to name the way out"


def test_one_run_names_every_missing_decision_instead_of_one_per_run():
    up, fork = _scope_split_pair()
    tf = Transform.derive(up, fork)
    to = dict(up)
    to["src/ui/blend-mode.test.tsx"] = NEW_TOKEN_FILE
    to["src/ui/other.test.tsx"] = NEW_SCOPE_FILE
    _, _, blockers = build_output(tf, to)
    open_tokens = [block for block in blockers if isinstance(block, UnmappableBrandTokenError)]
    assert len(open_tokens) == 2, [str(block) for block in blockers]
    assert {"https://cdn.nousresearch.com/beta.js", "nous-research/ui"} == {
        block.args[0].split(": span ")[1].split("'")[1] for block in open_tokens
    }


def test_a_recorded_human_decision_unblocks_the_new_token():
    decided = Transform.derive(
        UP_FROM,
        FORK_FROM,
        {"https://cdn.nousresearch.com/beta.js": "https://cdn.nastechai.com/beta.js"},
    )
    assert decided.spans[b"Nous"] == b"Nastech", "a decision must not overwrite a derived rule"
    to = dict(UP_TO)
    to["src/ui/blend-mode.test.tsx"] = NEW_TOKEN_FILE
    out, stats, blockers = build_output(decided, to)
    assert blockers == []
    assert b"https://cdn.nastechai.com/beta.js" in out["src/ui/blend-mode.test.tsx"]
    assert stats["outside_the_pair"] == ["src/ui/blend-mode.test.tsx", "src/ui/new-widget.tsx"]


def test_a_human_decision_cannot_make_the_derivation_prove_itself():
    tf = _derive()
    tf.human_decisions[b"plain"] = b"Zaphod"
    unchanged = UP_FROM["src/ui/plain.tsx"]
    # the proof pass (proven=True) may not use a decision to close a gap in the evidence ...
    assert tf.apply_path("src/ui/plain.tsx", unchanged, proven=True)[0] == unchanged
    # ... while the application pass reads it as the human decision it is
    assert b"Zaphod" in tf.apply_path("src/ui/plain.tsx", unchanged, proven=False)[0]


def test_an_occurrence_past_the_recorded_ledger_is_a_blocker():
    up = {"src/a.txt": b"Nous Nous Nous\n", "src/b.txt": b"Nous here\n"}
    fork = {"src/a.txt": b"Nastech Nastech Nous\n", "src/b.txt": b"Nastech here\n"}
    tf = Transform.derive(up, fork)
    assert tf.ledgers[("src/a.txt", b"Nous")] == [b"Nastech", b"Nastech", b"Nous"]
    assert b"Nous" not in tf.spans, "the third occurrence stayed Nous, so no global rule exists"
    to = {"src/a.txt": b"Nous Nous Nous Nous\n", "src/b.txt": b"Nous here\n"}
    _, _, blockers = build_output(tf, to)
    assert [type(block) for block in blockers] == [UnmappableBrandTokenError]
    assert "occurrence #4 of 'Nous' is past the 3" in str(blockers[0]), str(blockers[0])


# -- the guards that stop a run ---------------------------------------------


def test_a_path_the_pair_proved_untouched_stays_byte_identical():
    tf = _derive()
    out, stats, blockers = build_output(tf, UP_TO)
    assert blockers == []
    assert out["src/ui/plain.tsx"] == UP_FROM["src/ui/plain.tsx"]
    assert "src/ui/plain.tsx" in stats["identical"]


def test_a_new_brand_token_in_a_proven_untouched_path_is_a_leak_not_a_pass():
    tf = _derive()
    to = dict(UP_TO)
    leaked = UP_FROM["src/ui/plain.tsx"] + b"// and Nous again\n"
    to["src/ui/plain.tsx"] = leaked
    _, _, blockers = build_output(tf, to)
    assert [type(block) for block in blockers] == [BrandStemSurvivorError]
    text = str(blockers[0])
    assert "'nous' survives 1 time(s)" in text, text
    assert f"offsets [{leaked.find(b'Nous')}]" in text, text


def test_the_survivor_budget_is_the_humans_own_tolerance_not_a_word_list():
    tf = _derive()
    assert tf.tolerations["src/ui/plain.tsx"][b"hermes"] == 1
    # one 'hermes' is allowed exactly where the published fork tree still says it ...
    assert audit_stems(tf, {"src/ui/plain.tsx": PLAIN}) == []
    # ... and a second one, or one anywhere else, is not.
    assert audit_stems(tf, {"src/ui/plain.tsx": PLAIN + b"// hermes\n"})
    assert audit_stems(tf, {"README.md": FORK_FROM["README.md"] + b"// hermes\n"})


def test_a_rename_the_content_rules_cannot_spell_is_not_a_rename():
    fork = dict(FORK_FROM)
    fork["src/ui/sunny-badge.tsx"] = b'export const sunny = "Nastech";\n'
    with pytest.raises(PathSetError, match="rename set is not closed"):
        Transform.derive(UP_FROM, fork)


def test_two_possible_rename_partners_are_a_stop_not_a_coin_toss():
    up = {"src/ui/a-old.tsx": b'export const a = "Nous old";\n', "src/ui/b.tsx": b"// b\n"}
    fork = {
        "src/ui/zeta-other.tsx": b'export const a = "Nastech old";\n',
        "src/ui/b.tsx": b"// b\n",
    }
    with pytest.raises(PathSetError, match="a rename nobody can spell"):
        Transform.derive(up, fork)


def test_two_to_paths_that_map_to_one_output_name_would_drop_a_file():
    tf = _derive()
    to = dict(UP_TO)
    to["src/ui/nastech-girl.tsx"] = b'export const nastechGirl = "Nastech";\n'
    _, _, blockers = build_output(tf, to)
    assert [type(block) for block in blockers] == [PathSetError]
    assert "both map to" in str(blockers[0]) and "would drop a file" in str(blockers[0])


def test_a_moved_context_breaks_the_literal_patch():
    # Upstream re-flowed package.json in <to>: the window the <from> pair anchored is gone,
    # and approximating it would ship a scripts block the human deliberately deleted.
    tf = _derive()
    to = dict(UP_TO)
    to["package.json"] = (
        b'{\n  "name": "@nous-research/ui",\n  "version": "0.19.1",\n'
        b'  "scripts": { "dev": "storybook dev -p 6006" }\n}\n'
    )
    _, _, blockers = build_output(tf, to)
    assert [type(block) for block in blockers] == [PatchAnchorError]
    assert "residual edit expected exactly once, found 0" in str(blockers[0]), str(blockers[0])


def test_a_byte_change_with_no_rule_firing_for_it_is_refused(monkeypatch):
    tf = _derive()
    original = Transform._apply_patches

    def sneaky(self, path, data, firings=None):
        out = original(self, path, data, firings)
        return out + b"\n# edited\n" if path == "src/ui/plain.tsx" else out

    monkeypatch.setattr(Transform, "_apply_patches", sneaky)
    _, _, blockers = build_output(tf, UP_TO)
    assert any(isinstance(block, IdentityViolationError) for block in blockers), blockers
    assert any("cannot be attributed" in str(block) for block in blockers)


def test_a_derivation_bug_is_caught_by_the_proof_before_to_is_touched(monkeypatch):
    # The proof pass is a net against the derivation drifting from the published bytes, so
    # the honest test injects exactly that drift: one invented global span rule.
    original = Transform._derive_morphemes

    def buggy(self):
        original(self)
        self.spans[b"title"] = b"caption"

    monkeypatch.setattr(Transform, "_derive_morphemes", buggy)
    with pytest.raises(uipack.TransformProofError) as caught:
        Transform.derive(UP_FROM, FORK_FROM)
    text = str(caught.value)
    assert "src/ui/header.tsx" in text and "differ at offset" in text, text
    assert "derived=" in text and "published=" in text, text


# -- tarball shape and the registry wall ------------------------------------


def test_tarball_url_reproduces_npms_own_scoped_form():
    assert tarball_url("@nous-research/ui", "0.18.2") == (
        "https://registry.npmjs.org/@nous-research/ui/-/ui-0.18.2.tgz"
    )
    assert tarball_url("@nastech-research/ui", "0.19.1") == (
        "https://registry.npmjs.org/@nastech-research/ui/-/ui-0.19.1.tgz"
    )


def test_fetch_refuses_the_npm_not_found_body_instead_of_calling_it_a_tarball(monkeypatch):
    # Measured: npm answers an unpublished version with exactly this 21-byte body, which is
    # the state upstream 0.18.3/0.18.4 are in -- only the fork published them.
    _serve(monkeypatch, {"https://registry.npmjs.org/x": b'{"error":"Not found"}'})
    assert len(b'{"error":"Not found"}') == 21
    with pytest.raises(UiTarballFetchError, match=r"expected a gzip tarball, got 21B"):
        fetch("https://registry.npmjs.org/x")


def test_fetch_wraps_a_socket_failure(monkeypatch):
    def boom(url, *args, **kwargs):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(uipack.urllib.request, "urlopen", boom)
    with pytest.raises(UiTarballFetchError, match="no route to host"):
        fetch("https://registry.npmjs.org/x")


def test_an_unstubbed_request_is_the_assertion_the_fixture_promises():
    with pytest.raises(AssertionError, match="reached for the network"):
        fetch("https://registry.npmjs.org/@nous-research/ui/-/ui-0.18.2.tgz")


def test_read_tree_strips_only_the_package_root():
    tree = read_tree(_tarball(UP_FROM), source="upstream 0.18.2")
    assert tree == UP_FROM
    assert package_name(tree, source="upstream 0.18.2") == "@nous-research/ui"


def test_read_tree_refuses_a_member_outside_the_package_root():
    with pytest.raises(TarballShapeError, match="outside the 'package/' root"):
        read_tree(_tarball({"README.md": b"hi"}, prefix="elsewhere/"), source="x")


def test_read_tree_refuses_a_traversing_member():
    with pytest.raises(TarballShapeError, match="not a safe path"):
        read_tree(_tarball({"../evil.txt": b"hi"}), source="x")


def test_read_tree_refuses_an_empty_tarball():
    with pytest.raises(TarballShapeError, match="contains no files"):
        read_tree(_tarball({}), source="x")


def test_package_json_has_to_name_the_package():
    with pytest.raises(TarballShapeError, match="no readable package.json"):
        package_name({"README.md": b"hi"}, source="x")


def test_a_tarball_from_the_wrong_side_is_a_stop_not_a_silent_swap():
    tree = read_tree(_tarball(FORK_FROM), source="upstream 0.19.1")
    with pytest.raises(TarballShapeError, match="asked for .* package.json says"):
        uipack._expect_name(tree, uipack.UPSTREAM_PACKAGE, "0.19.1", "upstream")


# -- --out discipline --------------------------------------------------------


def test_out_must_be_absent_or_exactly_empty(tmp_path):
    missing = tmp_path / "ui-0.19.1-publish"
    assert check_out_dir(str(missing)) == missing.resolve()
    filled = tmp_path / "filled"
    filled.mkdir()
    (filled / "stray.txt").write_bytes(b"x")
    with pytest.raises(OutputDirError, match="not empty"):
        check_out_dir(str(filled))
    a_file = tmp_path / "a-file"
    a_file.write_bytes(b"x")
    with pytest.raises(OutputDirError, match="not a directory"):
        check_out_dir(str(a_file))
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "nowhere")
    with pytest.raises(OutputDirError, match="dangling symlink"):
        check_out_dir(str(dangling))


# -- the command surface -----------------------------------------------------


def test_the_three_versions_are_required_and_the_map_flag_is_repeatable():
    parser = uipack.build_parser()
    assert required_flags(parser) == ("--from", "--to", "--out")
    with pytest.raises(SystemExit):
        parser.parse_args(["--from", "0.18.2"])
    args = parser.parse_args(
        ["--from", "0.18.2", "--to", "0.19.1", "--out", "/tmp/x", "--map", "a=b", "--map", "c=d"]
    )
    assert (args.from_version, args.to_version, args.map) == ("0.18.2", "0.19.1", ["a=b", "c=d"])
    assert args.dry_run is False


def test_a_decision_has_to_name_both_sides():
    assert parse_decisions(["nousresearch=nastechai"]) == {"nousresearch": "nastechai"}
    for broken in ("nousresearch", "=nastechai", "nousresearch="):
        with pytest.raises(UiPackError, match="is not span=replacement"):
            parse_decisions([broken])


def test_rebranding_the_same_version_twice_is_not_a_rebrand():
    with pytest.raises(UiPackError, match="both 0.18.2"):
        uipack.run(["--from", "0.18.2", "--to", "0.18.2", "--out", "/tmp/unused"])


def test_the_publish_command_names_the_scope_rule_and_no_digest():
    command = publish_command("/tmp/ui-0.19.1-publish", "@nastech-research/ui")
    assert command.startswith("npm publish --access public /tmp/ui-0.19.1-publish")
    assert "publishConfig" in command
    for absent in ("integrity", "sha512", "--dry-run", "--tag"):
        assert absent not in command, command


def test_run_writes_the_rebranded_tree_and_prints_the_manifest(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "ui-0.19.1-publish"
    _serve(
        monkeypatch,
        {
            tarball_url(uipack.UPSTREAM_PACKAGE, "0.18.2"): _tarball(UP_FROM),
            tarball_url(uipack.FORK_PACKAGE, "0.18.2"): _tarball(FORK_FROM),
            tarball_url(uipack.UPSTREAM_PACKAGE, "0.19.1"): _tarball(UP_TO),
        },
    )
    code = uipack.run(["--from", "0.18.2", "--to", "0.19.1", "--out", str(out_dir)])
    assert code == 0
    printed = capsys.readouterr().out
    assert "ui rebrand @nous-research/ui@0.18.2 -> @nastech-research/ui@0.19.1" in printed
    assert "6 span(s) rewritten at every occurrence" in printed, printed
    assert "1 path rename(s) reproduced from content rules" in printed, printed
    assert "rebranded paths (5)" in printed, printed
    assert "renamed paths (1)" in printed, printed
    assert "no integrity value written" in printed
    assert "npm publish --access public" in printed
    assert (out_dir / "package.json").read_bytes() == (
        b'{\n  "name": "@nastech-research/ui",\n  "version": "0.19.1"\n}\n'
    )
    assert (out_dir / "src" / "ui" / "nastech-girl.tsx").read_bytes() == (
        b'export const nastechGirl = "Nastech";\n'
    )
    assert not (out_dir / "src" / "ui" / "nous-girl.tsx").exists()
    assert (out_dir / "src" / "ui" / "plain.tsx").read_bytes() == PLAIN
    shipped = b"".join(path.read_bytes() for path in sorted(out_dir.rglob("*")) if path.is_file())
    published = b"".join(FORK_FROM[path] for path in sorted(FORK_FROM))
    # Spelling the brand away is what the transform owes, so a stem the human's own publish holds
    # nowhere may appear anywhere. `hermes` is different: the published fork tree still says it
    # once, at src/ui/plain.tsx, so `audit_stems` budgets exactly that -- a blanket ban here would
    # either contradict the budget or force the transform to rewrite a span the pair never maps.
    for brand in (b"nous", b"Nous", b"nousresearch"):
        assert brand not in shipped, brand
    assert shipped.count(b"hermes") + shipped.count(b"Hermes") <= (
        published.count(b"hermes") + published.count(b"Hermes")
    ), "the rebrand invented a survivor the fork's own publish does not carry"
    for absent in (b"integrity", b"sha512"):
        assert absent not in shipped, absent


def test_a_dry_run_derives_proves_and_writes_nothing(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "ui-0.19.1-publish"
    _serve(
        monkeypatch,
        {
            tarball_url(uipack.UPSTREAM_PACKAGE, "0.18.2"): _tarball(UP_FROM),
            tarball_url(uipack.FORK_PACKAGE, "0.18.2"): _tarball(FORK_FROM),
            tarball_url(uipack.UPSTREAM_PACKAGE, "0.19.1"): _tarball(UP_TO),
        },
    )
    argv = ["--from", "0.18.2", "--to", "0.19.1", "--out", str(out_dir), "--dry-run"]
    assert uipack.run(argv) == 0
    assert "DRY RUN: nothing written" in capsys.readouterr().out
    assert not out_dir.exists()


def test_a_blocker_means_exit_one_and_an_untouched_out_dir(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "ui-0.19.1-publish"
    to = dict(UP_TO)
    to["src/ui/blend-mode.test.tsx"] = NEW_TOKEN_FILE
    _serve(
        monkeypatch,
        {
            tarball_url(uipack.UPSTREAM_PACKAGE, "0.18.2"): _tarball(UP_FROM),
            tarball_url(uipack.FORK_PACKAGE, "0.18.2"): _tarball(FORK_FROM),
            tarball_url(uipack.UPSTREAM_PACKAGE, "0.19.1"): _tarball(to),
        },
    )
    assert uipack.run(["--from", "0.18.2", "--to", "0.19.1", "--out", str(out_dir)]) == 1
    printed = capsys.readouterr().out
    assert "BLOCKED: UnmappableBrandTokenError:" in printed
    assert f"1 blocker(s); 1 path(s) refused, {len(to) - 1} of {len(to)} produced" in printed
    assert list(out_dir.rglob("*")) == [], "a blocked run must leave the out dir untouched"


def test_main_turns_a_stop_into_exit_one_rather_than_a_traceback(tmp_path, monkeypatch, capsys):
    # --out is checked before anything is downloaded, so this needs no stubbed registry.
    (tmp_path / "a-file").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    assert uipack.main(["--from", "0.18.2", "--to", "0.19.1", "--out", "a-file"]) == 1
    assert "BLOCKED: OutputDirError:" in capsys.readouterr().out


def test_the_registry_advertises_the_uipack_ways_and_their_symbols_resolve():
    registry = build_registry()
    mine = {way.way_id: way for way in registry.all() if way.uses.startswith("uipack.")}
    assert len(mine) >= 3, "the derived-rebrand capability is not registered as ways"
    for way_id, way in mine.items():
        resolution = resolve_symbol(way.uses)
        assert resolution.kind != "dangling", f"{way_id} -> {way.uses}: {resolution.detail}"
