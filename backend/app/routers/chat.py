"""Chat endpoints backing the Figure 3.7 interface.

From Phase 2 replies come from a locally-served model (`LocalModelRuntime`). This module
owns the three things the agent layer deliberately does not:

* loading memory (FR8) — the conversation buffer and the caller's role preferences
* resolving which models the agent chose in the wizard (FR5)
* writing the `request_log` row every turn, for the NFR3 latency evaluation
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..agent.memory import conversation_buffer, role_preferences
from ..agent.runtime import AgentInvocation, RoleContext, get_runtime
from ..config import get_settings
from ..graph import get_client
from ..graph.retrieval import RetrievalService
from ..pipeline import build_pipeline
from ..deps import CurrentUser, DbSession
from ..models import (
    Agent,
    AgentStatus,
    ChatMessage,
    ChatSession,
    MessageSender,
    RequestLog,
    UserRole,
)
from ..schemas import (
    ChatSessionCreate,
    ChatSessionOut,
    ChatSessionSummary,
    MessageIn,
    MessageOut,
    SendMessageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _assert_can_use_agent(agent: Agent, user) -> None:
    if user.role == UserRole.parent:
        if agent.status != AgentStatus.published:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This agent is not published yet")
        if UserRole.parent.value not in (agent.audience_roles or []):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This agent is not available to parents")


def _owned_session(session_id: int, user, db) -> ChatSession:
    session = db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return session


@router.get("/sessions", response_model=list[ChatSessionSummary])
def list_sessions(user: CurrentUser, db: DbSession) -> list[ChatSessionSummary]:
    sessions = db.scalars(
        select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.id.desc())
    ).all()
    return [ChatSessionSummary.model_validate(s) for s in sessions]


@router.post("/sessions", response_model=ChatSessionOut, status_code=status.HTTP_201_CREATED)
def create_session(payload: ChatSessionCreate, user: CurrentUser, db: DbSession) -> ChatSessionOut:
    agent = db.get(Agent, payload.agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    _assert_can_use_agent(agent, user)

    session = ChatSession(
        agent_id=agent.id,
        user_id=user.id,
        title=payload.title or f"Chat with {agent.name}",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return ChatSessionOut.model_validate(session)


@router.get("/sessions/{session_id}", response_model=ChatSessionOut)
def get_session(session_id: int, user: CurrentUser, db: DbSession) -> ChatSessionOut:
    return ChatSessionOut.model_validate(_owned_session(session_id, user, db))


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: int, user: CurrentUser, db: DbSession) -> None:
    db.delete(_owned_session(session_id, user, db))
    db.commit()


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
def send_message(
    session_id: int, payload: MessageIn, user: CurrentUser, db: DbSession
) -> SendMessageResponse:
    session = _owned_session(session_id, user, db)
    agent = db.get(Agent, session.agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent no longer exists")
    _assert_can_use_agent(agent, user)

    settings = get_settings()
    turn_started = time.perf_counter()

    # Memory (FR8): recent turns of this conversation, plus standing role preferences.
    history = conversation_buffer(db, session.id, settings.memory_buffer_turns)
    preferences = role_preferences(db, user.role)

    user_message = ChatMessage(
        session_id=session.id, sender=MessageSender.user, content=payload.content
    )
    db.add(user_message)
    db.flush()

    model_settings = agent.model_settings or {}
    invocation = AgentInvocation(
        agent_id=agent.id,
        agent_name=agent.name,
        role_instructions=agent.role_instructions,
        knowledge_sources=[s.label for s in agent.knowledge_sources],
        tools=[t.tool_key for t in agent.tools if t.enabled],
        guardrails=[f"{g.guardrail_type.value}: {g.value}" for g in agent.guardrails],
        model=model_settings.get("model", ""),
        fallback_model=model_settings.get("fallback_model", ""),
        preferences=preferences,
    )
    # The retrieval service is built here because this is where the database session
    # lives; the agent layer stays free of persistence. Passing `db` is what lets the
    # access audit (FR12) be written inside the retrieval call itself.
    runtime = get_runtime()
    if hasattr(runtime, "retrieval"):
        runtime.retrieval = RetrievalService(get_client(), db)
    if hasattr(runtime, "pipeline"):
        # Same object the Phase 4 harness calls, so evaluation measures the real path.
        runtime.pipeline = build_pipeline(db)

    reply = runtime.generate(
        invocation=invocation,
        history=history,
        message=payload.content,
        role=RoleContext(
            user_id=user.id,
            full_name=user.full_name,
            role=user.role.value,
            email=user.email,
            session_id=session.id,
        ),
    )

    agent_message = ChatMessage(
        session_id=session.id,
        sender=MessageSender.agent,
        content=reply.content,
        meta=reply.meta,
    )
    db.add(agent_message)

    _log_request(
        db,
        session=session,
        agent=agent,
        user=user,
        meta=reply.meta,
        total_s=time.perf_counter() - turn_started,
    )

    db.commit()
    db.refresh(user_message)
    db.refresh(agent_message)

    return SendMessageResponse(
        user_message=MessageOut.model_validate(user_message),
        agent_message=MessageOut.model_validate(agent_message),
    )


def _log_request(db, *, session, agent, user, meta: dict, total_s: float) -> None:
    """Write one `request_log` row (NFR3).

    Logging must never cost a user their answer, so a failure here is swallowed — the
    reply is already built and about to be committed.
    """
    try:
        db.add(
            RequestLog(
                session_id=session.id,
                agent_id=agent.id,
                user_id=user.id,
                role=user.role.value,
                route=str(meta.get("route", "unknown"))[:20],
                model_requested=str(meta.get("model_requested", ""))[:100],
                model_used=str(meta.get("model_used", ""))[:100],
                used_fallback=bool(meta.get("used_fallback", False)),
                total_ms=int(total_s * 1000),
                model_ms=int(float(meta.get("model_s", 0.0)) * 1000),
                tool_ms=int(float(meta.get("tool_s", 0.0)) * 1000),
                iterations=int(meta.get("iterations", 0)),
                tool_calls=len(meta.get("tool_calls", []) or []),
                stop_reason=str(meta.get("stop_reason", ""))[:50] or None,
                succeeded=bool(meta.get("succeeded", True)),
                error=(str(meta["error"])[:255] if meta.get("error") else None),
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not write request_log row")
