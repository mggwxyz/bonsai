import re
from pathlib import Path

import pytest
from test_config import VALID_CONFIG, write_config

import bonsai.workflows as workflows
from bonsai.config import load_config
from bonsai.errors import BonsaiConfigError, BonsaiWorkspaceError
from bonsai.git import (
    worktree_has_changes,
)
from bonsai.models import (
    BonsaiState,
    CommandResult,
    CommandSpec,
    ManagedWorktree,
    OpenUrlPlan,
    PortOwner,
)
from bonsai.process import RecordingRunner
from bonsai.rendering import render_env_local
from bonsai.state import load_state, save_state
from bonsai.workflows import (
    app_snippets_dir,
    command_summary,
    execute_sync,
    plan_all_workspace_summaries,
    plan_current_worktree_status,
    plan_open_url,
    plan_open_url_for_worktree,
    plan_workspace_ports,
    plan_workspace_summary,
    plan_workspace_urls,
    resolve_open_target,
    url_liveness_ok,
)
from bonsai.workflows import probes as wf_probes


def _caddy_open_plan() -> OpenUrlPlan:
    return OpenUrlPlan(
        branch="feature",
        worktree_path=Path("/ws/feature"),
        url="https://feature.authentic.localhost",
        service_name="frontend",
        port=4201,
    )


def test_worktree_has_changes_uses_porcelain_status() -> None:
    class DirtyRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            return CommandResult(returncode=0, stdout=" M src/app.py\n")

    runner = DirtyRunner()

    assert worktree_has_changes(runner, Path("/tmp/repo")) is True
    assert runner.commands == [
        CommandSpec(
            argv=("git", "-C", "/tmp/repo", "status", "--porcelain"),
            cwd=None,
        )
    ]


def test_plan_status_reports_current_worktree_services_and_env_status(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    config = load_config(write_config(default_worktree, VALID_CONFIG))
    expected_env = render_env_local(
        config,
        "feature",
        1,
        feature_worktree,
        workspace_root=workspace_root,
        default_branch="main",
    )
    (feature_worktree / ".env.local").write_text(expected_env, encoding="utf-8")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    status = plan_current_worktree_status(workspace_root, feature_worktree)

    assert status.workspace_name == "authentic"
    assert status.workspace_root == workspace_root
    assert status.config_path == default_worktree / ".bonsai.toml"
    current = status.current
    assert current is not None
    assert current.branch == "feature"
    assert current.worktree_path == feature_worktree
    assert current.slot == 1
    assert current.env_file_path == feature_worktree / ".env.local"
    assert current.env_file_status == "current"
    assert status.generated_env["FRONTEND_PORT"] == "4201"
    assert status.generated_env["BONSAI_BRANCH"] == "feature"
    assert status.generated_env["BONSAI_ROOT_PATH"] == str(workspace_root)
    assert status.generated_env["BONSAI_PRIMARY_URL"] == "https://feature.authentic.localhost"
    assert status.generated_env["COMPOSE_PROJECT_NAME"] == "authentic-feature"
    assert status.commands["start"] == "bonsai start"
    assert current.services[0].name == "frontend"
    assert current.services[0].port_env == "FRONTEND_PORT"
    assert current.services[0].port == 4201
    assert current.services[0].url == "https://feature.authentic.localhost"
    assert current.services[2].name == "db"
    assert current.services[2].public is False
    assert current.services[2].url is None


def test_plan_status_marks_missing_generated_env(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    status = plan_current_worktree_status(workspace_root, default_worktree)

    assert status.current is not None
    assert status.current.branch == "main"
    assert status.current.slot == 0
    assert status.current.env_file_status == "missing"
    assert status.generated_env["FRONTEND_PORT"] == "4200"


def test_plan_status_marks_stale_generated_env(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env.local").write_text("FRONTEND_PORT=9999\n", encoding="utf-8")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    status = plan_current_worktree_status(workspace_root, default_worktree)

    assert status.current is not None
    assert status.current.env_file_status == "stale"
    assert status.generated_env["FRONTEND_PORT"] == "4200"


def test_plan_workspace_summary_includes_default_managed_ports_urls_and_env_status(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    config = load_config(write_config(default_worktree, VALID_CONFIG))
    feature_worktree.joinpath(".env.local").write_text(
        render_env_local(
            config,
            "feature",
            2,
            feature_worktree,
            workspace_root=workspace_root,
            default_branch="main",
        ),
        encoding="utf-8",
    )
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=2)},
        ),
    )

    summary = plan_workspace_summary(workspace_root)

    assert summary.workspace_name == "authentic"
    assert summary.workspace_root == workspace_root
    assert summary.default_branch == "main"
    assert summary.default_worktree == "main"
    assert summary.config_path == default_worktree / ".bonsai.toml"
    assert summary.commands["status"] == "bonsai status"
    assert [worktree.branch for worktree in summary.worktrees] == ["main", "feature"]

    default_summary = summary.worktrees[0]
    assert default_summary.relative_path == "main"
    assert default_summary.kind == "default"
    assert default_summary.slot == 0
    assert default_summary.env_file_status == "missing"
    assert [(service.port_env, service.port) for service in default_summary.services] == [
        ("FRONTEND_PORT", 4200),
        ("API_PORT", 3333),
        ("DB_PORT", 5555),
    ]
    assert default_summary.services[0].url == "https://main.authentic.localhost"
    assert default_summary.services[1].url == "https://api-main.authentic.localhost"
    assert default_summary.services[2].url is None

    feature_summary = summary.worktrees[1]
    assert feature_summary.worktree_path == feature_worktree
    assert feature_summary.relative_path == "feature"
    assert feature_summary.kind == "managed"
    assert feature_summary.slug == "feature"
    assert feature_summary.slot == 2
    assert feature_summary.env_file_path == feature_worktree / ".env.local"
    assert feature_summary.env_file_status == "current"
    assert [(service.port_env, service.port) for service in feature_summary.services] == [
        ("FRONTEND_PORT", 4202),
        ("API_PORT", 3335),
        ("DB_PORT", 5557),
    ]
    assert feature_summary.services[0].url == "https://feature.authentic.localhost"
    assert feature_summary.services[1].url == "https://api-feature.authentic.localhost"
    assert feature_summary.services[2].url is None


def test_plan_all_workspace_summaries_uses_registered_workspaces(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for workspace_root, name in ((first, "first"), (second, "second")):
        default = workspace_root / "main"
        default.mkdir(parents=True)
        write_config(default, VALID_CONFIG.replace('name = "authentic"', f'name = "{name}"'))
        save_state(
            workspace_root / ".bonsai" / "state.json",
            BonsaiState(
                version=1,
                name=name,
                default_branch="main",
                default_worktree="main",
                repo_url="git@example.com:org/repo.git",
                worktrees={},
            ),
        )
        load_state(workspace_root / ".bonsai" / "state.json")

    summaries = plan_all_workspace_summaries()

    assert [(summary.workspace_name, summary.workspace_root) for summary in summaries] == [
        ("first", first),
        ("second", second),
    ]


def test_plan_workspace_summary_marks_stale_generated_env(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    default_worktree.joinpath(".env.local").write_text("FRONTEND_PORT=9999\n", encoding="utf-8")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    summary = plan_workspace_summary(workspace_root)

    assert summary.worktrees[0].env_file_status == "stale"


def test_plan_workspace_summary_wraps_unreadable_generated_env(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    env_file_path = default_worktree / ".env.local"
    env_file_path.write_bytes(b"\xff")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    with pytest.raises(
        BonsaiWorkspaceError,
        match=rf"Unable to read generated env file at {re.escape(str(env_file_path))}",
    ):
        plan_workspace_summary(workspace_root)


def test_plan_current_worktree_status_resolves_current_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    status = plan_current_worktree_status(workspace_root, feature_worktree / "src")

    assert status.workspace_name == "authentic"
    assert status.workspace_root == workspace_root
    assert status.default_branch == "main"
    assert status.default_worktree == "main"
    assert status.config_path == default_worktree / ".bonsai.toml"
    assert status.current.branch == "feature"
    assert status.current.worktree_path == feature_worktree
    assert status.current.relative_path == "feature"
    assert status.current.kind == "managed"
    assert status.current.slot == 1
    assert status.commands["list"] == "bonsai list"


def test_plan_current_worktree_status_reports_workspace_root_location(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    status = plan_current_worktree_status(workspace_root, workspace_root)

    assert status.workspace_name == "authentic"
    assert status.workspace_root == workspace_root
    assert status.location_kind == "workspace_root"
    assert status.location_path == workspace_root
    assert status.current is None
    assert status.commands["list"] == "bonsai list"


def test_plan_workspace_summary_reports_invalid_service_url_template(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    config_text = VALID_CONFIG.replace(
        'url = "https://${slug}.authentic.localhost"',
        'url = "https://${missing}.authentic.localhost"',
        1,
    )
    write_config(default_worktree, config_text)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    with pytest.raises(
        BonsaiConfigError,
        match="Service frontend URL uses unknown template key: missing",
    ):
        plan_workspace_summary(workspace_root)


def test_plan_workspace_ports_classifies_same_worktree_listener_as_owned(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class LsofRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            if argv[0] == "lsof" and "-iTCP:4201" in argv:
                return CommandResult(returncode=0, stdout="p123\ncnode\numichael\n")
            if argv == ["lsof", "-a", "-p", "123", "-d", "cwd", "-Fn"]:
                return CommandResult(returncode=0, stdout=f"p123\nn{feature_worktree}\n")
            return CommandResult(returncode=1)

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature-a"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature-a": ManagedWorktree(path="feature-a", slug="feature-a", slot=1),
            },
        ),
    )
    monkeypatch.setattr("bonsai.workflows.probes._check_port_listening", lambda _port: False)

    plan = plan_workspace_ports(LsofRunner(), workspace_root)
    frontend = next(port for port in plan.ports if port.branch == "feature-a" and port.port == 4201)

    assert frontend.status == "owned"
    assert frontend.owners == (
        PortOwner(
            pid=123,
            command="node",
            user="michael",
            cwd=feature_worktree,
            worktree_branch="feature-a",
            worktree_path=feature_worktree,
        ),
    )


def test_plan_workspace_ports_classifies_compose_published_port_as_owned(
    tmp_path: Path,
    monkeypatch,
) -> None:
    docker_cwd = tmp_path / "docker-data"

    class LsofDockerRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            if argv[0] == "lsof" and "-iTCP:5556" in argv:
                return CommandResult(returncode=0, stdout="p123\nccom.docker.backend\numichael\n")
            if argv == ["lsof", "-a", "-p", "123", "-d", "cwd", "-Fn"]:
                return CommandResult(returncode=0, stdout=f"p123\nn{docker_cwd}\n")
            if argv[:2] == ["docker", "ps"]:
                return CommandResult(returncode=0, stdout="container-1\n")
            if argv == ["docker", "inspect", "container-1"]:
                return CommandResult(
                    returncode=0,
                    stdout=(
                        '[{"Config":{"Labels":{"com.docker.compose.project":"feature-a"}},'
                        '"NetworkSettings":{"Ports":{"5432/tcp":[{"HostPort":"5556"}]}}}]'
                    ),
                )
            return CommandResult(returncode=1)

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature-a"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    (feature_worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature-a": ManagedWorktree(path="feature-a", slug="feature-a", slot=1),
            },
        ),
    )
    monkeypatch.setattr("bonsai.workflows.probes._check_port_listening", lambda _port: False)

    ports = plan_workspace_ports(LsofDockerRunner(), workspace_root).ports
    database = next(port for port in ports if port.branch == "feature-a" and port.port == 5556)
    repair = workflows.plan_port_repairs(workspace_root, runner=LsofDockerRunner())

    assert database.status == "owned"
    assert database.owners == (
        PortOwner(
            pid=123,
            command="com.docker.backend",
            user="michael",
            cwd=docker_cwd,
            worktree_branch="feature-a",
            worktree_path=feature_worktree,
        ),
    )
    assert repair.items == ()


def test_plan_open_url_renders_primary_url_for_current_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "mb-2036-multi-worktree-port-slots"
    nested_dir = branch_worktree / "src"
    nested_dir.mkdir(parents=True)
    default_worktree.mkdir()
    config_text = VALID_CONFIG.replace(
        'url = "https://${slug}.authentic.localhost"',
        'url = "https://${slug}-${FRONTEND_PORT}.authentic.localhost"',
        1,
    )
    write_config(default_worktree, config_text)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "MB-2036-multi-worktree-port-slots": ManagedWorktree(
                    path="mb-2036-multi-worktree-port-slots",
                    slug="mb-2036-multi-worktree-port-slots",
                    slot=2,
                )
            },
        ),
    )

    plan = plan_open_url(workspace_root, nested_dir)

    assert plan.branch == "MB-2036-multi-worktree-port-slots"
    assert plan.worktree_path == branch_worktree
    assert plan.url == "https://mb-2036-multi-worktree-port-slots-4202.authentic.localhost"
    assert plan.via == "caddy"


def test_resolve_open_target_keeps_caddy_when_caddy_listener_up(monkeypatch) -> None:
    monkeypatch.setattr(wf_probes, "_check_caddy_listening", lambda: True)
    monkeypatch.setattr(
        wf_probes,
        "_check_port_listening",
        lambda _port: pytest.fail("port probe must not gate the Caddy route"),
    )

    resolved = resolve_open_target(_caddy_open_plan())

    assert resolved.via == "caddy"
    assert resolved.url == "https://feature.authentic.localhost"


def test_resolve_open_target_demotes_to_port_when_caddy_down(monkeypatch) -> None:
    monkeypatch.setattr(wf_probes, "_check_caddy_listening", lambda: False)
    monkeypatch.setattr(wf_probes, "_check_port_listening", lambda port: port == 4201)

    resolved = resolve_open_target(_caddy_open_plan())

    assert resolved.via == "port"
    assert resolved.url == "http://localhost:4201"
    assert resolved.port == 4201


def test_resolve_open_target_keeps_caddy_plan_when_nothing_responds(monkeypatch) -> None:
    monkeypatch.setattr(wf_probes, "_check_caddy_listening", lambda: False)
    monkeypatch.setattr(wf_probes, "_check_port_listening", lambda _port: False)

    resolved = resolve_open_target(_caddy_open_plan())

    assert resolved.via == "caddy"
    assert resolved.url == "https://feature.authentic.localhost"


def test_url_liveness_ok_caddy_requires_listener_and_app_port(monkeypatch) -> None:
    monkeypatch.setattr(wf_probes, "_check_caddy_listening", lambda: True)
    monkeypatch.setattr(wf_probes, "_check_port_listening", lambda port: port == 4201)

    assert url_liveness_ok(_caddy_open_plan()) is True


def test_url_liveness_ok_caddy_is_false_when_app_port_dead(monkeypatch) -> None:
    monkeypatch.setattr(wf_probes, "_check_caddy_listening", lambda: True)
    monkeypatch.setattr(wf_probes, "_check_port_listening", lambda _port: False)

    assert url_liveness_ok(_caddy_open_plan()) is False


def test_url_liveness_ok_port_uses_port_probe(monkeypatch) -> None:
    port_plan = OpenUrlPlan(
        branch="feature",
        worktree_path=Path("/ws/feature"),
        url="http://localhost:4201",
        service_name="frontend",
        port=4201,
        via="port",
    )
    monkeypatch.setattr(
        wf_probes,
        "_check_caddy_listening",
        lambda: pytest.fail("port liveness must not consult the Caddy probe"),
    )
    monkeypatch.setattr(wf_probes, "_check_port_listening", lambda port: port == 4201)

    assert url_liveness_ok(port_plan) is True


def test_open_url_plan_defaults_to_caddy_via() -> None:
    plan = OpenUrlPlan(
        branch="feature",
        worktree_path=Path("/ws/feature"),
        url="https://feature.authentic.localhost",
        service_name="frontend",
        port=4201,
    )

    assert plan.via == "caddy"


def test_plan_open_url_rejects_directory_outside_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    with pytest.raises(BonsaiWorkspaceError, match="Current directory is not inside"):
        plan_open_url(workspace_root, workspace_root)


def test_command_summary_formats_command_and_cwd() -> None:
    summary = command_summary(
        CommandSpec(argv=("yarn", "install"), cwd=Path("/tmp/authentic/main"))
    )

    assert summary == "cd /tmp/authentic/main && yarn install"


def test_command_summary_shell_quotes_command_and_cwd() -> None:
    summary = command_summary(
        CommandSpec(argv=("python", "-c", "print(1)"), cwd=Path("/tmp/space dir"))
    )

    assert summary == "cd '/tmp/space dir' && python -c 'print(1)'"


def test_plan_open_url_for_worktree_renders_named_managed_worktree_url(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    plan = plan_open_url_for_worktree(workspace_root, "feature")

    assert plan.branch == "feature"
    assert plan.worktree_path == feature_worktree
    assert plan.url == "https://feature.authentic.localhost"


def test_plan_open_url_still_resolves_current_worktree(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    plan = plan_open_url(workspace_root, feature_worktree)

    assert plan.branch == "feature"
    assert plan.worktree_path == feature_worktree.resolve()
    assert plan.url == "https://feature.authentic.localhost"


def test_plan_open_url_for_worktree_rejects_unknown_name(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    with pytest.raises(BonsaiWorkspaceError, match="Unknown Bonsai worktree"):
        plan_open_url_for_worktree(workspace_root, "feature")


def test_plan_open_url_for_worktree_renders_selected_service_url(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    plan = plan_open_url_for_worktree(workspace_root, "feature", service_name="api")

    assert plan.branch == "feature"
    assert plan.worktree_path == feature_worktree
    assert plan.service_name == "api"
    assert plan.port == 3334
    assert plan.url == "https://api-feature.authentic.localhost"


def test_plan_open_url_for_worktree_rejects_private_or_unknown_service(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    with pytest.raises(BonsaiConfigError, match="No public URL service named db"):
        plan_open_url_for_worktree(workspace_root, "main", service_name="db")


def test_plan_workspace_urls_reports_route_tls_and_app_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class UrlRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["caddy", "version"]:
                return CommandResult(returncode=0, stdout="v2.8.0\n")
            if argv[:2] == ["caddy", "validate"]:
                return CommandResult(returncode=1, stderr="missing Caddyfile\n")
            if argv[0] == "lsof":
                return CommandResult(returncode=1)
            return CommandResult(returncode=1)

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )
    monkeypatch.setattr(wf_probes, "_check_port_listening", lambda _port: False)
    monkeypatch.setattr(
        wf_probes,
        "_check_caddy_listening",
        lambda: pytest.fail("`urls` must not probe Caddy or demote to the port URL"),
    )

    plan = plan_workspace_urls(UrlRunner(), workspace_root, name="feature", service_name="frontend")

    assert len(plan.urls) == 1
    item = plan.urls[0]
    assert item.branch == "feature"
    assert item.service_name == "frontend"
    assert item.port == 4201
    assert item.url == "https://feature.authentic.localhost"
    assert not item.url.startswith("http://localhost")
    assert item.caddy_snippet_path == app_snippets_dir("authentic") / "feature-frontend.caddy"
    checks = {check.name: check for check in item.checks}
    assert checks["root Caddyfile"].status == "fail"
    assert checks["Caddy route"].status == "fail"
    assert checks["Caddy validate"].status == "fail"
    assert checks["app listener"].status == "warn"
    assert checks["TLS"].status == "fail"
    assert checks["local CA trust"].status == "warn"
    assert "bonsai sync --apply" in checks["Caddy route"].hint
    assert "bonsai start feature" in checks["app listener"].hint


def test_plan_workspace_urls_matches_diagnosed_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )
    execute_sync(RecordingRunner(), workspace_root, apply=True)
    monkeypatch.setattr(wf_probes, "_check_port_listening", lambda _port: False)

    plan = plan_workspace_urls(
        RecordingRunner(),
        workspace_root,
        diagnose_url="https://api-feature.authentic.localhost",
    )

    assert [(item.branch, item.service_name, item.url) for item in plan.urls] == [
        ("feature", "api", "https://api-feature.authentic.localhost")
    ]


def test_plan_workspace_urls_rejects_unconfigured_diagnosed_url(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    with pytest.raises(BonsaiWorkspaceError, match="URL is not configured by Bonsai"):
        plan_workspace_urls(
            RecordingRunner(),
            workspace_root,
            diagnose_url="https://missing.authentic.localhost",
        )
