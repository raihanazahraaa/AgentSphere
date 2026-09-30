"""Administrator review of access decisions (FR12).

Refusals are the point: an administrator needs to see who asked for data they could not
have, and what they typed when they asked. Allowed requests are listed too, so a refusal
rate is meaningful rather than a number without a denominator.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from ..deps import AdminUser, DbSession
from ..models import AccessAudit, AppUser

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditEntry(BaseModel):
    id: int
    user_id: int | None
    user_email: str | None
    role: str
    template: str
    question: str | None
    parameters: dict
    allowed: bool
    refusal_reason: str | None
    requested_student: str | None
    result_count: int
    created_at: datetime


class AuditSummary(BaseModel):
    total: int
    refused: int
    allowed: int
    refusal_rate: float
    by_reason: dict[str, int]
    by_role: dict[str, int]


@router.get("", response_model=list[AuditEntry])
def list_audit(
    admin: AdminUser,
    db: DbSession,
    refused_only: bool = Query(default=False, description="Only refused requests"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditEntry]:
    query = (
        select(AccessAudit, AppUser.email)
        .join(AppUser, AppUser.id == AccessAudit.user_id, isouter=True)
        .order_by(AccessAudit.id.desc())
        .limit(limit)
    )
    if refused_only:
        query = query.where(AccessAudit.allowed.is_(False))

    return [
        AuditEntry(
            id=row.id, user_id=row.user_id, user_email=email, role=row.role,
            template=row.template, question=row.question, parameters=row.parameters,
            allowed=row.allowed, refusal_reason=row.refusal_reason,
            requested_student=row.requested_student, result_count=row.result_count,
            created_at=row.created_at,
        )
        for row, email in db.execute(query).all()
    ]


@router.get("/summary", response_model=AuditSummary)
def audit_summary(admin: AdminUser, db: DbSession) -> AuditSummary:
    total = db.scalar(select(func.count()).select_from(AccessAudit)) or 0
    refused = db.scalar(
        select(func.count()).select_from(AccessAudit).where(AccessAudit.allowed.is_(False))
    ) or 0

    by_reason = {
        reason: count
        for reason, count in db.execute(
            select(AccessAudit.refusal_reason, func.count())
            .where(AccessAudit.allowed.is_(False))
            .group_by(AccessAudit.refusal_reason)
        ).all()
        if reason
    }
    by_role = {
        role: count
        for role, count in db.execute(
            select(AccessAudit.role, func.count())
            .where(AccessAudit.allowed.is_(False))
            .group_by(AccessAudit.role)
        ).all()
    }

    return AuditSummary(
        total=total,
        refused=refused,
        allowed=total - refused,
        refusal_rate=round(refused / total, 4) if total else 0.0,
        by_reason=by_reason,
        by_role=by_role,
    )
