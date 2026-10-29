"""Rebrand a published ``ui`` tarball by *deriving* the transform, proving it,
or refusing to run.

The fork consumes ``@nastech-research/ui`` while upstream publishes
``@nous-research/ui``, and ``hundredways/updates.py`` stops the whole pipeline with
``UiScopePinError`` until a matching version exists on the fork side.  Exactly one
version has ever been published on both sides (0.18.2), so that pair is the only
place where the human's real rebranding decisions can be read rather than
guessed.  This module reads them:

* it downloads upstream's ``<to>`` tarball plus both ``<from>`` tarballs
  (upstream's and the fork's) from ``registry.npmjs.org``;
* it derives a transform -- path renames, byte-level token rewrites, and the
  residual literal edits that are not token rewrites at all -- from the ``<from>``
  pair alone, with no ``nous``/``hermes`` spelling hard-coded;
* it proves the derivation by running it over upstream's ``<from>`` bytes and
  requiring byte equality with the fork's published ``<from>`` bytes, for every
  path and for the path set itself.  One unexplained byte stops the tool.

Only then is the proven transform applied to ``<to>``, where the guards are: a path
the pair proved unchanged stays unchanged, a path the pair proved rewritten is
re-governed by the occurrence ledger it was recorded in, a file new in ``<to>`` may
only be touched by a rule the pair shows working at *every* one of its occurrences,
no byte may change without a rule firing for it, no upstream brand stem may survive
beyond the count the human's own published tree still holds at that path, and mapped
paths must stay unique.  A ``<to>`` brand token the derivation cannot map -- one never
observed, or one the human spelled two different ways -- is named with its path, byte
offset and conflicting evidence, and nothing is written.

What this module deliberately does *not* do:

* reuse :mod:`hundredways.rules` tokens.  The engine's brand table maps
  ``nousresearch`` to ``nastechairesearch``, while the published pair maps the same
  morpheme to ``nastechai`` in ``nastech.nastechai.io`` and to ``nastechairesearch``
  in ``nastech@nastechairesearch.com``.  A single global spelling for it does not
  exist, so none is assumed here;
* invent an integrity value.  ``dist.integrity`` is the registry's digest of a tarball
  it received, not something a local build can predict, so no integrity is written
  anywhere and ``hundredways/updates.py``'s ui lookup is untouched;
* publish, register or delete anything.  It prints the exact ``npm publish`` command
  and leaves running it to the human, who is the only one who can.

Run it with ``python3 -m hundredways.uipack --from <v> --to <v> --out <dir>``.
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import re
import tarfile
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

#: Registry identity: *which* two packages to download.  These names are inputs, not
#: part of the transform -- every spelling the rebrand uses is read out of the
#: ``<from>`` pair, so a future scope move needs new flags, not new code.
UPSTREAM_PACKAGE = "@nous-research/ui"
FORK_PACKAGE = "@nastech-research/ui"
REGISTRY = "https://registry.npmjs.org"
TARBALL_PREFIX = "package/"
#: Below this similarity a structural rename pairing is a guess, not evidence.
RENAME_RATIO = 0.6

#: A span is the longest run of identifier/path/URL characters, one whitespace run, or
#: one other byte.  Tokenising this way keeps ``nous-research/ui``, ``Hermes`` and
#: ``https://hermes.nousresearch.com/install.sh`` indivisible, so every rewrite is a
#: whole readable unit and never a fragment of one.
SPAN_RE = re.compile(rb"[A-Za-z0-9_][A-Za-z0-9_.+\-/@:]*|\s+|.", re.DOTALL)
#: Morphemes inside a span: acronyms, words, digits, separators.  Joining them back
#: reproduces the span byte for byte.
MORPH_RE = re.compile(rb"[A-Z]+(?![a-z])|[A-Za-z][a-z0-9]+|[A-Za-z][a-z]*|[0-9]+|.")
WORDS_RE = re.compile(rb"[A-Za-z]+")
#: Stems shorter than this sit too close to English to audit on their own; the per-path
#: budget read off the published tree is what keeps ``synchronous`` out of trouble,
#: which is the job :func:`hundredways.verify`'s english guard does for the tree.
MIN_STEM_LEN = 4

Tree = Mapping[str, bytes]


class UiPackError(ValueError):
    """Base for every stop this tool makes; each subclass names its evidence."""


class UiTarballFetchError(UiPackError):
    """A tarball could not be fetched, or is not a gzip tarball.

    npm answers an unpublished version with a short JSON body (measured: the 21-byte
    ``{"error":"Not found"}`` returned for ``@nous-research/ui`` 0.18.4, which only the
    fork published), so the payload is checked before :mod:`tarfile` sees it.  That is
    also how a ``<from>`` version published on one side only -- no pair, nothing to
    derive -- reaches the human as a decision instead of a mysterious crash.
    """


class TarballShapeError(UiPackError):
    """A tarball is not the flat ``package/`` tree npm publishes.

    The reader strips one ``package/`` prefix and refuses absolute or ``..`` paths:
    bytes nobody proved anything about must not land in ``--out``.
    """


class TransformProofError(UiPackError):
    """The derived transform did not reproduce the fork's ``<from>`` bytes.

    The transform is a claim about how the human rebranded and the published pair is
    the evidence for it, so it is checked before ``<to>`` is touched at all.  The
    message names the path, the first differing byte offset, both sides' windows and
    their lengths.
    """


class PathSetError(UiPackError):
    """The rename pairing or the mapped path set is not evidence.

    Upstream-only and fork-only paths are paired only when one pairing is possible at
    all (same depth, same extension, no competing candidate), and each pairing is then
    required to be reproducible by the rules derived from *content*.  A path that maps
    nowhere, or a fork path nothing maps to, means the rename story is incomplete, and
    publishing a tree whose layout is only believed would be worse than publishing
    nothing.
    """


class PatchAnchorError(UiPackError):
    """A residual literal edit could not be anchored exactly once.

    Residual edits are the ``<from>`` differences that are not token rewrites: in the
    published pair the fork deletes upstream's ``scripts`` block from ``package.json``
    and drops two release links, one word and the trailing newline from
    ``CHANGELOG.md``.  Each is stored as a window widened until it was unique in
    upstream's ``<from>`` bytes, and applying it to ``<to>`` requires that window there
    exactly once.  Zero hits means upstream rewrote the context, more than one means
    the window is not unique -- both are decisions to stop on, not to approximate.
    """


class UnmappableBrandTokenError(UiPackError):
    """A ``<to>`` brand token is outside everything the pair proves.

    Three shapes, all measured on the published pair:

    * the span was seen but the human rewrote it more than one way.
      ``nous-research/ui`` became ``nastech/ui`` in ``README.md``,
      ``dist/ui/header.d.ts`` and ``src/ui/header.tsx``, and ``nastech-research/ui`` in
      ``CHANGELOG.md`` and ``package.json`` -- and the characters on both sides are
      identical (``@`` before, quote or backtick after), so no context rule separates
      them and only the recorded path does.  A file new in ``<to>`` carrying that span
      has no recorded path;
    * the span was never seen and a brand morpheme inside it has more than one observed
      spelling: ``nousresearch`` became ``nastechai`` in the installer host and
      ``nastechairesearch`` in the mail address, so the spelling of
      ``portal.nousresearch.com`` -- upstream 0.19.1's new
      ``src/ui/components/blend-mode.test.tsx`` -- is a human decision;
    * a path component carries a brand stem the pair never renamed anywhere.

    Either way: name the path, offset and span, quote the conflicting evidence, write
    nothing.  ``--map`` is how the human records the decision.
    """


class BrandStemSurvivorError(UiPackError):
    """An upstream brand stem survived in the output past its budget.

    Stems are read off the observed rewrites (morphemes appearing only on the upstream
    side of a rewrite); the budget is what the human's own published ``<from>`` tree
    still holds at the mapped path, so preserved English is allowed exactly as much as
    the human allowed it and a new path is allowed nothing.  This audit never reuses the
    token pass -- it is the independent answer to "did any upstream brand token
    survive", and it is what stops a path the pair proved unchanged from quietly
    absorbing a new brand token in ``<to>``.
    """


class IdentityViolationError(UiPackError):
    """A path changed in the output with no rule firing for it.

    Every byte written must trace back to a derived rule; a change nobody can attribute
    is a bug in the derivation, not a detail to tolerate.
    """


class OutputDirError(UiPackError):
    """``--out`` is not an absent or empty directory.

    The output must be exactly the rebranded package root, because ``npm publish`` adds
    every file it finds there: a stray file would ship inside the published tarball.
    """


@dataclass(frozen=True)
class Token:
    value: bytes
    offset: int


@dataclass(frozen=True)
class Patch:
    """One residual literal edit, anchored by a window unique in ``<from>``."""

    path: str
    old: bytes
    new: bytes
    from_offset: int

    def evidence(self) -> str:
        return (
            f"{self.path}: {self.old[:48]!r} -> {self.new[:48]!r} "
            f"(window unique at offset {self.from_offset} of the <from> file)"
        )


@dataclass(frozen=True)
class Firing:
    """A rule that changed bytes at a path, kept so every change is attributed."""

    path: str
    kind: str
    old: bytes
    new: bytes


@dataclass
class Transform:
    """Everything the ``<from>`` pair proves, and nothing more.

    Rules are tiered by how much of the pair supports them:

    :attr:`spans`
        a span the human rewrote at *every* occurrence in the whole pair, one way only,
        so it is safe everywhere including in files that do not exist yet;
    :attr:`ledgers`
        a span whose occurrences at one path are recorded one by one, identity included,
        because the pair shows it rewritten another way (or not at all) elsewhere;
    :attr:`morphemes`
        a single morpheme that every whole-span rewrite aligning it agreed on, used only
        to compose a span the pair never showed;
    :attr:`patches`
        literal edits that are not token rewrites at all.
    """

    spans: dict[bytes, bytes] = field(default_factory=dict)
    ledgers: dict[tuple[str, bytes], list[bytes]] = field(default_factory=dict)
    ambiguous: dict[bytes, dict[bytes, list[str]]] = field(default_factory=dict)
    morphemes: dict[bytes, bytes] = field(default_factory=dict)
    morpheme_evidence: dict[bytes, dict[bytes, int]] = field(default_factory=dict)
    patches: dict[str, list[Patch]] = field(default_factory=dict)
    renames: dict[str, str] = field(default_factory=dict)
    path_components: dict[bytes, bytes] = field(default_factory=dict)
    stems: set[bytes] = field(default_factory=set)
    tolerations: dict[str, Counter] = field(default_factory=dict)
    changed_paths: set[str] = field(default_factory=set)
    from_paths: set[str] = field(default_factory=set)
    common_paths: set[str] = field(default_factory=set)
    span_observations: dict[bytes, Counter] = field(default_factory=dict)
    human_decisions: dict[bytes, bytes] = field(default_factory=dict)

    # -- derivation ---------------------------------------------------------

    @classmethod
    def derive(
        cls, up: Tree, fork: Tree, decisions: Mapping[str, str] | None = None
    ) -> "Transform":
        """Read the transform out of the published ``<from>`` pair and prove it.

        ``decisions`` are human spellings for tokens the pair cannot decide.  They are
        applied only to ``<to>``; the proof below runs without them, so a decision can
        never be used to make the derivation prove itself.
        """
        tf = cls()
        tf.from_paths = set(up)
        tf.common_paths = set(up) & set(fork)
        tf.human_decisions = {k.encode(): v.encode() for k, v in sorted((decisions or {}).items())}
        pairs = {path: path for path in sorted(tf.common_paths)}
        pairs.update(_pair_renames(sorted(set(up) - set(fork)), sorted(set(fork) - set(up))))
        tf._derive_patches(pairs, up, fork)
        # Patches fire first, so the token layer is observed on post-patch bytes and the
        # two layers can never claim one region twice.
        mid = {path: tf._apply_patches(path, up[path]) for path in pairs}
        tf._derive_tokens(pairs, mid, fork)
        tf._derive_morphemes()
        tf._derive_stems(fork)
        tf._derive_renames(pairs, mid, fork)
        tf._assert_proves(pairs, up, fork)
        return tf

    def _derive_patches(self, pairs: Mapping[str, str], up: Tree, fork: Tree) -> None:
        for path, target in pairs.items():
            if up[path] == fork[target]:
                continue
            a, b = _tokens(up[path]), _tokens(fork[target])
            for tag, i1, i2, j1, j2 in _opcodes([t.value for t in a], [t.value for t in b]):
                if tag == "equal":
                    continue
                if (i2 - i1, j2 - j1) == (1, 1) and not a[i1].value.isspace():
                    continue  # a whole readable unit rewritten: the token layer's job
                old, new = _widen(up[path], a, b, i1, i2, j1, j2, path)
                self.patches.setdefault(path, []).append(Patch(path, old, new, up[path].find(old)))

    def _derive_tokens(self, pairs: Mapping[str, str], mid: Tree, fork: Tree) -> None:
        """Record every occurrence the pair rewrites, path by path."""
        evidence: dict[tuple[bytes, bytes], set[str]] = defaultdict(set)
        rewrites: dict[tuple[str, bytes], dict[int, bytes]] = defaultdict(dict)
        for path, target in pairs.items():
            data = mid[path]
            if data == fork[target]:
                if path in self.patches:
                    self.changed_paths.add(path)
                continue
            self.changed_paths.add(path)
            a, b = _tokens(data), _tokens(fork[target])
            values = [t.value for t in a]
            for tag, i1, i2, j1, j2 in _opcodes(values, [t.value for t in b]):
                if tag == "equal":
                    continue
                old = b"".join(values[i1:i2])
                new = b"".join(t.value for t in b[j1:j2])
                if (i2 - i1, j2 - j1) != (1, 1) or old.isspace():
                    raise TransformProofError(
                        f"{path}: after the residual edits this pair still differs by a "
                        f"multi-token run {old[:60]!r} -> {new[:60]!r} at span {i1}, which is "
                        f"neither a whole-span rewrite nor anchorable as a literal edit"
                    )
                ordinal = values[:i1].count(old)
                known = rewrites[(path, old)].get(ordinal)
                if known is not None and known != new:
                    raise TransformProofError(
                        f"{path}: occurrence #{ordinal + 1} of {_show(old)!r} maps two ways, "
                        f"{_show(known)!r} and {_show(new)!r}"
                    )
                rewrites[(path, old)][ordinal] = new
                self.span_observations.setdefault(old, Counter())[new] += 1
                evidence[(old, new)].add(path)
        counts = _occurrence_counts(mid, set(self.span_observations))
        for old, outcomes in self.span_observations.items():
            rewritten = sum(outcomes.values())
            everywhere = sum(count for (path, span), count in counts.items() if span == old)
            if len(outcomes) == 1 and rewritten >= everywhere:
                # every single occurrence in the whole pair became this, so the rule is
                # the evidence itself and can be trusted on files that do not exist yet
                self.spans[old] = next(iter(outcomes))
                continue
            if len(outcomes) > 1:
                self.ambiguous[old] = {
                    new: sorted(paths) for (span, new), paths in evidence.items() if span == old
                }
            for path in pairs:
                total = counts[(path, old)]
                if not total:
                    continue
                ledger = [old] * total
                for ordinal, new in rewrites.get((path, old), {}).items():
                    if ordinal >= total:
                        raise TransformProofError(
                            f"{path}: occurrence #{ordinal + 1} of {_show(old)!r} is past the "
                            f"{total} the file holds"
                        )
                    ledger[ordinal] = new
                self.ledgers[(path, old)] = ledger

    def _derive_morphemes(self) -> None:
        """Morpheme rules from consistent whole-span rewrites only.

        A morpheme composes only when a rewrite aligned it one-to-one with the same
        replacement.  Anything with two spellings is refused by :meth:`_compose` rather
        than guessed, which is exactly the state ``nousresearch`` is in.
        """
        pairs: dict[bytes, Counter] = defaultdict(Counter)
        for old, outcomes in self.span_observations.items():
            if len(outcomes) != 1:
                continue
            new = next(iter(outcomes))
            left, right = MORPH_RE.findall(old), MORPH_RE.findall(new)
            if len(left) != len(right):
                continue
            for x, y in zip(left, right):
                if x != y:
                    pairs[x][y] += 1
        self.morpheme_evidence = {x: dict(y) for x, y in pairs.items()}
        self.morphemes = {x: next(iter(y)) for x, y in pairs.items() if len(y) == 1}

    def _derive_stems(self, fork: Tree) -> None:
        self.stems = self._brand_morphemes()
        # The budget is the human's own tolerance: whatever the published fork tree still
        # says at a path is allowed there, no more and no less.  Fork paths are already
        # output names, so they are keyed as found.
        self.tolerations = {path: _stem_counts(fork[path], self.stems) for path in sorted(fork)}

    def _brand_morphemes(self) -> set[bytes]:
        """Morphemes that only ever appear on the upstream side of a rewrite."""
        left: set[bytes] = set()
        right: set[bytes] = set()
        for old, outcomes in self.span_observations.items():
            left |= {m.lower() for m in MORPH_RE.findall(old) if m.isalpha()}
            for new in outcomes:
                right |= {m.lower() for m in MORPH_RE.findall(new) if m.isalpha()}
        return {m for m in left - right if len(m) >= MIN_STEM_LEN}

    def _derive_renames(self, pairs: Mapping[str, str], mid: Tree, fork: Tree) -> None:
        """Require every structural pairing to be reproducible by the derived rules."""
        for path, target in sorted(pairs.items()):
            if path == target:
                continue
            derived = self.map_path(path, proven=True)
            if derived != target:
                raise PathSetError(
                    f"{path} is paired with {target} by structure, but the rules derived from "
                    f"content map its name to {derived}; a rename nobody can spell is not a "
                    f"rename"
                )
            self.renames[path] = target
            left, right = path.split("/"), target.split("/")
            if len(left) != len(right):
                continue
            for x, y in zip(left, right):
                if x == y:
                    continue
                current = self.path_components.get(x.encode())
                if current is not None and current != y.encode():
                    raise PathSetError(
                        f"rename evidence gives component {x!r} two targets, "
                        f"{_show(current)} and {y}"
                    )
                self.path_components[x.encode()] = y.encode()
        if len(self.renames) != sum(1 for p, t in pairs.items() if p != t):
            raise PathSetError("a structural pairing failed to become a rename rule")

    # -- proof --------------------------------------------------------------

    def _assert_proves(self, pairs: Mapping[str, str], up: Tree, fork: Tree) -> None:
        """Run the derived transform over upstream's ``<from>`` bytes, decisions aside."""
        blockers: list[str] = []
        for path, target in sorted(pairs.items()):
            try:
                got, _ = self.apply_path(path, up[path], proven=True)
            except UiPackError as exc:
                blockers.append(f"{path}: {exc}")
                continue
            if got != fork[target]:
                blockers.append(_diff_report(path, target, got, fork[target]))
        if blockers:
            raise TransformProofError(
                f"the transform derived from the <from> pair failed to reproduce the fork's "
                f"published bytes on {len(blockers)} of {len(pairs)} path(s): "
                + " | ".join(blockers[:3])
                + (f" | (+{len(blockers) - 3} more)" if len(blockers) > 3 else "")
            )

    # -- application --------------------------------------------------------

    def map_path(self, path: str, *, proven: bool = False) -> str:
        """Where this path goes, using derived rename evidence only.

        A whole path the pair recorded keeps that exact recorded name; otherwise each
        component is spelled by a component rule recorded from a validated rename, or by
        composing the morpheme rules the content rewrites agreed on.  A component that
        carries a brand stem and is not the name the pair published is where the tool
        stops: a rename nobody can spell is not a rename.
        """
        if path in self.renames:
            return self.renames[path]
        parts = []
        for part in path.split("/"):
            raw = part.encode()
            if raw in self.path_components:
                parts.append(_show(self.path_components[raw]))
                continue
            if not self._brand_bearing(raw):
                parts.append(part)
                continue
            if path in self.common_paths:
                # the pair published this name unchanged, which is evidence, not a gap
                parts.append(part)
                continue
            if not proven and raw in self.human_decisions:
                parts.append(_show(self.human_decisions[raw]))
                continue
            composed = self._compose(raw, f"path {path}", 0)
            if composed == raw:
                raise UnmappableBrandTokenError(
                    f"path {path}: component {part!r} carries brand stem "
                    f"{_quoted(self._brand_morphs(raw))} and the <from> pair renamed nothing "
                    f"like it, so the published name is a human decision (--map)"
                )
            parts.append(_show(composed))
        return "/".join(parts)

    def apply_path(
        self,
        path: str,
        data: bytes,
        *,
        proven: bool = False,
        collect: list["UiPackError"] | None = None,
    ) -> tuple[bytes, list[Firing]]:
        """Run one file through the proven transform: residual edits, then tokens.

        ``proven=True`` is the check pass over the ``<from>`` pair, where the recorded
        evidence is complete by construction and a human decision must not paper over a
        gap in it.  ``collect`` gathers every unmappable token in the file instead of
        stopping at the first, so one run names every decision missing rather than
        making the human rediscover them one edit at a time; the caller must discard the
        bytes whenever it is non-empty.
        """
        firings: list[Firing] = []
        out = self._apply_patches(path, data, firings)
        counts: Counter = Counter()
        pieces: list[bytes] = []
        for token in _tokens(out):
            counts[token.value] += 1
            try:
                replaced = self._replacement(
                    path, token.value, counts[token.value], token.offset, proven=proven
                )
            except UnmappableBrandTokenError as exc:
                if collect is None:
                    raise
                collect.append(exc)
                replaced = token.value
            if replaced != token.value:
                firings.append(Firing(path, "span", token.value, replaced))
            pieces.append(replaced)
        return b"".join(pieces), firings

    def _replacement(
        self, path: str, value: bytes, nth: int, offset: int, *, proven: bool
    ) -> bytes:
        if not value or value.isspace():
            return value
        if path in self.changed_paths:
            ledger = self.ledgers.get((path, value))
            if ledger is not None:
                if nth > len(ledger):
                    raise UnmappableBrandTokenError(
                        f"{path} (offset {offset}): occurrence #{nth} of {_show(value)!r} is "
                        f"past the {len(ledger)} the <from> pair recorded here"
                        + self._ambiguity(value)
                    )
                return ledger[nth - 1]
            if value in self.spans:
                return self.spans[value]
            if value in self.ambiguous:
                raise self._ambiguous_error(path, value, nth, offset)
            return self._compose(value, path, offset)
        if path in self.from_paths:
            # the pair proves this file needed no rewrite at all; a brand token appearing
            # in it now is a leak to stop on, not a rule to invent
            if not proven and value in self.human_decisions:
                return self.human_decisions[value]
            return value
        if not proven and value in self.human_decisions:
            return self.human_decisions[value]
        if value in self.spans:
            return self.spans[value]
        if value in self.ambiguous:
            raise self._ambiguous_error(path, value, nth, offset)
        return self._compose(value, path, offset)

    def _ambiguous_error(self, path: str, value: bytes, nth: int, offset: int) -> Exception:
        return UnmappableBrandTokenError(
            f"{path} (offset {offset}): span {_show(value)!r} occurs #{nth} here, a site the "
            f"<from> pair never recorded, and it was rewritten "
            f"{len(self.ambiguous[value])} different ways there --" + self._ambiguity(value)
        )

    def _ambiguity(self, value: bytes) -> str:
        if value not in self.ambiguous:
            return ""
        return " " + "; ".join(
            f"{_show(new)} at {', '.join(paths)}"
            for new, paths in sorted(self.ambiguous[value].items(), key=lambda kv: kv[0])
        )

    def _compose(self, value: bytes, where: str, offset: int) -> bytes:
        """Map a span the pair never showed, morpheme by morpheme, or refuse."""
        morphs = self._brand_morphs(value)
        if not morphs:
            return value
        pieces = []
        for morph in MORPH_RE.findall(value):
            if morph.lower() not in self.stems:
                pieces.append(morph)
                continue
            if morph in self.morphemes:
                pieces.append(self.morphemes[morph])
                continue
            options = self.morpheme_evidence.get(morph, {})
            raise UnmappableBrandTokenError(
                f"{where} (offset {offset}): span {_show(value)!r} is not in the <from> pair "
                f"and its brand morpheme {_show(morph)!r} has no single derived spelling "
                f"(what the pair shows: {_evidence(options)}); record the decision with "
                f"--map {_show(value)}=<chosen spelling>"
            )
        return b"".join(pieces)

    def _brand_bearing(self, value: bytes) -> bool:
        return bool(self._brand_morphs(value))

    def _brand_morphs(self, value: bytes) -> set[bytes]:
        return {m.lower() for m in MORPH_RE.findall(value) if m.lower() in self.stems}

    def _apply_patches(self, path: str, data: bytes, firings: list[Firing] | None = None) -> bytes:
        out = data
        for patch in self.patches.get(path, ()):
            hits = out.count(patch.old)
            if hits != 1:
                raise PatchAnchorError(
                    f"{path}: residual edit expected exactly once, found {hits} -- "
                    f"{patch.evidence()}"
                )
            out = out.replace(patch.old, patch.new, 1)
            if firings is not None:
                firings.append(Firing(path, "patch", patch.old, patch.new))
        return out

    # -- reporting ----------------------------------------------------------

    def describe(self) -> list[str]:
        return [
            f"{len(self.spans)} span(s) rewritten at every occurrence in the pair, so they "
            f"are trusted on files that do not exist yet",
            f"{len(self.ledgers)} path-scoped occurrence ledger(s), covering "
            f"{len(self.ambiguous)} span(s) the pair rewrote two ways{_describe(self.ambiguous)}",
            f"{len(self.morphemes)} single-morpheme rule(s), "
            f"{sum(1 for v in self.morpheme_evidence.values() if len(v) > 1)} refused as "
            f"multi-spelled",
            f"{sum(len(v) for v in self.patches.values())} residual literal edit(s) across "
            f"{len(self.patches)} path(s)",
            f"{len(self.renames)} path rename(s) reproduced from content rules",
            f"{len(self.changed_paths)} path(s) the pair proves were touched, "
            f"{len(self.from_paths) - len(self.changed_paths)} proven untouched",
            f"brand stems read off the pair: {_quoted(self.stems)}",
        ]


def _describe(ambiguous: Mapping[bytes, Mapping[bytes, object]]) -> str:
    if not ambiguous:
        return ""
    return (
        " ("
        + ", ".join(
            f"{_show(old)} -> {' | '.join(_show(new) for new in sorted(spellings))}"
            for old, spellings in sorted(ambiguous.items())
        )
        + ")"
    )


def _pair_renames(up_only: list[str], fork_only: list[str]) -> dict[str, str]:
    """Pair renamed paths only where exactly one pairing is possible."""
    if len(up_only) != len(fork_only):
        raise PathSetError(
            f"the pair has {len(up_only)} upstream-only path(s) and {len(fork_only)} "
            f"fork-only path(s), so the rename set is not closed: {up_only} vs {fork_only}"
        )
    pairs: dict[str, str] = {}
    taken: set[str] = set()
    for path in up_only:
        candidates = [
            other
            for other in fork_only
            if other not in taken
            and len(other.split("/")) == len(path.split("/"))
            and Path(other).suffix == Path(path).suffix
            and difflib.SequenceMatcher(None, path, other).ratio() >= RENAME_RATIO
        ]
        if len(candidates) != 1:
            raise PathSetError(
                f"{path} has {len(candidates)} fork-side candidate(s) {candidates}; pairing "
                f"them by anything else would be a guess about the rename set"
            )
        best = candidates[0]
        rival = [
            other
            for other in up_only
            if other != path
            and difflib.SequenceMatcher(None, other, best).ratio() >= RENAME_RATIO
            and len(other.split("/")) == len(best.split("/"))
            and Path(other).suffix == Path(best).suffix
        ]
        if len(rival) > 1:
            raise PathSetError(
                f"{best} is a plausible rename of {len(rival) + 1} upstream paths "
                f"({[path, *rival]}), so the rename set is not evidence"
            )
        pairs[path] = best
        taken.add(best)
    return pairs


def _opcodes(left: list[bytes], right: list[bytes]):
    return difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes()


def _tokens(data: bytes) -> list[Token]:
    return [Token(match.group(0), match.start()) for match in SPAN_RE.finditer(data)]


def _occurrence_counts(tree: Tree, spans: set[bytes]) -> Counter:
    """How many times each span of interest occurs, per path."""
    counts: Counter = Counter()
    for path, data in tree.items():
        for token in _tokens(data):
            if token.value in spans:
                counts[(path, token.value)] += 1
    return counts


def _stem_counts(data: bytes, stems: set[bytes]) -> Counter:
    found: Counter = Counter()
    for match in WORDS_RE.finditer(data):
        low = match.group(0).lower()
        if low in stems:
            found[low] += 1
    return found


def _show(value: bytes) -> str:
    return value.decode(errors="replace")


def _quoted(values) -> str:
    return ", ".join(sorted(_show(value) for value in values)) or "-"


def _evidence(options: Mapping[bytes, int]) -> str:
    if not options:
        return "never observed"
    return ", ".join(f"{_show(k)} {v}x" for k, v in sorted(options.items(), key=lambda kv: kv[0]))


def _widen(
    up_data: bytes, a: list[Token], b: list[Token], i1: int, i2: int, j1: int, j2: int, path: str
) -> tuple[bytes, bytes]:
    """Grow a residual run until its old side is unique inside the ``<from>`` file."""
    for pad in range(0, len(a) + 2):
        for la, ra, lb, rb in _widening(i1, i2, j1, j2, len(a), len(b), pad):
            old = b"".join(token.value for token in a[la:ra])
            new = b"".join(token.value for token in b[lb:rb])
            if old and up_data.count(old) == 1:
                return old, new
    raise PatchAnchorError(
        f"{path}: residual edit "
        f"{b''.join(token.value for token in a[i1:i2])[:60]!r} could not be widened to a "
        f"window unique in the <from> file ({len(up_data)}B)"
    )


def _widening(i1: int, i2: int, j1: int, j2: int, na: int, nb: int, pad: int):
    """Windows of ``pad`` extra tokens, taken from both sides of the run.

    The two token lists differ only inside the run, so the same amount is mirrored on
    each side; an unmirrored window would not be an edit of the published pair.
    """
    for left in range(min(pad, i1, j1) + 1):
        right = pad - left
        if right > na - i2 or right > nb - j2:
            continue
        yield i1 - left, i2 + right, j1 - left, j2 + right


def _diff_report(path: str, target: str, got: bytes, want: bytes) -> str:
    at = next((i for i, (x, y) in enumerate(zip(got, want)) if x != y), min(len(got), len(want)))
    lo = max(0, at - 40)
    return (
        f"{path} -> {target}: derived bytes differ at offset {at} (derived {len(got)}B, "
        f"published {len(want)}B) derived={got[lo : at + 40]!r} published={want[lo : at + 40]!r}"
    )


def tarball_url(package: str, version: str) -> str:
    """npm's own tarball form, built from the package name rather than listed."""
    unscoped = package.split("/", 1)[1]
    return f"{REGISTRY}/{package}/-/{unscoped}-{version}.tgz"


def fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise UiTarballFetchError(f"{url}: {exc}") from exc
    if not payload.startswith(b"\x1f\x8b"):
        head = _show(payload[:80])
        raise UiTarballFetchError(
            f"{url}: expected a gzip tarball, got {len(payload)}B starting {head!r}; npm "
            f"answers an unpublished version this way"
        )
    return payload


def read_tree(blob: bytes, *, source: str) -> dict[str, bytes]:
    tree: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            if not member.name.startswith(TARBALL_PREFIX):
                raise TarballShapeError(
                    f"{source}: member {member.name!r} is outside the {TARBALL_PREFIX!r} root "
                    f"npm publishes"
                )
            relative = member.name[len(TARBALL_PREFIX) :]
            if relative.startswith("/") or ".." in relative.split("/"):
                raise TarballShapeError(f"{source}: member {member.name!r} is not a safe path")
            handle = tar.extractfile(member)
            if handle is None:
                raise TarballShapeError(f"{source}: member {member.name!r} has no content")
            tree[relative] = handle.read()
    if not tree:
        raise TarballShapeError(f"{source}: tarball contains no files")
    return tree


def write_tree(blob: bytes, out_dir: str) -> Path:
    """Write one already-proven tarball under ``--out`` and nowhere else."""
    root = check_out_dir(out_dir)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            target = root / member.name[len(TARBALL_PREFIX) :]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read())
    return root


def check_out_dir(out_dir: str) -> Path:
    root = Path(out_dir).expanduser()
    if root.is_symlink() and not root.exists():
        raise OutputDirError(f"{root} is a dangling symlink; refusing to write through it")
    root = root.resolve()
    if root.exists():
        if not root.is_dir():
            raise OutputDirError(f"{root} exists and is not a directory")
        if any(root.iterdir()):
            raise OutputDirError(
                f"{root} is not empty: npm publish adds every file it finds in the package "
                f"root, so the output must be exactly that tree"
            )
    return root


def package_name(tree: Tree, *, source: str) -> str:
    try:
        manifest = json.loads(tree["package.json"].decode())
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TarballShapeError(f"{source}: no readable package.json ({exc})") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("name"), str):
        raise TarballShapeError(f"{source}: package.json has no string name")
    return manifest["name"]


def build_output(
    transform: Transform, to: Tree
) -> tuple[dict[str, bytes], dict[str, object], list[UiPackError]]:
    """Apply the proven transform to ``<to>`` and audit every byte of the result."""
    out: dict[str, bytes] = {}
    mapping: dict[str, str] = {}
    blockers: list[UiPackError] = []
    for path in sorted(to):
        per_file: list[UiPackError] = []
        try:
            target = transform.map_path(path)
            data, firings = transform.apply_path(path, to[path], collect=per_file)
        except UiPackError as exc:
            blockers.append(exc)
            continue
        blockers.extend(per_file)
        if per_file:
            # the bytes were produced by leaving unmappable tokens alone; they are not a
            # candidate, and naming every blocker in one run beats one per run
            continue
        if target in out:
            blockers.append(
                PathSetError(
                    f"<to> paths {path!r} and {mapping[target]!r} both map to {target!r}, "
                    f"which would drop a file"
                )
            )
            continue
        if data != to[path] and not firings:
            blockers.append(
                IdentityViolationError(
                    f"{target}: bytes changed with no derived rule firing for them, so the "
                    f"change cannot be attributed"
                )
            )
            continue
        out[target] = data
        mapping[path] = target
    blockers.extend(audit_stems(transform, out))
    return out, _stats(transform, out, mapping, to), blockers


def _stats(
    transform: Transform, out: Tree, mapping: Mapping[str, str], to: Tree
) -> dict[str, object]:
    changed = {target for source, target in mapping.items() if out[target] != to[source]}
    renamed = {target for source, target in mapping.items() if target != source}
    carried = changed | renamed
    return {
        "paths": len(out),
        "total_bytes": sum(len(data) for data in out.values()),
        "rebranded": sorted(changed),
        "renamed": sorted(renamed),
        "identical": sorted(target for target in out if target not in carried),
        "outside_the_pair": sorted(
            target
            for source, target in mapping.items()
            if target in carried
            and source not in transform.changed_paths
            and source not in transform.renames
        ),
        "new_paths": sorted(p for p in to if p not in transform.from_paths),
    }


def audit_stems(transform: Transform, out: Tree) -> list[BrandStemSurvivorError]:
    """Independent answer to "did any upstream brand token survive"."""
    found: list[BrandStemSurvivorError] = []
    for target in sorted(out):
        budget = transform.tolerations.get(target, Counter())
        for stem, count in sorted(_stem_counts(out[target], transform.stems).items()):
            allowed = budget.get(stem, 0)
            if count <= allowed:
                continue
            offsets = [
                m.start() for m in WORDS_RE.finditer(out[target]) if m.group(0).lower() == stem
            ]
            found.append(
                BrandStemSurvivorError(
                    f"{target}: brand stem {_show(stem)!r} survives {count} time(s) where the "
                    f"fork's published <from> tree still holds {allowed} at this path; "
                    f"offsets {offsets[:8]}"
                )
            )
    return found


def render_manifest(
    packages: tuple[str, str],
    versions: tuple[str, str],
    transform: Transform,
    stats: Mapping[str, object],
) -> str:
    source, target = packages
    from_version, to_version = versions
    lines = [
        f"ui rebrand {source}@{from_version} -> {target}@{to_version}",
        f"transform derived from the published {from_version} pair, proved byte-exact on it:",
    ]
    lines.extend(f"  {line}" for line in transform.describe())
    if transform.human_decisions:
        lines.append(
            "  human decisions supplied with --map, not derived: "
            + ", ".join(
                f"{_show(old)}={_show(new)}"
                for old, new in sorted(transform.human_decisions.items())
            )
        )
    lines.append(
        f"applied to {to_version}: {stats['paths']} path(s), {len(stats['rebranded'])} "
        f"rebranded, {len(stats['renamed'])} renamed, {len(stats['identical'])} "
        f"byte-identical, {stats['total_bytes']}B total"
    )
    lines.append(f"rebranded paths ({len(stats['rebranded'])}):")
    lines.extend(f"  {path}" for path in stats["rebranded"])
    lines.append(f"renamed paths ({len(stats['renamed'])}):")
    lines.extend(f"  {path}" for path in stats["renamed"])
    for label, key in (
        ("paths no <from> path changed at", "outside_the_pair"),
        (f"paths new in {to_version}", "new_paths"),
    ):
        if stats[key]:
            lines.append(f"{label} ({len(stats[key])}):")
            lines.extend(f"  {path}" for path in stats[key])
    return "\n".join(lines)


def publish_command(out_dir: str, package: str) -> str:
    return (
        f"npm publish --access public {Path(out_dir).expanduser().resolve()}"
        f"  # scoped packages default to private and {package}'s package.json carries no "
        f"publishConfig, so --access public is required, not a preference"
    )


def parse_decisions(values: list[str]) -> dict[str, str]:
    """Turn ``--map span=replacement`` into the human's recorded choices."""
    decisions: dict[str, str] = {}
    for value in values:
        old, sep, new = value.partition("=")
        if not sep or not old or not new:
            raise UiPackError(
                f"--map {value!r} is not span=replacement; a decision must name the exact "
                f"span and the exact spelling to use for it"
            )
        decisions[old] = new
    return decisions


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.from_version == args.to_version:
        raise UiPackError(
            f"--from and --to are both {args.from_version}; a rebrand needs the version "
            f"published on both sides to derive from and a different one to apply it to"
        )
    root = check_out_dir(args.out)
    decisions = parse_decisions(args.map)
    from_up = read_tree(
        fetch(tarball_url(args.upstream_package, args.from_version)),
        source=f"upstream {args.from_version}",
    )
    _expect_name(from_up, args.upstream_package, args.from_version, "upstream")
    from_fork = read_tree(
        fetch(tarball_url(args.fork_package, args.from_version)), source=f"fork {args.from_version}"
    )
    _expect_name(from_fork, args.fork_package, args.from_version, "fork")
    to_up = read_tree(
        fetch(tarball_url(args.upstream_package, args.to_version)),
        source=f"upstream {args.to_version}",
    )
    transform = Transform.derive(from_up, from_fork, decisions)
    out, stats, blockers = build_output(transform, to_up)
    print(
        render_manifest(
            (args.upstream_package, args.fork_package),
            (args.from_version, args.to_version),
            transform,
            stats,
        )
    )
    for blocker in blockers:
        print(f"BLOCKED: {blocker.__class__.__name__}: {blocker}")
    if blockers:
        # a refused path is absent from ``out``, so the manifest's path count has to say
        # how far the produced tree is from the one the tarball actually holds
        print(
            f"{len(blockers)} blocker(s); {len(to_up) - len(out)} path(s) refused, "
            f"{len(out)} of {len(to_up)} produced; nothing was written to {root}, because a "
            f"tree with an unbranded or unattributed byte is worse than no tree"
        )
        return 1
    print(publish_command(args.out, args.fork_package))
    if args.dry_run:
        print(f"DRY RUN: nothing written to {root}")
        return 0
    written = write_tree(_repack(out), args.out)
    print(f"wrote {stats['paths']} file(s) under {written}; no integrity value written")
    return 0


def _expect_name(tree: Tree, expected: str, version: str, side: str) -> None:
    actual = package_name(tree, source=f"{side} {version}")
    if actual != expected:
        raise TarballShapeError(
            f"{side} {version}: asked for {expected!r}, package.json says {actual!r}"
        )


def _repack(tree: Mapping[str, bytes]) -> bytes:
    """Rebuild a deterministic tarball in memory so one code path writes files.

    ``npm publish`` re-tars the directory it is given, so this blob exists only to keep
    :func:`write_tree` a pure function of an already-proven artifact.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(tree):
            info = tarfile.TarInfo(f"{TARBALL_PREFIX}{path}")
            info.size = len(tree[path])
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(tree[path]))
    return buffer.getvalue()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uipack",
        description=(
            "Rebrand a published ui tarball into the fork's scope by deriving the transform "
            "from the one version published on both sides, proving it byte-for-byte, and "
            "refusing when anything is left unexplained."
        ),
    )
    parser.add_argument(
        "--from",
        dest="from_version",
        required=True,
        metavar="VERSION",
        help="version published by BOTH upstream and the fork",
    )
    parser.add_argument(
        "--to",
        dest="to_version",
        required=True,
        metavar="VERSION",
        help="upstream version to rebrand",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="absent or empty directory for the rebranded package root",
    )
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="SPAN=REPLACEMENT",
        help="record a brand spelling the pair cannot decide (repeatable)",
    )
    parser.add_argument(
        "--upstream-package",
        default=UPSTREAM_PACKAGE,
        metavar="NAME",
        help=f"registry name to download from (default {UPSTREAM_PACKAGE})",
    )
    parser.add_argument(
        "--fork-package",
        default=FORK_PACKAGE,
        metavar="NAME",
        help=f"registry name the pair was published under (default {FORK_PACKAGE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="derive, prove, apply, audit, print the manifest, write nothing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except UiPackError as exc:
        print(f"BLOCKED: {exc.__class__.__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
