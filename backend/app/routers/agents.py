"""FR3 — the no-code agent builder.

The wizard posts the whole configuration as one document; these routes validate it against
the server-side catalog so a staff member can never enter an invalid tool key. No code,
no prompt syntax, and no model settings are exposed to the end user.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent.catalog import VALID_TOOL_KEYS, build_catalog
from ..deps import CurrentUser, DbSession, StaffUser
from ..models import (
    Agent,
    AgentGuardrail,
    AgentKnowledgeSource,
    AgentStatus,
    AgentTool,
    UserRole,
)
from ..schemas import AgentIn, AgentOut, AgentSummary

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/catalog")
def catalog(user: CurrentUser) -> dict:
    """Everything the wizard needs to draw its option lists."""
    return build_catalog()


def _apply_children(agent: Agent, payload: AgentIn, db: Session) -> None:
    unknown = {tool.tool_key for tool in payload.tools} - VALID_TOOL_KEYS
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unknown tool(s): {', '.join(sorted(unknown))}",
        )

    # Replace rather than merge — the wizard always submits the full configuration.
    # Clear and flush before repopulating so the orphan DELETEs are emitted ahead of the
    # new INSERTs; otherwise re-saving an unchanged tool trips uq_agent_tool.
    agent.knowledge_sources.clear()
    agent.tools.clear()
    agent.guardrails.clear()
    db.flush()

    agent.knowledge_sources = [
        AgentKnowledgeSource(
            source_type=source.source_type,
            label=source.label,
            location=source.location,
            notes=source.notes,
        )
        for source in payload.knowledge_sources
    ]
    agent.tools = [
        AgentTool(tool_key=tool.tool_key, enabled=tool.enabled, config=tool.config)
        for tool in payload.tools
    ]
    agent.guardrails = [
        AgentGuardrail(guardrail_type=rail.guardrail_type, value=rail.value)
        for rail in payload.guardrails
    ]


@router.get("", response_model=list[AgentSummary])
def list_agents(user: CurrentUser, db: DbSession) -> list[AgentSummary]:
    """Staff see every agent; parents see only published agents aimed at them."""
    query = select(Agent).order_by(Agent.updated_at.desc())
    agents = db.scalars(query).all()

    if user.role == UserRole.parent:
        agents = [
            a
            for a in agents
            if a.status == AgentStatus.published and UserRole.parent.value in (a.audience_roles or [])
        ]
    return [AgentSummary.model_validate(a) for a in agents]


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentIn, staff: StaffUser, db: DbSession) -> AgentOut:
    agent = Agent(
        name=payload.name,
        description=payload.description,
        role_instructions=payload.role_instructions,
        audience_roles=[role.value for role in payload.audience_roles],
        status=payload.status,
        model_settings=payload.model_settings.model_dump(),
        created_by=staff.id,
    )
    _apply_children(agent, payload, db)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return AgentOut.model_validate(agent)


def _get_visible_agent(agent_id: int, user, db) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    if user.role == UserRole.parent and (
        agent.status != AgentStatus.published
        or UserRole.parent.value not in (agent.audience_roles or [])
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return agent


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: int, user: CurrentUser, db: DbSession) -> AgentOut:
    return AgentOut.model_validate(_get_visible_agent(agent_id, user, db))


@router.put("/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, payload: AgentIn, staff: StaffUser, db: DbSession) -> AgentOut:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")

    agent.name = payload.name
    agent.description = payload.description
    agent.role_instructions = payload.role_instructions
    agent.audience_roles = [role.value for role in payload.audience_roles]
    agent.status = payload.status
    agent.model_settings = payload.model_settings.model_dump()
    _apply_children(agent, payload, db)

    db.commit()
    db.refresh(agent)
    return AgentOut.model_validate(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(agent_id: int, staff: StaffUser, db: DbSession) -> None:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    db.delete(agent)
    db.commit()
