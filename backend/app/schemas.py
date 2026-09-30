"""Pydantic request/response models."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .models import (
    AgentStatus,
    AttendanceStatus,
    FeeStatus,
    GuardrailType,
    KnowledgeSourceType,
    MessageSender,
    UserRole,
)

ORM = ConfigDict(from_attributes=True)


# ----------------------------------------------------------------------------- auth (FR1)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=150)
    role: UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ORM

    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool


class SessionOut(BaseModel):
    """What the frontend stores; `role` is echoed so the UI can branch without decoding the JWT."""

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    role: UserRole
    user: UserOut


# ------------------------------------------------------------------- agent builder (FR3)


class KnowledgeSourceIn(BaseModel):
    source_type: KnowledgeSourceType
    label: str = Field(min_length=1, max_length=150)
    location: str = Field(min_length=1, max_length=500)
    notes: str | None = None


class KnowledgeSourceOut(KnowledgeSourceIn):
    model_config = ORM

    id: int


class ToolIn(BaseModel):
    tool_key: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    config: dict = Field(default_factory=dict)


class ToolOut(ToolIn):
    model_config = ORM

    id: int


class GuardrailIn(BaseModel):
    guardrail_type: GuardrailType
    value: str = Field(min_length=1)


class GuardrailOut(GuardrailIn):
    model_config = ORM

    id: int


class ModelSettingsIn(BaseModel):
    """Which local model answers for this agent, and what to use if it fails (FR5)."""

    model: str = Field(default="", max_length=100)
    fallback_model: str = Field(default="", max_length=100)


class AgentIn(BaseModel):
    """The whole wizard in one payload — the steps are a UI concern, not an API one."""

    name: str = Field(min_length=1, max_length=150)
    description: str | None = None
    role_instructions: str = ""
    audience_roles: list[UserRole] = Field(default_factory=list)
    status: AgentStatus = AgentStatus.draft
    model_settings: ModelSettingsIn = Field(default_factory=ModelSettingsIn)
    knowledge_sources: list[KnowledgeSourceIn] = Field(default_factory=list)
    tools: list[ToolIn] = Field(default_factory=list)
    guardrails: list[GuardrailIn] = Field(default_factory=list)


class AgentSummary(BaseModel):
    model_config = ORM

    id: int
    name: str
    description: str | None
    status: AgentStatus
    audience_roles: list[str]
    updated_at: datetime


class AgentOut(BaseModel):
    model_config = ORM

    id: int
    name: str
    description: str | None
    role_instructions: str
    audience_roles: list[str]
    status: AgentStatus
    model_settings: dict
    created_by: int | None
    created_at: datetime
    updated_at: datetime
    knowledge_sources: list[KnowledgeSourceOut]
    tools: list[ToolOut]
    guardrails: list[GuardrailOut]


# -------------------------------------------------------------------- chat (Figure 3.7)


class ChatSessionCreate(BaseModel):
    agent_id: int
    title: str | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1)


class MessageOut(BaseModel):
    model_config = ORM

    id: int
    sender: MessageSender
    content: str
    meta: dict
    created_at: datetime


class ChatSessionSummary(BaseModel):
    model_config = ORM

    id: int
    agent_id: int
    title: str
    created_at: datetime


class ChatSessionOut(ChatSessionSummary):
    messages: list[MessageOut]


class SendMessageResponse(BaseModel):
    user_message: MessageOut
    agent_message: MessageOut


# -------------------------------------------------------------- school records (Fig 3.6)


class StudentOut(BaseModel):
    model_config = ORM

    id: int
    student_no: str
    full_name: str
    date_of_birth: date | None
    class_id: int | None
    guardian_id: int | None


class ClassOut(BaseModel):
    model_config = ORM

    id: int
    name: str
    grade_level: str | None
    academic_year: str | None
    room: str | None
    teacher_id: int | None


class FeeOut(BaseModel):
    model_config = ORM

    id: int
    student_id: int
    description: str
    amount: Decimal
    amount_paid: Decimal
    due_date: date | None
    paid_date: date | None
    status: FeeStatus


class ResultOut(BaseModel):
    model_config = ORM

    id: int
    student_id: int
    subject: str
    term: str | None
    assessment: str | None
    score: Decimal | None
    max_score: Decimal | None
    grade: str | None


class AttendanceOut(BaseModel):
    model_config = ORM

    id: int
    student_id: int
    attendance_date: date
    status: AttendanceStatus
    remarks: str | None


class HomeworkOut(BaseModel):
    model_config = ORM

    id: int
    class_id: int
    subject: str
    title: str
    description: str | None
    assigned_date: date | None
    due_date: date | None


class ScheduleOut(BaseModel):
    model_config = ORM

    id: int
    class_id: int
    subject: str
    day_of_week: int
    start_time: time
    end_time: time
    room: str | None
