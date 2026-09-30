"""Long-term memory administration (FR8).

Standing preferences per role are school policy, so only administrators may change them.
Without this router they could only be set by re-seeding, which would make the feature
dead config rather than something a school can actually use.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..agent.memory import role_preferences, set_role_preference
from ..deps import AdminUser, CurrentUser, DbSession
from ..models import RolePreference, UserRole

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


class PreferenceIn(BaseModel):
    role: UserRole
    key: str = Field(min_length=1, max_length=100)
    preference: str = Field(min_length=1)
    priority: int = 0


class PreferenceOut(BaseModel):
    id: int
    role: str
    pref_key: str
    preference: str
    priority: int
    is_active: bool


@router.get("/me", response_model=list[str])
def my_preferences(user: CurrentUser, db: DbSession) -> list[str]:
    """What the agent will apply for the signed-in user's role."""
    return role_preferences(db, user.role)


@router.get("", response_model=list[PreferenceOut])
def list_preferences(admin: AdminUser, db: DbSession) -> list[PreferenceOut]:
    rows = db.scalars(
        select(RolePreference).order_by(RolePreference.role, RolePreference.priority.desc())
    ).all()
    return [PreferenceOut.model_validate(row, from_attributes=True) for row in rows]


@router.put("", response_model=PreferenceOut, status_code=status.HTTP_200_OK)
def upsert_preference(payload: PreferenceIn, admin: AdminUser, db: DbSession) -> PreferenceOut:
    row = set_role_preference(
        db,
        role=payload.role,
        key=payload.key,
        preference=payload.preference,
        priority=payload.priority,
    )
    db.commit()
    db.refresh(row)
    return PreferenceOut.model_validate(row, from_attributes=True)


@router.delete("/{preference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preference(preference_id: int, admin: AdminUser, db: DbSession) -> None:
    row = db.get(RolePreference, preference_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preference not found")
    db.delete(row)
    db.commit()
