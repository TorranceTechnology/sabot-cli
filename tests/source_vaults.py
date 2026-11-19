"""Helpers shared by the ingest and segment tests: an empty vault holding ingested sources."""

from pathlib import Path

from conftest import CliResult, CliRunner

RETRIEVED = "2026-11-19"


def snapshot(directory: Path) -> dict[str, bytes]:
    """Every file under a directory, by relative POSIX path, with its bytes."""
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def ingest_arguments(source: Path, key: str) -> tuple[str, ...]:
    """The required `ingest` arguments for a text file."""
    return (
        "ingest",
        str(source),
        "--key",
        key,
        "--label",
        f"Source {key}",
        "--title",
        f"Source {key}",
        "--edition",
        "test",
        "--retrieved",
        RETRIEVED,
        "--format",
        "text",
    )


def vault_with_sources(
    run_cli: CliRunner, directory: Path, sources: dict[str, Path] | None = None
) -> Path:
    """Create an empty vault and ingest each `{key: file}`; every step must succeed."""
    assert run_cli("init", str(directory), "--ruleset", "driftwood").returncode == 0
    for key, source in (sources or {}).items():
        result = run_cli(*ingest_arguments(source, key), "--vault", str(directory))
        assert result.returncode == 0, result.stderr
    return directory


def error_code(result: CliResult) -> object:
    """The error code a --json refusal printed; fails the test if there is no error object."""
    error = result.json()["error"]
    assert isinstance(error, dict)
    return error["code"]
