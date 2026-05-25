class BonsaiError(Exception):
    """Base error for user-facing Bonsai failures."""


class BonsaiConfigError(BonsaiError):
    """Raised when .bonsai.toml is missing or invalid."""


class BonsaiWorkspaceError(BonsaiError):
    """Raised when a managed workspace cannot be found or used."""


class BonsaiCommandError(BonsaiError):
    """Raised when an external command fails."""
