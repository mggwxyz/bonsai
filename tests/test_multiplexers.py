from bonsai.workflows.multiplexers import (
    detect_mux_backend,
    resolve_mux_backend,
    shell_env_command,
)


def test_detect_mux_backend_defaults_to_tmux() -> None:
    assert detect_mux_backend({}) == "tmux"
    assert detect_mux_backend({"TMUX": "/tmp/tmux-501/default,123,0"}) == "tmux"


def test_detect_mux_backend_prefers_herdr_inside_herdr() -> None:
    assert detect_mux_backend({"HERDR_ENV": "1"}) == "herdr"
    assert detect_mux_backend({"HERDR_ENV": "1", "CMUX_SOCKET_PATH": "/tmp/x"}) == "herdr"


def test_detect_mux_backend_detects_cmux_socket_or_workspace() -> None:
    assert detect_mux_backend({"CMUX_SOCKET_PATH": "/tmp/cmux.sock"}) == "cmux"
    assert detect_mux_backend({"CMUX_WORKSPACE_ID": "workspace:1"}) == "cmux"


def test_resolve_mux_backend_passes_explicit_choice_through() -> None:
    assert resolve_mux_backend("cmux", {"HERDR_ENV": "1"}) == "cmux"
    assert resolve_mux_backend("auto", {"HERDR_ENV": "1"}) == "herdr"


def test_shell_env_command_quotes_values_and_normalizes_command() -> None:
    rendered = shell_env_command(
        {"BONSAI_BRANCH": "feature", "BONSAI_PRIMARY_URL": "https://a.localhost?x=1 2"},
        "yarn dev",
    )
    assert rendered == (
        "env BONSAI_BRANCH=feature "
        "'BONSAI_PRIMARY_URL=https://a.localhost?x=1 2' yarn dev"
    )
