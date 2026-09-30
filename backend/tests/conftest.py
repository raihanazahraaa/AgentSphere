"""Test fixtures.

The orchestrator tests use a scripted fake client rather than a live model: they must be
fast, deterministic, and runnable with Ollama stopped. Tests that genuinely need a model
are marked `live` and skipped unless it is reachable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Tests run from backend/, importing the app package directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.ollama_client import Completion, GenerationResult, ModelAttempt, OllamaError  # noqa: E402


class FakeOllamaClient:
    """Returns queued responses in order; raises whatever is queued as an exception.

    Queue entries are either strings (returned as model output) or exception instances
    (raised, which is how model-failure and fallback paths get exercised).
    """

    def __init__(self, responses: list, model: str = "test-model") -> None:
        self.responses = list(responses)
        self.model = model
        self.calls: list[dict] = []

    def _next(self, model: str) -> Completion:
        if not self.responses:
            raise AssertionError("FakeOllamaClient ran out of queued responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return Completion(text=item, model=model, elapsed_s=0.01)

    def generate(self, *, model: str, prompt: str, **kwargs) -> Completion:
        self.calls.append({"model": model, "prompt": prompt, **kwargs})
        return self._next(model)

    def generate_with_fallback(
        self, *, model: str, fallback_model: str | None = None, **kwargs
    ) -> GenerationResult:
        attempts: list[ModelAttempt] = []
        try:
            completion = self.generate(model=model, **kwargs)
        except OllamaError as exc:
            attempts.append(ModelAttempt(model=model, ok=False, elapsed_s=0.0, error=str(exc)))
            if not fallback_model:
                raise
            completion = self.generate(model=fallback_model, **kwargs)
            completion.used_fallback = True
            completion.fallback_reason = str(exc)
        attempts.append(ModelAttempt(model=completion.model, ok=True, elapsed_s=0.01))
        return GenerationResult(completion=completion, attempts=attempts)

    def list_models(self) -> list[str]:
        return [self.model]


@pytest.fixture
def fake_client():
    return FakeOllamaClient


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point the file tools at a temp directory instead of backend/workspace."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    yield tmp_path
    get_settings.cache_clear()


def ollama_available() -> bool:
    from app.agent.ollama_client import OllamaClient

    return OllamaClient().is_available()


live = pytest.mark.skipif(not ollama_available(), reason="Ollama is not running")


@pytest.fixture
def db_session():
    """Throwaway in-memory database with the full schema."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app import models  # noqa: F401  (registers every table on Base)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def chat_session(db_session):
    from app.models import Agent, AppUser, ChatSession, UserRole

    user = AppUser(
        email="t@greenfield.edu", password_hash="x", full_name="Test", role=UserRole.parent
    )
    agent = Agent(name="Test agent", role_instructions="", audience_roles=[], model_settings={})
    db_session.add_all([user, agent])
    db_session.flush()

    session = ChatSession(agent_id=agent.id, user_id=user.id, title="t")
    db_session.add(session)
    db_session.flush()
    return session
