"""A plugin catalog entry's ``subdir`` is a directory inside somebody else's repo.

The engine already refuses to rename a repository it does not own, because the
branded URL 404s.  The same object problem exists one level deeper and had not
been noticed: an entry says *which repository to clone* and *which directory
inside it holds the plugin*, and branding the second half points the installer
at a path that has never existed in the first.

Measured 2026-10-27 against the live repositories of every catalog entry whose
``subdir`` branding changes -- 85 of the 861 shipped entries carry a branded
directory, and not one of them sits in a repository the fork publishes.  Probing
the 75 that resolve to a GitHub repository: every one answers the inherited
directory and 404s the branded one.  ``plastic-labs/honcho`` carries
``hermes-plugin-honcho`` at the pinned SHA and has never heard of
``nastech-plugin-honcho``, so ``nastech plugins install honcho`` clones a real
repository, looks for a directory that is not in it, and fails.
"""

from hundredways.brandscan import _allowed
from hundredways.rules import (
    BrandingRules,
    catalog_entry_repo_url,
    preserved_catalog_subdir_spans,
)
from hundredways.updates import brand_tree
from hundredways.weekly_sync import _allowed_brand_occurrence

HONCHO = (
    "name: honcho\n"
    "repo: https://github.com/plastic-labs/honcho\n"
    "sha: e2d2aba182862deeb846e5cd4a30a41bce0ed82a\n"
    "subdir: hermes-plugin-honcho\n"
    'description: "Memory infrastructure for the Hermes agent."\n'
    "maintainer: plastic-labs\n"
    "tier: community\n"
)


def test_a_directory_inside_a_repository_we_do_not_run_stays_whole():
    """The clone target is theirs, so the path inside it is theirs too."""
    branded = BrandingRules().transform_text(HONCHO)

    assert "subdir: hermes-plugin-honcho\n" in branded
    assert "name: honcho\n" in branded
    assert 'description: "Memory infrastructure for the Nastech agent."' in branded
    assert "maintainer: plastic-labs" in branded


def test_a_directory_inside_a_repository_we_publish_is_ours_to_rename():
    """The test is ownership at both ends, not the presence of a brand word.

    ``NousResearch/hermes-official-plugins`` moves into an org the fork runs, so
    a directory inside the fork's copy is a directory the fork can create, and
    leaving the inherited name would aim the installer at the wrong place.
    """
    entry = (
        "name: accent-picker\n"
        "repo: https://github.com/NousResearch/hermes-official-plugins\n"
        "subdir: hermes-accent-picker\n"
    )

    branded = BrandingRules().transform_text(entry)

    assert "repo: https://github.com/NastechResearch/nastech-official-plugins" in branded
    assert "subdir: nastech-accent-picker" in branded


def test_a_catalog_file_that_names_two_repositories_is_not_guessed_at():
    """Without one unambiguous repository there is no owner to test."""
    entry = (
        "repo: https://github.com/plastic-labs/honcho\n"
        "subdir: hermes-plugin-honcho\n"
        "repo: https://github.com/NousResearch/hermes-official-plugins\n"
        "subdir: hermes-accent-picker\n"
    )

    assert catalog_entry_repo_url(entry) is None
    assert "subdir: nastech-plugin-honcho" in BrandingRules().transform_text(entry)


def test_a_quoted_or_empty_subdir_is_handled_without_reaching_for_the_wrong_text():
    rules = BrandingRules()

    quoted = rules.transform_text(
        "repo: https://github.com/plastic-labs/honcho\n"
        'subdir: "hermes-plugin-honcho"\n'
    )
    empty = rules.transform_text(
        "repo: https://github.com/SmelterLabs/hermes-account-switcher\nsubdir: \"\"\n"
    )

    assert 'subdir: "hermes-plugin-honcho"\n' in quoted
    assert empty == (
        "repo: https://github.com/SmelterLabs/hermes-account-switcher\nsubdir: \"\"\n"
    )


def test_a_preserved_subdir_is_a_fixed_point_of_the_brand_pass():
    """Branding the candidate again must not move the directory a second time."""
    once = BrandingRules().transform_text(HONCHO)

    assert BrandingRules().transform_text(once) == once


def test_a_line_level_subdir_hit_is_inherited_text_where_the_transform_preserves_it():
    """Both brand audits agree a preserved ``subdir`` is not a leak to fix.

    The exemption carries the document's single ``repo:`` line, the same fact
    the transform keys on, so it holds for a catalog entry and for a docs page
    that quotes one, and it does not hold where the transform preserves nothing.
    """
    line = "subdir: hermes-plugin-honcho"
    repo = "https://github.com/plastic-labs/honcho"

    assert preserved_catalog_subdir_spans(line)
    assert _allowed("plugin-catalog/honcho.yaml", "hermes", line, repo) is True
    assert _allowed("website/docs/user-guide/features/honcho.md", "hermes", line, repo) is True
    assert _allowed("plugin-catalog/honcho.yaml", "hermes", line, repo) is (
        _allowed_brand_occurrence("hermes", line, "plugin-catalog/honcho.yaml", repo)
    )
    # No single repository means the transform preserves nothing here, so the
    # same text is a leak the gate still has to catch.
    assert _allowed("plugin-catalog/honcho.yaml", "hermes", line) is False
    assert _allowed_brand_occurrence("hermes", line, "plugin-catalog/honcho.yaml") is False


def test_catalog_yaml_spellings_the_rule_must_still_understand():
    """A trailing comment, an apostrophe, and a quoted URL are legal catalog YAML."""
    rules = BrandingRules()

    commented = rules.transform_text(
        "repo: https://github.com/plastic-labs/honcho\n"
        "subdir: hermes-plugin-honcho  # the plugin directory\n"
    )
    apostrophe = rules.transform_text(
        "repo: https://github.com/plastic-labs/honcho\n"
        "subdir: team's/hermes-plugin\n"
    )
    quoted_repo = rules.transform_text(
        'repo: "https://github.com/plastic-labs/honcho"  # pinned\n'
        "subdir: hermes-plugin-honcho\n"
    )

    assert "subdir: hermes-plugin-honcho  # the plugin directory" in commented
    assert "subdir: team's/hermes-plugin" in apostrophe
    assert 'repo: "https://github.com/plastic-labs/honcho"' in quoted_repo
    assert "subdir: hermes-plugin-honcho" in quoted_repo


def test_a_subdir_line_holding_a_preserved_span_is_never_masked_whole():
    """Re-masking a stashed line would restore a placeholder into published text."""
    text = (
        "repo: https://github.com/plastic-labs/honcho\n"
        "subdir: hermes-x/github.com/foo/hermes-bar\n"
    )

    branded = BrandingRules().transform_text(text)

    assert "\ue000" not in branded and "\ue001" not in branded
    assert "subdir: nastech-x/github.com/foo/hermes-bar" in branded


def test_brand_tree_ships_a_catalog_entry_the_installer_can_actually_resolve(
    tmp_path,
):
    """End to end: the published entry clones a real repo at a real directory."""
    source = tmp_path / "upstream" / "plugin-catalog"
    target = tmp_path / "fork"
    source.mkdir(parents=True)
    (source / "honcho.yaml").write_text(HONCHO, encoding="utf-8")

    brand_tree(str(tmp_path / "upstream"), str(target))

    shipped = (target / "plugin-catalog" / "honcho.yaml").read_text(encoding="utf-8")
    assert "subdir: hermes-plugin-honcho\n" in shipped
    assert "nastech-plugin-honcho" not in shipped
    assert "repo: https://github.com/plastic-labs/honcho" in shipped
