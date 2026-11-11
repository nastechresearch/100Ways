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


def test_urls_are_verbatim_while_brand_names_outside_them_change():
    rules = BrandingRules()
    text = (
        "Project Hermes by Nous Research.\n"
        "git: https://github.com/NousResearch/hermes-agent.git?ref=main\n"
        "bare: github.com/NousResearch/hermes-agent\n"
        "ssh: git@github.com:NousResearch/hermes-agent.git\n"
        "link: [Hermes docs](https://nousresearch.com/Hermes)\n"
    )
    expected = (
        "Project Nastech by Nastech Research.\n"
        "git: https://github.com/NousResearch/hermes-agent.git?ref=main\n"
        "bare: github.com/NousResearch/hermes-agent\n"
        "ssh: git@github.com:NousResearch/hermes-agent.git\n"
        "link: [Nastech docs](https://nousresearch.com/Hermes)\n"
    )
    assert rules.transform_text(text) == expected


def test_name_transform_is_idempotent_and_paths_only_change_brand_tokens():
    rules = BrandingRules()
    branded = rules.transform_text("Hermes-Agent NousResearch hermes_cli")
    assert branded == "Nastech-Agent NastechResearch nastech_cli"
    assert rules.transform_text(branded) == branded
    assert rules.transform_path("hermes-agent/Hermes.md") == "nastech-agent/Nastech.md"


def test_path_transforms():
    rules = BrandingRules()
    assert "nastech" in rules.transform_path("tools/hermes_runner.py").lower()


def test_custom_token():
    rules = BrandingRules(tokens=[TokenRule("Acme", "Widget")])
    assert "Widget" in rules.transform_text("Acme product")
    assert "Acme" not in rules.transform_text("Acme product")


def test_branding_applies_core_glyph_and_mascot_identity_mappings():
    rules = BrandingRules()
    text = "Hermes ⚕ ☤ nous-girl; nous-cat"
    assert rules.transform_text(text) == "Nastech 𓄃 𓄃 nastech-bantu; nastech-cat"
    url = "https://example.org/hermes/☤/nous-girl"
    assert rules.transform_text(url) == url


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


def test_girl_mascot_path_maps_to_nastech_bantu_art():
    """The established Bantu mascot name is a core brand identity mapping."""
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


def test_branding_does_not_rewrite_ci_configuration_or_dependencies():
    rules = BrandingRules()
    text = (
        "runs-on: ubuntu-latest-96-core\n"
        "runs-on: windows-latest-32-arm-core\n"
        "runs-on: blacksmith-4vcpu-ubuntu-2404\n"
        "uses: useblacksmith/checkout@25227e61ff9dafe400e22fa487b673eac4e4409a\n"
        "NASTECH_TEST_WORKERS: 96\n"
        "timeout-minutes: 30\n"
        "dependency = \"hermes-parser\"\n"
    )
    assert rules.transform_text(text) == text
