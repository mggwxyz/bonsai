from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class CaddyConfig:
    auto_install: bool = True
    auto_start: bool = True


@dataclass(frozen=True)
class CommandsConfig:
    preinstall: str | None = None
    install: str | None = None
    postinstall: str | None = None
    presetup: str | None = None
    setup: str | None = None
    postsetup: str | None = None
    postadd: str | None = None
    preremove: str | None = None
    prestart: str | None = None
    start: str | None = None
    poststart: str | None = None


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
class BrowserExtensionConfig:
    extension_id: str | None = None


@dataclass(frozen=True)
class RunConfig:
    mode: Literal["concurrent", "single"] = "concurrent"


@dataclass(frozen=True)
class BonsaiConfig:
    name: str
    base_branch: str | None
    caddy: CaddyConfig
    commands: CommandsConfig
    run: RunConfig = field(default_factory=RunConfig)
    browser_extension: BrowserExtensionConfig = field(default_factory=BrowserExtensionConfig)
    shared_files: tuple[SharedFileConfig, ...] = field(default_factory=tuple)
    env: tuple[EnvConfig, ...] = field(default_factory=tuple)
    services: tuple[ServiceConfig, ...] = field(default_factory=tuple)
    path: Path | None = None
    local_paths: tuple[Path, ...] = field(default_factory=tuple)

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
class WorktreeTarget:
    branch: str
    worktree: ManagedWorktree
    worktree_path: Path


@dataclass(frozen=True)
class BonsaiState:
    version: int
    name: str
    default_branch: str
    default_worktree: str
    repo_url: str
    worktrees: dict[str, ManagedWorktree]


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: Path | None = None
    env: tuple[tuple[str, str], ...] = ()
    log_path: Path | None = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class WorktreeCommandResult:
    branch: str
    worktree_path: Path
    exit_code: int


@dataclass(frozen=True)
class EachCommandResult:
    items: tuple[WorktreeCommandResult, ...]

    @property
    def exit_code(self) -> int:
        for item in self.items:
            if item.exit_code != 0:
                return item.exit_code
        return 0


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str
    hint: str | None = None
    id: str | None = None
    repair: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def failed(self) -> bool:
        return any(check.status == "fail" for check in self.checks)


@dataclass(frozen=True)
class DoctorApplyAction:
    kind: str
    detail: str


@dataclass(frozen=True)
class CaddySetupResult:
    actions: tuple[DoctorApplyAction, ...] = ()
    checks: tuple[DoctorCheck, ...] = ()


@dataclass(frozen=True)
class DoctorApplyPlan:
    actions: tuple[DoctorApplyAction, ...]


@dataclass(frozen=True)
class PortRepairServiceChange:
    name: str
    port_env: str
    old_port: int
    new_port: int
    owners: tuple[PortOwner, ...] = ()


@dataclass(frozen=True)
class PortRepairItem:
    branch: str
    slug: str
    current_slot: int
    proposed_slot: int
    services: tuple[PortRepairServiceChange, ...]


@dataclass(frozen=True)
class PortRepairPlan:
    items: tuple[PortRepairItem, ...]


@dataclass(frozen=True)
class PortOwner:
    pid: int
    command: str
    user: str | None = None
    cwd: Path | None = None
    worktree_branch: str | None = None
    worktree_path: Path | None = None


@dataclass(frozen=True)
class WorkspacePort:
    branch: str
    worktree_path: Path
    service_name: str
    port_env: str
    port: int
    status: str
    owners: tuple[PortOwner, ...]


@dataclass(frozen=True)
class WorkspacePortsPlan:
    workspace_root: Path
    ports: tuple[WorkspacePort, ...]


@dataclass(frozen=True)
class StopProcessItem:
    action: str
    branch: str
    worktree_path: Path
    service_name: str
    port_env: str
    port: int
    owner: PortOwner
    reason: str


@dataclass(frozen=True)
class StopProcessPlan:
    items: tuple[StopProcessItem, ...]
    apps: tuple[AppDownPlan, ...] = ()


@dataclass(frozen=True)
class AppUpPlan:
    branch: str
    worktree_path: Path
    pid: int
    log_path: Path
    ready_ports: tuple[int, ...]
    stale_pid: int | None = None


@dataclass(frozen=True)
class AppDownPlan:
    branch: str
    worktree_path: Path
    pid: int | None
    action: str
    log_path: Path | None = None


@dataclass(frozen=True)
class AppProcessItem:
    workspace_name: str
    workspace_root: Path
    branch: str
    worktree_path: Path
    pid: int
    command: tuple[str, ...]
    log_path: Path | None
    started_at: str | None = None


@dataclass(frozen=True)
class AppProcessPlan:
    items: tuple[AppProcessItem, ...]


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
class FileCopy:
    source: Path
    target: Path


@dataclass(frozen=True)
class SyncFileAction:
    kind: str
    path: Path
    content: str | None = None
    source: Path | None = None


@dataclass(frozen=True)
class SyncPlan:
    actions: tuple[SyncFileAction, ...]
    reload_caddy: bool


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
    copies: tuple[FileCopy, ...]
    updated_state: BonsaiState


@dataclass(frozen=True)
class PullRequestWorktreePlan:
    pr_number: int
    branch: str
    title: str
    url: str | None
    state: str
    read_only: bool
    add_plan: AddFilesPlan


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
    compose_project_name: str | None = None
    removed_logs: Path | None = None


@dataclass(frozen=True)
class MoveWorktreePlan:
    branch: str
    old_worktree_path: Path
    new_worktree_path: Path
    updated_state: BonsaiState


@dataclass(frozen=True)
class CleanupItem:
    branch: str
    worktree_path: Path
    action: str
    reason: str
    pr_url: str | None = None


@dataclass(frozen=True)
class CleanupPlan:
    items: tuple[CleanupItem, ...]


@dataclass(frozen=True)
class RepairItem:
    branch: str
    worktree_path: Path
    action: str
    reason: str
    old_slot: int | None = None
    new_slot: int | None = None


@dataclass(frozen=True)
class RepairPlan:
    items: tuple[RepairItem, ...]
    updated_state: BonsaiState
    state_changed: bool


@dataclass(frozen=True)
class CheckoutWorktreePlan:
    worktree_path: Path
    created: bool


@dataclass(frozen=True)
class OpenUrlPlan:
    branch: str
    worktree_path: Path
    url: str
    service_name: str
    port: int
    workspace_name: str = ""
    browser_extension_id: str | None = None
    via: Literal["caddy", "port"] = "caddy"


@dataclass(frozen=True)
class UrlCheck:
    name: str
    status: str
    detail: str
    hint: str | None = None


@dataclass(frozen=True)
class WorkspaceUrl:
    branch: str
    worktree_path: Path
    service_name: str
    port_env: str
    port: int
    primary: bool
    url: str
    caddy_snippet_path: Path
    checks: tuple[UrlCheck, ...]


@dataclass(frozen=True)
class WorkspaceUrlsPlan:
    workspace_root: Path
    caddyfile: Path
    urls: tuple[WorkspaceUrl, ...]


@dataclass(frozen=True)
class CommandLogPlan:
    branch: str
    worktree_path: Path
    log_path: Path
    content: str


@dataclass(frozen=True)
class WorkspaceServiceSummary:
    name: str
    port_env: str
    port: int
    public: bool
    primary: bool
    url: str | None


@dataclass(frozen=True)
class WorktreeSummary:
    branch: str
    worktree_path: Path
    relative_path: str
    slug: str
    slot: int
    kind: str
    env_file_path: Path
    env_file_status: str
    services: tuple[WorkspaceServiceSummary, ...]


@dataclass(frozen=True)
class WorkspaceSummary:
    workspace_name: str
    workspace_root: Path
    default_branch: str
    default_worktree: str
    config_path: Path
    worktrees: tuple[WorktreeSummary, ...]
    commands: dict[str, str]


@dataclass(frozen=True)
class WorkspaceStatus:
    workspace_name: str
    workspace_root: Path
    default_branch: str
    default_worktree: str
    config_path: Path
    current: WorktreeSummary | None
    commands: dict[str, str]
    location_kind: str = "worktree"
    location_path: Path | None = None
    generated_env: dict[str, str] = field(default_factory=dict)
