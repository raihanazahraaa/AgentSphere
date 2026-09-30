"""The parser has to survive whatever a 3B model emits."""

from __future__ import annotations

import pytest

from app.agent.action_parser import ActionParseError, parse_action


@pytest.mark.parametrize(
    "raw",
    [
        '{"action": "calculator", "input": "2+2"}',
        '  {"action": "calculator", "input": "2+2"}  ',
        '```json\n{"action": "calculator", "input": "2+2"}\n```',
        '```\n{"action": "calculator", "input": "2+2"}\n```',
        'Sure! Here is my action:\n{"action": "calculator", "input": "2+2"}',
        '{"action": "calculator", "input": "2+2"}\nI hope that helps.',
        '<think>Let me work this out.</think>{"action": "calculator", "input": "2+2"}',
        '{"action": "calculator", "action_input": "2+2"}',
        "{'action': 'calculator', 'input': '2+2'}",
        '{"action": "calculator", "input": "2+2",}',
        '{“action”: “calculator”, “input”: “2+2”}',
    ],
)
def test_parses_the_shapes_models_actually_emit(raw):
    action = parse_action(raw)
    assert action.name == "calculator"
    assert action.input == "2+2"
    assert not action.is_final


def test_recognises_final_answer():
    action = parse_action('{"action": "final_answer", "input": "Half term is in October."}')
    assert action.is_final
    assert action.input == "Half term is in October."


def test_accepts_answer_key_for_final():
    assert parse_action('{"action": "final_answer", "answer": "42"}').input == "42"


def test_coerces_non_string_input():
    # The prompt asks for a string; models pass numbers and objects anyway.
    assert parse_action('{"action": "calculator", "input": 42}').input == "42"
    assert "a" in parse_action('{"action": "x", "input": {"a": 1}}').input


def test_ignores_braces_inside_string_values():
    action = parse_action('{"action": "code_runner", "input": "print({\\"k\\": 1})"}')
    assert action.name == "code_runner"
    assert action.input == 'print({"k": 1})'


def test_picks_the_json_out_of_surrounding_prose():
    raw = 'I think I should calculate this.\n{"action": "calculator", "input": "5*5"}\nThen I will answer.'
    assert parse_action(raw).input == "5*5"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "I am not going to reply in JSON.",
        "{not json at all}",
        '{"input": "2+2"}',              # no action
        '{"action": "", "input": "2+2"}',  # empty action
        "<think>still reasoning and never finished</think>",
    ],
)
def test_rejects_unusable_output(raw):
    with pytest.raises(ActionParseError):
        parse_action(raw)
