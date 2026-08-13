import json

from hundredways.notifier import Notification, Notifier
from hundredways.watcher import Watcher, WatcherConfig
from tests.conftest import commit, git, git_repo
from tests.test_port import _make_fork_with_upstream_lead


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def notify(self, n: Notification) -> None:
        self.calls.append(n)


def _watcher(repo, notifier, state_file):
    return Watcher(
        WatcherConfig(
            repo=repo,
            upstream="upstream/main",
            fetch=False,
            state_file=state_file,
        ),
        notifier=notifier,
    )


def test_cycle_reports_new_commits(git_repo, tmp_path):
    _make_fork_with_upstream_lead(git_repo)
    notifier = FakeNotifier()
    watcher = _watcher(git_repo, notifier, str(tmp_path / "state.json"))
    event = watcher.cycle()
    assert event is not None
    assert event.kind == "new-commits"
    assert event.commit_count == 1
    assert len(notifier.calls) == 1
    assert notifier.calls[0].kind == "watch"
    assert watcher._load_state()["last_upstream_head"]


def test_second_cycle_no_duplicate_notification(git_repo, tmp_path):
    _make_fork_with_upstream_lead(git_repo)
    notifier = FakeNotifier()
    watcher = _watcher(git_repo, notifier, str(tmp_path / "state.json"))
    watcher.cycle()
    assert len(notifier.calls) == 1
    watcher.cycle()
    assert len(notifier.calls) == 1  # same head: no new notification


def test_state_persisted_between_cycles(git_repo, tmp_path):
    _make_fork_with_upstream_lead(git_repo)
    watcher = _watcher(git_repo, FakeNotifier(), str(tmp_path / "state.json"))
    watcher.cycle()
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["last_upstream_head"] == git(git_repo, "rev-parse", "upstream/main")


def test_notifier_renders_markdown():
    n = Notification("New commits", "body here", level="warn", kind="watch")
    rendered = n.render()
    assert "New commits" in rendered
    assert "body here" in rendered
    assert "*New commits*" in rendered
