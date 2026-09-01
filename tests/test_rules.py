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
        "runs-on: macos-14-xlarge-core\n"  # no digits before core -> untouched
    )
    out = rules.transform_text(text)
    assert "runs-on: ubuntu-latest\n" in out
    assert "runner: windows-latest\n" in out
    assert "runs-on: ubuntu-24.04-arm\n" in out
    assert "-core" not in out.split("macos")[0]
    # the non-matching line survives verbatim
    assert "macos-14-xlarge-core" in out


def test_runner_prose_is_untouched():
    rules = BrandingRules()
    text = "Measured on a 96-core EPYC, 377GB. The 32-core runner builds it."
    assert rules.transform_text(text) == text


def test_large_runner_test_workers_normalize_to_standard_runner_fanout():
    rules = BrandingRules()
    text = "          NASTECH_TEST_WORKERS: 96  # upstream larger runner\n"
    out = rules.transform_text(text)
    assert out == "          NASTECH_TEST_WORKERS: 8  # upstream larger runner\n"
    assert rules.transform_text(out) == out


def test_unrelated_worker_values_are_untouched():
    rules = BrandingRules()
    text = "NASTECH_TEST_WORKERS: 16\nOTHER_WORKERS: 96\n"
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
        "    name: e2e\n"
        "    timeout-minutes: 30\n"
    )
    out = rules.transform_text(text)
    assert "name: Run tests\n    runs-on: ubuntu-latest\n    timeout-minutes: 40" in out
    assert "name: e2e\n    timeout-minutes: 30" in out


def test_runner_normalization_is_idempotent_and_analyzer_stable():
    rules = BrandingRules()
    once = rules.transform_text("runs-on: ubuntu-latest-96-core")
    assert rules.transform_text(once) == once == "runs-on: ubuntu-latest"
