from unittest import mock

from hundredways.research import CATALOG, Idea, research, search_github


def test_catalog_has_real_entries():
    assert len(CATALOG) >= 15
    assert all(i.source == "catalog" for i in CATALOG)
    assert all(i.title and i.summary for i in CATALOG)


def test_research_offline_filters_catalog():
    ideas = research("rename", live=False)
    assert ideas
    assert all(i.source == "catalog" for i in ideas)


def test_research_no_match_falls_back_to_catalog():
    ideas = research("zzzz-nonsense-topic", include_catalog=True, live=False)
    assert ideas == CATALOG


def test_search_github_parses_payload():
    payload = {"items": [{"full_name": "a/b", "html_url": "https://x", "description": "d"}]}
    with mock.patch(
        "hundredways.research.urllib.request.urlopen",
        return_value=mock.Mock(
            __enter__=lambda *a: mock.Mock(read=lambda: __import__("json").dumps(payload).encode()),
            __exit__=lambda *a: None,
        ),
    ):
        ideas = search_github("fork sync", limit=1, max_results=1)
    assert len(ideas) == 1
    assert ideas[0].source == "github"
    assert ideas[0].title == "a/b"
    assert ideas[0].url == "https://x"


def test_search_github_failure_degrades():
    with mock.patch("hundredways.research.urllib.request.urlopen", side_effect=OSError("no net")):
        ideas = search_github("anything")
    assert ideas and ideas[0].source == "github"
    assert "search failed" in ideas[0].title


def test_idea_shape():
    idea = Idea("catalog", "t", "u", "s", ("tag",))
    assert idea.source == "catalog"
    assert idea.tags == ("tag",)
