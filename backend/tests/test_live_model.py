"""End-to-end against the real local model.

Marked `live` and skipped automatically when Ollama is not running, so the rest of the
suite stays fast and offline. These are the tests that would catch a model or prompt
regression that the scripted fakes cannot.
"""

from __future__ import annotations

import time

import pytest

from app.agent.local_runtime import LocalModelRuntime
from app.agent.ollama_client import ModelNotFound, OllamaClient
from app.agent.runtime import AgentInvocation, RoleContext


def _ollama_available() -> bool:
    return OllamaClient().is_available()


# Skips the whole module rather than failing when Ollama is not running, so the offline
# suite stays green on a machine with no models pulled.
pytestmark = pytest.mark.skipif(not _ollama_available(), reason="Ollama is not running")

PARENT = RoleContext(user_id=1, full_name="Mei Lin Tan", role="parent")

# NFR3: mean within 10s, p95 within 20s. A single test asserts the looser bound so it
# does not flake on a cold model load; the benchmark script measures the distribution.
NFR3_UPPER_BOUND_S = 20.0


def ask(message: str, tools: list[str]):
    runtime = LocalModelRuntime()
    invocation = AgentInvocation(
        agent_id=1,
        agent_name="Parent Helpdesk",
        role_instructions="Help parents with everyday school questions.",
        tools=tools,
    )
    started = time.perf_counter()
    reply = runtime.generate(
        invocation=invocation, history=[], message=message, role=PARENT
    )
    return reply, time.perf_counter() - started


def test_ollama_is_reachable_and_has_the_configured_models():
    from app.config import get_settings

    settings = get_settings()
    available = OllamaClient().list_models()
    assert settings.default_model in available, f"run: ollama pull {settings.default_model}"
    assert settings.fallback_model in available, f"run: ollama pull {settings.fallback_model}"


def test_greeting_answers_directly_and_quickly():
    reply, elapsed = ask("Hello!", [])
    assert reply.meta["route"] == "direct"
    assert reply.content.strip()
    assert elapsed < NFR3_UPPER_BOUND_S


def test_calculator_question_produces_the_right_number():
    reply, elapsed = ask("What is 450 minus 200, then multiplied by 0.15?", ["calculator"])
    assert "37.5" in reply.content
    assert "calculator" in reply.meta["tool_calls"]
    assert elapsed < NFR3_UPPER_BOUND_S


def test_school_record_question_routes_to_graph_retrieval():
    """Phase 3a: record questions reach SPARQL, not a placeholder.

    With no retrieval service attached the runtime must say so rather than answer without
    a role scope — failing closed is the whole point.
    """
    reply, _ = ask("Has my child missed any school this week?", ["search"])
    assert reply.meta["route"] == "retrieval"
    assert reply.meta["stop_reason"] == "retrieval_unavailable"
    assert "cannot look up" in reply.content.lower()


def test_retrieval_answers_from_the_graph_when_fuseki_is_available():
    """The real end-to-end path, skipped when Fuseki is not running."""
    from app.database import SessionLocal
    from app.graph import FusekiClient, Principal
    from app.graph.retrieval import RetrievalService
    from app.models import AppUser, UserRole
    from sqlalchemy import select

    fuseki = FusekiClient()
    if not fuseki.is_available():
        pytest.skip("Fuseki is not running")

    db = SessionLocal()
    try:
        user = db.scalar(
            select(AppUser).where(AppUser.email == "parent1@greenfield.edu")
        )
        if user is None:
            pytest.skip("synthetic dataset not loaded (run generate_data.py)")

        runtime = LocalModelRuntime(retrieval=RetrievalService(fuseki, db))
        reply = runtime.generate(
            invocation=AgentInvocation(
                agent_id=1, agent_name="Helpdesk",
                role_instructions="Help with school records.", tools=[],
            ),
            history=[],
            message="Has my child missed any school?",
            role=RoleContext(
                user_id=user.id, full_name=user.full_name,
                role=user.role.value, email=user.email,
            ),
        )
        assert reply.meta["route"] == "retrieval"
        assert reply.meta["template"] == "attendance"
        assert reply.meta["refused"] is False
        # Exactly one pupil: this parent has one child, and the scope enforces it.
        assert len(reply.meta["citations"]) == 1
        assert reply.content.strip()
    finally:
        db.close()


def test_meta_carries_everything_the_nfr3_log_needs():
    reply, _ = ask("Hello!", [])
    meta = reply.meta
    for key in ("model_used", "model_s", "tool_s", "total_s", "route", "iterations"):
        assert key in meta, f"missing {key}"
    assert meta["model_used"]
    assert meta["stub"] is False


def test_fallback_engages_when_the_default_model_is_missing():
    """FR5: a bad default must transparently fall back, not fail the turn."""
    from app.config import get_settings

    client = OllamaClient()
    result = client.generate_with_fallback(
        model="definitely-not-a-real-model:99b",
        fallback_model=get_settings().fallback_model,
        prompt="Say the single word: ready",
    )
    assert result.completion.used_fallback
    assert result.completion.model == get_settings().fallback_model
    assert len(result.attempts) == 2
    assert result.attempts[0].ok is False


def test_missing_model_with_no_fallback_raises():
    with pytest.raises(ModelNotFound):
        OllamaClient().generate(model="definitely-not-a-real-model:99b", prompt="hi")
