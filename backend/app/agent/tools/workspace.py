"""Shared path handling for the tools that touch the filesystem.

`search` and `file_reader` are both confined to one workspace directory. Confinement is
enforced by resolving the candidate path and checking it is still inside the workspace
after symlinks are followed — a `..` segment or a symlink pointing out of the tree fails
that check.
"""

from __future__ import annotations

from pathlib import Path

from ...config import get_settings

# Text-ish files only. A tool that hands 40 MB of binary to a 4B model helps nobody.
READABLE_SUFFIXES = {".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".html"}
MAX_FILE_BYTES = 200_000


def workspace_root() -> Path:
    """Directory the file tools may read. Created on first use.

    A relative setting resolves against backend/, not the working directory, so the tools
    read the same folder however the server was started.
    """
    configured = Path(get_settings().workspace_dir).expanduser()
    if not configured.is_absolute():
        configured = Path(__file__).resolve().parents[3] / configured
    root = configured.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


class OutsideWorkspace(Exception):
    pass


def resolve_in_workspace(relative_path: str) -> Path:
    """Resolve `relative_path` inside the workspace or raise `OutsideWorkspace`."""
    root = workspace_root()
    candidate = (root / relative_path.strip().lstrip("/")).resolve()
    # `is_relative_to` on the *resolved* path is what defeats both ../ and symlinks.
    if candidate != root and not candidate.is_relative_to(root):
        raise OutsideWorkspace(
            f"'{relative_path}' is outside the document folder. "
            "Only files inside it can be opened."
        )
    return candidate


def list_readable_files() -> list[Path]:
    root = workspace_root()
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in READABLE_SUFFIXES
    )


def relative_name(path: Path) -> str:
    try:
        return str(path.relative_to(workspace_root()))
    except ValueError:
        return path.name
