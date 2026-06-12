from __future__ import annotations

from pathlib import Path

from bonsai.errors import BonsaiConfigError, BonsaiWorkspaceError
from bonsai.models import (
    BonsaiConfig,
    CommandResult,
    ManagedWorktree,
    OpenUrlPlan,
    UrlCheck,
    WorkspacePort,
    WorkspaceStatus,
    WorkspaceSummary,
    WorkspaceUrl,
    WorkspaceUrlsPlan,
    WorktreeTarget,
)
from bonsai.process import Runner
from bonsai.registry import read_workspace_registry
from bonsai.rendering import (
    render_caddy_snippets,
    render_root_caddyfile,
    template_values,
)
from bonsai.state import load_state
from bonsai.templates import render_template
from bonsai.workflows import probes
from bonsai.workflows.inspection import (
    _port_owner_detail,
    _port_owner_label,
    plan_workspace_ports,
)
from bonsai.workflows.shared import (
    _app_snippet_dirs,
    _configured_worktree_targets,
    _resolve_current_worktree,
    _workspace_summary_commands,
    app_snippets_dir,
    global_caddy_paths,
    load_workspace_config,
    resolve_start_target,
    resolve_workspace_config_path,
)
from bonsai.workspace_facts import build_worktree_facts


def _public_url_service(config: BonsaiConfig, service_name: str | None):
    if service_name is None:
        try:
            return config.primary_service()
        except ValueError as exc:
            raise BonsaiConfigError("No primary public service configured") from exc
    for service in config.public_services():
        if service.name == service_name and service.url is not None:
            return service
    raise BonsaiConfigError(f"No public URL service named {service_name}")


def _plan_service_open_url(
    config: BonsaiConfig,
    branch: str,
    worktree: ManagedWorktree,
    worktree_path: Path,
    service_name: str | None = None,
    *,
    workspace_root: Path | None = None,
    default_branch: str | None = None,
) -> OpenUrlPlan:
    service = _public_url_service(config, service_name)
    if service.url is None:
        raise BonsaiConfigError("Primary public service does not have a URL")

    values = template_values(
        config,
        branch,
        worktree.slot,
        worktree_path,
        workspace_root=workspace_root,
        default_branch=default_branch,
    )
    try:
        url = render_template(service.url, values)
    except KeyError as exc:
        key = exc.args[0]
        raise BonsaiConfigError(f"Primary URL uses unknown template key: {key}") from exc
    except ValueError as exc:
        raise BonsaiConfigError(f"Invalid primary URL template: {exc}") from exc

    return OpenUrlPlan(
        branch=branch,
        worktree_path=worktree_path,
        url=url,
        service_name=service.name,
        port=service.base_port + worktree.slot,
        workspace_name=config.name,
        browser_extension_id=config.browser_extension.extension_id,
    )


def _port_open_plan(plan: OpenUrlPlan) -> OpenUrlPlan:
    return OpenUrlPlan(
        branch=plan.branch,
        worktree_path=plan.worktree_path,
        url=f"http://localhost:{plan.port}",
        service_name=plan.service_name,
        port=plan.port,
        workspace_name=plan.workspace_name,
        browser_extension_id=plan.browser_extension_id,
        via="port",
    )


def resolve_open_target(plan: OpenUrlPlan) -> OpenUrlPlan:
    """Choose between the Caddy route and the direct port for an open target.

    Runner-free, probe-driven, and opt-in: only the open/wizard flow calls this.
    ``bonsai urls`` keeps using the plain Caddy ``OpenUrlPlan`` so its output is
    unaffected. When Caddy's HTTPS listener is up the existing
    ``https://…localhost`` URL is kept (``via="caddy"``). When Caddy is down but
    the app port is live the plan is demoted to ``http://localhost:<port>``
    (``via="port"``). When neither responds the Caddy plan is returned unchanged
    so the caller's liveness gate can report the dead route.
    """
    if probes._check_caddy_listening():
        return plan
    if probes._check_port_listening(plan.port):
        return _port_open_plan(plan)
    return plan


def url_liveness_ok(plan: OpenUrlPlan) -> bool:
    """Confirm the chosen open target is actually reachable.

    Runner-free. The Caddy plan requires BOTH Caddy's HTTPS listener AND the
    app's own backend port (the port Caddy reverse-proxies to) to be live, so a
    persistent Caddy service can never greenlight a dead app. The port plan is
    gated on the app port alone; the port probe never greenlights the Caddy URL.
    """
    if plan.via == "caddy":
        return probes._check_caddy_listening() and probes._check_port_listening(plan.port)
    return probes._check_port_listening(plan.port)


def plan_open_url(
    workspace_root: Path,
    current_path: Path,
    service_name: str | None = None,
) -> OpenUrlPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    branch, worktree, worktree_path = _resolve_current_worktree(state, workspace_root, current_path)
    return _plan_service_open_url(
        config,
        branch,
        worktree,
        worktree_path,
        service_name=service_name,
        workspace_root=workspace_root,
        default_branch=state.default_branch,
    )


def plan_open_url_for_worktree(
    workspace_root: Path,
    name: str,
    service_name: str | None = None,
) -> OpenUrlPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    target = resolve_start_target(workspace_root, name, workspace_root)
    return _plan_service_open_url(
        config,
        target.branch,
        target.worktree,
        target.worktree_path,
        service_name=service_name,
        workspace_root=workspace_root,
        default_branch=state.default_branch,
    )


def _workspace_url_checks(
    runner: Runner,
    workspace_root: Path,
    config: BonsaiConfig,
    target: WorktreeTarget,
    service,
    caddy_snippet_path: Path,
    port_status: WorkspacePort,
    *,
    default_branch: str | None = None,
) -> tuple[UrlCheck, ...]:
    root_caddyfile, snippets_root = global_caddy_paths()
    expected_root = render_root_caddyfile(_app_snippet_dirs(snippets_root))
    port = service.base_port + target.worktree.slot
    expected_route = render_caddy_snippets(
        config,
        target.branch,
        target.worktree.slot,
        target.worktree_path,
        workspace_root=workspace_root,
        default_branch=default_branch,
    )[service.name]

    checks: list[UrlCheck] = []
    if not root_caddyfile.exists():
        checks.append(
            UrlCheck(
                "root Caddyfile",
                "fail",
                f"Missing {root_caddyfile}",
                "Run: bonsai sync --apply",
            )
        )
    elif root_caddyfile.read_text(encoding="utf-8") != expected_root:
        checks.append(
            UrlCheck(
                "root Caddyfile",
                "fail",
                f"Stale {root_caddyfile}",
                "Run: bonsai sync --apply",
            )
        )
    else:
        checks.append(
            UrlCheck(
                "root Caddyfile",
                "ok",
                f"imports app snippets under {snippets_root}",
            )
        )

    route_content = None
    if caddy_snippet_path.exists():
        route_content = caddy_snippet_path.read_text(encoding="utf-8")
    if route_content is None:
        checks.append(
            UrlCheck(
                "Caddy route",
                "fail",
                f"Missing {caddy_snippet_path}",
                "Run: bonsai sync --apply",
            )
        )
    elif route_content != expected_route:
        checks.append(
            UrlCheck(
                "Caddy route",
                "fail",
                f"Stale {caddy_snippet_path}",
                "Run: bonsai sync --apply",
            )
        )
    else:
        checks.append(
            UrlCheck(
                "Caddy route",
                "ok",
                f"{caddy_snippet_path} routes to localhost:{port}",
            )
        )

    if not root_caddyfile.exists():
        checks.append(
            UrlCheck(
                "Caddy validate",
                "fail",
                f"Cannot validate missing {root_caddyfile}",
                "Run: bonsai sync --apply",
            )
        )
    else:
        try:
            caddy = runner.run(
                ["caddy", "validate", "--config", str(root_caddyfile)],
                check=False,
            )
        except (FileNotFoundError, OSError):
            caddy = CommandResult(returncode=127, stderr="caddy not found")
        checks.append(
            UrlCheck(
                "Caddy validate",
                "ok" if caddy.returncode == 0 else "fail",
                caddy.stdout.strip() or caddy.stderr.strip() or "caddy validate failed",
                "Run: bonsai doctor --apply" if caddy.returncode != 0 else None,
            )
        )

    checks.append(_workspace_url_app_check(port_status, target.branch))
    if service.url is not None and service.url.startswith("http://"):
        checks.append(
            UrlCheck(
                "TLS",
                "warn",
                "URL uses HTTP; TLS is not configured for this route",
            )
        )
        checks.append(
            UrlCheck(
                "local CA trust",
                "ok",
                "not required for HTTP URLs",
            )
        )
    elif route_content is not None and "\ttls internal" in route_content:
        checks.append(
            UrlCheck(
                "TLS",
                "ok",
                "route uses Caddy internal TLS",
            )
        )
        checks.append(
            UrlCheck(
                "local CA trust",
                "warn",
                "Caddy internal certificates require local CA trust in browsers",
                "Run: caddy trust",
            )
        )
    else:
        checks.append(
            UrlCheck(
                "TLS",
                "fail",
                "route is missing tls internal",
                "Run: bonsai sync --apply",
            )
        )
        checks.append(
            UrlCheck(
                "local CA trust",
                "warn",
                "verify browser trust after TLS is restored",
                "Run: caddy trust",
            )
        )
    return tuple(checks)


def _workspace_url_app_check(port: WorkspacePort, branch: str) -> UrlCheck:
    if port.status == "owned":
        owner_text = ", ".join(_port_owner_label(owner) for owner in port.owners)
        return UrlCheck(
            "app listener",
            "ok",
            f"{port.port_env}={port.port} owned by {owner_text}",
        )
    if port.status == "free":
        return UrlCheck(
            "app listener",
            "warn",
            f"no listener detected on localhost:{port.port}",
            f"Run: bonsai start {branch}",
        )
    if port.status == "unknown":
        return UrlCheck(
            "app listener",
            "fail",
            f"localhost:{port.port} is busy but the owner could not be identified",
            "Run: bonsai ports --busy",
        )
    return UrlCheck(
        "app listener",
        "fail",
        _port_owner_detail(port),
        "Run: bonsai repair-ports",
    )


def plan_workspace_urls(
    runner: Runner,
    workspace_root: Path,
    name: str | None = None,
    service_name: str | None = None,
    diagnose_url: str | None = None,
) -> WorkspaceUrlsPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    snippets_dir = app_snippets_dir(config.name)
    targets = (
        (resolve_start_target(workspace_root, name, workspace_root),)
        if name is not None
        else _configured_worktree_targets(state, workspace_root)
    )
    port_statuses = {
        (port.branch, port.service_name): port
        for port in plan_workspace_ports(runner, workspace_root).ports
    }
    items: list[WorkspaceUrl] = []
    for target in targets:
        services = (
            (_public_url_service(config, service_name),)
            if service_name is not None
            else tuple(service for service in config.public_services() if service.url is not None)
        )
        for service in services:
            plan = _plan_service_open_url(
                config,
                target.branch,
                target.worktree,
                target.worktree_path,
                service_name=service.name,
                workspace_root=workspace_root,
                default_branch=state.default_branch,
            )
            if diagnose_url is not None and plan.url != diagnose_url:
                continue
            caddy_snippet_path = snippets_dir / f"{target.worktree.slug}-{service.name}.caddy"
            items.append(
                WorkspaceUrl(
                    branch=target.branch,
                    worktree_path=target.worktree_path,
                    service_name=service.name,
                    port_env=service.port_env,
                    port=service.base_port + target.worktree.slot,
                    primary=service.primary,
                    url=plan.url,
                    caddy_snippet_path=caddy_snippet_path,
                    checks=_workspace_url_checks(
                        runner,
                        workspace_root,
                        config,
                        target,
                        service,
                        caddy_snippet_path,
                        port_statuses[(target.branch, service.name)],
                        default_branch=state.default_branch,
                    ),
                )
            )
    if diagnose_url is not None and not items:
        raise BonsaiWorkspaceError(f"URL is not configured by Bonsai: {diagnose_url}")
    return WorkspaceUrlsPlan(
        workspace_root=workspace_root,
        caddyfile=global_caddy_paths()[0],
        urls=tuple(items),
    )


def plan_workspace_summary(workspace_root: Path) -> WorkspaceSummary:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    config_path = config.path or resolve_workspace_config_path(
        workspace_root,
        state.default_worktree,
    )
    targets = _configured_worktree_targets(state, workspace_root)
    default_target = targets[0]
    managed_targets = sorted(targets[1:], key=lambda target: target.branch.lower())
    worktrees = [
        build_worktree_facts(
            config,
            default_target,
            "default",
            workspace_root=workspace_root,
            default_branch=state.default_branch,
        ).summary
    ]
    worktrees.extend(
        build_worktree_facts(
            config,
            target,
            "managed",
            workspace_root=workspace_root,
            default_branch=state.default_branch,
        ).summary
        for target in managed_targets
    )
    return WorkspaceSummary(
        workspace_name=state.name,
        workspace_root=workspace_root,
        default_branch=state.default_branch,
        default_worktree=state.default_worktree,
        config_path=config_path,
        worktrees=tuple(worktrees),
        commands=_workspace_summary_commands(),
    )


def plan_all_workspace_summaries() -> tuple[WorkspaceSummary, ...]:
    return tuple(plan_workspace_summary(entry.root) for entry in read_workspace_registry())


def plan_current_worktree_status(
    workspace_root: Path,
    current_path: Path,
) -> WorkspaceStatus:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    config_path = config.path or resolve_workspace_config_path(
        workspace_root,
        state.default_worktree,
    )
    current_path_resolved = current_path.resolve()
    if current_path_resolved == workspace_root.resolve():
        return WorkspaceStatus(
            workspace_name=state.name,
            workspace_root=workspace_root,
            default_branch=state.default_branch,
            default_worktree=state.default_worktree,
            config_path=config_path,
            current=None,
            location_kind="workspace_root",
            location_path=workspace_root,
            commands=_workspace_summary_commands(),
        )

    branch, worktree, worktree_path = _resolve_current_worktree(
        state,
        workspace_root,
        current_path_resolved,
    )
    kind = "default" if branch == state.default_branch else "managed"
    target = WorktreeTarget(branch=branch, worktree=worktree, worktree_path=worktree_path)
    facts = build_worktree_facts(
        config,
        target,
        kind,
        workspace_root=workspace_root,
        default_branch=state.default_branch,
    )
    return WorkspaceStatus(
        workspace_name=state.name,
        workspace_root=workspace_root,
        default_branch=state.default_branch,
        default_worktree=state.default_worktree,
        config_path=config_path,
        current=facts.summary,
        location_kind="worktree",
        location_path=worktree_path,
        commands=_workspace_summary_commands(),
        generated_env=facts.generated_env,
    )
