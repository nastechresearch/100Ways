"""Fail-closed, input-pinned release migration conflict policy.

This is deliberately not a general release merger.  A decision is enabled only
for a recorded AST fingerprint triple; every other collision stays unresolved.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Callable

from hundredways.python_replay import member_fingerprint, merge_python_members


def is_reviewed_current_surface(relative_path: str, text: str) -> bool:
    """Recognize only the exact, reviewed already-reconciled candidate bytes."""
    reviewed = REVIEWED_SURFACES.get(relative_path)
    return reviewed is not None and hashlib.sha256(text.encode("utf-8")).hexdigest() == reviewed[1]


def _fingerprint(text: str) -> str | None:
    """Stable SHA-256 of a member's syntax, independent of formatting."""
    try:
        return member_fingerprint(text)
    except SyntaxError:
        return None


# Exact triples are intentionally data, not rules.  Parent integration may add
# snapshots only by changing this reviewed table; this module never learns a
# future collision from names or text patterns.
# (relative path, member key): {(ancestor AST, ours AST, theirs AST): decision}
KNOWN_CONFLICTS: dict[tuple[str, str], dict[tuple[str, str, str], str]] = {
    ("scripts/releases/versioning.py", "assign:SEED"): {
        (
            "6da7be065cef39826c8022de3ccbcb85333da8637714a1edaade85c75795fb98",
            "879df88d4cf81eda605e1c45f78a385f940bb7df21f9da24319aefc7d10642a3",
            "31979ea1d86be2eb3e20e358131837eecb12b48e60c87c24cc6b79dfbfc66c9d",
        ): "ours",
    },
    # b7736eca9 (baseline), f4082ebbd9a1 (branded candidate), 15d739801
    # (fork).  These were generated from complete top-level member chunks.
    ("scripts/releases/channel_releases.py", "member:publish_release"): {
        (
            "0e788be36f7d31b4acd665dfac97caa17c93177584c53add7ec7a1c324f1989d",
            "dec7f93264198ac235972a6af45a36277e5aa6291ba96b0cfd9cc7712ef434d8",
            "205177b9eea362cae246c916d394913d6ecdad670bf9997d3c0cbc2502e469e5",
        ): "ours",
    },
    ("scripts/bundles/release_artifacts.py", "member:promote"): {
        (
            "a624f8d22f4b3167311212058f2d292cc817b870beaa0aac61ed4103fb230f12",
            "4b7b9b9aa54f7455c84bd6b3e129b2652ad61b6018db0651228e0d721a440756",
            "7c7b940e177654e8d9b17f7a2337134f5fcb760550e787b44ac7a2ae7f27eff6",
        ): "theirs",
    },
    ("scripts/releases/darwin.py", "member:merge_mac_feeds"): {
        (
            "661d704f352478e6d18d08b27ffd2b991a05b1adf4399027b59765db9bc73437",
            "2c4fcc5e131d2eae1a29b73fd8ce134ce2126f04d838d8fdef89b6d9b2955926",
            "a2b546912a5680440a0ff3f3029bd63252b53a57d892a8d9be9522c2d9964f22",
        ): "ours",
    },
    ("scripts/releases/darwin.py", "member:finalize"): {
        (
            "f958a4d48916777164917a11ff0b1adc6a5f286644f89df6d1b8cf6ce126dce8",
            "c39b930d06b2e4d185013f4c7b41ad7bc4f6d8c43af06313c9ba00d119eb4892",
            "a83c632b1674d4604bc3979893a3b3b17632c120db5079821b36ff86918587cc",
        ): "ours",
    },
}

# Reviewed ARM-only migration surfaces. No workflow snapshots are duplicated:
# each decision is pinned to all three complete inputs and preserves the
# already-reconciled candidate, including unsigned builds and store selection.
REVIEWED_SURFACES = {
    ".github/workflows/desktop-bundled-release.yml": (
        "9b1322828aa4d372b95a0561a5bdf0729cca5b17c9769685bda4f6a1a4f35383",
        "b85759e4877e8e3d26046a89df756ccd1175d5662d48a741432a9d2bf975680d",
        "e06ddb8ba8c0179fdef264f62fe71f9f279d746c5aa6ed1965b0d0eee4be49c6",
    ),
    ".github/workflows/lint.yml": (
        "d45c61cfe25e408b58f631594bf745dd0a4c7d316cb894006a8c860d09a00399",
        "19d852df53df65782acc795a7a2245f2be835cd54b4cd7922510331217c2a8f0",
        "941549041e2809da1a4bdc81fbdb2f2ed651b0b3990a102aa2fdd3e7d2c24947",
    ),
    ".github/workflows/stable-release.yml": (
        "f3049b5893e55a655dc05edb7605012a34c409563ce7808487f7cb42271122c1",
        "70723429fd08a6b224e8d657eef64a2cf2f1e894f0e1dd158f7d0e85366812e0",
        "f673e0e9191711acc1bd2a9d72742e4b475be2d7877d63e77cc510f91dee0695",
    ),
    ".github/workflows/tests.yml": (
        "f82d18dfd692a720060032ccc6117b0dd9232d4cdee0922cdd1d935bac681fa2",
        "c3bfe47cf96d1dfb7fb8ccbba82f745c2dfce509e3a27328080d4af6a3cae824",
        "acc571680e6247b9e2ecc5a6cd09b527a89ffb571508ca09f5e5a6bad9315920",
    ),
    "tests/scripts/test_release_channels.py": (
        "8270e420a44d4087fae4f3843e9a2ba877dde74aa366110dccdcaa8c827ca49a",
        "0cabca4487bea5a91694d7e8404cd27eb04bae8616502fdb14759e1df29189a6",
        "e623869e0d1087cc50cbb0f055a90f770ab38fe3758e5373d016c0059c648997",
    ),
    "tests/scripts/test_release_entrypoint.py": (
        "d13d04c71880cd3b3aefc5c41c5ee2d36e74164d0a38894a5166b355d073daab",
        "4ad9dbc5d09e951c793c2cb3dedf10db668da2cdc6fa5dbd515b5415aa742582",
        "bab3aa520ab57a557b836adb3f0dd75d12ee1ea2e68ff0ba861e7742a82659f8",
    ),
    "tests/scripts/test_release_lanes.py": (
        "6abd52b6df405b771be12600271b5bab2dce87c33594f128f6be300c179aaf91",
        "1569f8a5dd8bfef83bf61f434b178db4e137225a3725d304e508082ca3f37162",
        "98175cc96bc8f614549f1661553cb0718044ccb3bca891a408124a45fa07909e",
    ),
    "tests/scripts/test_stable_release.py": (
        "92108f2bc7af1bce20381f3b210e1594c38638a9ef56af0d5ed800272bd2a58c",
        "212f9406c246997d96badc06f5fc97ded88f69a5ee4693fec8be49330ff8b7c3",
        "f97217f1da6ae125943be0bf51653af3dd748234c767e2d6cef4c4d20cd2597b",
    ),
}

_OWNERS = {
    "scripts/releases/versioning.py": {"assign:SEED": "ours"},
    "scripts/releases/channel_releases.py": {"member:publish_release": "ours"},
    "scripts/bundles/release_artifacts.py": {"member:promote": "theirs"},
    "scripts/releases/darwin.py": {"member:merge_mac_feeds": "ours", "member:finalize": "ours"},
    "scripts/bundles/channel_artifacts.py": {"member:assemble": "ours"},
}


def _resolver(rel: str) -> Callable[[tuple[str, ...], str, str, str], str | None]:
    def resolve(key: tuple[str, ...], base: str, ours: str, theirs: str) -> str | None:
        if len(key) != 1:
            return None
        decision = KNOWN_CONFLICTS.get((rel, key[0]), {}).get(
            (_fingerprint(base), _fingerprint(ours), _fingerprint(theirs))
        )
        if decision == "ours":
            return ours
        if decision == "theirs":
            return theirs
        return None

    return resolve


REVIEWED_SURFACES.update(
    {
        "tests/scripts/test_release_darwin.py": (
            "cd31d64661508c5056110a0388456098f8ac953c3d5e4601b10359d8286a7a46",
            "ea5ade0db4fa0703de935d28b7c4b55467f44296fb2764b7e797f6add27728d6",
            "f6aac9805bce739d84d6d61d0ca229125d81a1a012eb9c3f620cc85597573c6a",
        ),
        "tests/scripts/test_release_entrypoint.py": (
            "d13d04c71880cd3b3aefc5c41c5ee2d36e74164d0a38894a5166b355d073daab",
            "4ad9dbc5d09e951c793c2cb3dedf10db668da2cdc6fa5dbd515b5415aa742582",
            "bab3aa520ab57a557b836adb3f0dd75d12ee1ea2e68ff0ba861e7742a82659f8",
        ),
        "tests/scripts/test_release_lanes.py": (
            "6abd52b6df405b771be12600271b5bab2dce87c33594f128f6be300c179aaf91",
            "1569f8a5dd8bfef83bf61f434b178db4e137225a3725d304e508082ca3f37162",
            "98175cc96bc8f614549f1661553cb0718044ccb3bca891a408124a45fa07909e",
        ),
        "tests/scripts/test_stable_release.py": (
            "92108f2bc7af1bce20381f3b210e1594c38638a9ef56af0d5ed800272bd2a58c",
            "212f9406c246997d96badc06f5fc97ded88f69a5ee4693fec8be49330ff8b7c3",
            "f97217f1da6ae125943be0bf51653af3dd748234c767e2d6cef4c4d20cd2597b",
        ),
    }
)
REVIEWED_OUTPUTS = dict(
    [
        (
            "tests/scripts/test_release_darwin.py",
            "c5a90d1705e3354c0355f84aed0a61c283889a04e7c8658aeb681a9c8ca9e8e6",
        ),
        (
            "tests/scripts/test_release_entrypoint.py",
            "40dd8852fd820c9ec65fccc23e0abee7ab4e3c1a47d69cafee154dd2c86c364d",
        ),
        (
            "tests/scripts/test_release_lanes.py",
            "bd638df121f321a1aba4a69390119682492d804125dcfc2dab14b42130863bc9",
        ),
        (
            "tests/scripts/test_stable_release.py",
            "8f9da2a4e18231e4f20a5e00decc178305145fded85f0ea7df72ca4f7dd09f68",
        ),
    ]
)


def reviewed_release_surface(rel: str, ancestor: str, ours: str, theirs: str) -> str | None:
    """Accept only the audited migration or its exact repaired fixed point."""
    frozen = REVIEWED_SURFACES.get(rel)
    if frozen is None:
        return None
    digests = tuple(
        hashlib.sha256(text.encode("utf-8")).hexdigest() for text in (ancestor, ours, theirs)
    )
    if digests == frozen or (
        digests[0] == frozen[0]
        and digests[2] == frozen[2]
        and digests[1] == REVIEWED_OUTPUTS.get(rel)
    ):
        return ours
    return None


# Exact complete-input/output pins for two audited member merges.
# Unlike the member ownership table, no future inputs at these paths are accepted.
REVIEWED_MEMBER_MERGES: dict[str, tuple[str, str, str, str]] = {
    'scripts/releases/channel_publish.py': (
        'abc9f7718e0c590c0d78814d1ec2ae7931ac1d1ba547bc2fa31835515a4c7110',
        '8d23fad075ea0422a9e8769d3bd49d1d49bf75f9cb34ef0c67259a6187ca9e5c',
        '392c8dd4c02fe04de7d4a58aefe6fc3315736e33d867e274693677d50302a6c3',
        '8d23fad075ea0422a9e8769d3bd49d1d49bf75f9cb34ef0c67259a6187ca9e5c',
    ),
    'tests/scripts/test_release_artifacts.py': (
        'cc2ebd7e5539527ed397ac0203b475d2a389a35bada1e9287adf05db86a418c7',
        'be5bb74f0c79e77a510ff6365a7a411c895b9292b31b53725d97ee5f182dda08',
        'ed3f78b8d608cbb3348c467d50d2192cb385bb431ba0fb588b853dad86c0ddaa',
        '1fb63b5e399ecc84fde0aebfb4675756920ae2a9a936ca9aaf688b4e19cfde9a',
    ),
}


def _reviewed_member_merge(rel: str, ancestor: str, ours: str, theirs: str) -> str | None:
    frozen = REVIEWED_MEMBER_MERGES.get(rel)
    if frozen is None:
        return None
    digests = tuple(
        hashlib.sha256(text.encode("utf-8")).hexdigest() for text in (ancestor, ours, theirs)
    )
    if digests == (frozen[0], frozen[3], frozen[2]):
        return ours
    if digests != frozen[:3]:
        return None
    merged = merge_python_members(ancestor, ours, theirs)
    if merged is None or hashlib.sha256(merged.encode("utf-8")).hexdigest() != frozen[3]:
        return None
    return merged


def replay_release_module(rel: str, ancestor: str, ours: str, theirs: str) -> str | None:
    """Replay only a recognised migration module and frozen member collisions.

    Unknown paths, changed snapshots, malformed modules and ordinary future
    conflicts return ``None``.  This preserves the caller's normal loud
    conflict path rather than silently selecting upstream or fork behaviour.
    """
    approved = reviewed_release_surface(rel, ancestor, ours, theirs)
    if approved is not None:
        return approved
    pinned = _reviewed_member_merge(rel, ancestor, ours, theirs)
    if pinned is not None:
        return pinned
    if rel not in _OWNERS:
        return None
    if rel == "scripts/bundles/release_artifacts.py":
        # Normalise the fork side before merging. This makes a second replay a
        # fixed point instead of conflicting with the compatibility edits made
        # on the first pass.
        theirs = _adapt_promotion_helpers(theirs)
        if theirs is None:
            return None
    return merge_python_members(ancestor, ours, theirs, resolve=_resolver(rel))


def _adapt_promotion_helpers(source: str) -> str | None:
    """Bridge the reviewed fork helpers to the current target/store contracts."""
    fingerprints = {
        "promote_desktop_feeds": "80d7c142c6fb5ac29e087e899ed2992a2c975c172d0a64127f2154157447ab7d",
        "publish_pointer": "54eea9659e518258acda9d13047278c302a4fabc4e126bef5ea424d67a8dd83f",
    }
    # The long fingerprint is checked exactly; never adapt an unknown helper.
    fingerprints["promote_desktop_feeds"] = (
        "80d7c142c6fb5ac29e087e899ed2992a2c975c172d0a64127f2154157447ab7d"
    )
    nodes = {n.name: n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    for name in ("promote_desktop_feeds", "publish_pointer"):
        node = nodes.get(name)
        if node is None:
            return None
        original = ast.get_source_segment(source, node)
        if _fingerprint(original) != fingerprints[name]:
            return None
        if name == "promote_desktop_feeds":
            # Hermes centralises macOS architectures in desktop_targets; the
            # transport's lazy finalize wrapper no longer accepts arches=.
            lines = original.splitlines(keepends=True)
            remove = next(
                (
                    n
                    for n in node.body
                    if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "arches" for t in n.targets)
                ),
                None,
            )
            if remove is None or original.count("arches=arches, ") != 1:
                return None
            del lines[remove.lineno - node.lineno : remove.end_lineno - node.lineno + 1]
            replacement = "".join(lines).replace("arches=arches, ", "")
            replacement = replacement.replace(
                "    # macOS Intel is opt-in: without it the feed carries the arm64 leg alone.\n",
                "",
            )
        else:
            replacement = '''\
def publish_pointer(manifest: dict, public_base: str, key: str, file: Path) -> None:
    """Publish and verify a pointer through the same selected archive store."""
    from scripts.releases.handoff import store

    chosen = store(public_base)
    chosen.put(tag=manifest["tag"], key=key, file=file, key_is_full=True)
    with tempfile.TemporaryDirectory() as directory:
        try:
            chosen.download_public_object(
                public_base, key, Path(directory) / "readback",
                expected_size=file.stat().st_size, expected_sha256=sha256_file(file),
            )
        except ValueError as error:
            raise ValueError(f"Channel read-back differs: {key}") from error'''
        source = source.replace(original, replacement, 1)
        nodes = {n.name: n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    compile(source, "<release-replay>", "exec")
    return source
