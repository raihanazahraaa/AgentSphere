"""The one shared tool schema (FR7).

Every tool — built-in now, school-data and retrieval later — implements `Tool`. The
orchestrator only ever sees this interface, so adding a tool never touches the ReAct loop,
and `describe()` is what gets rendered into the prompt the model reads.

Keeping one schema is what lets the wizard stay no-code: `agent_tool.tool_key` rows point
at entries in this registry, and an agent is handed exactly the tools ticked in its config.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ToolResult:
    """What a tool hands back to the observe step of the loop."""

    ok: bool
    # Fed back to the model as the Observation. Keep it short — it costs context.
    output: str
    error: str = ""
    elapsed_s: float = 0.0
    # Anything the UI or Phase 4 evaluation may want, never shown to the model.
    meta: dict = field(default_factory=dict)

    @classmethod
    def success(cls, output: str, **meta) -> "ToolResult":
        return cls(ok=True, output=output, meta=meta)

    @classmethod
    def failure(cls, error: str, **meta) -> "ToolResult":
        # The model sees `output`, so the error has to be phrased for it to act on.
        return cls(ok=False, output=f"Error: {error}", error=error, meta=meta)


@runtime_checkable
class Tool(Protocol):
    key: str
    label: str
    description: str
    # One-line argument description rendered into the prompt.
    input_hint: str

    def run(self, tool_input: str) -> ToolResult: ...


class BaseTool:
    """Convenience base: times every call and turns a crash into a clean failure.

    A tool raising into the ReAct loop would abort a user's whole turn, so nothing is
    allowed to propagate — an exception becomes an Observation the model can react to.
    """

    key: str = ""
    label: str = ""
    description: str = ""
    input_hint: str = ""

    def run(self, tool_input: str) -> ToolResult:
        started = time.perf_counter()
        try:
            result = self._run(tool_input)
        except Exception as exc:  # noqa: BLE001 - deliberate catch-all, see docstring
            result = ToolResult.failure(f"{type(exc).__name__}: {exc}")
        result.elapsed_s = time.perf_counter() - started
        return result

    def _run(self, tool_input: str) -> ToolResult:
        raise NotImplementedError

    def describe(self) -> str:
        """The line the model sees in its tool list."""
        return f"- {self.key}: {self.description} Input: {self.input_hint}"


class ToolRegistry:
    """The tools one agent may use, resolved from its wizard config."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {t.key: t for t in (tools or [])}

    def __contains__(self, key: str) -> bool:
        return key in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def keys(self) -> list[str]:
        return sorted(self._tools)

    def get(self, key: str) -> Tool | None:
        return self._tools.get(key)

    def describe_all(self) -> str:
        if not self._tools:
            return "(no tools available)"
        return "\n".join(self._tools[key].describe() for key in sorted(self._tools))

    def run(self, key: str, tool_input: str) -> ToolResult:
        tool = self._tools.get(key)
        if tool is None:
            available = ", ".join(sorted(self._tools)) or "none"
            return ToolResult.failure(
                f"No such tool '{key}'. Available tools: {available}."
            )
        return tool.run(tool_input)
