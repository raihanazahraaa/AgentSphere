"""The option lists the no-code wizard renders (FR3).

Kept server-side so a staff member never types a tool identifier or edits config by hand —
the UI just draws whatever this catalog returns. Phase 2/3 extend these lists as real tools
and retrieval strategies land; the wizard needs no change when they do.
"""

from __future__ import annotations

from ..config import get_settings
from ..models import GuardrailType, KnowledgeSourceType, UserRole
from .ollama_client import OllamaClient
from .tools import catalog_entries as builtin_tool_entries

# School-data tools. Declared here since Phase 1 but not yet implemented — they need the
# role-scoped retrieval that Phase 3 builds, so they are advertised as unavailable and the
# wizard greys them out rather than letting staff tick something that will not work.
SCHOOL_TOOL_CATALOG: list[dict] = [
    {
        "key": "lookup_student_record",
        "label": "Look up a student record",
        "description": "Finds a pupil by name or student number and reads their profile.",
        "requires_roles": [UserRole.teacher.value, UserRole.administrator.value],
    },
    {
        "key": "check_attendance",
        "label": "Check attendance",
        "description": "Reports attendance for a pupil or a class over a date range.",
        "requires_roles": [UserRole.parent.value, UserRole.teacher.value, UserRole.administrator.value],
    },
    {
        "key": "check_fees",
        "label": "Check fee balance",
        "description": "Reports outstanding and paid fees for a pupil.",
        "requires_roles": [UserRole.parent.value, UserRole.administrator.value],
    },
    {
        "key": "check_results",
        "label": "Check exam results",
        "description": "Reports assessment scores and grades by subject and term.",
        "requires_roles": [UserRole.parent.value, UserRole.teacher.value, UserRole.administrator.value],
    },
    {
        "key": "list_homework",
        "label": "List homework",
        "description": "Lists homework set for a class, with due dates.",
        "requires_roles": [UserRole.parent.value, UserRole.teacher.value, UserRole.administrator.value],
    },
    {
        "key": "class_schedule",
        "label": "Read the timetable",
        "description": "Reads the weekly timetable for a class.",
        "requires_roles": [UserRole.parent.value, UserRole.teacher.value, UserRole.administrator.value],
    },
]

KNOWLEDGE_SOURCE_CATALOG: list[dict] = [
    {
        "key": KnowledgeSourceType.document.value,
        "label": "Uploaded document",
        "description": "A policy, handbook, or newsletter (PDF or Word).",
        "location_label": "File name",
        "location_placeholder": "school-handbook-2026.pdf",
    },
    {
        "key": KnowledgeSourceType.url.value,
        "label": "Web page",
        "description": "A page on the school website the agent may quote.",
        "location_label": "Address",
        "location_placeholder": "https://school.example.edu/term-dates",
    },
    {
        "key": KnowledgeSourceType.database_table.value,
        "label": "School records table",
        "description": "Live records from the school database.",
        "location_label": "Table",
        "location_placeholder": "attendance",
        "options": ["student", "class", "fee", "result", "attendance", "homework", "schedule"],
    },
    {
        "key": KnowledgeSourceType.faq.value,
        "label": "Question and answer list",
        "description": "Common questions you have already written answers for.",
        "location_label": "List name",
        "location_placeholder": "Admissions FAQ",
    },
]

GUARDRAIL_CATALOG: list[dict] = [
    {
        "key": GuardrailType.blocked_topic.value,
        "label": "Topic the agent must not discuss",
        "input_label": "Topic",
        "placeholder": "Staff disciplinary matters",
        "help": "The agent refuses and offers to pass the question to a person.",
    },
    {
        "key": GuardrailType.required_disclaimer.value,
        "label": "Sentence to add to every answer",
        "input_label": "Sentence",
        "placeholder": "Please confirm with the school office before acting on this.",
        "help": "Appended to each reply.",
    },
    {
        "key": GuardrailType.pii_redaction.value,
        "label": "Hide personal details",
        "input_label": "What to hide",
        "placeholder": "phone numbers, home addresses",
        "help": "Matching details are removed before the reply is shown.",
    },
    {
        "key": GuardrailType.refusal_message.value,
        "label": "Wording used when the agent declines",
        "input_label": "Message",
        "placeholder": "I can't help with that — please email the school office.",
        "help": "Shown instead of the default refusal wording.",
    },
    {
        "key": GuardrailType.max_response_words.value,
        "label": "Longest answer allowed",
        "input_label": "Word limit",
        "placeholder": "150",
        "help": "Replies are kept under this length.",
    },
]

def _school_tools() -> list[dict]:
    return [{**entry, "available": False, "coming_in_phase": 3} for entry in SCHOOL_TOOL_CATALOG]


def tool_catalog() -> list[dict]:
    """Built-in tools first (usable now), then the school-data tools still to come."""
    return builtin_tool_entries() + _school_tools()


AUDIENCE_ROLES: list[dict] = [
    {"key": UserRole.parent.value, "label": "Parents"},
    {"key": UserRole.teacher.value, "label": "Teachers"},
    {"key": UserRole.administrator.value, "label": "Administrators"},
]

# Both lists stay valid on submit: an agent saved in Phase 1 with school tools ticked must
# still load. The orchestrator simply skips keys with no implementation.
VALID_TOOL_KEYS = {entry["key"] for entry in builtin_tool_entries()} | {
    tool["key"] for tool in SCHOOL_TOOL_CATALOG
}


def model_catalog() -> dict:
    """Models actually pulled on this machine (FR5).

    Queried live rather than hardcoded, so pulling a new model makes it selectable in the
    wizard with no code change. Empty `available` means Ollama is not running — the wizard
    shows a warning instead of an empty dropdown.
    """
    settings = get_settings()
    available = OllamaClient().list_models()
    return {
        "available": available,
        "default": settings.default_model,
        "fallback": settings.fallback_model,
        "ollama_reachable": bool(available),
    }


def build_catalog() -> dict:
    return {
        "tools": tool_catalog(),
        "knowledge_source_types": KNOWLEDGE_SOURCE_CATALOG,
        "guardrail_types": GUARDRAIL_CATALOG,
        "audience_roles": AUDIENCE_ROLES,
        "models": model_catalog(),
    }
