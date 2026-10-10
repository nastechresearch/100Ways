from hundredways.secrets import EXTENDED_SECRET_PATTERNS, SECRET_PATTERNS, find_secrets, redact


def test_extended_set_is_a_superset_of_the_gating_set():
    assert set(SECRET_PATTERNS) <= set(EXTENDED_SECRET_PATTERNS)
    assert len(EXTENDED_SECRET_PATTERNS) > len(SECRET_PATTERNS)


def test_extended_detects_documented_provider_shapes():
    shapes = (
        "sk-" + "a" * 32,                       # OpenAI-style
        "xoxb-1234567890-abcdefghij",           # Slack
        "AIza" + "A" * 35,                      # Google API key
        "-----BEGIN RSA PRIVATE KEY-----",       # PEM private key
        "ghp_" + "a" * 30,                      # GitHub (base set)
        "AKIA" + "A" * 16,                      # AWS (base set)
    )
    for shape in shapes:
        assert find_secrets(f"value = {shape}"), shape


def test_gating_set_leaves_ordinary_prose_alone():
    assert find_secrets("synchronous messaging is anonymous", SECRET_PATTERNS) == []
    assert find_secrets("the skiff and the task are done", SECRET_PATTERNS) == []


def test_redact_masks_tokens_and_preserves_the_rest():
    token = "sk-" + "b" * 32
    rendered = redact(f"token={token} and a plain trailing sentence")
    assert token not in rendered
    assert "[REDACTED]" in rendered
    assert rendered.endswith("and a plain trailing sentence")
