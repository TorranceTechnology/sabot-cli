"""Compare the exact state of a directory tree before and after a command."""

from pathlib import Path


def vault_state(root: Path) -> dict[str, bytes | None]:
    """Every file under `root` with its bytes and every directory with None, hidden ones too.

    Keys are relative POSIX paths; a directory's key ends in `/`. An absent root has no entries.
    """
    if not root.is_dir():
        return {}
    state: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            state[f"{relative}/"] = None
        else:
            state[relative] = path.read_bytes()
    return state
