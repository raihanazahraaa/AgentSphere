"""Arithmetic tool.

Parsed with `ast` and walked against a whitelist rather than handed to `eval`, which would
execute anything the model emitted. Only literal numbers, arithmetic operators and a short
list of math functions survive the walk.
"""

from __future__ import annotations

import ast
import math

from .base import BaseTool, ToolResult

_ALLOWED_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "pow": pow,
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)

# 2**8192 is instant; 9**9**9 hangs the process. Cap the exponent instead of the result.
_MAX_EXPONENT = 1000


class CalculatorTool(BaseTool):
    key = "calculator"
    label = "Calculator"
    description = "Evaluates an arithmetic expression."
    input_hint = "a single expression, e.g. (450 - 200) * 0.15"

    def _run(self, tool_input: str) -> ToolResult:
        expression = tool_input.strip()
        if not expression:
            return ToolResult.failure("No expression given.")
        # Guard the parser itself; a megabyte of nested parens is its own denial of service.
        if len(expression) > 500:
            return ToolResult.failure("Expression is too long (limit 500 characters).")

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            return ToolResult.failure(f"That is not a valid expression ({exc.msg}).")

        try:
            value = self._evaluate(tree.body)
        except _UnsupportedExpression as exc:
            return ToolResult.failure(str(exc))
        except ZeroDivisionError:
            return ToolResult.failure("Division by zero.")
        except (OverflowError, ValueError) as exc:
            return ToolResult.failure(f"Could not compute that ({exc}).")

        # Trim float noise: 0.1+0.2 should read as 0.3, not 0.30000000000000004.
        if isinstance(value, float):
            rendered = f"{value:.10g}"
        else:
            rendered = str(value)
        return ToolResult.success(rendered, expression=expression)

    def _evaluate(self, node: ast.AST):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise _UnsupportedExpression("Only numbers are allowed.")
            return node.value

        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, _ALLOWED_BINOPS):
                raise _UnsupportedExpression(f"Operator {type(node.op).__name__} is not allowed.")
            left = self._evaluate(node.left)
            right = self._evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
                raise _UnsupportedExpression(f"Exponent is too large (limit {_MAX_EXPONENT}).")
            return _apply_binop(node.op, left, right)

        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _ALLOWED_UNARYOPS):
                raise _UnsupportedExpression("Only + and - may be used as signs.")
            operand = self._evaluate(node.operand)
            return operand if isinstance(node.op, ast.UAdd) else -operand

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCTIONS:
                allowed = ", ".join(sorted(_ALLOWED_FUNCTIONS))
                raise _UnsupportedExpression(f"Only these functions are allowed: {allowed}.")
            if node.keywords:
                raise _UnsupportedExpression("Keyword arguments are not supported.")
            args = [self._evaluate(arg) for arg in node.args]
            return _ALLOWED_FUNCTIONS[node.func.id](*args)

        if isinstance(node, ast.Name):
            if node.id not in _ALLOWED_NAMES:
                raise _UnsupportedExpression(f"Unknown name '{node.id}'.")
            return _ALLOWED_NAMES[node.id]

        if isinstance(node, ast.Tuple):
            return tuple(self._evaluate(element) for element in node.elts)
        if isinstance(node, ast.List):
            return [self._evaluate(element) for element in node.elts]

        raise _UnsupportedExpression(f"{type(node).__name__} is not allowed in an expression.")


def _apply_binop(op: ast.operator, left, right):
    if isinstance(op, ast.Add):
        return left + right
    if isinstance(op, ast.Sub):
        return left - right
    if isinstance(op, ast.Mult):
        return left * right
    if isinstance(op, ast.Div):
        return left / right
    if isinstance(op, ast.FloorDiv):
        return left // right
    if isinstance(op, ast.Mod):
        return left % right
    return left**right


class _UnsupportedExpression(Exception):
    pass
