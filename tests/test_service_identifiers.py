"""Branding an object that lives inside a service we do not run breaks it.

The measured case is the fork's DocSearch triple.  The brand pass renamed the
index while the app id and search-only key stayed inherited, so the published
site asked app ``APPID`` for an index that app has never heard of: the search
box 404s for every visitor and the docs-search monitor has been red on every
run since.  Renaming somebody else's repository yields the same class of defect
one layer up, and the repository rule already knows how to avoid it -- these
tests pin that the same reasoning now covers a service's object list.
"""

import pytest

from hundredways.brandscan import _allowed
from hundredways.rules import (
    BrandingRules,
    preserved_algolia_index_spans,
    preserved_external_url_spans,
    preserved_url_link_label_spans,
    provisioned_algolia_indices,
    transform_strict_metadata_text,
)
from hundredways.weekly_sync import _allowed_brand_occurrence

# Synthetic app id on purpose: the rule keys off the index NAME, and the real
# one is somebody else's service handle with no business in this repo.
CONFIG = (
    "    algolia: {\n"
    "      appId: 'APPID12345',\n"
    "      indexName: 'hermes docs',\n"
    "      contextualSearch: true,\n"
    "    },\n"
)

PROVISIONED = "hermes docs"


@pytest.fixture(autouse=True)
def _no_owner_index_hook(monkeypatch):
    """Keep ``HUNDREDWAYS_ALGOLIA_INDICES`` out of the default expectations."""
    monkeypatch.delenv("HUNDREDWAYS_ALGOLIA_INDICES", raising=False)


@pytest.mark.parametrize(
    "line",
    [
        "      indexName: 'hermes docs',",
        '      indexName: "hermes docs",',
        '      "indexName": "hermes docs",',
        '  "indexName":"hermes docs"',
    ],
)
def test_a_provisioned_index_name_survives_branding(line):
    assert preserved_algolia_index_spans(line)
    assert BrandingRules().transform_text(line) == line


def test_the_whole_docsearch_config_is_left_intact_but_prose_around_it_is_branded():
    text = CONFIG + "  # Hermes reads the hermes docs index for its own site.\n"

    branded = BrandingRules().transform_text(text)

    assert CONFIG in branded
    assert "Nastech reads the nastech docs index" in branded


@pytest.mark.parametrize(
    "line",
    [
        "      indexName: 'hermes wiki',",
        "      indexName: 'hermes docs.tmp',",
        "      indexName: 'NousResearch hermes docs',",
    ],
)
def test_an_index_nobody_provisioned_is_branded_like_any_other_string(line):
    """Protection follows evidence, so an unverified name is not special."""
    assert not preserved_algolia_index_spans(line)
    assert BrandingRules().transform_text(line) != line


@pytest.mark.parametrize(
    "line",
    [
        "indexName = 'hermes docs';",
        "indexName: hermes docs,",
        "otherIndexName: 'hermes docs',",
    ],
)
def test_forms_the_engine_does_not_recognise_are_left_to_ordinary_branding(line):
    """The matched set is exactly the ``key: "value"`` spellings a config uses.

    An unquoted value has no boundary a rule could trust, and a longer key that
    merely ends in ``indexName`` is somebody else's field.
    """
    assert not preserved_algolia_index_spans(line)
    assert "nastech docs" in BrandingRules().transform_text(line)


def test_prose_about_the_docs_is_still_branded():
    assert BrandingRules().transform_text("Search the hermes docs.\n") == (
        "Search the nastech docs.\n"
    )
    assert not preserved_algolia_index_spans("Search the hermes docs.")


def test_a_fork_with_an_index_of_its_own_gets_it_back(monkeypatch):
    """The owner's env hook is the whole difference between 404 and branding.

    Once the fork has created and crawled ``nastech docs`` in an app its own
    credentials reach, the inherited name is a leak again and must go.
    """
    monkeypatch.setenv("HUNDREDWAYS_ALGOLIA_INDICES", "NASTECH DOCS, other index")

    assert provisioned_algolia_indices() >= {PROVISIONED, "nastech docs", "other index"}
    line = "      indexName: 'hermes docs',"
    assert not preserved_algolia_index_spans(line, BrandingRules())
    assert BrandingRules().transform_text(line) == "      indexName: 'nastech docs',"


def test_preserving_the_index_name_leaves_the_other_service_identifier_alone():
    """Only the object name is exempt, and only where a service owns it."""
    text = "      indexName: 'hermes docs',  // hermes docs for NousResearch users\n"

    branded = BrandingRules().transform_text(text)

    assert "indexName: 'hermes docs'" in branded
    assert "NastechResearch users" in branded


def test_a_preserved_foreign_repo_and_a_preserved_index_share_one_line():
    """Both stashes restore, so the sentinel numbering must not collide."""
    line = (
        "see https://github.com/lagoon-labs/hermes-s3-plugin "
        "and indexName: 'hermes docs',\n"
    )

    assert BrandingRules().transform_text(line) == line


def test_the_preserved_spelling_is_a_branding_fixed_point():
    once = BrandingRules().transform_text(CONFIG)
    assert BrandingRules().transform_text(once) == once


def test_generated_reports_keep_the_zero_brand_policy():
    """The strict helper is for records we author, so it renames everything."""
    assert "nastech docs" in transform_strict_metadata_text(CONFIG)


def test_brand_tree_ships_a_search_config_the_deployed_site_can_actually_query(tmp_path):
    """End to end: the stage that publishes the tree must not split the triple."""
    from hundredways.updates import brand_tree

    source = tmp_path / "upstream"
    target = tmp_path / "fork"
    (source / "website").mkdir(parents=True)
    (source / "website" / "docusaurus.config.ts").write_text(CONFIG, encoding="utf-8")
    (source / "README.md").write_text(
        "Hermes searches the hermes docs for NousResearch users.\n", encoding="utf-8"
    )

    brand_tree(str(source), str(target))

    shipped = (target / "website" / "docusaurus.config.ts").read_text(encoding="utf-8")
    assert shipped == CONFIG
    assert (target / "README.md").read_text(encoding="utf-8") == (
        "Nastech searches the nastech docs for NastechResearch users.\n"
    )


@pytest.mark.parametrize(
    "path, word",
    [
        ("website/docusaurus.config.ts", "hermes"),
        ("website/docusaurus.config.ts", "docs"),
    ],
)
def test_the_two_brand_audits_agree_that_a_provisioned_index_is_not_a_leak(path, word):
    line = "      indexName: 'hermes docs',"

    allowed = _allowed(path, word, line)
    assert allowed is True
    assert allowed is _allowed_brand_occurrence(word, line, path)


def test_the_two_brand_audits_still_flag_the_same_name_outside_a_config():
    line = "The hermes docs index is the site search."

    assert _allowed("README.md", "hermes", line) is False
    assert _allowed_brand_occurrence("hermes", line, "README.md") is False


def test_the_audit_exemption_follows_the_owner_index_hook(monkeypatch):
    """With a fork-owned index the inherited name is a leak, not a fixed point."""
    monkeypatch.setenv("HUNDREDWAYS_ALGOLIA_INDICES", "nastech docs")
    line = "      indexName: 'hermes docs',"

    assert _allowed("website/docusaurus.config.ts", "hermes", line) is False
    assert _allowed_brand_occurrence("hermes", line, "website/docusaurus.config.ts") is False


# Each row is a URL a human clicks, measured 2026-10-27 by fetching both
# spellings: the inherited path answers 200 on a service the fork does not run
# and the branded one 4xx's (or, for the fragment rows, is an anchor the page
# does not carry).
MEASURED_EXTERNAL_LINES = [
    "https://docs.honcho.dev/v3/guides/integrations/hermes",
    "https://hindsight.vectorize.io/sdks/integrations/hermes",
    "https://botcabinet.com/hermes/manual/",
    "https://docs.apify.com/integrations/hermes-agent",
    "https://docs.cortexlayer.net/docs/agents/hermes",
    "https://docs.openmail.sh/integrations/hermes",
    "https://pidgeon.ai/docs/hermes",
    "https://mybot.farm/install/hermes",
    "https://linchpin.studio/school-of-hermes/manual/",
    "https://patrykkopycinski.github.io/hermes-plugin-agent-memory-es/",
    "https://briancaffey.github.io/hermes-otel/",
    # A subdomain of a listed service root, and its own connect deep-link: the
    # guide that documents it is ``supermemory.ai/docs/integrations/hermes``
    # (200) while ``.../integrations/nastech`` is a 404 (probed 2026-10-27).
    "https://app.supermemory.ai/integrations?connect=hermes",
]


@pytest.fixture(autouse=True)
def _no_owned_service_hosts(monkeypatch):
    """Keep ``HUNDREDWAYS_OWNED_URL_HOSTS`` out of the default expectations."""
    monkeypatch.delenv("HUNDREDWAYS_OWNED_URL_HOSTS", raising=False)


@pytest.mark.parametrize("line", MEASURED_EXTERNAL_LINES)
def test_a_path_on_a_host_the_fork_does_not_run_survives_branding(line):
    assert preserved_external_url_spans(line)
    assert BrandingRules().transform_text(line) == line


@pytest.mark.parametrize(
    "path",
    [
        "plugin-catalog/bot-cabinet-v2.yaml",
        "website/docs/user-guide/features/memory-providers.md",
    ],
)
def test_the_two_brand_audits_agree_that_a_preserved_url_is_not_a_leak(path):
    line = (
        "https://botcabinet.com/hermes/manual/ docs "
        "https://docs.honcho.dev/v3/guides/integrations/hermes"
    )
    word = "hermes"

    allowed = _allowed(path, word, line)
    assert allowed is True
    assert allowed is _allowed_brand_occurrence(word, line, path)


def test_the_two_brand_audits_still_flag_a_brand_word_outside_a_preserved_url():
    """The exemption is the URL, not the line: prose next to it is still ours."""
    line = "The hermes manual lives at https://openrouter.ai/pricing"

    assert preserved_external_url_spans(line) == ()
    assert _allowed("README.md", "hermes", line) is False
    assert _allowed_brand_occurrence("hermes", line, "README.md") is False


def test_a_host_the_owner_has_provisioned_is_brandable_again(monkeypatch):
    """The env hook is what the owner flips once a Nastech object exists."""
    monkeypatch.setenv("HUNDREDWAYS_OWNED_URL_HOSTS", "docs.honcho.dev")
    line = "read https://docs.honcho.dev/v3/guides/integrations/hermes"

    assert preserved_external_url_spans(line) == ()
    assert BrandingRules().transform_text(line) == (
        "read https://docs.honcho.dev/v3/guides/integrations/nastech"
    )


def test_brand_tree_ships_the_third_party_links_a_human_clicks(tmp_path):
    """End to end: publishing the tree must not break a catalog entry's docs."""
    from hundredways.updates import brand_tree

    source = tmp_path / "upstream"
    target = tmp_path / "fork"
    (source / "plugin-catalog").mkdir(parents=True)
    (source / "plugin-catalog" / "apify.yaml").write_text(
        "name: Hermes Scraper\n"
        "description: The Hermes agent integration by Apify.\n"
        "docs_url: https://docs.apify.com/integrations/hermes-agent\n",
        encoding="utf-8",
    )

    brand_tree(str(source), str(target))

    shipped = (target / "plugin-catalog" / "apify.yaml").read_text(encoding="utf-8")
    assert "docs_url: https://docs.apify.com/integrations/hermes-agent\n" in shipped
    assert "name: Nastech Scraper" in shipped
    assert "The Nastech agent integration by Apify" in shipped


def test_a_link_label_that_prints_the_address_it_links_to_keeps_both_halves():
    """The label is the address written a second time, not prose about it."""
    rules = BrandingRules()
    text = (
        "- Hosted: API key from "
        "[app.supermemory.ai/integrations?connect=hermes]"
        "(http://app.supermemory.ai/integrations?connect=hermes)\n"
    )

    assert rules.transform_text(text) == text
    assert preserved_url_link_label_spans(text.splitlines()[0])
    assert rules.transform_text(rules.transform_text(text)) == text


def test_prose_naming_us_beside_a_preserved_link_is_still_branded():
    """Only an address-shaped label whose host matches its href is protected."""
    rules = BrandingRules()

    prose = "see [the Hermes integration](https://docs.honcho.dev/v3/guides/integrations/hermes)\n"
    assert "the Nastech integration" in rules.transform_text(prose)

    other_host = "[hermes docs](https://docs.honcho.dev/v3/guides/integrations/hermes)\n"
    assert "[nastech docs]" in rules.transform_text(other_host)


def test_the_label_exemption_is_the_same_in_both_brand_audits():
    line = (
        "[app.supermemory.ai/integrations?connect=hermes]"
        "(http://app.supermemory.ai/integrations?connect=hermes)"
    )
    assert _allowed("plugins/memory/supermemory/README.md", "hermes", line) is True
    assert _allowed_brand_occurrence(
        "hermes", line, "plugins/memory/supermemory/README.md"
    ) is True
    # the same word in prose is still a leak
    prose = "[the hermes integration](https://docs.honcho.dev/v3/x)"
    assert _allowed("README.md", "hermes", prose) is False
