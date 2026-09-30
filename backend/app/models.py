"""ORM models.

Two groups:

1. School records (Figure 3.6): app_user, student, class, fee, result, attendance,
   homework, schedule. These are the data an agent will eventually be grounded on.
2. Platform tables: user_session (FR1), agent + its child config tables (FR3),
   chat_session / chat_message (Figure 3.7 transcript storage).
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class UserRole(str, enum.Enum):
    """The three actors in Table 3.1. Carried as a claim on every session token."""

    parent = "parent"
    teacher = "teacher"
    administrator = "administrator"


class AttendanceStatus(str, enum.Enum):
    present = "present"
    absent = "absent"
    late = "late"
    excused = "excused"


class FeeStatus(str, enum.Enum):
    unpaid = "unpaid"
    partial = "partial"
    paid = "paid"
    waived = "waived"


class AgentStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class KnowledgeSourceType(str, enum.Enum):
    document = "document"
    url = "url"
    database_table = "database_table"
    faq = "faq"


class GuardrailType(str, enum.Enum):
    blocked_topic = "blocked_topic"
    required_disclaimer = "required_disclaimer"
    pii_redaction = "pii_redaction"
    refusal_message = "refusal_message"
    max_response_words = "max_response_words"


class MessageSender(str, enum.Enum):
    user = "user"
    agent = "agent"
    system = "system"


def _enum(py_enum: type[enum.Enum], name: str) -> Enum:
    """Store enums by value so MySQL ENUM(...) and the sqlite fallback agree."""
    return Enum(py_enum, name=name, values_callable=lambda e: [m.value for m in e])


# --------------------------------------------------------------------------------------
# School records (Figure 3.6)
# --------------------------------------------------------------------------------------


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user")
    children: Mapped[list["Student"]] = relationship(back_populates="guardian")


class SchoolClass(Base):
    """Table `class`. Renamed on the Python side because `class` is a keyword."""

    __tablename__ = "class"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    grade_level: Mapped[str | None] = mapped_column(String(50))
    academic_year: Mapped[str | None] = mapped_column(String(20))
    room: Mapped[str | None] = mapped_column(String(50))
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))

    teacher: Mapped[AppUser | None] = relationship()
    students: Mapped[list["Student"]] = relationship(back_populates="school_class")


class Student(Base):
    __tablename__ = "student"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("class.id", ondelete="SET NULL"))
    # Parent-to-child link: how a `parent` token is scoped to the rows it may read.
    guardian_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    enrolled_on: Mapped[date | None] = mapped_column(Date)

    school_class: Mapped[SchoolClass | None] = relationship(back_populates="students")
    guardian: Mapped[AppUser | None] = relationship(back_populates="children")


class Fee(Base):
    __tablename__ = "fee"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    amount_paid: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    due_date: Mapped[date | None] = mapped_column(Date)
    paid_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[FeeStatus] = mapped_column(_enum(FeeStatus, "fee_status"), default=FeeStatus.unpaid)

    student: Mapped[Student] = relationship()


class Result(Base):
    __tablename__ = "result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id", ondelete="CASCADE"))
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    term: Mapped[str | None] = mapped_column(String(50))
    assessment: Mapped[str | None] = mapped_column(String(100))
    score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    max_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    grade: Mapped[str | None] = mapped_column(String(5))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    student: Mapped[Student] = relationship()


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("student_id", "attendance_date", name="uq_attendance_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id", ondelete="CASCADE"))
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[AttendanceStatus] = mapped_column(_enum(AttendanceStatus, "attendance_status"))
    remarks: Mapped[str | None] = mapped_column(String(255))

    student: Mapped[Student] = relationship()


class Homework(Base):
    __tablename__ = "homework"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("class.id", ondelete="CASCADE"))
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    assigned_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)

    school_class: Mapped[SchoolClass] = relationship()


class Schedule(Base):
    __tablename__ = "schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("class.id", ondelete="CASCADE"))
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    # 1 = Monday .. 7 = Sunday (ISO-8601).
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    room: Mapped[str | None] = mapped_column(String(50))

    school_class: Mapped[SchoolClass] = relationship()


# --------------------------------------------------------------------------------------
# Platform tables
# --------------------------------------------------------------------------------------


class UserSession(Base):
    """One row per issued access token (FR1), so logout can revoke server-side."""

    __tablename__ = "user_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "session_role"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped[AppUser] = relationship(back_populates="sessions")


class Agent(Base):
    """An agent configured through the no-code wizard (FR3)."""

    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Wizard step 2: the persona/instructions staff write in plain English.
    role_instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Which signed-in roles are allowed to chat with this agent.
    audience_roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[AgentStatus] = mapped_column(_enum(AgentStatus, "agent_status"), default=AgentStatus.draft)
    # Reserved for Phase 2 (model id, temperature, context window). Empty while stubbed.
    model_settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    knowledge_sources: Mapped[list["AgentKnowledgeSource"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    tools: Mapped[list["AgentTool"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    guardrails: Mapped[list["AgentGuardrail"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class AgentKnowledgeSource(Base):
    """Wizard step 3. Phase 3 will index these into the RAG store / knowledge graph."""

    __tablename__ = "agent_knowledge_source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"))
    source_type: Mapped[KnowledgeSourceType] = mapped_column(_enum(KnowledgeSourceType, "knowledge_source_type"))
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    # File path, URL, or school table name depending on source_type.
    location: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    agent: Mapped[Agent] = relationship(back_populates="knowledge_sources")


class AgentTool(Base):
    """Wizard step 4. tool_key matches an entry in app/agent/catalog.py."""

    __tablename__ = "agent_tool"
    __table_args__ = (UniqueConstraint("agent_id", "tool_key", name="uq_agent_tool"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"))
    tool_key: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    agent: Mapped[Agent] = relationship(back_populates="tools")


class AgentGuardrail(Base):
    """Wizard step 5."""

    __tablename__ = "agent_guardrail"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"))
    guardrail_type: Mapped[GuardrailType] = mapped_column(_enum(GuardrailType, "guardrail_type"))
    value: Mapped[str] = mapped_column(Text, nullable=False)

    agent: Mapped[Agent] = relationship(back_populates="guardrails")


class ChatSession(Base):
    __tablename__ = "chat_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    agent: Mapped[Agent] = relationship()
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_session.id", ondelete="CASCADE"))
    sender: Mapped[MessageSender] = mapped_column(_enum(MessageSender, "message_sender"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Phase 3/4 will store retrieved citations and eval scores here.
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped[ChatSession] = relationship(back_populates="messages")

# --------------------------------------------------------------------------------------
# Phase 2: memory (FR8) and latency logging (NFR3)
# --------------------------------------------------------------------------------------


class RolePreference(Base):
    """Long-term memory: standing preferences that apply to everyone with a role (FR8).

    Keyed by role rather than by user, so this is school policy rather than personal data.
    `pref_key` makes updates idempotent; `priority` orders them in the system prompt.
    """

    __tablename__ = "role_preference"
    __table_args__ = (UniqueConstraint("role", "pref_key", name="uq_role_preference"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    pref_key: Mapped[str] = mapped_column(String(100), nullable=False)
    preference: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class RequestLog(Base):
    """One row per chat turn, for the NFR3 latency evaluation.

    NFR3 targets mean <= 10s and p95 <= 20s, so both the total and the model-only time are
    recorded: if the mean drifts, this says whether the cause is inference, tools, or the
    orchestration around them. `model_used` matters because a fallback answer is slower by
    construction and should be visible as such rather than skewing the headline figure.
    """

    __tablename__ = "request_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_session.id", ondelete="SET NULL")
    )
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agent.id", ondelete="SET NULL"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    role: Mapped[str | None] = mapped_column(String(20))

    route: Mapped[str] = mapped_column(String(20), nullable=False)
    model_requested: Mapped[str] = mapped_column(String(100), nullable=False)
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    used_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    total_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    model_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(50))
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# --------------------------------------------------------------------------------------
# Phase 3a: announcements (Table 3.4) and the access audit trail (FR12)
# --------------------------------------------------------------------------------------


class Announcement(Base):
    """School- or class-wide notices. Part of the Table 3.4 synthetic dataset."""

    __tablename__ = "announcement"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL means school-wide rather than aimed at one class.
    class_id: Mapped[int | None] = mapped_column(ForeignKey("class.id", ondelete="CASCADE"))
    audience: Mapped[str] = mapped_column(String(20), nullable=False, default="all")
    published_on: Mapped[date] = mapped_column(Date, nullable=False)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))


class TeacherClass(Base):
    """Which classes a teacher takes.

    `class.teacher_id` records the form tutor only. A teacher's access-control scope is
    every class they teach, which is many-to-many, so it needs its own table. This is the
    relational twin of the `agso:teaches` edge added to the ontology.
    """

    __tablename__ = "teacher_class"
    __table_args__ = (UniqueConstraint("teacher_id", "class_id", name="uq_teacher_class"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    class_id: Mapped[int] = mapped_column(ForeignKey("class.id", ondelete="CASCADE"))
    subject: Mapped[str | None] = mapped_column(String(100))


class AccessAudit(Base):
    """Every retrieval decision, allowed or refused (FR10 enforcement, FR12 review).

    Refusals are the reason this table exists: an administrator needs to see who asked for
    what they could not have. Allowed requests are logged too, so a refusal rate is
    meaningful and so an investigation can reconstruct what a user actually saw.
    """

    __tablename__ = "access_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_session.id", ondelete="SET NULL")
    )

    template: Mapped[str] = mapped_column(String(50), nullable=False)
    # What the user actually typed, so an administrator can judge intent.
    question: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Set only on refusals: which rule blocked it.
    refusal_reason: Mapped[str | None] = mapped_column(String(255))
    # The subject the user tried to reach but could not, when that is identifiable.
    requested_student: Mapped[str | None] = mapped_column(String(50))

    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --------------------------------------------------------------------------------------
# Phase 3b: document corpus and vector store (FR4, FR9)
# --------------------------------------------------------------------------------------


class DocumentStatus(str, enum.Enum):
    pending = "pending"
    indexed = "indexed"
    failed = "failed"


class Document(Base):
    """An uploaded document, and who is allowed to read from it.

    `audience_roles` is the document-level half of RBAC: the staff handbook is not for
    parents, and a fee circular is not for teachers. Chunk retrieval filters on it before
    ranking, so text a caller may not see never reaches the model's context at all —
    filtering afterwards would already have leaked it into the prompt.
    """

    __tablename__ = "document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False, default="general")
    media_type: Mapped[str] = mapped_column(String(100), nullable=False, default="text/plain")

    audience_roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Set when the document concerns one class only; NULL means school-wide.
    class_id: Mapped[int | None] = mapped_column(ForeignKey("class.id", ondelete="SET NULL"))

    # Where the original file lives, relative to the corpus directory.
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # SHA-256 of the file, so re-uploading an unchanged file is a no-op.
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus, "document_status"), default=DocumentStatus.pending
    )
    error: Mapped[str | None] = mapped_column(String(500))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime)

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    """One retrievable passage, with its embedding.

    The vector is stored as a raw float32 BLOB rather than in sqlite-vec: the python.org
    macOS build of Python ships `sqlite3` with loadable extensions disabled, so no SQLite
    extension can be loaded at all. At this corpus size exact cosine search over a numpy
    matrix beats an approximate index anyway — it is a few milliseconds and has perfect
    recall. See app/rag/store.py.
    """

    __tablename__ = "document_chunk"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Page or section the passage came from, so a citation can point at it.
    location: Mapped[str | None] = mapped_column(String(100))
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    document: Mapped[Document] = relationship(back_populates="chunks")
