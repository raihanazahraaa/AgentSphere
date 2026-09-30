"""Read-only access to the Figure 3.6 school records, scoped by the caller's role.

This is where the role token stops being decoration: a parent only ever sees rows for their
own children, a teacher only their own classes, an administrator everything. Phase 2 tools
must call `visible_student_ids` rather than querying these tables directly, so an agent can
never return data the signed-in user could not have fetched themselves.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import CurrentUser, DbSession
from ..models import (
    Attendance,
    AppUser,
    TeacherClass,
    Fee,
    Homework,
    Result,
    Schedule,
    SchoolClass,
    Student,
    UserRole,
)
from ..schemas import (
    AttendanceOut,
    ClassOut,
    FeeOut,
    HomeworkOut,
    ResultOut,
    ScheduleOut,
    StudentOut,
)

router = APIRouter(prefix="/api/school", tags=["school"])


def visible_class_ids(user: AppUser, db: Session) -> list[int] | None:
    """Class ids this user may read. None means "no restriction" (administrator)."""
    if user.role == UserRole.administrator:
        return None
    if user.role == UserRole.teacher:
        # Every class they teach, from teacher_class — not just the one they tutor.
        # `class.teacher_id` is the form tutor only, and using it here meant this path
        # and the knowledge-graph path (which traverses agso:teaches) disagreed about
        # what the same teacher could see.
        taught = set(
            db.scalars(
                select(TeacherClass.class_id).where(TeacherClass.teacher_id == user.id)
            ).all()
        )
        taught.update(
            db.scalars(select(SchoolClass.id).where(SchoolClass.teacher_id == user.id)).all()
        )
        return sorted(taught)
    # Parent: the classes their children are in.
    return list(
        db.scalars(
            select(Student.class_id).where(Student.guardian_id == user.id, Student.class_id.is_not(None))
        ).all()
    )


def visible_student_ids(user: AppUser, db: Session) -> list[int] | None:
    """Student ids this user may read. None means "no restriction" (administrator)."""
    if user.role == UserRole.administrator:
        return None
    if user.role == UserRole.teacher:
        class_ids = visible_class_ids(user, db)
        if not class_ids:
            return []
        return list(db.scalars(select(Student.id).where(Student.class_id.in_(class_ids))).all())
    return list(db.scalars(select(Student.id).where(Student.guardian_id == user.id)).all())


def _scoped_student_ids(user: AppUser, db: Session, student_id: int | None) -> list[int] | None:
    """Resolve an optional ?student_id filter against what the user may see."""
    allowed = visible_student_ids(user, db)
    if student_id is None:
        return allowed
    if allowed is not None and student_id not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted to view this student")
    return [student_id]


@router.get("/students", response_model=list[StudentOut])
def list_students(user: CurrentUser, db: DbSession) -> list[StudentOut]:
    allowed = visible_student_ids(user, db)
    query = select(Student).order_by(Student.full_name)
    if allowed is not None:
        if not allowed:
            return []
        query = query.where(Student.id.in_(allowed))
    return [StudentOut.model_validate(s) for s in db.scalars(query).all()]


@router.get("/classes", response_model=list[ClassOut])
def list_classes(user: CurrentUser, db: DbSession) -> list[ClassOut]:
    allowed = visible_class_ids(user, db)
    query = select(SchoolClass).order_by(SchoolClass.name)
    if allowed is not None:
        if not allowed:
            return []
        query = query.where(SchoolClass.id.in_(allowed))
    return [ClassOut.model_validate(c) for c in db.scalars(query).all()]


@router.get("/fees", response_model=list[FeeOut])
def list_fees(
    user: CurrentUser, db: DbSession, student_id: int | None = Query(default=None)
) -> list[FeeOut]:
    if user.role == UserRole.teacher:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Fees are visible to parents and administrators")
    allowed = _scoped_student_ids(user, db, student_id)
    query = select(Fee).order_by(Fee.due_date)
    if allowed is not None:
        if not allowed:
            return []
        query = query.where(Fee.student_id.in_(allowed))
    return [FeeOut.model_validate(f) for f in db.scalars(query).all()]


@router.get("/results", response_model=list[ResultOut])
def list_results(
    user: CurrentUser, db: DbSession, student_id: int | None = Query(default=None)
) -> list[ResultOut]:
    allowed = _scoped_student_ids(user, db, student_id)
    query = select(Result).order_by(Result.subject)
    if allowed is not None:
        if not allowed:
            return []
        query = query.where(Result.student_id.in_(allowed))
    return [ResultOut.model_validate(r) for r in db.scalars(query).all()]


@router.get("/attendance", response_model=list[AttendanceOut])
def list_attendance(
    user: CurrentUser, db: DbSession, student_id: int | None = Query(default=None)
) -> list[AttendanceOut]:
    allowed = _scoped_student_ids(user, db, student_id)
    query = select(Attendance).order_by(Attendance.attendance_date.desc())
    if allowed is not None:
        if not allowed:
            return []
        query = query.where(Attendance.student_id.in_(allowed))
    return [AttendanceOut.model_validate(a) for a in db.scalars(query).all()]


@router.get("/homework", response_model=list[HomeworkOut])
def list_homework(
    user: CurrentUser, db: DbSession, class_id: int | None = Query(default=None)
) -> list[HomeworkOut]:
    allowed = visible_class_ids(user, db)
    if class_id is not None:
        if allowed is not None and class_id not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted to view this class")
        allowed = [class_id]
    query = select(Homework).order_by(Homework.due_date)
    if allowed is not None:
        if not allowed:
            return []
        query = query.where(Homework.class_id.in_(allowed))
    return [HomeworkOut.model_validate(h) for h in db.scalars(query).all()]


@router.get("/schedule", response_model=list[ScheduleOut])
def list_schedule(
    user: CurrentUser, db: DbSession, class_id: int | None = Query(default=None)
) -> list[ScheduleOut]:
    allowed = visible_class_ids(user, db)
    if class_id is not None:
        if allowed is not None and class_id not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted to view this class")
        allowed = [class_id]
    query = select(Schedule).order_by(Schedule.day_of_week, Schedule.start_time)
    if allowed is not None:
        if not allowed:
            return []
        query = query.where(Schedule.class_id.in_(allowed))
    return [ScheduleOut.model_validate(s) for s in db.scalars(query).all()]
