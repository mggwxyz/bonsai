#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
PROJECT_VERSION_FILES = (
    Path("pyproject.toml"),
    Path("src/bonsai/__init__.py"),
    Path("tests/test_cli.py"),
    Path("Formula/bonsai.rb"),
    Path("uv.lock"),
)


def validate_version(version: str) -> str:
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"Version must look like 1.2.3, got {version!r}")
    return version


def read_project_version(repo: Path) -> str:
    text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, flags=re.MULTILINE)
    if match is None:
        raise RuntimeError("Could not find project version in pyproject.toml")
    return match.group(1)


def replace_exact(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated = text.replace(old, new)
    if updated == text:
        raise RuntimeError(f"Expected to replace {old!r} in {path}")
    path.write_text(updated, encoding="utf-8")


def bump_uv_lock_project_version(path: Path, old_version: str, new_version: str) -> None:
    text = path.read_text(encoding="utf-8")
    old_block = f'[[package]]\nname = "bonsai"\nversion = "{old_version}"'
    new_block = f'[[package]]\nname = "bonsai"\nversion = "{new_version}"'
    updated = text.replace(old_block, new_block, 1)
    if updated == text:
        raise RuntimeError(f"Expected to replace bonsai package version in {path}")
    path.write_text(updated, encoding="utf-8")


def bump_project_versions(repo: Path, new_version: str) -> str:
    new_version = validate_version(new_version)
    old_version = read_project_version(repo)
    if old_version == new_version:
        raise RuntimeError(f"Project is already at {new_version}")

    replace_exact(
        repo / "pyproject.toml",
        f'version = "{old_version}"',
        f'version = "{new_version}"',
    )
    replace_exact(
        repo / "src/bonsai/__init__.py",
        f'__version__ = "{old_version}"',
        f'__version__ = "{new_version}"',
    )
    replace_exact(repo / "tests/test_cli.py", f"bonsai {old_version}", f"bonsai {new_version}")
    replace_exact(repo / "Formula/bonsai.rb", f'tag: "v{old_version}"', f'tag: "v{new_version}"')
    lockfile = repo / "uv.lock"
    if lockfile.exists():
        bump_uv_lock_project_version(lockfile, old_version, new_version)
    return old_version


def sync_tap_formula(repo: Path, tap_repo: Path) -> None:
    source = repo / "Formula" / "bonsai.rb"
    target = tap_repo / "Formula" / "bonsai.rb"
    if not target.parent.exists():
        raise RuntimeError(f"Homebrew tap Formula directory does not exist: {target.parent}")
    shutil.copyfile(source, target)


def project_publish_commands(version: str) -> list[list[str]]:
    version = validate_version(version)
    return [
        ["git", "add", *(str(path) for path in PROJECT_VERSION_FILES)],
        ["git", "commit", "-m", f"chore: release {version}"],
        ["git", "tag", f"v{version}"],
        ["git", "push"],
        ["git", "push", "origin", f"v{version}"],
    ]


def tap_publish_commands(version: str) -> list[list[str]]:
    version = validate_version(version)
    return [
        ["git", "add", "Formula/bonsai.rb"],
        ["git", "commit", "-m", f"bonsai {version}"],
        ["git", "push"],
    ]


def run_command(argv: list[str], cwd: Path, *, capture: bool = False) -> str:
    print(f"+ cd {cwd} && {' '.join(argv)}")
    result = subprocess.run(
        argv,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else ""


def git_root(start: Path) -> Path:
    result = run_command(["git", "rev-parse", "--show-toplevel"], start, capture=True)
    return Path(result.strip())


def ensure_clean_git(repo: Path, label: str) -> None:
    status = run_command(["git", "status", "--porcelain"], repo, capture=True)
    if status.strip():
        raise RuntimeError(f"{label} working tree is not clean:\n{status}")


def ensure_branch(repo: Path, expected: str) -> None:
    branch = run_command(["git", "branch", "--show-current"], repo, capture=True).strip()
    if branch != expected:
        raise RuntimeError(f"Release must run from {expected}, currently on {branch}")


def ensure_tag_available(repo: Path, version: str) -> None:
    tag = f"v{validate_version(version)}"
    local = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if local.returncode == 0:
        raise RuntimeError(f"Local tag already exists: {tag}")

    remote = run_command(["git", "ls-remote", "--tags", "origin", tag], repo, capture=True)
    if remote.strip():
        raise RuntimeError(f"Remote tag already exists: {tag}")


def discover_tap_repo(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()

    brew = subprocess.run(
        ["brew", "--repo", "mggwxyz/tap"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if brew.returncode == 0 and brew.stdout.strip():
        return Path(brew.stdout.strip()).resolve()

    for candidate in (
        Path("/opt/homebrew/Library/Taps/mggwxyz/homebrew-tap"),
        Path("/usr/local/Homebrew/Library/Taps/mggwxyz/homebrew-tap"),
    ):
        if candidate.exists():
            return candidate.resolve()
    raise RuntimeError("Could not find tap repo; pass --tap-repo /path/to/homebrew-tap")


def run_checks(repo: Path, version: str) -> None:
    had_uv_lock = (repo / "uv.lock").exists()
    run_command(["uv", "run", "pytest"], repo)
    run_command(["uv", "run", "ruff", "check", "."], repo)
    version_output = run_command(["uv", "run", "bonsai", "--version"], repo, capture=True)
    if f"bonsai {version}" not in version_output:
        raise RuntimeError(f"Unexpected version output: {version_output.strip()}")
    uv_lock = repo / "uv.lock"
    if not had_uv_lock and uv_lock.exists():
        uv_lock.unlink()


def run_many(commands: list[list[str]], cwd: Path) -> None:
    for command in commands:
        run_command(command, cwd)


def print_dry_run(repo: Path, tap_repo: Path, old_version: str, new_version: str) -> None:
    print(f"Would release bonsai {old_version} -> {new_version}")
    print(f"Project repo: {repo}")
    print(f"Homebrew tap repo: {tap_repo}")
    print("Would update project version files:")
    for path in PROJECT_VERSION_FILES:
        print(f"  - {path}")
    print("Would run checks:")
    print("  - uv run pytest")
    print("  - uv run ruff check .")
    print("  - uv run bonsai --version")
    print("Would run project publish commands:")
    for command in project_publish_commands(new_version):
        print(f"  - {' '.join(command)}")
    print("Would copy Formula/bonsai.rb into the tap repo.")
    print("Would run tap publish commands:")
    for command in tap_publish_commands(new_version):
        print(f"  - {' '.join(command)}")


def release(new_version: str, *, tap_repo_arg: str | None = None, dry_run: bool = False) -> None:
    new_version = validate_version(new_version)
    repo = git_root(Path(__file__).resolve().parents[1])
    tap_repo = discover_tap_repo(tap_repo_arg)
    old_version = read_project_version(repo)

    ensure_branch(repo, "main")
    ensure_clean_git(repo, "Project")
    ensure_clean_git(tap_repo, "Homebrew tap")
    ensure_tag_available(repo, new_version)

    if dry_run:
        print_dry_run(repo, tap_repo, old_version, new_version)
        return

    bump_project_versions(repo, new_version)
    run_checks(repo, new_version)
    run_many(project_publish_commands(new_version), repo)
    sync_tap_formula(repo, tap_repo)
    run_many(tap_publish_commands(new_version), tap_repo)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Release Bonsai and update the Homebrew tap.")
    parser.add_argument("version", help="New version, for example 0.1.3")
    parser.add_argument("--tap-repo", help="Path to the mggwxyz/homebrew-tap checkout.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate inputs and print the actions without writing, committing, "
            "tagging, or pushing."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        release(args.version, tap_repo_arg=args.tap_repo, dry_run=args.dry_run)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"release failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
