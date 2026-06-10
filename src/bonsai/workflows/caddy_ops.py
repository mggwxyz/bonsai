from __future__ import annotations

from pathlib import Path

from bonsai.caddy import (
    caddy_boot_config_path,
    caddy_reload_plan,
    caddy_setup_plan,
    merge_boot_config,
)
from bonsai.models import (
    BonsaiConfig,
    CaddySetupResult,
    DoctorApplyAction,
    DoctorCheck,
)
from bonsai.process import Runner
from bonsai.rendering import (
    render_root_caddyfile,
)
from bonsai.state import load_state
from bonsai.workflows.shared import (
    _app_snippet_dirs,
    _command_available,
    command_summary,
    global_caddy_paths,
    load_workspace_config,
)


def _run_caddy_setup(runner: Runner, config: BonsaiConfig) -> CaddySetupResult:
    if not config.public_services():
        return CaddySetupResult()

    commands = caddy_setup_plan(
        auto_install=config.caddy.auto_install,
        auto_start=config.caddy.auto_start,
        caddy_exists=_command_available(runner, ["caddy", "version"]),
        brew_exists=_command_available(runner, ["brew", "--version"]),
    )
    actions: list[DoctorApplyAction] = []
    for command in commands:
        result = runner.run(list(command.argv), cwd=command.cwd, check=False)
        if result.returncode != 0:
            check = DoctorCheck(
                name="caddy",
                status="fail",
                detail=f"{command_summary(command)} failed ({result.returncode})",
                hint=(
                    "Caddy install/start failed - Bonsai will use a direct port URL. "
                    "Fix later with `brew install caddy` then `bonsai doctor`."
                ),
                id="caddy-setup",
            )
            return CaddySetupResult(actions=tuple(actions), checks=(check,))
        actions.append(DoctorApplyAction(kind="caddy", detail=command_summary(command)))
    return CaddySetupResult(actions=tuple(actions))


def setup_caddy(runner: Runner, workspace_root: Path) -> CaddySetupResult:
    """Run Caddy install/start for a workspace, loading its config first.

    Thin seam over ``_run_caddy_setup`` for callers that hold a workspace root
    rather than a loaded ``BonsaiConfig`` (e.g. the guided ``start-here`` flow).
    """
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    return _run_caddy_setup(runner, config)


def reload_workspace_caddy(runner: Runner) -> None:
    root_caddyfile, snippets_root = global_caddy_paths()
    snippets_root.mkdir(parents=True, exist_ok=True)
    app_dirs = _app_snippet_dirs(snippets_root)
    expected_root = render_root_caddyfile(app_dirs)
    if not root_caddyfile.exists() or root_caddyfile.read_text(encoding="utf-8") != expected_root:
        root_caddyfile.parent.mkdir(parents=True, exist_ok=True)
        root_caddyfile.write_text(expected_root, encoding="utf-8")
    _ensure_caddy_boot_config(runner, app_dirs)
    command = caddy_reload_plan(root_caddyfile)
    runner.run(list(command.argv), cwd=command.cwd)


def _ensure_caddy_boot_config(runner: Runner, app_dirs: list[Path]) -> None:
    if not _command_available(runner, ["caddy", "version"]):
        return
    boot_path = caddy_boot_config_path(runner)
    if boot_path is None:
        return
    existing = boot_path.read_text(encoding="utf-8") if boot_path.exists() else ""
    import_lines = [f"import {directory}/*.caddy" for directory in app_dirs]
    merged = merge_boot_config(existing, import_lines)
    if merged != existing:
        boot_path.parent.mkdir(parents=True, exist_ok=True)
        boot_path.write_text(merged, encoding="utf-8")
