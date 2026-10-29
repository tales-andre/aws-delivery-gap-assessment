"""Repository evidence from a local clone (read-only git commands only).

Collects what the delivery record says: commits in the contract window,
who committed, release tags, CI/CD workflow files, IaC files on the
delivered branch, and branches that were never merged (work that exists
but was not delivered).
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

ALLOWED_GIT = {"log", "rev-parse", "for-each-ref", "ls-tree", "branch", "show"}

CI_PATTERNS = (".github/workflows/", ".gitlab-ci.yml", "buildspec.yml", "buildspec.yaml", "Jenkinsfile", "bitbucket-pipelines.yml", ".circleci/")
IAC_SUFFIXES = (".tf", ".tf.json", ".hcl")
IAC_NAMES = ("cdk.json", "template.yaml", "template.yml", "serverless.yml", "Pulumi.yaml")


class UnsafeGitCommand(RuntimeError):
    pass


def git(repo: Path, *args: str) -> str:
    if not args or args[0] not in ALLOWED_GIT:
        raise UnsafeGitCommand(f"git {args[:1]} is not allowed")
    if args[0] == "branch" and not any(a in args for a in ("--no-merged", "--merged", "--list", "-a", "-r")):
        raise UnsafeGitCommand("git branch is only allowed in list mode")
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def collect_repo_evidence(repo: Path, branch: str = "HEAD", since: str | None = None, until: str | None = None) -> dict:
    repo = Path(repo)
    window = [f"--since={since}"] if since else []
    window += [f"--until={until}"] if until else []

    head = git(repo, "rev-parse", branch).strip()
    log = git(repo, "log", branch, *window, "--no-merges", "--format=%H%x09%aI%x09%an").strip().splitlines()
    commits = [dict(zip(("sha", "date", "author"), line.split("\t"))) for line in log if line]
    authors: dict[str, int] = {}
    for c in commits:
        authors[c["author"]] = authors.get(c["author"], 0) + 1

    tags = []
    for line in git(repo, "for-each-ref", "refs/tags", "--format=%(refname:short)%09%(creatordate:iso-strict)").splitlines():
        if line.strip():
            name, date = line.split("\t")
            tags.append({"tag": name, "date": date})

    files = git(repo, "ls-tree", "-r", "--name-only", branch).splitlines()
    ci_files = sorted(f for f in files if any(f.startswith(p) or f == p for p in CI_PATTERNS))
    iac_dirs: dict[str, int] = {}
    for f in files:
        p = PurePosixPath(f)
        if f.endswith(IAC_SUFFIXES) or p.name in IAC_NAMES:
            d = str(p.parent)
            iac_dirs[d] = iac_dirs.get(d, 0) + 1

    unmerged = []
    for line in git(repo, "branch", "-a", "--no-merged", head, "--format=%(refname:short)").splitlines():
        name = line.strip()
        if name and not name.endswith("/HEAD"):
            last = git(repo, "log", "-1", "--format=%aI%x09%an%x09%s", name).strip().split("\t")
            unmerged.append({"branch": name, "last_commit": last[0], "author": last[1] if len(last) > 1 else None, "subject": last[2] if len(last) > 2 else None})

    return {
        "repo": repo.name,
        "branch": branch,
        "head": head,
        "window": {"since": since, "until": until},
        "commit_count": len(commits),
        "first_commit": commits[-1] if commits else None,
        "last_commit": commits[0] if commits else None,
        "commits_by_author": dict(sorted(authors.items(), key=lambda kv: -kv[1])),
        "tags": tags,
        "ci_files": ci_files,
        "iac_dirs": dict(sorted(iac_dirs.items())),
        "unmerged_branches": unmerged,
    }
