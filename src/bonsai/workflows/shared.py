from __future__ import annotations

import shlex
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from bonsai.config import load_config
from bonsai.env import parse_env_content
from bonsai.errors import BonsaiCommandError, BonsaiConfigError, BonsaiWorkspaceError
from bonsai.logs import LogKind, next_command_log_path
from bonsai.models import (
    BonsaiConfig,
    BonsaiState,
    CommandSpec,
    FileCopy,
    FileSymlink,
    FileWrite,
    ManagedWorktree,
    ResolvedWorktree,
    WorktreeTarget,
)
from bonsai.process import Runner, format_command
from bonsai.rendering import (
    render_caddy_snippets,
    render_env_local,
)
from bonsai.slug import branch_slug
from bonsai.state import load_state

ConfigInitializer = Callable[[Path, str, str, Path], None]

_PREPARE_COMMAND_KINDS: tuple[LogKind, ...] = (
    "preinstall",
    "install",
    "postinstall",
    "presetup",
    "setup",
    "postsetup",
)
_POST_ADD_COMMAND_KINDS: tuple[LogKind, ...] = ("postadd",)
_WORKTREEINCLUDE_NAME = ".worktreeinclude"
_WORKTREEINCLUDE_SKIPPED_DIRS = frozenset(
    {
        ".cache",
        ".gradle",
        ".next",
        ".nuxt",
        ".parcel-cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svelte-kit",
        ".tox",
        ".turbo",
        ".venv",
        "__pycache__",
        "bower_components",
        "build",
        "coverage",
        "dist",
        "env",
        "node_modules",
        "out",
        "target",
        "vendor",
        "venv",
    }
)


def workspace_config_path(workspace_root: Path) -> Path:
    return workspace_root / ".bonsai.toml"


def workspace_local_config_path(workspace_root: Path) -> Path:
    return workspace_root / ".bonsai.local.toml"


def repo_config_path(workspace_root: Path, default_worktree: str) -> Path:
    return workspace_root / default_worktree / ".bonsai.toml"


def repo_local_config_path(workspace_root: Path, default_worktree: str) -> Path:
    return workspace_root / default_worktree / ".bonsai.local.toml"


def resolve_workspace_config_path(workspace_root: Path, default_worktree: str) -> Path:
    root_config = workspace_config_path(workspace_root)
    if root_config.exists():
        return root_config

    fallback_config = repo_config_path(workspace_root, default_worktree)
    if fallback_config.exists():
        return fallback_config

    raise BonsaiConfigError(
        f"Missing .bonsai.toml at {root_config} or {fallback_config}"
    )


def workspace_local_config_paths(
    workspace_root: Path,
    default_worktree: str,
    config_path: Path,
) -> tuple[Path, ...]:
    root_local = workspace_local_config_path(workspace_root)
    if config_path == workspace_config_path(workspace_root):
        return (root_local,)
    return (root_local, repo_local_config_path(workspace_root, default_worktree))


def load_workspace_config(workspace_root: Path, state: BonsaiState) -> BonsaiConfig:
    config_path = resolve_workspace_config_path(workspace_root, state.default_worktree)
    return load_config(
        config_path,
        local_paths=workspace_local_config_paths(
            workspace_root,
            state.default_worktree,
            config_path,
        ),
    )


def _safe_path_segment(value: str, label: str) -> str:
    path = Path(value)
    if (
        value == ""
        or value in {".", ".."}
        or path.is_absolute()
        or len(path.parts) != 1
        or "/" in value
        or "\\" in value
    ):
        raise BonsaiWorkspaceError(f"Invalid {label}: {value!r}")
    return value


def global_caddy_paths() -> tuple[Path, Path]:
    """Return (root Caddyfile, snippets root) under ~/.bonsai."""
    root = Path.home() / ".bonsai"
    return root / "Caddyfile", root / "caddy.d"


def app_snippets_dir(app_name: str) -> Path:
    _, snippets_root = global_caddy_paths()
    return snippets_root / _safe_path_segment(app_name, "workspace name")


def _app_snippet_dirs(snippets_root: Path) -> list[Path]:
    """App subdirectories under the global snippets root that contain snippets."""
    if not snippets_root.exists():
        return []
    return sorted(
        directory
        for directory in snippets_root.iterdir()
        if directory.is_dir() and any(directory.glob("*.caddy"))
    )


def generated_worktree_files(
    config: BonsaiConfig,
    branch: str,
    slot: int,
    worktree_path: Path,
    *,
    workspace_root: Path | None = None,
    default_branch: str | None = None,
) -> tuple[FileWrite, ...]:
    slug = branch_slug(branch)
    if slug == "":
        raise BonsaiWorkspaceError(f"Invalid branch slug: {branch!r}")
    snippets_dir = app_snippets_dir(config.name)
    files = [
        FileWrite(
            path=worktree_path / ".env.local",
            content=render_env_local(
                config,
                branch,
                slot,
                worktree_path,
                workspace_root=workspace_root,
                default_branch=default_branch,
            ),
        )
    ]
    for service_name, content in render_caddy_snippets(
        config,
        branch,
        slot,
        worktree_path,
        workspace_root=workspace_root,
        default_branch=default_branch,
    ).items():
        service_name = _safe_path_segment(service_name, "service name")
        files.append(FileWrite(path=snippets_dir / f"{slug}-{service_name}.caddy", content=content))
    return tuple(files)


def worktreeinclude_file_copies(
    config: BonsaiConfig,
    default_worktree_path: Path,
    worktree_path: Path,
) -> tuple[FileCopy, ...]:
    include_path = default_worktree_path / _WORKTREEINCLUDE_NAME
    if not include_path.is_file():
        return ()

    explicit_paths = {
        Path(path)
        for shared_file in config.shared_files
        for path in (shared_file.source, shared_file.target)
    }
    copies: list[FileCopy] = []
    for relative_path in _worktreeinclude_relative_paths(default_worktree_path, include_path):
        if relative_path in explicit_paths:
            continue
        if _is_skipped_worktreeinclude_path(relative_path):
            continue
        source = default_worktree_path / relative_path
        if not source.is_file() or source.is_symlink():
            continue
        copies.append(FileCopy(source=source, target=worktree_path / relative_path))
    return tuple(copies)


def _worktreeinclude_relative_paths(
    default_worktree_path: Path,
    include_path: Path,
) -> tuple[Path, ...]:
    candidates = _git_worktreeinclude_candidates(default_worktree_path, include_path)
    if not candidates:
        return ()
    ignored = _git_ignored_paths(default_worktree_path, candidates)
    paths: list[Path] = []
    for path in candidates:
        if path not in ignored:
            continue
        if not _is_safe_relative_worktreeinclude_path(path):
            continue
        paths.append(path)
    return tuple(paths)


def _git_worktreeinclude_candidates(
    default_worktree_path: Path,
    include_path: Path,
) -> tuple[Path, ...]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(default_worktree_path),
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-from",
                str(include_path),
                "-z",
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return ()
    if result.returncode != 0:
        return ()
    return _decode_nul_paths(result.stdout)


def _git_ignored_paths(default_worktree_path: Path, paths: tuple[Path, ...]) -> set[Path]:
    if not paths:
        return set()
    stdin = b"".join(
        f"{path.as_posix()}\0".encode("utf-8", errors="surrogateescape")
        for path in paths
    )
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(default_worktree_path),
                "check-ignore",
                "--stdin",
                "-z",
            ],
            input=stdin,
            capture_output=True,
            check=False,
        )
    except OSError:
        return set()
    if result.returncode not in {0, 1}:
        return set()
    return set(_decode_nul_paths(result.stdout))


def _decode_nul_paths(output: bytes) -> tuple[Path, ...]:
    paths: list[Path] = []
    for value in output.split(b"\0"):
        if not value:
            continue
        text = value.decode("utf-8", errors="surrogateescape")
        paths.append(Path(text))
    return tuple(paths)


def _is_safe_relative_worktreeinclude_path(path: Path) -> bool:
    return (
        path != Path()
        and not path.is_absolute()
        and ".." not in path.parts
        and all(part not in {"", "."} for part in path.parts)
    )


def _is_skipped_worktreeinclude_path(path: Path) -> bool:
    return any(part in _WORKTREEINCLUDE_SKIPPED_DIRS for part in path.parts)


def resolve_managed_worktree(state: BonsaiState, name: str) -> ResolvedWorktree | None:
    worktree = state.worktrees.get(name)
    if worktree is not None:
        return ResolvedWorktree(branch=name, worktree=worktree)
    for branch, candidate in state.worktrees.items():
        if name in {candidate.path, candidate.slug}:
            return ResolvedWorktree(branch=branch, worktree=candidate)
    return None


def _worktree_name_aliases(branch: str, worktree: ManagedWorktree) -> tuple[str, ...]:
    aliases: list[str] = []
    for alias in (branch, worktree.path, worktree.slug):
        if alias and alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)


def _normalized_worktree_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _worktree_name_matches(query: str, alias: str) -> bool:
    if query == "":
        return True
    folded_query = query.casefold()
    folded_alias = alias.casefold()
    if folded_query in folded_alias:
        return True
    normalized_query = _normalized_worktree_name(query)
    return bool(normalized_query) and normalized_query in _normalized_worktree_name(alias)


def _fuzzy_worktree_target(
    targets: tuple[WorktreeTarget, ...],
    name: str,
) -> WorktreeTarget | None:
    matches = [
        target
        for target in targets
        if any(
            _worktree_name_matches(name, alias)
            for alias in _worktree_name_aliases(
                target.branch,
                target.worktree,
            )
        )
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        choices = ", ".join(target.branch for target in matches)
        raise BonsaiWorkspaceError(f"Ambiguous Bonsai worktree {name!r}: {choices}")
    return None


def worktree_name_completions(
    workspace_root: Path,
    incomplete: str,
    *,
    include_default: bool = True,
) -> tuple[str, ...]:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    targets = _configured_worktree_targets(state, workspace_root)
    if not include_default:
        targets = tuple(target for target in targets if target.branch != state.default_branch)

    completions: list[str] = []
    for target in targets:
        for alias in _worktree_name_aliases(target.branch, target.worktree):
            if alias in completions or not _worktree_name_matches(incomplete, alias):
                continue
            completions.append(alias)
    return tuple(completions)


def _default_worktree_names(state: BonsaiState) -> set[str]:
    return {
        state.default_branch,
        state.default_worktree,
        branch_slug(state.default_branch),
    }


def _resolve_current_worktree(
    state: BonsaiState,
    workspace_root: Path,
    current_path: Path,
) -> tuple[str, ManagedWorktree, Path]:
    current_path = current_path.resolve()
    default_worktree = ManagedWorktree(
        path=state.default_worktree,
        slug=branch_slug(state.default_branch),
        slot=0,
    )
    candidates = [(state.default_branch, default_worktree), *state.worktrees.items()]
    resolved_candidates = [
        ((workspace_root / worktree.path).resolve(), branch, worktree)
        for branch, worktree in candidates
    ]

    for worktree_path, branch, worktree in sorted(
        resolved_candidates,
        key=lambda candidate: len(candidate[0].parts),
        reverse=True,
    ):
        if current_path == worktree_path or current_path.is_relative_to(worktree_path):
            return branch, worktree, worktree_path

    raise BonsaiWorkspaceError(f"Current directory is not inside a Bonsai worktree: {current_path}")


def _configured_worktree_targets(
    state: BonsaiState,
    workspace_root: Path,
) -> tuple[WorktreeTarget, ...]:
    default = WorktreeTarget(
        branch=state.default_branch,
        worktree=ManagedWorktree(
            path=state.default_worktree,
            slug=branch_slug(state.default_branch),
            slot=0,
        ),
        worktree_path=workspace_root / state.default_worktree,
    )
    managed = tuple(
        WorktreeTarget(
            branch=branch,
            worktree=worktree,
            worktree_path=workspace_root / worktree.path,
        )
        for branch, worktree in state.worktrees.items()
    )
    return (default, *managed)


def resolve_start_target(
    workspace_root: Path,
    name: str | None,
    current_path: Path,
) -> WorktreeTarget:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    if name is None:
        branch, worktree, worktree_path = _resolve_current_worktree(
            state,
            workspace_root,
            current_path,
        )
        return WorktreeTarget(branch=branch, worktree=worktree, worktree_path=worktree_path)

    for target in _configured_worktree_targets(state, workspace_root):
        if name in {target.branch, target.worktree.path, target.worktree.slug}:
            return target

    raise BonsaiWorkspaceError(f"Unknown Bonsai worktree: {name}")


def _workspace_summary_commands() -> dict[str, str]:
    return {
        "context": "bonsai context --format json",
        "status": "bonsai status",
        "list": "bonsai list",
        "start": "bonsai start",
        "open": "bonsai open",
        "sync": "bonsai sync --apply",
        "doctor": "bonsai doctor",
    }


def write_files(files: tuple[FileWrite, ...]) -> None:
    for file in files:
        file.path.parent.mkdir(parents=True, exist_ok=True)
        file.path.write_text(file.content, encoding="utf-8")


def apply_symlinks(symlinks: tuple[FileSymlink, ...]) -> None:
    for symlink in symlinks:
        if not symlink.source.exists():
            raise BonsaiWorkspaceError(f"Shared file source does not exist: {symlink.source}")
        if symlink.target.is_symlink():
            if symlink.target.resolve() == symlink.source.resolve():
                continue
            raise BonsaiWorkspaceError(
                f"Shared file target already exists with a different symlink: {symlink.target}"
            )
        if symlink.target.exists():
            raise BonsaiWorkspaceError(f"Shared file target already exists: {symlink.target}")
        symlink.target.parent.mkdir(parents=True, exist_ok=True)
        symlink.target.symlink_to(symlink.source)


def apply_file_copies(copies: tuple[FileCopy, ...]) -> None:
    for copy in copies:
        if not copy.source.exists():
            raise BonsaiWorkspaceError(f"Shared file source does not exist: {copy.source}")
        if copy.target.exists() or copy.target.is_symlink():
            continue
        copy.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copy.source, copy.target)


def command_summary(command: CommandSpec) -> str:
    return format_command(command.argv, cwd=command.cwd)


def run_command_specs(runner: Runner, commands: list[CommandSpec]) -> None:
    for command in commands:
        runner.run(list(command.argv), cwd=command.cwd, env=dict(command.env))


def generated_worktree_env(files: tuple[FileWrite, ...]) -> dict[str, str]:
    for file in files:
        if file.path.name == ".env.local":
            return parse_env_content(file.content)
    return {}


def run_worktree_command(
    runner: Runner,
    command: str,
    cwd: Path,
    env: Mapping[str, str],
) -> None:
    runner.run(shlex.split(command), cwd=cwd, env=env)


def run_lifecycle_command(
    runner: Runner,
    workspace_root: Path,
    worktree_slug: str,
    kind: LogKind,
    command: str,
    cwd: Path,
    env: Mapping[str, str],
    check: bool = True,
) -> int:
    argv = shlex.split(command)
    log_path = next_command_log_path(workspace_root, worktree_slug, kind)
    exit_code = runner.run_stream_logged(argv, cwd=cwd, env=env, log_path=log_path, label=kind)

    if check and exit_code != 0:
        raise BonsaiCommandError(
            f"Command failed ({exit_code}): {format_command(argv, cwd=cwd)}\nLog: {log_path}"
        )
    return exit_code


def run_configured_lifecycle_commands(
    runner: Runner,
    config: BonsaiConfig,
    workspace_root: Path,
    worktree_slug: str,
    kinds: tuple[LogKind, ...],
    cwd: Path,
    env: Mapping[str, str],
) -> None:
    for kind in kinds:
        command = getattr(config.commands, kind)
        if command:
            run_lifecycle_command(
                runner,
                workspace_root=workspace_root,
                worktree_slug=worktree_slug,
                kind=kind,
                command=command,
                cwd=cwd,
                env=env,
            )


def _command_available(runner: Runner, argv: list[str]) -> bool:
    try:
        result = runner.run(argv, check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _branch_sort_key(item: tuple[str, ManagedWorktree]) -> tuple[str, str]:
    branch = item[0]
    return (branch.lower(), branch)
