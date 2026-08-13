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


def test_path_transforms():
    rules = BrandingRules()
    assert "nastech" in rules.transform_path("tools/hermes_runner.py").lower()


def test_custom_token():
    rules = BrandingRules(tokens=[TokenRule("Acme", "Widget")])
    assert "Widget" in rules.transform_text("Acme product")
    assert "Acme" not in rules.transform_text("Acme product")


def test_locked_paths():
    assert is_locked_path("assets/logo.png")
    assert is_locked_path("tests/package-lock.json")
    assert is_locked_path("static/favicon.ico")
    assert not is_locked_path("src/main.py")
    assert not is_locked_path("docs/guide.md")


def test_case_sensitivity_embedded_word():
    """CamelCase tokens must not corrupt words where the token is a substring."""
    rules = BrandingRules()
    out = rules.transform_text("HermesAnalytics is hermes-analytics")
    assert "hermes" not in out.lower()
    assert "nastech" in out.lower()
