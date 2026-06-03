import json
from pathlib import Path

from bonsai.config import load_config
from bonsai.onboarding import (
    StarterConfig,
    detect_project_defaults,
    render_starter_config,
    write_starter_config,
)


def _starter_from_defaults(defaults: object) -> StarterConfig:
    return StarterConfig(
        name=defaults.app_name,
        base_branch=defaults.base_branch,
        install_command=defaults.install_command,
        setup_command=defaults.setup_command,
        start_command=defaults.start_command,
        symlink_env=defaults.has_env_file,
        service_name=defaults.service_name,
        port_env=defaults.port_env,
        base_port=defaults.base_port,
        url=defaults.url,
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
    assert defaults.service_name == "frontend"
    assert defaults.port_env == "PORT"
    assert defaults.base_port == 3000
    assert defaults.url == "https://${slug}.authentic.localhost"

    text = render_starter_config(_starter_from_defaults(defaults))
    assert 'install = "pnpm install"' in text
    assert 'start = "pnpm dev"' in text
    assert 'name = "frontend"' in text
    assert "setup =" not in text


def test_detect_project_defaults_bare_repo_keeps_frontend_default(tmp_path: Path) -> None:
    defaults = detect_project_defaults(
        tmp_path,
        fallback_name="bonsai-thing",
        base_branch="main",
    )

    assert defaults.service_name == "frontend"
    assert defaults.port_env == "PORT"
    assert defaults.base_port == 3000
    assert defaults.install_command is None
    assert defaults.setup_command is None
    assert defaults.start_command is None
    assert defaults.has_env_file is False
    assert defaults.url == "https://${slug}.bonsai-thing.localhost"


def test_detect_project_defaults_python_uv(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "svc"\n[project.scripts]\nserve = "svc:main"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    defaults = detect_project_defaults(
        tmp_path,
        fallback_name="bonsai-svc",
        base_branch="main",
    )

    assert defaults.install_command == "uv sync"
    assert defaults.start_command == "serve"
    assert defaults.service_name == "api"
    assert defaults.port_env == "API_PORT"
    assert defaults.base_port == 8000

    path = write_starter_config(tmp_path / ".bonsai.toml", _starter_from_defaults(defaults))
    config = load_config(path)
    assert config.commands.install == "uv sync"
    assert config.commands.start == "serve"
    assert config.primary_service().name == "api"


def test_detect_project_defaults_python_requirements_without_scripts(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")

    defaults = detect_project_defaults(
        tmp_path,
        fallback_name="bonsai-svc",
        base_branch="main",
    )

    assert defaults.install_command == "pip install -r requirements.txt"
    assert defaults.start_command is None
    assert defaults.service_name == "api"
    assert defaults.port_env == "API_PORT"
    assert defaults.base_port == 8000


def test_detect_project_defaults_go(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/svc\n\ngo 1.22\n", encoding="utf-8")

    defaults = detect_project_defaults(
        tmp_path,
        fallback_name="bonsai-svc",
        base_branch="main",
    )

    assert defaults.install_command == "go mod download"
    assert defaults.start_command == "go run ."
    assert defaults.service_name == "app"
    assert defaults.port_env == "PORT"
    assert defaults.base_port == 8080

    path = write_starter_config(tmp_path / ".bonsai.toml", _starter_from_defaults(defaults))
    config = load_config(path)
    assert config.commands.install == "go mod download"
    assert config.commands.start == "go run ."
    assert config.primary_service().name == "app"


def test_detect_project_defaults_rails(tmp_path: Path) -> None:
    (tmp_path / "Gemfile").write_text('source "https://rubygems.org"\n', encoding="utf-8")

    defaults = detect_project_defaults(
        tmp_path,
        fallback_name="bonsai-web",
        base_branch="main",
    )

    assert defaults.install_command == "bundle install"
    assert defaults.start_command == "bin/rails server"
    assert defaults.service_name == "web"
    assert defaults.port_env == "PORT"
    assert defaults.base_port == 3000

    path = write_starter_config(tmp_path / ".bonsai.toml", _starter_from_defaults(defaults))
    config = load_config(path)
    assert config.commands.install == "bundle install"
    assert config.commands.start == "bin/rails server"
    assert config.primary_service().name == "web"


def test_detect_project_defaults_compose_only(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    defaults = detect_project_defaults(
        tmp_path,
        fallback_name="bonsai-app",
        base_branch="main",
    )

    assert defaults.install_command is None
    assert defaults.start_command == "docker compose up"
    assert defaults.service_name == "app"
    assert defaults.port_env == "PORT"
    assert defaults.base_port == 8080

    path = write_starter_config(tmp_path / ".bonsai.toml", _starter_from_defaults(defaults))
    config = load_config(path)
    assert config.commands.start == "docker compose up"
    assert config.commands.install is None
    assert config.primary_service().name == "app"


def test_detect_project_defaults_makefile_fallback(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text(
        "install:\n\tpip install .\ndev:\n\tpython -m svc\n",
        encoding="utf-8",
    )

    defaults = detect_project_defaults(
        tmp_path,
        fallback_name="bonsai-app",
        base_branch="main",
    )

    assert defaults.install_command == "make install"
    assert defaults.start_command == "make dev"
    assert defaults.service_name == "app"
    assert defaults.port_env == "PORT"
    assert defaults.base_port == 8080

    path = write_starter_config(tmp_path / ".bonsai.toml", _starter_from_defaults(defaults))
    config = load_config(path)
    assert config.commands.install == "make install"
    assert config.commands.start == "make dev"


def test_detect_project_defaults_prefers_language_over_compose(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    defaults = detect_project_defaults(
        tmp_path,
        fallback_name="bonsai-svc",
        base_branch="main",
    )

    assert defaults.service_name == "api"
    assert defaults.install_command == "pip install -r requirements.txt"


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
