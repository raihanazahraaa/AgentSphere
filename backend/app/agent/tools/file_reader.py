"""Reads a text file from the workspace folder."""

from __future__ import annotations

from .base import BaseTool, ToolResult
from .workspace import (
    MAX_FILE_BYTES,
    READABLE_SUFFIXES,
    OutsideWorkspace,
    list_readable_files,
    relative_name,
    resolve_in_workspace,
)

# Truncate before the observation goes back into the prompt.
MAX_CHARS_RETURNED = 4000


class FileReaderTool(BaseTool):
    key = "file_reader"
    label = "File reader"
    description = "Reads a document from the school's document folder."
    input_hint = "a file name, e.g. term-dates.md (use 'list' to see what is available)"

    def _run(self, tool_input: str) -> ToolResult:
        argument = tool_input.strip()

        if not argument or argument.lower() in {"list", "ls", "*"}:
            return self._list_files()

        try:
            path = resolve_in_workspace(argument)
        except OutsideWorkspace as exc:
            return ToolResult.failure(str(exc))

        if path.is_dir():
            return self._list_files()
        if not path.exists():
            available = ", ".join(relative_name(p) for p in list_readable_files()[:20])
            return ToolResult.failure(
                f"No file called '{argument}'. Available: {available or 'none'}"
            )
        if path.suffix.lower() not in READABLE_SUFFIXES:
            allowed = ", ".join(sorted(READABLE_SUFFIXES))
            return ToolResult.failure(f"'{argument}' is not a readable text type ({allowed}).")
        if path.stat().st_size > MAX_FILE_BYTES:
            return ToolResult.failure(f"'{argument}' is too large to read in full.")

        text = path.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > MAX_CHARS_RETURNED
        if truncated:
            text = text[:MAX_CHARS_RETURNED] + "\n... (truncated)"

        return ToolResult.success(text, file=relative_name(path), truncated=truncated)

    def _list_files(self) -> ToolResult:
        files = [relative_name(p) for p in list_readable_files()]
        if not files:
            return ToolResult.success("The document folder is empty.")
        return ToolResult.success("Available files: " + ", ".join(files), count=len(files))
