"""Built-in tool registry (FR7).

`BUILTIN_TOOLS` is the single source of truth: the wizard catalog is generated from it,
`routers/agents.py` validates submitted tool keys against it, and the orchestrator builds
each agent's registry from it. Adding a tool means adding one entry here.
"""

from __future__ import annotations

from .base import BaseTool, Tool, ToolRegistry, ToolResult
from .calculator import CalculatorTool
from .code_runner import CodeRunnerTool
from .file_reader import FileReaderTool
from .search import SearchTool

BUILTIN_TOOL_CLASSES: list[type[BaseTool]] = [
    SearchTool,
    CalculatorTool,
    FileReaderTool,
    CodeRunnerTool,
]

BUILTIN_TOOLS: dict[str, type[BaseTool]] = {cls.key: cls for cls in BUILTIN_TOOL_CLASSES}


def build_registry(tool_keys: list[str]) -> ToolRegistry:
    """The tools this agent ticked in the wizard, and nothing else.

    Unknown keys are skipped rather than raising: Phase 1 agents may still reference
    school-data tool keys that have no implementation yet, and one stale key should not
    stop an agent from answering.
    """
    tools: list[Tool] = [
        BUILTIN_TOOLS[key]() for key in tool_keys if key in BUILTIN_TOOLS
    ]
    return ToolRegistry(tools)


def catalog_entries() -> list[dict]:
    """Wizard-facing description of every built-in tool."""
    return [
        {
            "key": cls.key,
            "label": cls.label,
            "description": cls.description,
            "input_hint": cls.input_hint,
            "available": True,
        }
        for cls in BUILTIN_TOOL_CLASSES
    ]


__all__ = [
    "BUILTIN_TOOLS",
    "BUILTIN_TOOL_CLASSES",
    "BaseTool",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "build_registry",
    "catalog_entries",
]
