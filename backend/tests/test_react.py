"""The reason/act/observe loop, with the failure paths that matter most.

Uses the scripted fake client from conftest, so these run in milliseconds with no model.
"""

from __future__ import annotations

from app.agent.ollama_client import OllamaTimeout, OllamaUnavailable
from app.agent.react import SAFE_FALLBACK, ReActOrchestrator
from app.agent.tools import build_registry

SYSTEM = "You are a test agent."


def make(fake_client, responses, tools=("calculator",), **kwargs):
    client = fake_client(responses)
    orchestrator = ReActOrchestrator(
        client=client,
        registry=build_registry(list(tools)),
        model="primary",
        fallback_model=kwargs.pop("fallback_model", "secondary"),
        max_iterations=kwargs.pop("max_iterations", 4),
    )
    return orchestrator, client


# ------------------------------------------------------------------- the happy paths


def test_answers_immediately_without_a_tool(fake_client):
    orchestrator, _ = make(fake_client, ['{"action": "final_answer", "input": "Hello there."}'])
    result = orchestrator.run(question="hi", system_prompt=SYSTEM)

    assert result.answer == "Hello there."
    assert result.iterations == 1
    assert result.tool_calls == []
    assert result.stop_reason == "final_answer"
    assert result.succeeded


def test_calls_a_tool_then_answers(fake_client):
    orchestrator, _ = make(
        fake_client,
        [
            '{"action": "calculator", "input": "(450-200)*0.15"}',
            '{"action": "final_answer", "input": "That comes to 37.5."}',
        ],
    )
    result = orchestrator.run(question="what is it", system_prompt=SYSTEM)

    assert result.answer == "That comes to 37.5."
    assert result.tool_calls == ["calculator"]
    assert result.iterations == 2
    # The observation the tool produced must reach the transcript.
    assert result.steps[0].observation == "37.5"
    assert result.tool_s >= 0


def test_routing_hint_reaches_the_prompt(fake_client):
    """FR6 feeds the loop, not just the metadata.

    Regression: without this the router computed `suggested_tools` and the runtime threw
    it away, and llama3.2:3b answered "the trip costs 35 for 3 children" with "35" from
    final_answer instead of calling the calculator (0/6 correct; 6/6 with the hint).
    """
    orchestrator, client = make(fake_client, ['{"action": "final_answer", "input": "ok"}'])
    orchestrator.run(
        question="what is the total", system_prompt=SYSTEM, suggested_tools=["calculator"]
    )
    assert "`calculator`" in client.calls[0]["prompt"]


def test_routing_hint_is_dropped_after_the_first_pass(fake_client):
    # A stale hint just pushes the model to repeat the tool it already ran.
    orchestrator, client = make(
        fake_client,
        [
            '{"action": "calculator", "input": "2+2"}',
            '{"action": "final_answer", "input": "4"}',
        ],
    )
    orchestrator.run(question="q", system_prompt=SYSTEM, suggested_tools=["calculator"])
    assert "The wording of this question suggests" in client.calls[0]["prompt"]
    assert "The wording of this question suggests" not in client.calls[1]["prompt"]


def test_history_is_included_in_the_prompt(fake_client):
    orchestrator, client = make(fake_client, ['{"action": "final_answer", "input": "ok"}'])
    orchestrator.run(
        question="and after that?",
        system_prompt=SYSTEM,
        history=[("user", "when is half term"), ("agent", "late October")],
    )
    assert "late October" in client.calls[0]["prompt"]


# ------------------------------------------------------------------- malformed output


def test_retries_once_when_json_is_malformed(fake_client):
    orchestrator, client = make(
        fake_client,
        [
            "I am not replying in JSON today.",
            '{"action": "final_answer", "input": "Recovered."}',
        ],
    )
    result = orchestrator.run(question="q", system_prompt=SYSTEM)

    assert result.answer == "Recovered."
    assert result.steps[0].repaired is True
    assert len(client.calls) == 2  # original + one repair


def test_gives_safe_fallback_after_two_bad_parses(fake_client):
    orchestrator, client = make(fake_client, ["still not json", "and still not json"])
    result = orchestrator.run(question="q", system_prompt=SYSTEM)

    assert result.answer == SAFE_FALLBACK
    assert result.stop_reason == "unparseable_output"
    assert not result.succeeded
    # Exactly one retry — it must not keep hammering the model.
    assert len(client.calls) == 2


def test_empty_final_answer_falls_back_safely(fake_client):
    orchestrator, _ = make(fake_client, ['{"action": "final_answer", "input": "   "}'])
    result = orchestrator.run(question="q", system_prompt=SYSTEM)
    assert result.answer == SAFE_FALLBACK


# ------------------------------------------------------------------ bad tool requests


def test_invented_tool_is_fed_back_for_correction(fake_client):
    orchestrator, _ = make(
        fake_client,
        [
            '{"action": "send_email", "input": "head@school"}',
            '{"action": "final_answer", "input": "I cannot send email."}',
        ],
    )
    result = orchestrator.run(question="q", system_prompt=SYSTEM)

    assert result.answer == "I cannot send email."
    assert "No such tool" in result.steps[0].observation
    assert result.steps[0].ok is False


def test_tool_failure_does_not_abort_the_turn(fake_client):
    orchestrator, _ = make(
        fake_client,
        [
            '{"action": "calculator", "input": "1/0"}',
            '{"action": "final_answer", "input": "That cannot be divided."}',
        ],
    )
    result = orchestrator.run(question="q", system_prompt=SYSTEM)
    assert result.answer == "That cannot be divided."
    assert result.steps[0].ok is False


# ------------------------------------------------------------------------- loop safety


def test_repeated_identical_call_is_nudged_not_rerun(fake_client):
    orchestrator, _ = make(
        fake_client,
        [
            '{"action": "calculator", "input": "2+2"}',
            '{"action": "calculator", "input": "2+2"}',   # duplicate -> nudge
            '{"action": "final_answer", "input": "It is 4."}',
        ],
    )
    result = orchestrator.run(question="q", system_prompt=SYSTEM)

    assert result.answer == "It is 4."
    # The tool ran once; the duplicate was answered with a nudge instead.
    assert result.tool_calls == ["calculator"]
    assert "already ran" in result.steps[1].observation


def test_persistent_repetition_ends_the_turn(fake_client):
    orchestrator, _ = make(
        fake_client,
        [
            '{"action": "calculator", "input": "2+2"}',
            '{"action": "calculator", "input": "2+2"}',
            '{"action": "calculator", "input": "2+2"}',
            "Here is a plain summary: it is 4.",  # the salvage generation
        ],
    )
    result = orchestrator.run(question="q", system_prompt=SYSTEM)
    assert result.stop_reason == "repetition_guard"
    assert "4" in result.answer
    # A salvaged answer is still an answer — it must not inflate the NFR3 failure count.
    assert result.succeeded


def test_iteration_cap_is_respected(fake_client):
    orchestrator, _ = make(
        fake_client,
        [
            '{"action": "calculator", "input": "1+1"}',
            '{"action": "calculator", "input": "2+2"}',
            "A plain summary of what I found.",
        ],
        max_iterations=2,
    )
    result = orchestrator.run(question="q", system_prompt=SYSTEM)

    assert result.iterations == 2
    assert result.stop_reason == "max_iterations_summarised"
    assert result.answer == "A plain summary of what I found."
    assert result.succeeded


# ------------------------------------------------------------- model failure (FR5)


def test_falls_back_to_the_second_model_on_timeout(fake_client):
    orchestrator, client = make(
        fake_client,
        [
            OllamaTimeout("primary timed out"),
            '{"action": "final_answer", "input": "Answered by the backup."}',
        ],
    )
    result = orchestrator.run(question="q", system_prompt=SYSTEM)

    assert result.answer == "Answered by the backup."
    assert result.used_fallback
    assert "timed out" in result.fallback_reason
    assert client.calls[0]["model"] == "primary"
    assert client.calls[1]["model"] == "secondary"


def test_reports_unavailable_when_every_model_fails(fake_client):
    orchestrator, _ = make(
        fake_client,
        [OllamaUnavailable("down"), OllamaUnavailable("also down")],
    )
    result = orchestrator.run(question="q", system_prompt=SYSTEM)

    assert result.stop_reason == "model_unavailable"
    assert not result.succeeded
    assert "unavailable" in result.answer.lower()


def test_timings_are_recorded_for_nfr3(fake_client):
    orchestrator, _ = make(
        fake_client,
        [
            '{"action": "calculator", "input": "2+2"}',
            '{"action": "final_answer", "input": "4"}',
        ],
    )
    result = orchestrator.run(question="q", system_prompt=SYSTEM)

    assert result.model_s > 0      # two generations
    assert result.tool_s >= 0      # one tool call
    assert result.model == "primary"
