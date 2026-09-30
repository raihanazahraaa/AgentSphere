"""Agent runtime boundary.

`AgentRuntime` is the contract every runtime implements, so routers and the frontend never
know which one is active:

* `StubAgentRuntime` — deterministic placeholder, no model. Kept because it makes the API
  testable with no Ollama running, which the test suite relies on.
* `LocalModelRuntime` (`local_runtime.py`) — Phase 2. Real answers via Ollama.

Selected by the `AGENT_RUNTIME` setting; `local` is the default from Phase 2 onwards.

Phase 3: `AgentInvocation.knowledge_sources` and `role` are what retrieval will be keyed
on — the role decides which rows/documents the retriever is even allowed to see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..config import get_settings


@dataclass(frozen=True)
class RoleContext:
    """Who is asking, carried from the session token into every agent call.

    Phase 3a added `email` and `session_id`: the graph identifies a person by their email
    (it is the natural key behind their node IRI), and the access audit needs the
    conversation the request came from. Both come from the validated session, never from
    anything the model or the user typed.
    """

    user_id: int
    full_name: str
    role: str
    email: str = ""
    session_id: int | None = None


@dataclass(frozen=True)
class AgentInvocation:
    """The configured agent, flattened into what a runtime needs."""

    agent_id: int
    agent_name: str
    role_instructions: str
    knowledge_sources: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    guardrails: list[str] = field(default_factory=list)
    # Phase 2 (FR5): chosen in the wizard. Empty falls back to the settings defaults.
    model: str = ""
    fallback_model: str = ""
    # Phase 2 (FR8): long-term role preferences, loaded by the caller from the database.
    preferences: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentReply:
    content: str
    meta: dict


class AgentRuntime(Protocol):
    def generate(
        self,
        *,
        invocation: AgentInvocation,
        history: list[tuple[str, str]],
        message: str,
        role: RoleContext,
    ) -> AgentReply: ...


class StubAgentRuntime:
    """Placeholder runtime. Echoes the configuration so the wiring is visible in the UI."""

    name = "stub"

    def generate(
        self,
        *,
        invocation: AgentInvocation,
        history: list[tuple[str, str]],
        message: str,
        role: RoleContext,
    ) -> AgentReply:
        lines = [
            f'This is a placeholder reply from "{invocation.agent_name}". '
            "The model layer is not implemented yet (Phase 2).",
            "",
            f"You asked: {message}",
            "",
            f"Answering as: {role.full_name} ({role.role})",
        ]
        if invocation.role_instructions:
            lines.append(f"Configured role: {invocation.role_instructions}")
        lines.append(
            f"Knowledge sources: {', '.join(invocation.knowledge_sources) or 'none configured'}"
        )
        lines.append(f"Tools enabled: {', '.join(invocation.tools) or 'none configured'}")
        lines.append(f"Guardrails: {', '.join(invocation.guardrails) or 'none configured'}")
        lines.append(f"Turns in this conversation so far: {len(history)}")

        meta = {
            "runtime": self.name,
            "stub": True,
            "agent_id": invocation.agent_id,
            "role": role.role,
            "tools_available": invocation.tools,
            # Phase 3 fills this with real retrieved passages.
            "citations": [],
        }
        return AgentReply(content="\n".join(lines), meta=meta)


def _runtime_classes() -> dict[str, type]:
    """Imported lazily: local_runtime imports this module for its dataclasses."""
    from .local_runtime import LocalModelRuntime

    return {"stub": StubAgentRuntime, "local": LocalModelRuntime}


def get_runtime(name: str | None = None) -> AgentRuntime:
    """Resolve a runtime by name, defaulting to the AGENT_RUNTIME setting."""
    runtimes = _runtime_classes()
    key = name or get_settings().agent_runtime
    runtime_cls = runtimes.get(key)
    if runtime_cls is None:
        raise RuntimeError(
            f"Unknown AGENT_RUNTIME '{key}'. Known: {', '.join(sorted(runtimes))}"
        )
    return runtime_cls()
