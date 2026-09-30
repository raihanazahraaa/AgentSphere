"""Synthetic school dataset generator (Table 3.4).

    .venv/bin/python generate_data.py             # generate, check, load
    .venv/bin/python generate_data.py --dry-run   # generate and check, load nothing
    .venv/bin/python generate_data.py --report    # row counts of what is in the database

Reproducible: every random draw comes from one seeded generator, so the same dataset is
produced on every machine and any figure quoted in the report can be regenerated exactly.

Table 3.4 targets, and how each is met:

| Target                          | How                                                |
|---------------------------------|----------------------------------------------------|
| 300 students, 12 classes (F1-5) | 25 per class, forms 1A..5B                          |
| 600 fee invoices over 2 terms   | one per student per term                            |
| ~8,000 marks (8 subjects x 2)   | 8 subjects x 2 assessments x 2 terms, minus the     |
|                                 | ~17% not sat -- see MISSING_MARK_RATE below         |
| ~30,000 attendance entries      | 300 students x 100 school days = exactly 30,000     |
| 1,200 homework tasks            | 100 per class                                       |
| 60 announcements                | mixed school-wide and class-wide                    |
| 12 class timetables             | one per class, 5 days x 6 periods = 360 entries     |

**On the marks figure.** The brief gives both a total (~8,000) and a formula
(8 subjects x 2 assessments). Those do not agree: 300 students x 8 x 2 is 4,800 for one
term, and 9,600 across the two terms the fee target establishes. A full 9,600-cell grid is
also unrealistic -- pupils miss assessments. So the generator fills the full grid and then
drops MISSING_MARK_RATE of the cells, landing on the stated ~8,000 while keeping the stated
structure. Set MISSING_MARK_RATE = 0.0 for the complete 9,600-mark grid.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from pathlib import Path

from faker import Faker
from sqlalchemy import delete, func, select

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Announcement,
    AppUser,
    Attendance,
    AttendanceStatus,
    Fee,
    FeeStatus,
    Homework,
    Result,
    Schedule,
    SchoolClass,
    Student,
    TeacherClass,
    UserRole,
)
from app.security import hash_password  # noqa: E402

# --------------------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------------------

SEED = 20260401

STUDENTS_PER_CLASS = 25
CLASS_NAMES = [
    "1A", "1B", "1C",
    "2A", "2B",
    "3A", "3B", "3C",
    "4A", "4B",
    "5A", "5B",
]  # 12 classes across Forms 1-5

SUBJECTS = [
    "Mathematics", "English", "Science", "History",
    "Geography", "Art", "Physical Education", "Computing",
]
ASSESSMENTS = ["Mid-term", "End-of-term"]
TERMS = ["Term 1", "Term 2"]

SCHOOL_DAYS = 100
# Overall attendance must land in 92-97%. Per-student baselines are drawn around this and
# then modulated per day, so the rate is not uniform random -- see _generate_attendance.
ATTENDANCE_MEAN = 0.945
ATTENDANCE_SPREAD = 0.025

MISSING_MARK_RATE = 0.17   # see the module docstring
HOMEWORK_PER_CLASS = 100
ANNOUNCEMENT_COUNT = 60
PERIODS_PER_DAY = 6
TEACHING_DAYS = 5

TERM_START = date(2025, 9, 2)
DEMO_PASSWORD = "password123"

# Test accounts. Parents are linked to one student each; teachers to the classes they take.
PARENT_COUNT = 6
TEACHER_COUNT = 15

# Created by seed.py, preserved here and joined into the generated dataset so the
# documented demo logins keep working after a regeneration.
SEED_ADMIN = "admin@greenfield.edu"
SEED_TEACHER = "teacher@greenfield.edu"
SEED_PARENT = "parent@greenfield.edu"
SEED_ACCOUNTS = (SEED_ADMIN, SEED_TEACHER, SEED_PARENT)


@dataclass
class Dataset:
    """Everything generated, held in memory so consistency can be checked before loading."""

    classes: list[dict] = field(default_factory=list)
    students: list[dict] = field(default_factory=list)
    teachers: list[dict] = field(default_factory=list)
    parents: list[dict] = field(default_factory=list)
    admin: dict | None = None
    teacher_classes: list[dict] = field(default_factory=list)
    fees: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    attendance: list[dict] = field(default_factory=list)
    homework: list[dict] = field(default_factory=list)
    announcements: list[dict] = field(default_factory=list)
    schedule: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------------------


def generate() -> Dataset:
    rng = random.Random(SEED)
    fake = Faker("en_GB")
    Faker.seed(SEED)

    data = Dataset()

    # --- classes ----------------------------------------------------------------------
    for index, name in enumerate(CLASS_NAMES, start=1):
        data.classes.append(
            {
                "local_id": index,
                "name": f"Form {name}",
                "grade_level": f"Form {name[0]}",
                "academic_year": "2025/2026",
                "room": f"{name[0]}-{rng.randint(1, 20):02d}",
            }
        )

    # --- staff ------------------------------------------------------------------------
    for index in range(1, TEACHER_COUNT + 1):
        name = fake.name()
        data.teachers.append(
            {
                "local_id": index,
                "full_name": name,
                "email": f"teacher{index}@greenfield.edu",
            }
        )

    data.admin = {"full_name": fake.name(), "email": "head@greenfield.edu"}

    # Each class gets a form tutor; each teacher additionally takes 2-3 other classes, so
    # `teaches` is genuinely many-to-many and teacher scope is not just "my form group".
    for class_index, school_class in enumerate(data.classes):
        tutor = data.teachers[class_index % TEACHER_COUNT]
        school_class["tutor_local_id"] = tutor["local_id"]
        data.teacher_classes.append(
            {
                "teacher_local_id": tutor["local_id"],
                "class_local_id": school_class["local_id"],
                "subject": None,  # form tutor, not subject-specific
            }
        )

    for teacher in data.teachers:
        taken = {
            tc["class_local_id"]
            for tc in data.teacher_classes
            if tc["teacher_local_id"] == teacher["local_id"]
        }
        extra_classes = rng.sample(
            [c["local_id"] for c in data.classes if c["local_id"] not in taken],
            k=rng.randint(2, 3),
        )
        subject = SUBJECTS[teacher["local_id"] % len(SUBJECTS)]
        for class_local_id in extra_classes:
            data.teacher_classes.append(
                {
                    "teacher_local_id": teacher["local_id"],
                    "class_local_id": class_local_id,
                    "subject": subject,
                }
            )

    # --- students ---------------------------------------------------------------------
    student_id = 0
    for school_class in data.classes:
        form_number = int(school_class["name"].split()[1][0])
        for _ in range(STUDENTS_PER_CLASS):
            student_id += 1
            # Form 1 pupils are ~11, rising a year per form.
            age = 10 + form_number
            born = date(2026 - age, rng.randint(1, 12), rng.randint(1, 28))
            data.students.append(
                {
                    "local_id": student_id,
                    "student_no": f"S-2026-{student_id:04d}",
                    "full_name": fake.name(),
                    "date_of_birth": born,
                    "class_local_id": school_class["local_id"],
                    "enrolled_on": date(2026 - (form_number - 1), 9, 1),
                }
            )

    # --- parents ----------------------------------------------------------------------
    # Spread across different classes so the teacher-scope tests have a student who is
    # inside one teacher's classes and outside another's.
    parent_students = rng.sample(data.students, k=PARENT_COUNT)
    for index, student in enumerate(parent_students, start=1):
        data.parents.append(
            {
                "local_id": index,
                "full_name": fake.name(),
                "email": f"parent{index}@greenfield.edu",
                "student_local_id": student["local_id"],
            }
        )

    # --- fees: one invoice per student per term ---------------------------------------
    for student in data.students:
        for term_index, term in enumerate(TERMS):
            amount = rng.choice([450.00, 480.00, 520.00, 560.00])
            due = TERM_START + timedelta(days=30 + term_index * 120)
            roll = rng.random()
            if roll < 0.62:
                status, paid = FeeStatus.paid, amount
            elif roll < 0.80:
                status, paid = FeeStatus.partial, round(amount * rng.uniform(0.2, 0.8), 2)
            elif roll < 0.96:
                status, paid = FeeStatus.unpaid, 0.0
            else:
                status, paid = FeeStatus.waived, 0.0

            data.fees.append(
                {
                    "student_local_id": student["local_id"],
                    "description": f"{term} tuition",
                    "amount": amount,
                    "amount_paid": paid,
                    "due_date": due,
                    "paid_date": (due - timedelta(days=rng.randint(0, 20)))
                    if status == FeeStatus.paid
                    else None,
                    "status": status,
                    "term": term,
                }
            )

    # --- results ----------------------------------------------------------------------
    for student in data.students:
        # A per-student ability, so a pupil's marks correlate across subjects rather than
        # each mark being an independent coin flip.
        ability = rng.gauss(62, 12)
        for term in TERMS:
            for subject in SUBJECTS:
                subject_bias = rng.gauss(0, 6)
                for assessment in ASSESSMENTS:
                    if rng.random() < MISSING_MARK_RATE:
                        continue  # not sat
                    score = ability + subject_bias + rng.gauss(0, 5)
                    score = max(8.0, min(100.0, score))
                    data.results.append(
                        {
                            "student_local_id": student["local_id"],
                            "subject": subject,
                            "term": term,
                            "assessment": assessment,
                            "score": round(score, 1),
                            "max_score": 100.0,
                            "grade": _grade(score),
                        }
                    )

    # --- attendance -------------------------------------------------------------------
    data.attendance = _generate_attendance(rng, data.students)

    # --- homework ---------------------------------------------------------------------
    for school_class in data.classes:
        teachers_for_class = [
            tc["teacher_local_id"]
            for tc in data.teacher_classes
            if tc["class_local_id"] == school_class["local_id"]
        ]
        for n in range(HOMEWORK_PER_CLASS):
            subject = rng.choice(SUBJECTS)
            assigned = TERM_START + timedelta(days=rng.randint(0, 180))
            data.homework.append(
                {
                    "class_local_id": school_class["local_id"],
                    "teacher_local_id": rng.choice(teachers_for_class),
                    "subject": subject,
                    "title": f"{subject}: {fake.sentence(nb_words=4).rstrip('.')}",
                    "description": fake.paragraph(nb_sentences=2),
                    "assigned_date": assigned,
                    "due_date": assigned + timedelta(days=rng.randint(2, 10)),
                }
            )

    # --- announcements ----------------------------------------------------------------
    for n in range(ANNOUNCEMENT_COUNT):
        class_wide = rng.random() < 0.45
        school_class = rng.choice(data.classes) if class_wide else None
        data.announcements.append(
            {
                "title": fake.sentence(nb_words=6).rstrip("."),
                "body": fake.paragraph(nb_sentences=3),
                "class_local_id": school_class["local_id"] if school_class else None,
                "audience": rng.choice(["all", "parents", "teachers", "students"]),
                "published_on": TERM_START + timedelta(days=rng.randint(0, 180)),
            }
        )

    # --- timetables: one per class ----------------------------------------------------
    for school_class in data.classes:
        teachers_for_class = [
            tc["teacher_local_id"]
            for tc in data.teacher_classes
            if tc["class_local_id"] == school_class["local_id"]
        ]
        subject_cycle = SUBJECTS * 5
        rng.shuffle(subject_cycle)
        slot = 0
        for day in range(1, TEACHING_DAYS + 1):
            for period in range(PERIODS_PER_DAY):
                start_hour = 9 + period + (1 if period >= 3 else 0)  # lunch after period 3
                data.schedule.append(
                    {
                        "class_local_id": school_class["local_id"],
                        "teacher_local_id": rng.choice(teachers_for_class),
                        "subject": subject_cycle[slot % len(subject_cycle)],
                        "day_of_week": day,
                        "start_time": time(start_hour, 0),
                        "end_time": time(start_hour, 50),
                        "room": school_class["room"],
                    }
                )
                slot += 1

    return data


def _generate_attendance(rng: random.Random, students: list[dict]) -> list[dict]:
    """One mark per student per school day, deliberately not uniform random.

    Three effects stack, so the marks cluster the way real registers do:
      * a per-student baseline (some pupils attend far better than others)
      * a per-day school effect (bad weather, a bug going round)
      * a Monday/Friday dip
    A uniform draw would give every pupil the same expected rate and no bad weeks, which
    would make any downstream analysis of the dataset meaningless.
    """
    first_monday = TERM_START
    school_dates: list[date] = []
    cursor = first_monday
    while len(school_dates) < SCHOOL_DAYS:
        if cursor.weekday() < 5:  # Mon-Fri
            school_dates.append(cursor)
        cursor += timedelta(days=1)

    student_baseline = {
        s["local_id"]: min(0.995, max(0.86, rng.gauss(ATTENDANCE_MEAN, ATTENDANCE_SPREAD)))
        for s in students
    }

    # A handful of bad days across the year (illness going round, snow).
    day_effect: dict[date, float] = {}
    for day in school_dates:
        effect = 1.0
        if day.weekday() == 0:      # Monday
            effect -= 0.012
        elif day.weekday() == 4:    # Friday
            effect -= 0.018
        if rng.random() < 0.06:     # a bad day for everyone
            effect -= rng.uniform(0.04, 0.10)
        day_effect[day] = effect

    records: list[dict] = []
    for student in students:
        baseline = student_baseline[student["local_id"]]
        for day in school_dates:
            present_probability = baseline * day_effect[day]
            if rng.random() < present_probability:
                status = AttendanceStatus.present
                remarks = None
            else:
                # Split the non-present marks the way a register does.
                roll = rng.random()
                if roll < 0.45:
                    status, remarks = AttendanceStatus.absent, rng.choice(
                        ["Unwell", "Family reasons", None]
                    )
                elif roll < 0.78:
                    status, remarks = AttendanceStatus.late, None
                else:
                    status, remarks = AttendanceStatus.excused, rng.choice(
                        ["Medical appointment", "Approved leave"]
                    )
            records.append(
                {
                    "student_local_id": student["local_id"],
                    "attendance_date": day,
                    "status": status,
                    "remarks": remarks,
                }
            )
    return records


def _grade(score: float) -> str:
    for threshold, grade in ((80, "A"), (70, "B"), (60, "C"), (50, "D"), (40, "E")):
        if score >= threshold:
            return grade
    return "F"


# --------------------------------------------------------------------------------------
# Consistency checks
# --------------------------------------------------------------------------------------


def check(data: Dataset) -> list[str]:
    """Referential and distributional checks, run before anything touches the database.

    Catching a dangling student reference here is far cheaper than catching it as a foreign
    key error halfway through a 30,000-row load, and the attendance-rate check is the one
    that would otherwise silently drift out of the 92-97% band the brief specifies.
    """
    problems: list[str] = []
    student_ids = {s["local_id"] for s in data.students}
    class_ids = {c["local_id"] for c in data.classes}
    teacher_ids = {t["local_id"] for t in data.teachers}

    def every_link(rows: list[dict], key: str, valid: set, label: str) -> None:
        dangling = {row[key] for row in rows if row.get(key) is not None} - valid
        if dangling:
            problems.append(f"{label}: {len(dangling)} reference(s) to missing rows: "
                            f"{sorted(dangling)[:5]}")

    every_link(data.fees, "student_local_id", student_ids, "fees -> student")
    every_link(data.results, "student_local_id", student_ids, "results -> student")
    every_link(data.attendance, "student_local_id", student_ids, "attendance -> student")
    every_link(data.students, "class_local_id", class_ids, "students -> class")
    every_link(data.homework, "class_local_id", class_ids, "homework -> class")
    every_link(data.homework, "teacher_local_id", teacher_ids, "homework -> teacher")
    every_link(data.schedule, "class_local_id", class_ids, "schedule -> class")
    every_link(data.schedule, "teacher_local_id", teacher_ids, "schedule -> teacher")
    every_link(data.announcements, "class_local_id", class_ids, "announcements -> class")
    every_link(data.teacher_classes, "teacher_local_id", teacher_ids, "teaches -> teacher")
    every_link(data.teacher_classes, "class_local_id", class_ids, "teaches -> class")
    every_link(data.parents, "student_local_id", student_ids, "parents -> student")

    # Every student must sit in exactly one class, or role scoping has a hole.
    unplaced = [s["local_id"] for s in data.students if s["class_local_id"] is None]
    if unplaced:
        problems.append(f"{len(unplaced)} student(s) are not in a class")

    # Every student must have fee, mark and attendance rows, or the fixtures are uneven.
    for label, rows in (
        ("fee", data.fees), ("attendance", data.attendance), ("result", data.results)
    ):
        covered = {row["student_local_id"] for row in rows}
        missing = student_ids - covered
        if missing:
            problems.append(f"{len(missing)} student(s) have no {label} rows")

    # No duplicate attendance marks for a student on a day (the table has a unique key).
    seen = Counter((r["student_local_id"], r["attendance_date"]) for r in data.attendance)
    duplicates = [key for key, count in seen.items() if count > 1]
    if duplicates:
        problems.append(f"{len(duplicates)} duplicate attendance entries")

    # Attendance rate must land inside the 92-97% band the brief specifies.
    present_like = sum(
        1 for r in data.attendance
        if r["status"] in (AttendanceStatus.present, AttendanceStatus.late)
    )
    rate = present_like / len(data.attendance) if data.attendance else 0
    if not 0.92 <= rate <= 0.97:
        problems.append(f"attendance rate {rate:.1%} is outside the required 92-97%")

    # Fee arithmetic must be coherent.
    bad_fees = [
        f for f in data.fees
        if f["amount_paid"] > f["amount"]
        or (f["status"] == FeeStatus.paid and f["amount_paid"] < f["amount"])
    ]
    if bad_fees:
        problems.append(f"{len(bad_fees)} fee row(s) have inconsistent amounts")

    # Marks must be within range.
    bad_marks = [r for r in data.results if not 0 <= r["score"] <= r["max_score"]]
    if bad_marks:
        problems.append(f"{len(bad_marks)} mark(s) outside 0..max_score")

    return problems


def summarise(data: Dataset) -> None:
    present_like = sum(
        1 for r in data.attendance
        if r["status"] in (AttendanceStatus.present, AttendanceStatus.late)
    )
    rate = present_like / len(data.attendance) if data.attendance else 0
    per_student = Counter(r["student_local_id"] for r in data.attendance)

    print(f"  classes          {len(data.classes):>7,}   (target 12)")
    print(f"  students         {len(data.students):>7,}   (target 300)")
    print(f"  teachers         {len(data.teachers):>7,}")
    print(f"  teaches edges    {len(data.teacher_classes):>7,}")
    print(f"  parents          {len(data.parents):>7,}")
    print(f"  fee invoices     {len(data.fees):>7,}   (target 600)")
    print(f"  marks            {len(data.results):>7,}   (target ~8,000)")
    print(f"  attendance       {len(data.attendance):>7,}   (target ~30,000)")
    print(f"  homework         {len(data.homework):>7,}   (target 1,200)")
    print(f"  announcements    {len(data.announcements):>7,}   (target 60)")
    print(f"  timetable rows   {len(data.schedule):>7,}   (12 classes x 30 periods)")
    print(f"  attendance rate  {rate:>7.1%}   (required 92-97%)")
    print(f"  days per student {min(per_student.values()) if per_student else 0:>7,}"
          f"   (target {SCHOOL_DAYS})")


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------

# Generated rows only; the demo accounts and agent config from seed.py are left alone.
GENERATED_TABLES = [
    Attendance, Result, Fee, Homework, Schedule, Announcement,
    TeacherClass, Student, SchoolClass,
]


def load(data: Dataset) -> None:
    """Replace any previously generated rows, then insert this dataset.

    Local ids are mapped to real primary keys after each insert, because MySQL and SQLite
    assign them and the in-memory dataset only knows its own numbering.
    """
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        print("Clearing previously generated rows...")
        # Children first: these run as bulk DELETEs, which do not cascade in SQLite.
        for model in GENERATED_TABLES:
            db.execute(delete(model))
        # Delete only accounts this generator created. The three seed.py demo logins
        # (admin@ / teacher@ / parent@) are documented in README and CLAUDE.md and used by
        # smoke_test.py; wiping them would silently break all of those.
        db.execute(
            delete(AppUser).where(
                AppUser.email.notin_(SEED_ACCOUNTS),
                AppUser.email.like("%@greenfield.edu"),
            )
        )
        db.commit()

        print("Inserting...")
        # --- classes ------------------------------------------------------------------
        class_rows = [
            SchoolClass(
                name=c["name"], grade_level=c["grade_level"],
                academic_year=c["academic_year"], room=c["room"],
            )
            for c in data.classes
        ]
        db.add_all(class_rows)
        db.flush()
        class_pk = {c["local_id"]: row.id for c, row in zip(data.classes, class_rows)}

        # --- staff and parents --------------------------------------------------------
        teacher_rows = [
            AppUser(
                email=t["email"], password_hash=hash_password(DEMO_PASSWORD),
                full_name=t["full_name"], role=UserRole.teacher,
            )
            for t in data.teachers
        ]
        admin_row = AppUser(
            email=data.admin["email"], password_hash=hash_password(DEMO_PASSWORD),
            full_name=data.admin["full_name"], role=UserRole.administrator,
        )
        parent_rows = [
            AppUser(
                email=p["email"], password_hash=hash_password(DEMO_PASSWORD),
                full_name=p["full_name"], role=UserRole.parent,
            )
            for p in data.parents
        ]
        db.add_all(teacher_rows + [admin_row] + parent_rows)
        db.flush()
        teacher_pk = {t["local_id"]: row.id for t, row in zip(data.teachers, teacher_rows)}
        parent_pk = {p["local_id"]: row.id for p, row in zip(data.parents, parent_rows)}

        # Form tutor on each class.
        for c, row in zip(data.classes, class_rows):
            row.teacher_id = teacher_pk[c["tutor_local_id"]]

        # --- students -----------------------------------------------------------------
        guardian_for = {p["student_local_id"]: parent_pk[p["local_id"]] for p in data.parents}
        student_rows = [
            Student(
                student_no=s["student_no"], full_name=s["full_name"],
                date_of_birth=s["date_of_birth"], class_id=class_pk[s["class_local_id"]],
                guardian_id=guardian_for.get(s["local_id"]), enrolled_on=s["enrolled_on"],
            )
            for s in data.students
        ]
        db.add_all(student_rows)
        db.flush()
        student_pk = {s["local_id"]: row.id for s, row in zip(data.students, student_rows)}
        db.commit()

        # --- bulk children ------------------------------------------------------------
        _bulk(db, TeacherClass, [
            {"teacher_id": teacher_pk[tc["teacher_local_id"]],
             "class_id": class_pk[tc["class_local_id"]], "subject": tc["subject"]}
            for tc in data.teacher_classes
        ], "teaches edges")

        _bulk(db, Fee, [
            {"student_id": student_pk[f["student_local_id"]], "description": f["description"],
             "amount": f["amount"], "amount_paid": f["amount_paid"], "due_date": f["due_date"],
             "paid_date": f["paid_date"], "status": f["status"]}
            for f in data.fees
        ], "fees")

        _bulk(db, Result, [
            {"student_id": student_pk[r["student_local_id"]], "subject": r["subject"],
             "term": r["term"], "assessment": r["assessment"], "score": r["score"],
             "max_score": r["max_score"], "grade": r["grade"]}
            for r in data.results
        ], "marks")

        _bulk(db, Attendance, [
            {"student_id": student_pk[a["student_local_id"]],
             "attendance_date": a["attendance_date"], "status": a["status"],
             "remarks": a["remarks"]}
            for a in data.attendance
        ], "attendance")

        _bulk(db, Homework, [
            {"class_id": class_pk[h["class_local_id"]],
             "teacher_id": teacher_pk[h["teacher_local_id"]], "subject": h["subject"],
             "title": h["title"], "description": h["description"],
             "assigned_date": h["assigned_date"], "due_date": h["due_date"]}
            for h in data.homework
        ], "homework")

        _bulk(db, Schedule, [
            {"class_id": class_pk[s["class_local_id"]],
             "teacher_id": teacher_pk[s["teacher_local_id"]], "subject": s["subject"],
             "day_of_week": s["day_of_week"], "start_time": s["start_time"],
             "end_time": s["end_time"], "room": s["room"]}
            for s in data.schedule
        ], "timetable")

        _bulk(db, Announcement, [
            {"title": a["title"], "body": a["body"],
             "class_id": class_pk[a["class_local_id"]] if a["class_local_id"] else None,
             "audience": a["audience"], "published_on": a["published_on"],
             "author_id": admin_row.id}
            for a in data.announcements
        ], "announcements")

        _attach_seed_accounts(db, class_rows, student_rows)
        db.commit()
    finally:
        db.close()


def _attach_seed_accounts(db, class_rows, student_rows) -> None:
    """Join seed.py's demo logins into the generated dataset.

    Without this the documented accounts survive but see nothing: the demo parent's child
    was deleted with the old fixtures, and the demo teacher teaches no class. Both would
    then be indistinguishable from an RBAC bug when someone tries them.
    """
    seed_parent = db.scalar(select(AppUser).where(AppUser.email == SEED_PARENT))
    seed_teacher = db.scalar(select(AppUser).where(AppUser.email == SEED_TEACHER))

    if seed_parent is not None and student_rows:
        # Give the demo parent a child in a class the demo teacher does *not* take, so the
        # teacher-scope boundary is demonstrable with the documented accounts alone.
        student_rows[-1].guardian_id = seed_parent.id

    if seed_teacher is not None and class_rows:
        existing = {
            row.class_id
            for row in db.scalars(
                select(TeacherClass).where(TeacherClass.teacher_id == seed_teacher.id)
            ).all()
        }
        for school_class in class_rows[:2]:
            if school_class.id not in existing:
                db.add(
                    TeacherClass(
                        teacher_id=seed_teacher.id, class_id=school_class.id,
                        subject="Mathematics",
                    )
                )
    db.flush()


def _bulk(db, model, rows: list[dict], label: str) -> None:
    if not rows:
        return
    # Chunked so a 30,000-row insert does not build one enormous statement.
    for start in range(0, len(rows), 1000):
        db.bulk_insert_mappings(model, rows[start : start + 1000])
    db.commit()
    print(f"    {label:16} {len(rows):>7,}")


def report() -> None:
    db = SessionLocal()
    try:
        print("Rows currently in the database:")
        for model, label in (
            (SchoolClass, "classes"), (Student, "students"), (Fee, "fees"),
            (Result, "marks"), (Attendance, "attendance"), (Homework, "homework"),
            (Schedule, "timetable"), (Announcement, "announcements"),
            (TeacherClass, "teaches edges"),
        ):
            count = db.scalar(select(func.count()).select_from(model))
            print(f"  {label:16} {count:>7,}")
        for role in UserRole:
            count = db.scalar(
                select(func.count()).select_from(AppUser).where(AppUser.role == role)
            )
            print(f"  {role.value:16} {count:>7,}")
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="generate and check only")
    parser.add_argument("--report", action="store_true", help="show database row counts")
    args = parser.parse_args()

    if args.report:
        report()
        return 0

    print(f"Generating Table 3.4 dataset (seed {SEED})...\n")
    data = generate()
    summarise(data)

    print("\nConsistency checks...")
    problems = check(data)
    if problems:
        for problem in problems:
            print(f"  FAIL  {problem}")
        print(f"\n{len(problems)} problem(s) found. Nothing was loaded.")
        return 1
    print("  PASS  every fee, mark and attendance row links to a real student")
    print("  PASS  every student is in a class and has fee, mark and attendance rows")
    print("  PASS  attendance rate inside 92-97%, no duplicate day entries")
    print("  PASS  fee amounts and mark ranges coherent")

    if args.dry_run:
        print("\n--dry-run: nothing loaded.")
        return 0

    print()
    load(data)
    print("\nLoaded. Demo logins (password 'password123'):")
    print(f"  seed accounts kept: {', '.join(SEED_ACCOUNTS)}")
    print(f"  administrator  {data.admin['email']}")
    print(f"  teacher        {data.teachers[0]['email']} .. "
          f"{data.teachers[-1]['email']}")
    print(f"  parent         {data.parents[0]['email']} .. "
          f"{data.parents[-1]['email']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
