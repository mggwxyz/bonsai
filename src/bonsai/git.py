from __future__ import annotations

from pathlib import Path

from bonsai.errors import BonsaiCommandError
from bonsai.process import Runner


def parse_default_branch(ls_remote_output: str) -> str:
    for line in ls_remote_output.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line.removeprefix("ref: refs/heads/").removesuffix("\tHEAD")
    raise BonsaiCommandError("Unable to determine the remote default branch")


def discover_default_branch(runner: Runner, git_url: str) -> str:
    result = runner.run(["git", "ls-remote", "--symref", "--", git_url, "HEAD"])
    return parse_default_branch(result.stdout)


def clone_default_branch(runner: Runner, git_url: str, branch: str, target: Path) -> None:
    runner.run(["git", "clone", "--branch", branch, "--", git_url, str(target)])


def fetch_origin(runner: Runner, repo: Path) -> None:
    runner.run(["git", "-C", str(repo), "fetch", "origin"])


def remote_branch_exists(runner: Runner, repo: Path, branch: str) -> bool:
    result = runner.run(
        ["git", "-C", str(repo), "ls-remote", "--heads", "origin", branch],
    )
    return bool(result.stdout.strip())


def is_git_worktree(runner: Runner, repo: Path) -> bool:
    result = runner.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def current_branch(runner: Runner, repo: Path) -> str:
    result = runner.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip()


def add_existing_worktree(runner: Runner, repo: Path, branch: str, target: Path) -> None:
    runner.run(["git", "-C", str(repo), "worktree", "add", str(target), branch])


def add_new_worktree(
    runner: Runner,
    repo: Path,
    branch: str,
    target: Path,
    base_branch: str,
) -> None:
    runner.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-b",
            branch,
            str(target),
            f"origin/{base_branch}",
        ]
    )
