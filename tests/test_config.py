import re
from pathlib import Path

import pytest

from bonsai.config import load_config
from bonsai.errors import BonsaiConfigError
from bonsai.models import BonsaiState
from bonsai.state import save_state
from bonsai.workflows.shared import load_workspace_config

VALID_CONFIG = """
name = "authentic"
base_branch = "main"

[caddy]
auto_install = true
auto_start = true

[commands]
install = "yarn install"
setup = "yarn setup"
start = "yarn dev"

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


def write_local_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".bonsai.local.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_config_parses_valid_file(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))

    assert config.name == "authentic"
    assert config.base_branch == "main"
    assert config.caddy.auto_install is True
    assert config.commands.install == "yarn install"
    assert config.commands.setup == "yarn setup"
    assert config.commands.start == "yarn dev"
    assert not hasattr(config.commands, "migrate")
    assert config.run.mode == "concurrent"
    assert config.shared_files[0].source == ".env"
    assert config.env[0].name == "COMPOSE_PROJECT_NAME"
    assert [service.name for service in config.services] == ["frontend", "api", "db"]
    assert config.services[0].base_port == 4200
    assert config.primary_service().name == "frontend"
    assert config.browser_extension.extension_id is None


def test_load_config_applies_path_local_overlay(tmp_path: Path) -> None:
    write_config(tmp_path, VALID_CONFIG)
    write_local_config(
        tmp_path,
        """
[caddy]
auto_start = false

[commands]
prestart = "echo local"
start = "pnpm dev"

[run]
mode = "single"

[browser_extension]
extension_id = "abcdefghijklmnopabcdefghijklmnop"

[[env]]
name = "LOCAL_ONLY"
value = "yes"

[[services]]
name = "frontend"
port_env = "WEB_PORT"
base_port = 4300
primary = true
url = "https://${slug}.local.authentic.localhost"
""",
    )

    config = load_config(tmp_path / ".bonsai.toml")

    assert config.name == "authentic"
    assert config.base_branch == "main"
    assert config.caddy.auto_install is True
    assert config.caddy.auto_start is False
    assert config.commands.install == "yarn install"
    assert config.commands.prestart == "echo local"
    assert config.commands.start == "pnpm dev"
    assert config.run.mode == "single"
    assert config.browser_extension.extension_id == "abcdefghijklmnopabcdefghijklmnop"
    assert [(item.name, item.value) for item in config.env] == [("LOCAL_ONLY", "yes")]
    assert [(service.name, service.port_env, service.base_port) for service in config.services] == [
        ("frontend", "WEB_PORT", 4300)
    ]


def test_invalid_local_overlay_error_names_local_file(tmp_path: Path) -> None:
    write_config(tmp_path, VALID_CONFIG)
    local_path = write_local_config(tmp_path, '[caddy]\nauto_start = "false"\n')

    with pytest.raises(BonsaiConfigError, match=rf"{re.escape(str(local_path))}"):
        load_config(tmp_path / ".bonsai.toml")


def test_workspace_root_local_config_overlays_repo_config(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    write_local_config(
        workspace_root,
        """
[commands]
start = "pnpm dev"

[browser_extension]
extension_id = "abcdefghijklmnopabcdefghijklmnop"
""",
    )
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )
    save_state(workspace_root / ".bonsai" / "state.json", state)

    config = load_workspace_config(workspace_root, state)

    assert config.path == default_worktree / ".bonsai.toml"
    assert config.commands.install == "yarn install"
    assert config.commands.start == "pnpm dev"
    assert config.browser_extension.extension_id == "abcdefghijklmnopabcdefghijklmnop"


def test_load_config_parses_browser_extension_id(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace(
        "[commands]\ninstall = \"yarn install\"",
        "\n".join(
            [
                "[browser_extension]",
                'extension_id = "abcdefghijklmnopabcdefghijklmnop"',
                "",
                "[commands]",
                'install = "yarn install"',
            ]
        ),
    )

    config = load_config(write_config(tmp_path, text))

    assert config.browser_extension.extension_id == "abcdefghijklmnopabcdefghijklmnop"


@pytest.mark.parametrize(
    "extension_id",
    [
        "",
        "abcdefghijklmnopabcdefghijklmnopa",
        "ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP",
        "abcdefghijklmnopabcdefghijklmnoq",
    ],
)
def test_browser_extension_id_must_be_a_chrome_extension_id(
    tmp_path: Path,
    extension_id: str,
) -> None:
    text = VALID_CONFIG.replace(
        "[commands]\ninstall = \"yarn install\"",
        "\n".join(
            [
                "[browser_extension]",
                f'extension_id = "{extension_id}"',
                "",
                "[commands]",
                'install = "yarn install"',
            ]
        ),
    )

    with pytest.raises(BonsaiConfigError, match="browser_extension.extension_id"):
        load_config(write_config(tmp_path, text))


def test_browser_extension_id_must_be_a_string(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace(
        "[commands]\ninstall = \"yarn install\"",
        "[browser_extension]\nextension_id = 123\n\n[commands]\ninstall = \"yarn install\"",
    )

    with pytest.raises(BonsaiConfigError, match="browser_extension.extension_id"):
        load_config(write_config(tmp_path, text))


def test_load_config_parses_optional_pre_and_post_commands(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace(
        '[commands]\ninstall = "yarn install"\nsetup = "yarn setup"\nstart = "yarn dev"',
        "\n".join(
            [
                "[commands]",
                'preinstall = "yarn preinstall"',
                'install = "yarn install"',
                'postinstall = "yarn postinstall"',
                'presetup = "yarn presetup"',
                'setup = "yarn setup"',
                'postsetup = "yarn postsetup"',
                'postadd = "yarn postadd"',
                'preremove = "yarn preremove"',
                'prestart = "yarn prestart"',
                'start = "yarn dev"',
                'poststart = "yarn poststart"',
            ]
        ),
    )

    config = load_config(write_config(tmp_path, text))

    assert config.commands.preinstall == "yarn preinstall"
    assert config.commands.postinstall == "yarn postinstall"
    assert config.commands.presetup == "yarn presetup"
    assert config.commands.postsetup == "yarn postsetup"
    assert config.commands.postadd == "yarn postadd"
    assert config.commands.preremove == "yarn preremove"
    assert config.commands.prestart == "yarn prestart"
    assert config.commands.poststart == "yarn poststart"


@pytest.mark.parametrize("mode", ["concurrent", "single"])
def test_load_config_parses_run_mode(tmp_path: Path, mode: str) -> None:
    text = VALID_CONFIG.replace(
        "[commands]",
        f'[run]\nmode = "{mode}"\n\n[commands]',
    )

    config = load_config(write_config(tmp_path, text))

    assert config.run.mode == mode


def test_run_mode_must_be_supported(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace("[commands]", '[run]\nmode = "serial"\n\n[commands]')

    with pytest.raises(BonsaiConfigError, match="run.mode"):
        load_config(write_config(tmp_path, text))


def test_run_mode_must_be_a_string(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace("[commands]", "[run]\nmode = true\n\n[commands]")

    with pytest.raises(BonsaiConfigError, match="Config key run.mode must be a string"):
        load_config(write_config(tmp_path, text))


def test_missing_config_file_raises_domain_error(tmp_path: Path) -> None:
    with pytest.raises(BonsaiConfigError, match="Missing .bonsai.toml"):
        load_config(tmp_path / ".bonsai.toml")


def test_invalid_toml_raises_domain_error(tmp_path: Path) -> None:
    with pytest.raises(BonsaiConfigError, match="Invalid TOML"):
        load_config(write_config(tmp_path, "name = "))


def test_boolean_base_port_is_rejected(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace("base_port = 4200", "base_port = true")

    with pytest.raises(BonsaiConfigError, match="Config key base_port must be an integer"):
        load_config(write_config(tmp_path, text))


def test_retired_config_keys_are_ignored(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace(
        "[caddy]\nauto_install = true",
        "\n".join(
            [
                "[workspace]",
                'default_parent = "~/Projects"',
                "",
                "[caddy]",
                'root_caddyfile = "Caddyfile"',
                'snippets_dir = "caddy.d"',
                "auto_install = true",
            ]
        ),
    )

    config = load_config(write_config(tmp_path, text))

    assert config.name == "authentic"
    assert config.caddy.auto_install is True


def test_shared_files_must_contain_tables(tmp_path: Path) -> None:
    text = """
name = "authentic"
base_branch = "main"
shared_files = ["bad"]

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"
"""

    with pytest.raises(BonsaiConfigError, match="Config key shared_files must contain tables"):
        load_config(write_config(tmp_path, text))


def test_caddy_boolean_values_must_be_booleans(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace("auto_install = true", 'auto_install = "false"')

    with pytest.raises(BonsaiConfigError, match="Config key auto_install must be a boolean"):
        load_config(write_config(tmp_path, text))


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


def test_service_port_env_cannot_use_reserved_bonsai_name(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace('port_env = "FRONTEND_PORT"', 'port_env = "BONSAI_BRANCH"')

    with pytest.raises(BonsaiConfigError, match="reserved environment name: BONSAI_BRANCH"):
        load_config(write_config(tmp_path, text))


def test_user_env_cannot_override_reserved_bonsai_name(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace(
        'name = "COMPOSE_PROJECT_NAME"',
        'name = "BONSAI_PRIMARY_URL"',
    )

    with pytest.raises(BonsaiConfigError, match="reserved environment name: BONSAI_PRIMARY_URL"):
        load_config(write_config(tmp_path, text))


def test_shared_file_copy_mode_is_supported(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace('mode = "symlink"', 'mode = "copy"')

    config = load_config(write_config(tmp_path, text))

    assert config.shared_files[0].mode == "copy"


def test_unsupported_shared_file_mode_is_rejected(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace('mode = "symlink"', 'mode = "hardlink"')

    with pytest.raises(BonsaiConfigError, match="Unsupported shared file mode: hardlink"):
        load_config(write_config(tmp_path, text))


def test_shared_file_mode_must_be_a_string(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace('mode = "symlink"', 'mode = "copy"')
    text = text.replace('mode = "copy"', "mode = true")

    with pytest.raises(BonsaiConfigError, match="Config key mode must be a string"):
        load_config(write_config(tmp_path, text))


@pytest.mark.parametrize(
    ("config_text", "message"),
    [
        (VALID_CONFIG.replace('name = "authentic"', 'name = ""'), "Config key name"),
        (VALID_CONFIG.replace('source = ".env"', 'source = ""'), "Config key source"),
        (VALID_CONFIG.replace('value = "authentic-${slug}"', 'value = ""'), "Config key value"),
        (
            VALID_CONFIG.replace('port_env = "FRONTEND_PORT"', 'port_env = ""'),
            "Config key port_env",
        ),
        (
            VALID_CONFIG.replace(
                'url = "https://${slug}.authentic.localhost"',
                'url = ""',
            ),
            "Config key url must be a non-empty string",
        ),
    ],
)
def test_required_string_values_must_be_non_empty(
    tmp_path: Path,
    config_text: str,
    message: str,
) -> None:
    with pytest.raises(BonsaiConfigError, match=message):
        load_config(write_config(tmp_path, config_text))
