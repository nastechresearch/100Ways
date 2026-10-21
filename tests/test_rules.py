from hundredways.rules import BrandingRules, TokenRule, is_locked_path


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


def test_one_identifier_is_branded_under_every_spelling_it_uses():
    """A file carrying both a bare identifier and its URL must brand both.

    The fork's release tests build a URL *from* a repository string and compare it
    with a URL literal: ``target_repository="example/hermes-agent"`` is what turns
    into ``https://github.com/example/hermes-agent/archive/<sha>.zip``.  The bare
    form is ordinary token-rule text and was branded in place while the URL form
    was preserved verbatim, so the generated file asserted ``nastech-agent``
    against ``hermes-agent`` and failed.  A repository identifier is therefore
    only preserved when the text never spells it bare.
    """
    rules = BrandingRules()
    both = (
        '    repository = "example/hermes-agent"\n'
        '    url = "https://github.com/example/hermes-agent/releases/tag/v1"\n'
    )
    branded = rules.transform_text(both)
    assert branded == (
        '    repository = "example/nastech-agent"\n'
        '    url = "https://github.com/example/nastech-agent/releases/tag/v1"\n'
    )
    assert "hermes" not in branded
    assert rules.transform_text(branded) == branded
    # a trailing ``.git`` belongs to the identifier, so it must not hide the bare
    # spelling sitting two lines away (the real shape of the fixture fork in
    # ``tests/nastech_cli/test_source_release_channels.py``).
    dotted = (
        '    url = "https://github.com/Fixture/hermes-agent.git"\n'
        '    target_repository = "Fixture/hermes-agent"\n'
    )
    assert rules.transform_text(dotted) == (
        '    url = "https://github.com/Fixture/nastech-agent.git"\n'
        '    target_repository = "Fixture/nastech-agent"\n'
    )


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
