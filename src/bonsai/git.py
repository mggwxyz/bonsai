from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bonsai.errors import BonsaiCommandError
from bonsai.process import Runner


@dataclass(frozen=True)
class GitWorktree:
    path: Path
    branch: str | None


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


def parse_worktree_list(output: str) -> tuple[GitWorktree, ...]:
    worktrees: list[GitWorktree] = []
    path: Path | None = None
    branch: str | None = None
    for line in [*output.splitlines(), ""]:
        if line == "":
            if path is not None:
                worktrees.append(GitWorktree(path=path, branch=branch))
            path = None
            branch = None
        elif line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch refs/heads/"):
            branch = line.removeprefix("branch refs/heads/")
    return tuple(worktrees)


def list_worktrees(runner: Runner, repo: Path) -> tuple[GitWorktree, ...]:
    result = runner.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"])
    return parse_worktree_list(result.stdout)


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


def remote_origin_url(runner: Runner, repo: Path) -> str:
    result = runner.run(
        ["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def worktree_has_changes(runner: Runner, repo: Path) -> bool:
    result = runner.run(["git", "-C", str(repo), "status", "--porcelain"])
    return bool(result.stdout.strip())


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


def remove_worktree(
    runner: Runner,
    repo: Path,
    target: Path,
    force: bool = False,
) -> None:
    argv = ["git", "-C", str(repo), "worktree", "remove"]
    if force:
        argv.append("--force")
    argv.append(str(target))
    runner.run(argv)


def move_worktree(
    runner: Runner,
    repo: Path,
    source: Path,
    target: Path,
) -> None:
    runner.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "move",
            str(source),
            str(target),
        ]
    )
