"""The refusal a write or a recovery raises."""

__all__ = ["TransactionError"]


class TransactionError(Exception):
    """A write was refused or undone; `code` is the refusal's error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
