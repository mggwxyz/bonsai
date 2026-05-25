import json
from pathlib import Path

from bonsai.config import load_config
from bonsai.onboarding import (
    StarterConfig,
    detect_project_defaults,
    render_starter_config,
    write_starter_config,
)


def test_detect_project_defaults_uses_package_scripts_and_lockfile(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "@quiller/authentic", "scripts": {"dev": "vite --host"}}),
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value\n", encoding="utf-8")

    defaults = detect_project_defaults(
        tmp_path,
        fallback_name="bonsai-authentic",
        base_branch="main",
    )

    assert defaults.app_name == "authentic"
    assert defaults.install_command == "pnpm install"
    assert defaults.setup_command is None
    assert defaults.start_command == "pnpm dev"
    assert defaults.has_env_file is True
    assert defaults.url == "https://${slug}.authentic.localhost"


def test_render_starter_config_loads_as_valid_bonsai_config() -> None:
    text = render_starter_config(
        StarterConfig(
            name="authentic",
            base_branch="main",
            install_command="pnpm install",
            setup_command="pnpm setup",
            start_command="pnpm dev",
            symlink_env=True,
            service_name="frontend",
            port_env="FRONTEND_PORT",
            base_port=4200,
            url="https://${slug}.authentic.localhost",
        )
    )

    assert 'name = "authentic"' in text
    assert 'install = "pnpm install"' in text
    assert 'setup = "pnpm setup"' in text
    assert 'source = ".env"' in text


def test_write_starter_config_creates_loadable_file(tmp_path: Path) -> None:
    path = write_starter_config(
        tmp_path / ".bonsai.toml",
        StarterConfig(
            name="my app",
            base_branch="staging",
            install_command=None,
            setup_command=None,
            start_command=None,
            symlink_env=False,
            service_name="web",
            port_env="PORT",
            base_port=3000,
            url="https://${slug}.my-app.localhost",
        ),
    )

    config = load_config(path)

    assert config.name == "my app"
    assert config.base_branch == "staging"
    assert config.commands.install is None
    assert config.commands.setup is None
    assert config.shared_files == ()
    assert config.primary_service().name == "web"
