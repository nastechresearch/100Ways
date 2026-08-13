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
