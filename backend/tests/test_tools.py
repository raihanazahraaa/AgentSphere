"""Each built-in tool, including the escapes the sandbox must refuse."""

from __future__ import annotations

import pytest

from app.agent.tools import BUILTIN_TOOLS, build_registry
from app.agent.tools.calculator import CalculatorTool
from app.agent.tools.code_runner import CodeRunnerTool


# --------------------------------------------------------------------------- registry


def test_registry_only_exposes_ticked_tools():
    registry = build_registry(["calculator", "search"])
    assert registry.keys == ["calculator", "search"]
    assert "code_runner" not in registry


def test_registry_skips_unknown_keys():
    # Phase 1 agents still reference school-data tool keys that have no implementation.
    registry = build_registry(["calculator", "check_attendance"])
    assert registry.keys == ["calculator"]


def test_unknown_tool_returns_error_not_exception():
    registry = build_registry(["calculator"])
    result = registry.run("nonexistent", "x")
    assert not result.ok
    assert "No such tool" in result.output
    # The model needs to see what it *can* use, to correct itself.
    assert "calculator" in result.output


def test_every_builtin_tool_describes_itself():
    for key, cls in BUILTIN_TOOLS.items():
        tool = cls()
        assert tool.key == key
        assert tool.description and tool.input_hint
        assert key in tool.describe()


def test_tool_crash_becomes_failure_not_exception():
    class ExplodingTool(CalculatorTool):
        key = "boom"

        def _run(self, tool_input):
            raise RuntimeError("kaboom")

    result = ExplodingTool().run("1+1")
    assert not result.ok
    assert "kaboom" in result.output


# ------------------------------------------------------------------------- calculator


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2+2", "4"),
        ("(450 - 200) * 0.15", "37.5"),
        ("0.1 + 0.2", "0.3"),          # float noise trimmed
        ("sqrt(144)", "12"),
        ("round(2.7)", "3"),
        ("10 // 3", "3"),
        ("2 ** 10", "1024"),
    ],
)
def test_calculator_evaluates(expression, expected):
    result = CalculatorTool().run(expression)
    assert result.ok, result.output
    assert result.output == expected


@pytest.mark.parametrize(
    "expression",
    [
        '__import__("os").system("ls")',
        "open('/etc/passwd').read()",
        "exec('x=1')",
        "9**9**9",          # would hang the process
        "[].__class__",
        "",
    ],
)
def test_calculator_refuses_anything_but_arithmetic(expression):
    result = CalculatorTool().run(expression)
    assert not result.ok


def test_calculator_reports_division_by_zero_clearly():
    result = CalculatorTool().run("1/0")
    assert not result.ok
    assert "zero" in result.output.lower()


# -------------------------------------------------------------- search / file_reader


def _seed(workspace):
    (workspace / "term-dates.md").write_text(
        "Half term: 27 October to 31 October 2025.\nSpring term starts 6 January 2026.",
        encoding="utf-8",
    )
    (workspace / "fees.md").write_text("Tuition is due within 30 days.", encoding="utf-8")


def test_search_finds_matching_document(workspace):
    _seed(workspace)
    result = build_registry(["search"]).run("search", "half term")
    assert result.ok, result.output
    assert "term-dates.md" in result.output


def test_search_reports_no_match(workspace):
    _seed(workspace)
    result = build_registry(["search"]).run("search", "trampolining")
    assert not result.ok


def test_search_rejects_only_stopwords(workspace):
    _seed(workspace)
    result = build_registry(["search"]).run("search", "what is the")
    assert not result.ok


def test_file_reader_reads_and_lists(workspace):
    _seed(workspace)
    registry = build_registry(["file_reader"])

    read = registry.run("file_reader", "term-dates.md")
    assert read.ok
    assert "27 October" in read.output

    listing = registry.run("file_reader", "list")
    assert listing.ok
    assert "fees.md" in listing.output


@pytest.mark.parametrize("path", ["../app/config.py", "../../etc/passwd", "../.env"])
def test_file_reader_refuses_paths_outside_the_workspace(workspace, path):
    _seed(workspace)
    result = build_registry(["file_reader"]).run("file_reader", path)
    assert not result.ok
    assert "outside" in result.output.lower() or "no file" in result.output.lower()


# ------------------------------------------------------------------------ code_runner


def test_code_runner_returns_printed_output():
    result = CodeRunnerTool().run("print(sum(range(10)))")
    assert result.ok, result.output
    assert result.output.strip() == "45"


def test_code_runner_strips_markdown_fences():
    result = CodeRunnerTool().run("```python\nprint('hi')\n```")
    assert result.ok
    assert result.output.strip() == "hi"


def test_code_runner_allows_stdlib_imports():
    result = CodeRunnerTool().run("import json\nprint(json.dumps({'a': 1}))")
    assert result.ok, result.output
    assert '"a": 1' in result.output


def test_code_runner_explains_silent_code():
    result = CodeRunnerTool().run("x = 1")
    assert result.ok
    assert "print()" in result.output


def test_code_runner_surfaces_tracebacks():
    result = CodeRunnerTool().run("print(1/0)")
    assert not result.ok
    assert "ZeroDivisionError" in result.output


def test_code_runner_stops_infinite_loops():
    result = CodeRunnerTool().run("while True: pass")
    assert not result.ok
    assert "longer than" in result.output


@pytest.mark.parametrize(
    "code",
    [
        "import socket",
        "import subprocess",
        "__import__('os')",
        "import urllib.request",
    ],
)
def test_code_runner_refuses_network_and_process_imports(code):
    result = CodeRunnerTool().run(code + "\nprint('ran')")
    assert not result.ok
    assert "not permitted" in result.output


@pytest.mark.parametrize(
    "snippet",
    [
        "print(open('/etc/passwd').read())",
        "import os; fd = os.open('/etc/passwd', os.O_RDONLY)",
        "from pathlib import Path; print(Path('/etc/hosts').read_text())",
    ],
)
def test_code_runner_cannot_read_outside_its_temp_dir(snippet):
    # The regression that matters: backend/.env holds JWT_SECRET, and an Observation goes
    # straight into the model's context and then the user's transcript.
    result = CodeRunnerTool().run(snippet)
    assert not result.ok
    assert "PermissionError" in result.output or "not permitted" in result.output


def test_code_runner_can_use_its_own_temp_dir():
    result = CodeRunnerTool().run(
        "open('scratch.txt', 'w').write('ok')\nprint(open('scratch.txt').read())"
    )
    assert result.ok, result.output
    assert result.output.strip() == "ok"
