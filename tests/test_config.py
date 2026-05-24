from pathlib import Path

import pytest

from bonsai.config import load_config
from bonsai.errors import BonsaiConfigError

VALID_CONFIG = """
name = "authentic"
base_branch = "main"

[workspace]
default_parent = "~/Projects"

[caddy]
auto_install = true
auto_start = true
root_caddyfile = "Caddyfile"
snippets_dir = "caddy.d"

[commands]
install = "yarn install"
start = "yarn dev"
migrate = "yarn docker:migrate --abort-on-container-exit"

[[shared_files]]
source = ".env"
target = ".env"
mode = "symlink"

[[env]]
name = "COMPOSE_PROJECT_NAME"
value = "authentic-${slug}"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"

[[services]]
name = "api"
port_env = "API_PORT"
base_port = 3333
url = "https://api-${slug}.authentic.localhost"

[[services]]
name = "db"
port_env = "DB_PORT"
base_port = 5555
public = false
"""


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".bonsai.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_config_parses_valid_file(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))

    assert config.name == "authentic"
    assert config.base_branch == "main"
    assert config.workspace.default_parent == "~/Projects"
    assert config.caddy.snippets_dir == "caddy.d"
    assert config.commands.start == "yarn dev"
    assert config.shared_files[0].source == ".env"
    assert config.env[0].name == "COMPOSE_PROJECT_NAME"
    assert [service.name for service in config.services] == ["frontend", "api", "db"]
    assert config.primary_service().name == "frontend"


def test_missing_config_file_raises_domain_error(tmp_path: Path) -> None:
    with pytest.raises(BonsaiConfigError, match="Missing .bonsai.toml"):
        load_config(tmp_path / ".bonsai.toml")


def test_duplicate_service_names_are_rejected(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace('name = "api"', 'name = "frontend"')

    with pytest.raises(BonsaiConfigError, match="Duplicate service name: frontend"):
        load_config(write_config(tmp_path, text))


def test_multiple_primary_public_services_are_rejected(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace(
        'url = "https://api-${slug}.authentic.localhost"',
        'primary = true\nurl = "https://api-${slug}.authentic.localhost"',
    )

    with pytest.raises(BonsaiConfigError, match="Multiple primary public services"):
        load_config(write_config(tmp_path, text))


def test_public_service_requires_url(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace('url = "https://api-${slug}.authentic.localhost"', "")

    with pytest.raises(BonsaiConfigError, match="Public service api requires a url"):
        load_config(write_config(tmp_path, text))


def test_public_services_require_one_primary(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace("primary = true\n", "")

    with pytest.raises(BonsaiConfigError, match="Exactly one primary public service is required"):
        load_config(write_config(tmp_path, text))
