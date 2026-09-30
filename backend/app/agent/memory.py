"""Memory (FR8).

Two kinds, deliberately separate:

* **Short term** — the last N turns of this conversation, read from `chat_message`. Bounded
  because context is expensive: a 4B model given twenty turns of history spends its budget
  re-reading the transcript instead of answering.
* **Long term** — standing preferences per *role*, in `role_preference`. These are how a
  school says "answers to parents should avoid jargon" once, rather than every agent's
  instructions repeating it. They are injected into the system prompt.

Long-term memory is keyed by role rather than by user on purpose: it is school policy, not
personal data, so nothing here needs consent handling or per-user retention rules.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChatMessage, MessageSender, RolePreference, UserRole


def conversation_buffer(
    db: Session, session_id: int, max_turns: int
) -> list[tuple[str, str]]:
    """Last `max_turns` user+agent exchanges, oldest first, as (sender, text) pairs.

    System messages are excluded: they are bookkeeping, not conversation.
    """
    if max_turns <= 0:
        return []

    # One "turn" is a user message plus its reply, so take twice as many rows.
    rows = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.sender != MessageSender.system,
        )
        .order_by(ChatMessage.id.desc())
        .limit(max_turns * 2)
    ).all()

    return [(m.sender.value, m.content) for m in reversed(rows)]


def role_preferences(db: Session, role: UserRole | str) -> list[str]:
    """Active standing preferences for this role, highest priority first."""
    role_value = role.value if isinstance(role, UserRole) else str(role)
    rows = db.scalars(
        select(RolePreference)
        .where(RolePreference.role == role_value, RolePreference.is_active.is_(True))
        .order_by(RolePreference.priority.desc(), RolePreference.id)
    ).all()
    return [row.preference for row in rows]


def set_role_preference(
    db: Session,
    *,
    role: UserRole | str,
    key: str,
    preference: str,
    priority: int = 0,
) -> RolePreference:
    """Create or update one preference. `key` makes the write idempotent."""
    role_value = role.value if isinstance(role, UserRole) else str(role)
    existing = db.scalar(
        select(RolePreference).where(
            RolePreference.role == role_value, RolePreference.pref_key == key
        )
    )
    if existing is None:
        existing = RolePreference(role=role_value, pref_key=key)
        db.add(existing)

    existing.preference = preference
    existing.priority = priority
    existing.is_active = True
    db.flush()
    return existing
