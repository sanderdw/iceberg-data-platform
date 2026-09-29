import io
import tarfile

import pytest

from dbt_portal.errors import DbtError
from dbt_portal.git_repo import GitRepos

AUTHOR = {"name": "bob", "email": "bob@users.iceberg-platform"}


@pytest.fixture
def repos(tmp_path):
    repos = GitRepos(tmp_path)
    repos.create("prj-a", {"dbt_project.yml": "name: shop\n", "models/a.sql": "select 1\n"}, AUTHOR)
    return repos


def test_commits_are_atomic_and_detect_concurrent_writers(repos):
    repos.create_branch("prj-a", "feature")
    head = repos.resolve("prj-a", "feature")
    new = repos.commit("prj-a", "feature", {"models/b.sql": "select 2\n", "models/a.sql": None}, "Swap", AUTHOR,
                       expected=head)
    revision, files = repos.files("prj-a", "feature")
    assert revision == new and {f["path"] for f in files} == {"dbt_project.yml", "models/b.sql"}
    assert repos.read("prj-a", "main", "models/a.sql")[1] == b"select 1\n"
    with pytest.raises(DbtError) as stale:
        repos.commit("prj-a", "feature", {"models/c.sql": "select 3\n"}, "Late", AUTHOR, expected=head)
    assert stale.value.code == "stale_branch"
    with pytest.raises(DbtError) as same:
        repos.commit("prj-a", "feature", {"models/b.sql": "select 2\n"}, "Nothing", AUTHOR)
    assert same.value.code == "empty_commit"


@pytest.mark.parametrize("path", ["/etc/passwd", "../x", "a/../b", ".git/config", "a//b", "models/.git/x"])
def test_paths_stay_inside_the_project(repos, path):
    with pytest.raises(DbtError) as error:
        repos.commit("prj-a", "main", {path: "x"}, "Escape", AUTHOR)
    assert error.value.code == "invalid_path"


@pytest.mark.parametrize("name", ["-rf", "a..b", "x.lock", "/abs", "a b", ""])
def test_branch_names_are_validated(repos, name):
    with pytest.raises(DbtError):
        repos.create_branch("prj-a", name)


def test_merge_fast_forward_merge_commit_and_conflict(repos):
    repos.create_branch("prj-a", "one")
    repos.commit("prj-a", "one", {"models/one.sql": "select 1\n"}, "One", AUTHOR)
    assert repos.merge("prj-a", "one", "", AUTHOR)["merged"] is True
    assert repos.resolve("prj-a", "main") == repos.resolve("prj-a", "one")
    assert repos.merge("prj-a", "one", "", AUTHOR)["merged"] is False
    repos.create_branch("prj-a", "two")
    repos.create_branch("prj-a", "three")
    repos.commit("prj-a", "two", {"models/two.sql": "select 2\n"}, "Two", AUTHOR)
    repos.commit("prj-a", "three", {"models/three.sql": "select 3\n"}, "Three", AUTHOR)
    repos.merge("prj-a", "two", "", AUTHOR)
    merged = repos.merge("prj-a", "three", "Merge three", AUTHOR)
    assert merged["merged"]
    _, files = repos.files("prj-a", "main")
    assert {"models/two.sql", "models/three.sql"} <= {f["path"] for f in files}
    assert repos.log("prj-a", "main", 1)[0]["subject"] == "Merge three"
    repos.create_branch("prj-a", "left")
    repos.create_branch("prj-a", "right")
    repos.commit("prj-a", "left", {"models/a.sql": "select 'left'\n"}, "Left", AUTHOR)
    repos.commit("prj-a", "right", {"models/a.sql": "select 'right'\n"}, "Right", AUTHOR)
    repos.merge("prj-a", "left", "", AUTHOR)
    with pytest.raises(DbtError) as conflict:
        repos.merge("prj-a", "right", "", AUTHOR)
    assert conflict.value.code == "merge_conflict" and conflict.value.detail["files"] == ["models/a.sql"]


def test_archive_is_the_exact_revision(repos):
    first = repos.resolve("prj-a", "main")
    repos.create_branch("prj-a", "next")
    repos.commit("prj-a", "next", {"models/b.sql": "select 2\n"}, "B", AUTHOR)
    with tarfile.open(fileobj=io.BytesIO(repos.archive("prj-a", first))) as tar:
        assert sorted(tar.getnames()) == ["dbt_project.yml", "models", "models/a.sql"]
    assert repos.is_on_main("prj-a", first) and not repos.is_on_main("prj-a", repos.resolve("prj-a", "next"))
    with pytest.raises(DbtError) as missing:
        repos.resolve("prj-a", "nope")
    assert missing.value.code == "unknown_ref"
    with pytest.raises(DbtError):
        repos.delete_branch("prj-a", "main")
