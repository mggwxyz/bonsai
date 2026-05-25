from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceConfig:
    default_parent: str = "~/Projects"


@dataclass(frozen=True)
class CaddyConfig:
    auto_install: bool = True
    auto_start: bool = True
    root_caddyfile: str = "Caddyfile"
    snippets_dir: str = "caddy.d"


@dataclass(frozen=True)
class CommandsConfig:
    install: str | None = None
    setup: str | None = None
    start: str | None = None


@dataclass(frozen=True)
class SharedFileConfig:
    source: str
    target: str
    mode: str = "symlink"


@dataclass(frozen=True)
class EnvConfig:
    name: str
    value: str


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    port_env: str
    base_port: int
    public: bool = True
    primary: bool = False
    url: str | None = None


@dataclass(frozen=True)
class BonsaiConfig:
    name: str
    base_branch: str | None
    workspace: WorkspaceConfig
    caddy: CaddyConfig
    commands: CommandsConfig
    shared_files: tuple[SharedFileConfig, ...] = field(default_factory=tuple)
    env: tuple[EnvConfig, ...] = field(default_factory=tuple)
    services: tuple[ServiceConfig, ...] = field(default_factory=tuple)
    path: Path | None = None

    def public_services(self) -> tuple[ServiceConfig, ...]:
        return tuple(service for service in self.services if service.public)

    def primary_service(self) -> ServiceConfig:
        for service in self.public_services():
            if service.primary:
                return service
        raise ValueError("No primary public service configured")


@dataclass(frozen=True)
class ManagedWorktree:
    path: str
    slug: str
    slot: int


@dataclass(frozen=True)
class BonsaiState:
    version: int
    name: str
    default_branch: str
    default_worktree: str
    repo_url: str
    worktrees: dict[str, ManagedWorktree]


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    default_worktree: Path
    state_file: Path
    caddyfile: Path
    snippets_dir: Path


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: Path | None = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class FileWrite:
    path: Path
    content: str

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class FileSymlink:
    source: Path
    target: Path


@dataclass(frozen=True)
class CloneWorkspacePlan:
    workspace_root: Path
    default_worktree: Path
    state: BonsaiState
    files: tuple[FileWrite, ...]


@dataclass(frozen=True)
class AddFilesPlan:
    branch: str
    worktree_path: Path
    slot: int
    files: tuple[FileWrite, ...]
    symlinks: tuple[FileSymlink, ...]
    updated_state: BonsaiState


@dataclass(frozen=True)
class ResolvedWorktree:
    branch: str
    worktree: ManagedWorktree


@dataclass(frozen=True)
class RemoveWorktreePlan:
    branch: str
    worktree_path: Path
    removed_snippets: tuple[Path, ...]
    updated_state: BonsaiState


@dataclass(frozen=True)
class CheckoutWorktreePlan:
    worktree_path: Path
    created: bool
