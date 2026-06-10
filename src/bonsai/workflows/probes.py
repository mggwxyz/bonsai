from __future__ import annotations


def _check_port_listening(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


_CADDY_HTTPS_PORT = 443


def _check_caddy_listening() -> bool:
    """Probe Caddy's local HTTPS listener.

    A plain socket connect confirms Caddy is accepting connections without
    performing a TLS handshake, so a missing ``*.localhost`` certificate trust
    never falsely demotes a healthy Caddy route to its direct port.
    """
    return _check_port_listening(_CADDY_HTTPS_PORT)
