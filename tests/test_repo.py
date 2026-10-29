import subprocess

import pytest

from evidence_kit.repo import UnsafeGitCommand, collect_repo_evidence, git


def sh(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def delivered_repo(tmp_path):
    repo = tmp_path / "platform"
    repo.mkdir()
    sh(repo, "init", "-b", "main")
    sh(repo, "config", "user.name", "Vendor Dev")
    sh(repo, "config", "user.email", "dev@example.com")

    def commit(path, text, msg, date):
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        sh(repo, "add", path)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", msg],
            check=True,
            capture_output=True,
            env={**__import__("os").environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
        )

    commit("infra/prod/main.tf", "# vpc", "vpc", "2026-04-02T10:00:00+00:00")
    commit("infra/prod/rds.tf", "# rds", "rds", "2026-05-10T10:00:00+00:00")
    commit(".github/workflows/deploy.yml", "on: push", "ci", "2026-06-01T10:00:00+00:00")
    sh(repo, "tag", "v1.0.0")
    sh(repo, "checkout", "-b", "feature/waf")
    commit("infra/prod/waf.tf", "# waf", "waf draft", "2026-09-20T10:00:00+00:00")
    sh(repo, "checkout", "main")
    return repo


def test_repo_evidence_reads_delivery_record(delivered_repo):
    ev = collect_repo_evidence(delivered_repo, branch="main", since="2026-05-01")
    assert ev["commit_count"] == 2  # window excludes the April commit
    assert ev["commits_by_author"] == {"Vendor Dev": 2}
    assert ev["ci_files"] == [".github/workflows/deploy.yml"]
    assert ev["iac_dirs"] == {"infra/prod": 2}  # waf.tf is not on the delivered branch
    assert [t["tag"] for t in ev["tags"]] == ["v1.0.0"]
    (unmerged,) = ev["unmerged_branches"]
    assert unmerged["branch"] == "feature/waf"
    assert unmerged["subject"] == "waf draft"


@pytest.mark.parametrize("args", [("commit", "-m", "x"), ("push",), ("reset", "--hard"), ("branch", "-D", "x"), ("checkout", "x")])
def test_git_writes_are_refused(delivered_repo, args):
    with pytest.raises(UnsafeGitCommand):
        git(delivered_repo, *args)
