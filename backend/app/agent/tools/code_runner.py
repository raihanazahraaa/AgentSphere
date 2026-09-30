"""Runs short Python snippets in a throwaway sandboxed subprocess.

Never `eval`/`exec` in the server process: model-written code would then run with the
API's own privileges, database handles and network access. Instead each call gets a fresh
temp directory, a separate process in its own session, and the guards in
`_sandbox_harness.py`.

The parent enforces wall-clock timeout and kills the whole process group, so a hung child
cannot outlive the request.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from ...config import get_settings
from .base import BaseTool, ToolResult

_HARNESS_SOURCE = Path(__file__).with_name("_sandbox_harness.py")

# Cheap pre-flight rejections. Not a security boundary on their own — the sandbox is —
# but they give the model a useful error instead of a confusing PermissionError.
_BANNED_SUBSTRINGS = (
    "import socket",
    "import subprocess",
    "import requests",
    "import urllib",
    "import httpx",
    "__import__",
)


class CodeRunnerTool(BaseTool):
    key = "code_runner"
    label = "Code runner"
    description = "Runs a short Python snippet and returns what it prints."
    input_hint = "Python code; print() the answer you want back"

    def _run(self, tool_input: str) -> ToolResult:
        code = _strip_code_fence(tool_input)
        if not code.strip():
            return ToolResult.failure("No code given.")
        if len(code) > 10_000:
            return ToolResult.failure("That snippet is too long (limit 10,000 characters).")

        lowered = code.lower()
        for banned in _BANNED_SUBSTRINGS:
            if banned in lowered:
                return ToolResult.failure(
                    f"'{banned}' is not permitted — the sandbox has no network or "
                    "process access. Use plain Python and print() the result."
                )

        settings = get_settings()
        timeout_s = settings.code_runner_timeout_s

        workdir = Path(tempfile.mkdtemp(prefix="agentsphere-sandbox-"))
        try:
            script = workdir / "main.py"
            script.write_text(code, encoding="utf-8")
            harness = workdir / "harness.py"
            shutil.copy2(_HARNESS_SOURCE, harness)

            # -I isolates the interpreter: no PYTHONPATH, no user site-packages, no env
            # inheritance of import paths. Nothing from the app is importable.
            command = [sys.executable, "-I", str(harness), str(script)]
            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(workdir),
                "TMPDIR": str(workdir),
                "SANDBOX_CPU_SECONDS": str(int(timeout_s)),
                "PYTHONIOENCODING": "utf-8",
                # Belt and braces: even if a socket slipped through, send it nowhere.
                "no_proxy": "*",
            }

            try:
                completed = subprocess.run(
                    command,
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    # Own session, so a timeout can kill children the snippet spawned.
                    start_new_session=True,
                    check=False,
                )
            except subprocess.TimeoutExpired as expired:
                _kill_process_group(expired)
                return ToolResult.failure(
                    f"The code ran longer than {timeout_s:.0f} seconds and was stopped."
                )

            return self._format(completed, settings.code_runner_max_output_chars)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _format(self, completed: subprocess.CompletedProcess, max_chars: int) -> ToolResult:
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()

        if completed.returncode != 0:
            detail = stderr or stdout or f"exited with code {completed.returncode}"
            return ToolResult.failure(
                _truncate(detail, max_chars), returncode=completed.returncode
            )

        if not stdout:
            return ToolResult.success(
                "The code ran but printed nothing. Use print() to return a result.",
                returncode=0,
            )

        return ToolResult.success(
            _truncate(stdout, max_chars), returncode=0, stderr=_truncate(stderr, 500)
        )


def _kill_process_group(expired: subprocess.TimeoutExpired) -> None:
    """subprocess only kills the direct child; take the whole session with it."""
    pid = getattr(expired, "pid", None)
    if not pid:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _strip_code_fence(text: str) -> str:
    """Models wrap code in ``` fences even when told not to."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    lines = lines[1:]  # drop the opening fence (and any language tag)
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (output truncated)"
