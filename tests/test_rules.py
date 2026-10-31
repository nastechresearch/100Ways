from hundredways.rules import (
    BrandingRules,
    TokenRule,
    is_locked_path,
    is_vendored_path,
)


def test_simple_branding():
    rules = BrandingRules()
    out = rules.transform_text("The Hermes agent uses Hermes-1.5")
    assert "nastech" in out.lower()
    assert "hermes" not in out.lower()


def test_preserves_english_words():
    """venous, anonymous, thermometer must NOT be rebranded."""
    rules = BrandingRules()
    text = "venous blood, anonymous user, thermometer reading"
    out = rules.transform_text(text)
    assert "venous" in out
    assert "anonymous" in out
    assert "thermometer" in out


def test_camelcase_identifiers_are_rebranded():
    """camelCase boundaries must match: refreshHermesConfig -> refreshNastechConfig."""
    rules = BrandingRules()
    pairs = {
        "refreshHermesConfig": "refreshNastechConfig",
        "updateHermes": "updateNastech",
        "locateHermes": "locateNastech",
        "startHermes": "startNastech",
        "getHermesConfigRecord": "getNastechConfigRecord",
        "titleNous": "titleNastech",
        "annguyenNous": "annguyenNastech",
        "remoteHermesPath": "remoteNastechPath",
        "useHermesConfigRecord": "useNastechConfigRecord",
    }
    for before, after in pairs.items():
        assert rules.transform_text(before) == after, (before, rules.transform_text(before))


def test_snake_case_handling():
    rules = BrandingRules()
    out = rules.transform_text("hermes_provider hermesAgent hermes-agent")
    assert "hermes" not in out.lower()


def test_room_grant_auth_scheme_stays_aligned():
    """The lowercased HTTP Authorization scheme must be rebranded along with
    the title-case token the tests send, or every room-grant request 401s.

    Parser guards ``scheme.lower() != "hermesroom"`` while tests send
    ``HermesRoom <grant>``.  A generic lowercase ``hermes`` token is
    boundary-guarded and can't fire inside ``hermesroom``, so it needs an
    explicit compound.
    """
    rules = BrandingRules()
    parser = rules.transform_text('scheme.lower() != "hermesroom"')
    assert "nastechroom" in parser
    assert "hermesroom" not in parser
    sent = rules.transform_text('f"HermesRoom {grant}"')
    assert "NastechRoom" in sent
    # both the scheme the parser compares against and the scheme the test
    # sends lower to the same value, so the auth branch lines up.
    assert "nastechroom" in sent.lower()


def test_compact_python_module_flags_are_branded():
    rules = BrandingRules()
    assert rules.transform_text("python3 -mhermes_cli.main gateway") == \
        "python3 -mnastech_cli.main gateway"


def test_third_party_misaki_git_url_is_branded_to_the_fork():
    rules = BrandingRules()
    text = '"misaki[en] @ git+https://github.com/NousResearch/misaki.git@abc"'
    assert rules.transform_text(text) == \
        '"misaki[en] @ git+https://github.com/NastechResearch/misaki.git@abc"'


def test_repository_paths_we_do_not_own_are_preserved_verbatim():
    """A foreign repository identifier is not ours to rename.

    Branding rewrote the repo NAME inside third-party GitHub URLs
    (``hermes-agent-memory`` -> ``nastech-agent-memory``) while leaving the
    foreign owner, which produced 383 catalog entries pointing at repositories
    that do not exist.  Renaming somebody else's repository only ever yields a
    404, so the whole identifier must survive.
    """
    rules = BrandingRules()
    for url in (
        "https://github.com/lancedb/hermes-agent-memory",
        "https://github.com/prismhq/hermes-prism-provider",
        "https://github.com/oxylabs/hermes-web-oxylabs",
    ):
        assert rules.transform_text(f"repo: {url}") == f"repo: {url}"
    # a pinned sha appended to the path is still part of the identifier
    pinned = "https://github.com/adolanium/hermes-terminal/blob/abc123/README.md"
    assert rules.transform_text(f"docs_url: {pinned}") == f"docs_url: {pinned}"
    # several identifiers on one line must each survive
    line = "see https://github.com/foo/hermes-a and https://github.com/bar/hermes-b"
    assert rules.transform_text(line) == line


def test_repositories_we_own_are_still_branded():
    """The exemption must not become a hole in our own orgs."""
    rules = BrandingRules()
    # an inherited owner a token rule maps into an org we own
    assert rules.transform_text(
        "git = https://github.com/NousResearch/misaki.git?rev=abc"
    ) == "git = https://github.com/NastechResearch/misaki.git?rev=abc"
    # already ours
    assert rules.transform_text(
        "repo: https://github.com/NastechResearch/nastech-official-plugins"
    ) == "repo: https://github.com/NastechResearch/nastech-official-plugins"
    # prose around a preserved identifier is still branded
    assert rules.transform_text(
        "The Hermes agent ships https://github.com/lancedb/hermes-agent-memory"
    ) == "The Nastech agent ships https://github.com/lancedb/hermes-agent-memory"


def test_rest_api_repository_paths_read_the_owner_not_the_repos_segment():
    """The ``/repos/`` in a REST URL is API path, not the owner.

    ``api.github.com/repos/<owner>/<repo>`` occurs ~270 times across the tree.
    Matching only ``github.com/<seg>/<seg>`` read the owner as a literal
    ``repos``, so a first-party identifier (``NousResearch``) was "preserved"
    and leaked upstream branding, while a foreign repo name was still renamed
    (``alice/hermes-plugin`` -> ``alice/nastech-plugin``, a 404).  Both halves
    of the rule must hold for the REST form exactly as for the web form.
    """
    from hundredways.rules import is_owned_repo_path

    rules = BrandingRules()
    # a first-party owner is ours to rename, in either form and either case
    assert rules.transform_text(
        "https://api.github.com/repos/NousResearch/hermes-agent/issues/comments/123"
    ) == "https://api.github.com/repos/NastechResearch/nastech-agent/issues/comments/123"
    assert rules.transform_text(
        "https://uploads.github.com/repos/NousResearch/hermes-agent/releases/1"
    ) == "https://uploads.github.com/repos/NastechResearch/nastech-agent/releases/1"
    # a foreign repository survives verbatim, name included
    foreign = "https://api.github.com/repos/alice/hermes-plugin-foo/issues/1"
    assert rules.transform_text(f"url: {foreign}") == f"url: {foreign}"
    # the owner is read from the identifier, not from a fixed position
    assert is_owned_repo_path("github.com/nastechresearch/nastech-agent")
    assert is_owned_repo_path("api.github.com/repos/NastechResearch/nastech-agent")
    assert not is_owned_repo_path("api.github.com/repos/alice/hermes-plugin-foo")
    # and the correction is stable under a second pass
    once = rules.transform_text(foreign)
    assert rules.transform_text(once) == once


def test_raw_asset_urls_are_repository_identifiers_too():
    """``raw.githubusercontent.com`` names a repository, so it is protected.

    Measured on the published fork catalog: 191 distinct foreign-owner plugin
    entries spell their repository twice -- once as
    ``github.com/<owner>/hermes-x`` (matched, so preserved) and once as
    ``raw.githubusercontent.com/<owner>/hermes-x/<sha>/docs/hero.png`` (not
    matched).  The unmatched spelling read as a *bare* identifier, which is the
    condition :meth:`BrandingRules._bare_also_branded` uses to rebrand the whole
    file, so the preservation pass above was silently defeated for every such
    entry.  Sampled pairs, 8/8: ``Adolanium/nastech-clinepass`` answers 404
    while ``Adolanium/hermes-clinepass`` answers 200.
    """
    rules = BrandingRules()
    for url in (
        "https://raw.githubusercontent.com/Adolanium/hermes-clinepass/"
        "ab12cd34/docs/screenshots/hero.png",
        "https://raw.githubusercontent.com/roqianjas/hermes-account-pool/"
        "ab12cd34/docs/screenshots/accounts.png",
    ):
        assert rules.transform_text(f"image: {url}") == f"image: {url}"

    # the catalog entry shape that broke: both spellings in one file
    entry = (
        "repo: https://github.com/Adolanium/hermes-clinepass\n"
        "image: https://raw.githubusercontent.com/Adolanium/hermes-clinepass/"
        "ab12cd34/docs/hero.png\n"
    )
    assert rules.transform_text(entry) == entry


def test_raw_asset_urls_into_repositories_we_own_are_still_branded():
    """The exemption reads the owner, so a first-party asset URL keeps moving."""
    rules = BrandingRules()
    assert rules.transform_text(
        "curl -fL https://raw.githubusercontent.com/NousResearch/hermes-agent/"
        "main/scripts/install.sh | bash"
    ) == (
        "curl -fL https://raw.githubusercontent.com/NastechResearch/nastech-agent/"
        "main/scripts/install.sh | bash"
    )
    once = rules.transform_text(
        "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/README.md"
    )
    assert rules.transform_text(once) == once


def test_the_path_after_a_foreign_repository_is_not_ours_to_rename():
    """A tree, blob or asset path inside somebody else's repo stays verbatim.

    Matching ``raw.githubusercontent.com`` protected the repository name but not
    what comes after it, and the segment after the name is a directory or a file
    in the same foreign repository.  Measured in the published fork tree: 78
    distinct URLs of this shape answer 404, 76 of them with a live 200 twin under
    the inherited spelling --
    ``plastic-labs/honcho/tree/main/nastech-plugin-honcho`` versus
    ``.../hermes-plugin-honcho``, and the catalog banner
    ``cocktailpeanut/nastech-mod/master/nastech.png`` versus
    ``.../hermes-mod/master/hermes.png``.
    """
    rules = BrandingRules()
    for url in (
        "https://github.com/plastic-labs/honcho/tree/main/hermes-plugin-honcho",
        "https://github.com/plastic-labs/honcho/blob/main/hermes-plugin-honcho/cli.py",
        "https://raw.githubusercontent.com/cocktailpeanut/hermes-mod/master/hermes.png",
        "https://github.com/ActVox/actvox-plugins/tree/main/hermes",
    ):
        assert rules.transform_text(f"docs: {url} next") == f"docs: {url} next"

    # Prose and markup delimiters end the identifier, so a second URL written
    # straight after the first is still read as one -- and still branded.
    assert rules.transform_text(
        "see https://github.com/o/r/tree/main/hermes,"
        "https://github.com/NousResearch/hermes-agent for more"
    ) == (
        "see https://github.com/o/r/tree/main/hermes,"
        "https://github.com/NastechResearch/nastech-agent for more"
    )
    link = "https://github.com/plastic-labs/honcho/tree/main/hermes-plugin-honcho"
    assert rules.transform_text(f"[honcho]({link})") == f"[honcho]({link})"
    assert rules.transform_text(f"image: {link}#inline") == f"image: {link}#inline"


def test_the_path_after_a_repository_we_own_is_branded_with_it():
    """The tail group is not an exemption: our own tree keeps moving with us."""
    rules = BrandingRules()
    assert rules.transform_text(
        "https://api.github.com/repos/NousResearch/hermes-agent/contents/hermes_cli"
    ) == "https://api.github.com/repos/NastechResearch/nastech-agent/contents/nastech_cli"
    once = rules.transform_text(
        "https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/pm/__init__.py"
    )
    assert "nastech_cli" in once
    assert rules.transform_text(once) == once


def test_a_path_on_a_host_the_fork_does_not_run_stays_whole():
    """A URL into somebody else's site is their object, one level above a repo.

    ``GITHUB_REPO_RE`` stops the brand at a foreign repository; the same
    reasoning applies to a whole URL whose host the fork does not run, because
    the path is a page on *their* service.  Measured 2026-10-27 by fetching both
    spellings of every third-party link the published fork tree carries: 20 of
    23 sampled pairs answer 200 inherited and 4xx branded, e.g.
    ``botcabinet.com/hermes/manual/`` 200 against ``botcabinet.com/nastech/manual/``
    404, ``vshulcz.github.io/deja-vu/guide/memory-for-hermes.html`` 200 against
    the branded 404.  The two pairs differing only in their fragment carry the
    inherited anchor id and not the branded one.
    """
    rules = BrandingRules()
    for url in (
        "https://docs.honcho.dev/v3/guides/integrations/hermes",
        "https://docs.honcho.dev/v3/guides/integrations/hermes"
        "#running-honcho-locally-with-hermes",
        "https://hindsight.vectorize.io/sdks/integrations/hermes",
        "https://docs.apify.com/integrations/hermes-agent",
        "https://docs.cortexlayer.net/docs/agents/hermes",
        "https://docs.openmail.sh/integrations/hermes",
        "https://pidgeon.ai/docs/hermes",
        "https://mybot.farm/install/hermes",
        "https://botcabinet.com/hermes/manual/",
        "https://linchpin.studio/school-of-hermes/manual/",
        "https://patrykkopycinski.github.io/hermes-plugin-agent-memory-es/",
        "https://adolanium.github.io/hermes-gadget-sdk/docs/connect-hermes.html",
        "https://briancaffey.github.io/hermes-otel/",
    ):
        once = rules.transform_text(f"docs: {url} next")
        assert once == f"docs: {url} next", url
        assert rules.transform_text(once) == once, url

    # Only the URL is preserved; the sentence around it still brands.
    assert rules.transform_text(
        "run `hermes plugin install`, then read https://mybot.farm/install/hermes"
    ) == "run `nastech plugin install`, then read https://mybot.farm/install/hermes"
    # A markdown link's closing bracket ends the URL instead of swallowing it.
    assert rules.transform_text("[manual](https://botcabinet.com/hermes/manual/)") == (
        "[manual](https://botcabinet.com/hermes/manual/)"
    )


def test_a_third_party_object_the_fork_can_claim_itself_still_brands():
    """Preserving a link is for pages we cannot publish, not for ones we haven't yet.

    ``discord.gg`` is the measured case: the invite API answers the guild "Nous
    Research" for ``NousResearch`` and ``{"message": "Unknown Invite", "code":
    10006}`` for ``NastechResearch``, and the code ``nastechresearch`` is
    unclaimed.  Keeping the upstream community in our docs would send Nastech's
    users to somebody else's server forever, and would cost 10 of the 8 files
    of reviewed drift the rules gate leaves under its parity floor, so the
    engine brands it and the owner creates the invite.
    """
    rules = BrandingRules()
    assert rules.transform_text("join https://discord.gg/NousResearch") == (
        "join https://discord.gg/NastechResearch"
    )
    assert not rules.preserves_external_url("https://discord.gg/NousResearch")
    # Once the fork runs its own object on a host, saying so resumes branding
    # without a code change: the same escape hatch the Algolia rule uses.
    assert rules.preserves_external_url("https://docs.honcho.dev/v3/guides/integrations/hermes")


def test_display_text_on_a_third_party_host_is_still_ours_to_brand():
    """Preserving somebody's page is not preserving a label that names us.

    The rule is about objects a host serves, so a badge caption or a free-text
    parameter keeps the brand: ``img.shields.io`` renders whatever the path
    says, and the ascii skill asks the service to draw our wordmark.
    """
    rules = BrandingRules()
    assert rules.transform_text("https://img.shields.io/badge/discord-hermes-blue") == (
        "https://img.shields.io/badge/discord-nastech-blue"
    )
    assert rules.transform_text(
        "https://asciified.thelicato.io/api/v2/ascii?text=HERMES&font=Slant"
    ) == "https://asciified.thelicato.io/api/v2/ascii?text=NASTECH&font=Slant"


def test_a_host_the_rules_move_onto_a_host_we_run_is_never_preserved():
    """The ``*.github.io`` shape ends where our own Pages origin begins."""
    rules = BrandingRules()
    assert rules.transform_text("https://nousresearch.github.io/hermes-agent/docs") == (
        "https://nastechresearch.github.io/nastech-agent/docs"
    )
    assert rules.transform_text("https://nousresearch.com") == "https://nastechresearch.com"


def test_a_preserved_url_carrying_a_repository_identifier_keeps_both_rules():
    """A nested identifier is not masked twice, so no sentinel reaches output.

    The repository pass runs first, so a URL that embeds one arrives here with a
    placeholder inside it.  Masking the whole span again would restore that
    placeholder after its own substitution had already run and publish it.
    """
    rules = BrandingRules()
    out = rules.transform_text(
        "https://docs.honcho.dev/redirect?to=https://github.com/Adolanium/hermes-clinepass"
        " plus https://github.com/NousResearch/hermes-agent"
    )
    assert "\ue000" not in out
    assert out == (
        "https://docs.honcho.dev/redirect?to=https://github.com/Adolanium/hermes-clinepass"
        " plus https://github.com/NastechResearch/nastech-agent"
    )


def test_preserved_repository_path_is_a_branding_fixed_point():
    """The audit defines clean as ``transform_text(text) == text``.

    A preserved identifier therefore passes the zero-upstream-brand audit by
    construction, rather than by weakening the audit.
    """
    from hundredways.brandaudit import residual_brand_lines

    rules = BrandingRules()
    entry = "repo: https://github.com/lancedb/hermes-agent-memory\n"
    assert rules.transform_text(entry) == entry

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "lancedb.yaml").write_text(
            "name: agent-memory\nrepo: https://github.com/lancedb/hermes-agent-memory\n",
            encoding="utf-8",
        )
        # a genuinely unbranded first-party line is still reported
        (root / "leak.yaml").write_text(
            "repo: https://github.com/NousResearch/hermes-agent\n", encoding="utf-8"
        )
        residual = residual_brand_lines(str(root), rules)
    assert [(item.path, item.line) for item in residual] == [("leak.yaml", 1)]


def test_hosted_community_plugin_maps_to_our_own_fork():
    """The holographic plugin is one we host ourselves, under ``nastechtest``.

    Branding rewrites the repo NAME inside a foreign GitHub URL
    (``hermes-plugin-holographic`` -> ``nastech-plugin-holographic``) but
    cannot rename the owner, so the naive rewrite produced
    ``itpartypattaya/nastech-plugin-holographic`` -- a repo that does not
    exist.  Both the ``repo:`` and the ``docs_url:`` (which carries the same
    path plus the pinned sha) must point at our fork instead.
    """
    from hundredways.rules import transform_strict_metadata_text

    rules = BrandingRules()
    sha = "a6d009315bc72a7c4e68d6a0e60a6eeed68080fd"
    assert rules.transform_text(
        "repo: https://github.com/itpartypattaya/hermes-plugin-holographic"
    ) == "repo: https://github.com/nastechtest/nastech-plugin-holographic"
    assert rules.transform_text(
        "docs_url: https://github.com/itpartypattaya/hermes-plugin-holographic"
        f"/blob/{sha}/README.md"
    ) == (
        "docs_url: https://github.com/nastechtest/nastech-plugin-holographic"
        f"/blob/{sha}/README.md"
    )
    # strict metadata applies tokens in order, so the compound must still win
    # rather than the generic hermes token firing first.
    assert "itpartypattaya" not in transform_strict_metadata_text(
        "https://github.com/itpartypattaya/hermes-plugin-holographic"
    )


def test_one_identifier_keeps_every_spelling_when_we_do_not_own_it():
    """A file carrying a bare identifier and its URL keeps both inherited.

    The fork's release tests build a URL *from* a repository string and compare
    it with a URL literal: ``target_repository="roqianjas/hermes-account-pool"``
    is what turns into ``https://github.com/roqianjas/hermes-account-pool/...``.
    Branding the bare half while the URL stayed verbatim produced a file that
    asserted ``nastech-account-pool`` against ``hermes-account-pool``, and
    branding both halves renamed the repository to one that does not exist
    (measured 2026-10-28: upstream 200, fork 404).  Consistency is kept the
    only way that also keeps the link alive: both halves stay inherited.
    """
    rules = BrandingRules()
    both = (
        '    repository = "roqianjas/hermes-account-pool"\n'
        '    url = "https://github.com/roqianjas/hermes-account-pool/releases/tag/v1"\n'
    )
    assert rules.transform_text(both) == both
    assert rules.transform_text(rules.transform_text(both)) == both

    # the real shape of the fixture fork in
    # ``tests/nastech_cli/test_source_release_channels.py``: a trailing ``.git``
    # belongs to the identifier, so it must not hide the bare spelling beside it
    dotted = (
        '    url = "https://github.com/roqianjas/hermes-account-pool.git"\n'
        '    target_repository = "roqianjas/hermes-account-pool"\n'
    )
    assert rules.transform_text(dotted) == dotted

    # a repository we do own is still branded under every spelling it uses
    ours = (
        '    repository = "NousResearch/hermes-agent"\n'
        '    url = "https://github.com/NousResearch/hermes-agent/releases/tag/v1"\n'
    )
    branded = rules.transform_text(ours)
    assert branded == (
        '    repository = "NastechResearch/nastech-agent"\n'
        '    url = "https://github.com/NastechResearch/nastech-agent/releases/tag/v1"\n'
    )


def test_a_preserved_repository_is_kept_in_the_rest_path_form():
    """The same identifier is preserved when the file spells it as a REST path.

    The fork's release fixture keys responses by ``/repos/<owner>/<repo>/...``
    while the origin URL beside it is preserved verbatim.  Branding only the
    path half asked the fixture server for a repository it never served, so
    ``resolve_source_release`` logged a 404 and returned ``(None, None)``.
    """
    rules = BrandingRules()
    text = (
        '    url = "https://github.com/Fixture/hermes-agent.git"\n'
        '    releases.responses["/repos/Fixture/hermes-agent/releases/latest"] = {}\n'
    )
    assert rules.transform_text(text) == text
    # a repository we do own is still branded under this shape too
    ours = (
        '    url = "https://github.com/NousResearch/hermes-agent.git"\n'
        '    releases.responses["/repos/NousResearch/hermes-agent/releases/latest"] = {}\n'
    )
    out = rules.transform_text(ours)
    assert "hermes" not in out.lower()
    assert "NousResearch" not in out


def test_the_bare_spelling_exception_never_widens_preservation():
    """Preservation still holds for a URL the file does not spell bare.

    The exception is one-way: it can move a would-be-preserved identifier into
    ``brand_repo_path``, never the other way round.  An occurrence that only
    exists *inside* repository identifiers is not a bare spelling -- the pass
    masks those before it looks -- so the 383 catalog entries that started this
    exemption stay verbatim.
    """
    rules = BrandingRules()
    for text in (
        "repo: https://github.com/lancedb/hermes-agent-memory\n",
        # the same identifier twice, both times as part of a URL
        "a = 'https://github.com/lancedb/hermes-agent-memory'\n"
        "b = 'https://github.com/lancedb/hermes-agent-memory/issues/1'\n",
        # already ours, so nothing here is a rename in either direction
        'url = "https://github.com/NastechResearch/nastech-agent/releases"\n',
    ):
        assert rules.transform_text(text) == text


def test_yaml_comment_does_not_force_a_foreign_repo_url_to_brand():
    """A comment is documentation, not a value the code compares.

    ``plugin-catalog/aichipmunk.yaml:2`` credits
    ``Pacific-Cloud-Solutions/hermes-plugins`` in a comment while the ``repo:``
    URL and the hero asset point at the same repository.  Branding that
    repository renamed three live objects into 404s.
    """
    rules = BrandingRules()
    sha = "d565d7698a28f4364f819446e5f7ae2b67c57352"
    text = (
        "# Generated by Pacific-Cloud-Solutions/hermes-plugins:scripts/catalog_entry.py.\n"
        "repo: https://github.com/Pacific-Cloud-Solutions/hermes-plugins\n"
        "image: https://raw.githubusercontent.com/Pacific-Cloud-Solutions/"
        f"hermes-plugins/{sha}/plugins/aichipmunk/docs/hero-2x1.webp\n"
    )
    branded = rules.transform_text(text)
    assert "https://github.com/Pacific-Cloud-Solutions/hermes-plugins" in branded
    assert (
        "https://raw.githubusercontent.com/Pacific-Cloud-Solutions/"
        f"hermes-plugins/{sha}/plugins/aichipmunk/docs/hero-2x1.webp"
    ) in branded
    assert "Pacific-Cloud-Solutions/nastech-plugins/" not in branded
    assert rules.transform_text(branded) == branded


def test_prose_in_a_yaml_value_does_not_force_foreign_assets_to_brand():
    """A catalog description naming a repository is prose, not a value.

    The cloud-file-manager entry's ``description`` mentions
    ``electricsheephq/hermes-cloud-file-manager`` in a sentence, and the entry
    carries one ``repo:``, one ``docs_url:`` and seven raw screenshots of the
    same repository.  All nine URLs answered 404 after branding and 200
    upstream.
    """
    rules = BrandingRules()
    sha = "9678d440f0c1735996d2a0b1a5e97fe58013fddd"
    repo = "electricsheephq/hermes-cloud-file-manager"
    assets = [
        f"https://raw.githubusercontent.com/{repo}/{sha}/docs/media/{name}"
        for name in (
            "banner.png",
            "screenshot-page.png",
            "screenshot-upload.png",
            "screenshot-picker.png",
            "screenshot-drive-page.png",
            "screenshot-editor-review.png",
            "screenshot-chat.png",
        )
    ]
    text = (
        f'description: "searches {repo} issues with gh after approval"\n'
        f"repo: https://github.com/{repo}\n"
        f"docs_url: https://github.com/{repo}#readme\n"
        + "".join(f"  - {asset}\n" for asset in assets)
    )
    branded = rules.transform_text(text)
    assert f"https://github.com/{repo}" in branded
    assert f"https://github.com/{repo}#readme" in branded
    for asset in assets:
        assert asset in branded
    assert "electricsheephq/nastech-cloud-file-manager/" not in branded
    assert rules.transform_text(branded) == branded


def test_path_transforms():
    rules = BrandingRules()
    assert "nastech" in rules.transform_path("tools/hermes_runner.py").lower()


def test_custom_token():
    rules = BrandingRules(tokens=[TokenRule("Acme", "Widget")])
    assert "Widget" in rules.transform_text("Acme product")
    assert "Acme" not in rules.transform_text("Acme product")


def test_inherited_medical_symbols_are_rebranded_to_the_approved_nastech_glyph():
    rules = BrandingRules()
    assert rules.transform_text("⚕ Hermes") == "𓄃 Nastech"
    assert rules.transform_text("☤ hermes") == "𓄃 nastech"


def test_locked_paths():
    assert is_locked_path("assets/logo.png")
    assert is_locked_path("tests/package-lock.json")
    assert is_locked_path("static/favicon.ico")
    assert not is_locked_path("src/main.py")
    assert not is_locked_path("docs/guide.md")


def test_dotenv_templates_are_brandable():
    """Real .env secret files stay locked, but templates/direnv configs brand."""
    assert is_locked_path(".env")
    assert is_locked_path("services/api/.env")
    assert not is_locked_path(".env.example")
    assert not is_locked_path("apps/desktop/.env.example")
    assert not is_locked_path(".envrc")


def test_vendored_third_party_content_is_locked():
    """Vendored fixtures are checksum-pinned by their origin, so they never brand.

    ``plugins/config_backends/remote/vendor/conformance`` ships a
    ``config-fixtures.sha256`` manifest and a ``SOURCE.json`` naming the real
    owner; rewriting ``"service.name": "hermes"`` inside a fixture changed its
    digest and the vendored-manifest test failed against the pinned value.
    """
    assert is_locked_path("plugins/config_backends/remote/vendor/SOURCE.json")
    assert is_locked_path(
        "plugins/config_backends/remote/vendor/conformance/config-fixtures.sha256"
    )
    assert is_locked_path("native/fts5_cjk/vendor/sqlite3.h")
    # a whole segment, not a substring: our own files still brand
    assert not is_locked_path("src/vendored_helpers.py")
    assert not is_locked_path("vendorized/config.yaml")


def test_vendored_paths_are_distinguishable_from_other_locked_paths():
    """The audits exempt vendored bytes, not every locked file.

    A lockfile's brand word may still be a first-party leak worth reporting, so
    the lock reason has to be nameable instead of inferred from ``is_locked_path``.
    """
    assert is_vendored_path("plugins/config_backends/remote/vendor/SOURCE.json")
    assert is_vendored_path("native/fts5_cjk/vendor/sqlite3.h")
    assert not is_vendored_path("package-lock.json")
    assert not is_vendored_path("assets/logo.png")
    assert not is_vendored_path("src/vendored_helpers.py")


def test_docs_svg_is_brandable():
    """Text SVGs under static/img/docs are brandable; binaries stay locked."""
    assert not is_locked_path("website/static/img/docs/cli-layout.svg")
    assert not is_locked_path("website/static/img/docs/session-recap.svg")
    assert is_locked_path("website/static/img/logo.png")


def test_case_sensitivity_embedded_word():
    """CamelCase tokens must not corrupt words where the token is a substring."""
    rules = BrandingRules()
    out = rules.transform_text("HermesAnalytics is hermes-analytics")
    assert "hermes" not in out.lower()
    assert "nastech" in out.lower()


def test_girl_mascot_renames_to_bantu():
    """nous-girl.jpg must brand to nastech-bantu.jpg — the nous->nastech
    prefix survives, only the girl->bantu mascot word is swapped."""
    rules = BrandingRules()
    assert rules.transform_path("apps/desktop/public/nous-girl.jpg") == \
        "apps/desktop/public/nastech-bantu.jpg"
    assert rules.transform_path("apps/bootstrap-installer/public/nous-girl.jpg") == \
        "apps/bootstrap-installer/public/nastech-bantu.jpg"
    out = rules.transform_text("the nous-girl mascot")
    assert "nastech-bantu" in out
    assert "nous-girl" not in out


def test_escape_prefixed_tokens_are_branded():
    """A token following an escape sequence must still be branded: `\bhermes`
    is a regex word-boundary + the token, `\xfehermes` is a hex byte + the
    token, `\nhermes` / `\r\nhermes` are line escapes + the token.  The
    escape's trailing letter (`b`, `e`, `n`) is not a word-adjacency blocker.
    """
    rules = BrandingRules()
    cases = {
        r"\bhermes": r"\bnastech",
        r"\bhermes\b": r"\bnastech\b",
        r"\nhermes": r"\nnastech",
        r"\r\nhermes": r"\r\nnastech",
        r"\xfehermes": r"\xfenastech",
        r"\x7fhermes": r"\x7fnastech",
        r"\xefhermes": r"\xefnastech",
        r"\u00e9hermes": r"\u00e9nastech",
        r"\u00cahermes": r"\u00canastech",
        r"\U0001f600hermes": r"\U0001f600nastech",
        r"\U0001f60ahermes": r"\U0001f60anastech",
        r"launchctl\s+...\bhermes[.\-]?gateway": r"launchctl\s+...\bnastech[.\-]?gateway",
        r"(r'\bhermes\s+update\b', 'update (restarts gateway)')":
            r"(r'\bnastech\s+update\b', 'update (restarts gateway)')",
    }
    for before, after in cases.items():
        got = rules.transform_text(before)
        assert got == after, (before, got, after)


def test_real_english_words_still_protected_after_escapes():
    """The escape exception must not open a hole for real words: a lowercase
    letter that is genuinely adjacent (not escape-prefixed) still blocks."""
    rules = BrandingRules()
    text = (
        r"nhermes venous xhermes autonomous "
        r"\nhermes_nous "
        r"not\hermes"
    )
    out = rules.transform_text(text)
    assert "nhermes" in out
    assert "venous" in out
    assert "xhermes" in out
    assert "autonomous" in out
    assert r"\nnastech_nastech" in out
    assert r"not\nastech" in out


def test_runner_labels_normalize_to_standard_hosted():
    rules = BrandingRules()
    text = (
        "runs-on: ubuntu-latest-96-core\n"
        "runs-on: ubuntu-latest-32-core\n"
        "runner: windows-latest-32-core\n"
        "runs-on: ubuntu-latest-32-arm-core\n"
        "runs-on: windows-latest-32-arm-core\n"
        "runs-on: macos-14-xlarge-core\n"  # no digits before core -> untouched
    )
    out = rules.transform_text(text)
    assert "runs-on: ubuntu-latest\n" in out
    assert "runner: windows-latest\n" in out
    assert "runs-on: ubuntu-24.04-arm\n" in out
    assert "-core" not in out.split("macos")[0]
    # the non-matching line survives verbatim
    assert "macos-14-xlarge-core" in out


def test_arm_windows_runner_keeps_arm64():
    # An arm64 Windows row is an architecture requirement. Landing it on the
    # x64 image builds a win32-x64 payload inside a win32-arm64 package, which
    # the bundle's own audit rejects, so it must take the standard arm64 tier.
    rules = BrandingRules()
    text = (
        "runs-on: windows-latest-32-arm-core\n"
        "runner: windows-latest-32-arm-core\n"
        "runs-on: ${{ inputs.arch == 'arm64' && 'windows-latest-32-arm-core' "
        "|| 'blacksmith-32vcpu-windows-2025' }}\n"
    )
    out = rules.transform_text(text)
    assert "runs-on: windows-11-arm\n" in out
    assert "runner: windows-11-arm\n" in out
    assert "&& 'windows-11-arm' || 'windows-latest' " in out
    # idempotent: a branded tree re-brands to itself
    assert rules.transform_text(out) == out


def test_blacksmith_runner_labels_normalize_to_standard_hosted():
    rules = BrandingRules()
    text = (
        "runs-on: blacksmith-4vcpu-ubuntu-2404\n"
        "runs-on: blacksmith-32vcpu-ubuntu-2404\n"
        "runs-on: blacksmith-2vcpu-ubuntu-2404\n"
        "runs-on: blacksmith-32vcpu-ubuntu-2404-arm\n"
        "runs-on: blacksmith-4vcpu-ubuntu-2404-arm\n"
        "runner: blacksmith-32vcpu-windows-2025\n"
        "runner: blacksmith-2vcpu-windows-2025\n"
        "runner: blacksmith-32vcpu-windows-2025-arm\n"
        "runs-on: blacksmith-12vcpu-macos-latest\n"
        "runs-on: blacksmith-6vcpu-macos-latest\n"
    )
    out = rules.transform_text(text)
    assert out == (
        "runs-on: ubuntu-latest\n"
        "runs-on: ubuntu-latest\n"
        "runs-on: ubuntu-latest\n"
        "runs-on: ubuntu-24.04-arm\n"
        "runs-on: ubuntu-24.04-arm\n"
        "runner: windows-latest\n"
        "runner: windows-latest\n"
        "runner: windows-11-arm\n"
        "runs-on: macos-latest\n"
        "runs-on: macos-latest\n"
    )
    # idempotent: re-branding the output changes nothing
    assert rules.transform_text(out) == out


def test_blacksmith_action_forks_normalize_to_upstream_actions():
    rules = BrandingRules()
    text = (
        "        uses: useblacksmith/checkout@"
        "25227e61ff9dafe400e22fa487b673eac4e4409a  # v1\n"
        "        uses: useblacksmith/setup-docker-builder@"
        "19215110ab936351210feebdfa5b440b4493e184  # v2\n"
        "        uses: useblacksmith/build-push-action@"
        "9b0579bbec7a6cad2f171596c57e7ac1e7658850  # v2\n"
    )
    out = rules.transform_text(text)
    assert out == (
        "        uses: actions/checkout@"
        "f548e57e544e1ff5a4c46bf1e1b8685f8e4a348a # v7.0.1\n"
        "        uses: docker/setup-buildx-action@"
        "8d2750c68a42422c14e847fe6c8ac0403b4cbd6f # v3\n"
        "        uses: docker/build-push-action@"
        "bcafcacb16a39f128d818304e6c9c0c18556b85f # v7.1.0\n"
    )
    assert "useblacksmith" not in out
    # idempotent
    assert rules.transform_text(out) == out


def test_blacksmith_words_in_prose_are_untouched():
    rules = BrandingRules()
    text = (
        "Blacksmith is a CI vendor; useblacksmith is mentioned without an "
        "action step, so neither is rewritten by the runner/action rules.\n"
    )
    assert rules.transform_text(text) == text


def test_runner_prose_is_untouched():
    rules = BrandingRules()
    text = "Measured on a 96-core EPYC, 377GB. The 32-core runner builds it."
    assert rules.transform_text(text) == text


def test_large_runner_test_workers_normalize_to_standard_runner_fanout():
    rules = BrandingRules()
    text = (
        "          NASTECH_TEST_WORKERS: 96  # upstream larger runner\n"
        "          NASTECH_TEST_WORKERS: 32  # upstream later moved to 32-core\n"
    )
    out = rules.transform_text(text)
    assert out == (
        "          NASTECH_TEST_WORKERS: 8  # upstream larger runner\n"
        "          NASTECH_TEST_WORKERS: 8  # upstream later moved to 32-core\n"
    )
    assert rules.transform_text(out) == out


def test_unrelated_worker_values_are_untouched():
    rules = BrandingRules()
    # At-or-below the standard-runner ceiling stays put; other variables are
    # never touched.  (Quoted / expression worker counts are likewise left
    # alone; see test_large_runner_test_workers_normalize_to_standard_runner_fanout.)
    text = "NASTECH_TEST_WORKERS: 6\nOTHER_WORKERS: 96\n"
    assert rules.transform_text(text) == text


def test_full_python_test_timeout_normalizes_to_40_minutes_only():
    rules = BrandingRules()
    text = (
        "jobs:\n"
        "  test:\n"
        "    name: Run tests\n"
        "    runs-on: ubuntu-latest-96-core\n"
        "    timeout-minutes: 30\n"
        "  e2e:\n"
        "    name: Run tests\n"
        "    timeout-minutes: 30\n"
        "  unrelated:\n"
        "    name: e2e\n"
        "    timeout-minutes: 30\n"
    )
    out = rules.transform_text(text)
    assert "name: Run tests\n    runs-on: ubuntu-latest\n    timeout-minutes: 40" in out
    assert out.count("name: Run tests\n") == 2
    assert out.count("timeout-minutes: 40") == 2
    assert "name: e2e\n    timeout-minutes: 30" in out


def test_os_matrix_workers_cap_to_the_free_runner_cores():
    rules = BrandingRules()
    line = (
        "          NASTECH_TEST_WORKERS: ${{ matrix.marker == 'windows' "
        "&& (runner.arch == 'ARM64' && '16' || '16') || '' }}\n"
    )
    out = rules.transform_text(line)
    assert out == (
        "          NASTECH_TEST_WORKERS: "
        "${{ matrix.marker == 'windows' && '4' || '2' }}\n"
    )
    assert rules.transform_text(out) == out


def test_os_matrix_workers_rewrites_a_quoted_fallback_too():
    rules = BrandingRules()
    # Upstream narrowing the ``||`` arm to a literal must not turn the rule into
    # a silent no-op: the macOS rows still get this fork's cap.
    line = "          NASTECH_TEST_WORKERS: ${{ matrix.marker == 'windows' && '16' || '9' }}\n"
    assert rules.transform_text(line) == (
        "          NASTECH_TEST_WORKERS: ${{ matrix.marker == 'windows' && '4' || '2' }}\n"
    )


def test_runner_normalization_is_idempotent_and_analyzer_stable():
    rules = BrandingRules()
    once = rules.transform_text("runs-on: ubuntu-latest-96-core")
    assert rules.transform_text(once) == once == "runs-on: ubuntu-latest"
