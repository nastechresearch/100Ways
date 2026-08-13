import subprocess
import pytest

GIT = ["git"]


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([*GIT, "-C", str(repo), "init", "-q"], check=True)
    subprocess.run([*GIT, "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run([*GIT, "-C", str(repo), "config", "user.name", "t"], check=True)
    return str(repo)


def git(repo, *args):
    out = subprocess.run([*GIT, "-C", repo, *args], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def commit(repo, files, message, branch="master"):
    for path, content in files.items():
        p = repo + "/" + path
        import os
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(content)
        subprocess.run([*GIT, "-C", repo, "add", path], check=True)
    subprocess.run(
        [*GIT, "-C", repo, "commit", "-q", "-m", message, "--allow-empty"], check=True
    )
    return git(repo, "rev-parse", "HEAD")


def diverged(git_repo, up_branch="upstream/main", na_branch="nastech/main"):
    """Two branches diverging from a seeded base; returns (up_tip, na_tip)."""
    commit(git_repo, {"seed.txt": "seed"}, "seed")
    git(git_repo, "checkout", "-q", "-b", up_branch)
    up = commit(git_repo, {}, "upstream base")
    git(git_repo, "checkout", "-q", "master")
    git(git_repo, "checkout", "-q", "-b", na_branch, up_branch)
    na = commit(git_repo, {}, "nastech base")
    git(git_repo, "checkout", "-q", "master")
    return up, na


def commit_on(repo, branch, files, message):
    """Check out ``branch``, commit files, return the new sha, restore master."""
    git(repo, "checkout", "-q", branch)
    sha = commit(repo, files, message)
    git(repo, "checkout", "-q", "master")
    return sha


def commit_bytes(repo, branch, path, data, message):
    """Check out ``branch``, write raw bytes to ``path``, commit, restore master."""
    git(repo, "checkout", "-q", branch)
    p = repo + "/" + path
    import os
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data)
    subprocess.run([*GIT, "-C", repo, "add", path], check=True)
    subprocess.run([*GIT, "-C", repo, "commit", "-q", "-m", message], check=True)
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "master")
    return sha
