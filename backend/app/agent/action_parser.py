"""Extracts the JSON action block from model output.

Small models decorate their output: code fences, a sentence of preamble, trailing
commentary, single quotes, smart quotes. The loop still demands a strict shape
(`action` + `input`), so this module does the tolerant *extraction* and strict
*validation* separately — salvage the JSON if it is in there somewhere, then insist it
has the right fields.

Anything this cannot parse goes back to the model once via the repair prompt; a second
failure ends the turn with a safe fallback rather than looping.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

FINAL_ACTION = "final_answer"

# ```json ... ``` or bare ``` ... ```
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
# qwen3 emits <think>…</think> when thinking is folded into the response instead of split
# into its own field; strip it before looking for JSON.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)


class ActionParseError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    name: str
    input: str

    @property
    def is_final(self) -> bool:
        return self.name == FINAL_ACTION


def parse_action(raw: str) -> Action:
    """Return the action in `raw`, or raise `ActionParseError`."""
    if not raw or not raw.strip():
        raise ActionParseError("The model returned nothing.")

    text = _THINK_BLOCK.sub("", raw)
    text = _UNCLOSED_THINK.sub("", text).strip()
    if not text:
        raise ActionParseError("The model returned only its reasoning, with no answer.")

    for candidate in _candidates(text):
        payload = _load(candidate)
        if payload is None:
            continue
        action = _validate(payload)
        if action is not None:
            return action

    raise ActionParseError("No valid JSON action object found in the model's reply.")


def _candidates(text: str):
    """Progressively less literal readings of the output, best first."""
    stripped = text.strip()
    yield stripped

    for match in _FENCE.findall(text):
        if match.strip():
            yield match.strip()

    # Balanced-brace scan: finds the object even when prose surrounds it, and does not
    # get fooled by braces inside string values the way a greedy regex would.
    for block in _brace_blocks(text):
        yield block


def _brace_blocks(text: str):
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : index + 1]


def _load(candidate: str) -> dict | None:
    try:
        payload = json.loads(candidate)
    except (ValueError, TypeError):
        payload = _load_lenient(candidate)
    return payload if isinstance(payload, dict) else None


def _load_lenient(candidate: str) -> dict | None:
    """One retry with the mistakes small models actually make cleaned up."""
    repaired = candidate
    # Smart quotes, courtesy of instruction-tuned models imitating prose.
    for fancy, plain in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")):
        repaired = repaired.replace(fancy, plain)
    # Trailing comma before the closing brace.
    repaired = re.sub(r",\s*}", "}", repaired)
    # Single-quoted keys/values, only when no double quotes are present to confuse it.
    if '"' not in repaired and "'" in repaired:
        repaired = repaired.replace("'", '"')

    try:
        payload = json.loads(repaired)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _validate(payload: dict) -> Action | None:
    """Accept the documented shape, plus the near-misses models reliably produce."""
    name = payload.get("action")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()

    if "input" in payload:
        value = payload["input"]
    elif "action_input" in payload:  # the other common ReAct spelling
        value = payload["action_input"]
    elif name == FINAL_ACTION and "answer" in payload:
        value = payload["answer"]
    else:
        value = ""

    # The prompt asks for a string, but models pass numbers and nested objects anyway.
    if value is None:
        value = ""
    elif not isinstance(value, str):
        value = json.dumps(value) if isinstance(value, (dict, list)) else str(value)

    return Action(name=name, input=value)
