"""MySQL -> RDF conversion, following backend/ontology/agentsphere.ttl.

    .venv/bin/python build_graph.py                 # write graph/agentsphere.ttl
    .venv/bin/python build_graph.py --format nt     # N-Triples (faster to load, bigger)
    .venv/bin/python build_graph.py --upload        # write, then POST to Fuseki

Every relational row becomes a node with a stable IRI derived from its natural key, not its
autoincrement id: `student/S-2026-0001` rather than `student/17`. Reloading the database
renumbers primary keys, and IRIs that changed on every reload would break any saved query,
citation or audit record that referenced them.

The two edges access control depends on are written explicitly:

    agso:guardianOf   parent  -> student     (from student.guardian_id)
    agso:teaches      teacher -> class       (from the teacher_class table)

Both are traversed by every role-scoped SPARQL template, so if either is missing from the
graph the corresponding role silently loses all access rather than gaining any.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import httpx
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD
from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Announcement,
    AppUser,
    Attendance,
    AttendanceStatus,
    Fee,
    Homework,
    Result,
    Schedule,
    SchoolClass,
    Student,
    TeacherClass,
    UserRole,
)

AGSO = Namespace("https://agentsphere.local/ontology#")
# NB: use AGSO["name"] for any property whose name collides with a str or Namespace
# attribute -- `AGSO.term` and `AGSO.title` silently resolve to methods, not IRIs.
AGSD = Namespace("https://agentsphere.local/data/")

OUTPUT_DIR = Path(__file__).resolve().parent / "graph"
ONTOLOGY_FILE = Path(__file__).resolve().parent / "ontology" / "agentsphere.ttl"


# --------------------------------------------------------------------------------------
# IRI minting — stable, derived from natural keys
# --------------------------------------------------------------------------------------


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in str(value)).strip("-").lower()


def student_iri(student_no: str) -> URIRef:
    return AGSD[f"student/{student_no}"]


def class_iri(name: str) -> URIRef:
    return AGSD[f"class/{_slug(name)}"]


def person_iri(user: AppUser) -> URIRef:
    # Email is the natural key for an account and is already unique in app_user.
    return AGSD[f"person/{_slug(user.email)}"]


def subject_iri(name: str) -> URIRef:
    return AGSD[f"subject/{_slug(name)}"]


def fee_iri(student_no: str, index: int) -> URIRef:
    return AGSD[f"fee/{student_no}-{index}"]


def result_iri(student_no: str, index: int) -> URIRef:
    return AGSD[f"result/{student_no}-{index}"]


def attendance_iri(student_no: str, day: str) -> URIRef:
    return AGSD[f"attendance/{student_no}-{day}"]


def homework_iri(index: int) -> URIRef:
    return AGSD[f"homework/{index}"]


def schedule_iri(index: int) -> URIRef:
    return AGSD[f"schedule/{index}"]


def announcement_iri(index: int) -> URIRef:
    return AGSD[f"announcement/{index}"]


# --------------------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------------------


def build(db: Session) -> Graph:
    graph = Graph()
    graph.bind("agso", AGSO)
    graph.bind("agsd", AGSD)

    # The ontology travels with the data so Fuseki can answer questions about the schema
    # (labels, domains) without a second dataset.
    if ONTOLOGY_FILE.exists():
        graph.parse(ONTOLOGY_FILE, format="turtle")

    print("Converting...")

    # --- classes ----------------------------------------------------------------------
    classes = db.scalars(select(SchoolClass)).all()
    class_iris: dict[int, URIRef] = {}
    for school_class in classes:
        iri = class_iri(school_class.name)
        class_iris[school_class.id] = iri
        graph.add((iri, RDF.type, AGSO.Class))
        graph.add((iri, RDFS.label, Literal(school_class.name)))
        graph.add((iri, AGSO.className, Literal(school_class.name)))
        if school_class.grade_level:
            graph.add((iri, AGSO.formLevel, Literal(school_class.grade_level)))
        if school_class.academic_year:
            graph.add((iri, AGSO.academicYear, Literal(school_class.academic_year)))
        if school_class.room:
            graph.add((iri, AGSO.room, Literal(school_class.room)))
    print(f"    classes        {len(classes):>7,}")

    # --- subjects: implied by results, homework and the timetable ----------------------
    subject_names = set()
    for model in (Result, Homework, Schedule):
        subject_names.update(db.scalars(select(model.subject).distinct()).all())
    subject_names.discard(None)
    for name in sorted(subject_names):
        iri = subject_iri(name)
        graph.add((iri, RDF.type, AGSO.Subject))
        graph.add((iri, RDFS.label, Literal(name)))
        graph.add((iri, AGSO.subjectName, Literal(name)))
    print(f"    subjects       {len(subject_names):>7,}")

    # --- people -----------------------------------------------------------------------
    users = db.scalars(select(AppUser)).all()
    user_iris: dict[int, URIRef] = {}
    for user in users:
        iri = person_iri(user)
        user_iris[user.id] = iri
        rdf_type = {
            UserRole.parent: AGSO.Parent,
            UserRole.teacher: AGSO.Teacher,
            UserRole.administrator: AGSO.Person,
        }[user.role]
        graph.add((iri, RDF.type, rdf_type))
        graph.add((iri, RDFS.label, Literal(user.full_name)))
        graph.add((iri, AGSO.fullName, Literal(user.full_name)))
        graph.add((iri, AGSO.email, Literal(user.email)))
    print(f"    people         {len(users):>7,}")

    # Form tutor edge.
    for school_class in classes:
        if school_class.teacher_id and school_class.teacher_id in user_iris:
            graph.add(
                (class_iris[school_class.id], AGSO.classTeacher,
                 user_iris[school_class.teacher_id])
            )

    # --- teaches: the teacher access-control edge -------------------------------------
    teaches_rows = db.scalars(select(TeacherClass)).all()
    for row in teaches_rows:
        if row.teacher_id in user_iris and row.class_id in class_iris:
            graph.add((user_iris[row.teacher_id], AGSO.teaches, class_iris[row.class_id]))
    print(f"    teaches edges  {len(teaches_rows):>7,}")

    # --- students, and the parent access-control edge ---------------------------------
    students = db.scalars(select(Student)).all()
    student_iris: dict[int, URIRef] = {}
    student_numbers: dict[int, str] = {}
    guardian_edges = 0
    for student in students:
        iri = student_iri(student.student_no)
        student_iris[student.id] = iri
        student_numbers[student.id] = student.student_no
        graph.add((iri, RDF.type, AGSO.Student))
        graph.add((iri, RDFS.label, Literal(student.full_name)))
        graph.add((iri, AGSO.fullName, Literal(student.full_name)))
        graph.add((iri, AGSO.studentNo, Literal(student.student_no)))
        if student.date_of_birth:
            graph.add((iri, AGSO.dateOfBirth, Literal(student.date_of_birth, datatype=XSD.date)))
        if student.enrolled_on:
            graph.add((iri, AGSO.enrolledOn, Literal(student.enrolled_on, datatype=XSD.date)))
        if student.class_id in class_iris:
            graph.add((iri, AGSO.enrolledIn, class_iris[student.class_id]))
        if student.guardian_id and student.guardian_id in user_iris:
            graph.add((user_iris[student.guardian_id], AGSO.guardianOf, iri))
            guardian_edges += 1
    print(f"    students       {len(students):>7,}")
    print(f"    guardianOf     {guardian_edges:>7,}")

    # --- fees -------------------------------------------------------------------------
    fees = db.scalars(select(Fee).order_by(Fee.id)).all()
    per_student: dict[int, int] = {}
    for fee in fees:
        if fee.student_id not in student_iris:
            continue
        number = student_numbers[fee.student_id]
        index = per_student[fee.student_id] = per_student.get(fee.student_id, 0) + 1
        iri = fee_iri(number, index)
        graph.add((iri, RDF.type, AGSO.Fee))
        graph.add((student_iris[fee.student_id], AGSO.hasFee, iri))
        graph.add((iri, AGSO.feeDescription, Literal(fee.description)))
        graph.add((iri, AGSO.amount, Literal(fee.amount, datatype=XSD.decimal)))
        graph.add((iri, AGSO.amountPaid, Literal(fee.amount_paid, datatype=XSD.decimal)))
        graph.add((iri, AGSO.feeStatus, Literal(fee.status.value)))
        # "Term 1 tuition" -> "Term 1", so templates can filter by term.
        graph.add((iri, AGSO["term"], Literal(fee.description.split(" tuition")[0])))
        if fee.due_date:
            graph.add((iri, AGSO.dueDate, Literal(fee.due_date, datatype=XSD.date)))
        if fee.paid_date:
            graph.add((iri, AGSO.paidDate, Literal(fee.paid_date, datatype=XSD.date)))
    print(f"    fees           {len(fees):>7,}")

    # --- results ----------------------------------------------------------------------
    results = db.scalars(select(Result).order_by(Result.id)).all()
    per_student = {}
    for result in results:
        if result.student_id not in student_iris:
            continue
        number = student_numbers[result.student_id]
        index = per_student[result.student_id] = per_student.get(result.student_id, 0) + 1
        iri = result_iri(number, index)
        graph.add((iri, RDF.type, AGSO.Result))
        graph.add((student_iris[result.student_id], AGSO.hasResult, iri))
        graph.add((iri, AGSO.forSubject, subject_iri(result.subject)))
        if result.term:
            graph.add((iri, AGSO["term"], Literal(result.term)))
        if result.assessment:
            graph.add((iri, AGSO.assessment, Literal(result.assessment)))
        if result.score is not None:
            graph.add((iri, AGSO.score, Literal(result.score, datatype=XSD.decimal)))
        if result.max_score is not None:
            graph.add((iri, AGSO.maxScore, Literal(result.max_score, datatype=XSD.decimal)))
        if result.grade:
            graph.add((iri, AGSO.grade, Literal(result.grade)))
    print(f"    marks          {len(results):>7,}")

    # --- attendance -------------------------------------------------------------------
    # Projected, not copied. MySQL keeps all 30,000 daily marks as the system of record;
    # the graph stores a per-student summary plus only the days that were *not* a plain
    # present. About 95% of marks are "present" and carry no information beyond "nothing
    # happened", so copying them adds ~150,000 triples, slows every query, and answers no
    # question the assistant is asked. The two questions that do get asked -- "has my child
    # missed school recently" and "what is their attendance rate" -- are served better by
    # exceptions plus a summary than by a wall of present marks.
    attendance = db.scalars(select(Attendance).order_by(Attendance.id)).all()
    per_student_counts: dict[int, Counter] = defaultdict(Counter)
    exceptions = 0
    for record in attendance:
        if record.student_id not in student_iris:
            continue
        per_student_counts[record.student_id][record.status.value] += 1
        if record.status == AttendanceStatus.present:
            continue

        number = student_numbers[record.student_id]
        iri = attendance_iri(number, record.attendance_date.isoformat())
        graph.add((iri, RDF.type, AGSO.AttendanceRecord))
        graph.add((student_iris[record.student_id], AGSO.hasAttendance, iri))
        graph.add((iri, AGSO.attendanceDate,
                   Literal(record.attendance_date, datatype=XSD.date)))
        graph.add((iri, AGSO.attendanceStatus, Literal(record.status.value)))
        if record.remarks:
            graph.add((iri, AGSO.remarks, Literal(record.remarks)))
        exceptions += 1

    for student_id, counts in per_student_counts.items():
        iri = student_iris[student_id]
        total = sum(counts.values())
        present_like = counts["present"] + counts["late"]
        graph.add((iri, AGSO.schoolDaysRecorded, Literal(total, datatype=XSD.integer)))
        graph.add((iri, AGSO.daysPresent,
                   Literal(counts["present"], datatype=XSD.integer)))
        graph.add((iri, AGSO.daysAbsent, Literal(counts["absent"], datatype=XSD.integer)))
        graph.add((iri, AGSO.daysLate, Literal(counts["late"], datatype=XSD.integer)))
        graph.add((iri, AGSO.daysExcused,
                   Literal(counts["excused"], datatype=XSD.integer)))
        graph.add((iri, AGSO.attendanceRate,
                   Literal(round(present_like / total, 4) if total else 0,
                           datatype=XSD.decimal)))
    print(f"    attendance     {exceptions:>7,}   (exceptions only, from "
          f"{len(attendance):,} marks)")
    print(f"    att. summaries {len(per_student_counts):>7,}")

    # --- homework ---------------------------------------------------------------------
    homework = db.scalars(select(Homework).order_by(Homework.id)).all()
    for index, task in enumerate(homework, start=1):
        iri = homework_iri(index)
        graph.add((iri, RDF.type, AGSO.Homework))
        graph.add((iri, AGSO["title"], Literal(task.title)))
        graph.add((iri, AGSO.homeworkSubject, subject_iri(task.subject)))
        if task.class_id in class_iris:
            graph.add((iri, AGSO.assignedToClass, class_iris[task.class_id]))
        if task.teacher_id in user_iris:
            graph.add((iri, AGSO.setBy, user_iris[task.teacher_id]))
        if task.description:
            graph.add((iri, AGSO.description, Literal(task.description)))
        if task.assigned_date:
            graph.add((iri, AGSO.assignedDate, Literal(task.assigned_date, datatype=XSD.date)))
        if task.due_date:
            graph.add((iri, AGSO.dueDate, Literal(task.due_date, datatype=XSD.date)))
    print(f"    homework       {len(homework):>7,}")

    # --- timetable --------------------------------------------------------------------
    schedule = db.scalars(select(Schedule).order_by(Schedule.id)).all()
    for index, entry in enumerate(schedule, start=1):
        iri = schedule_iri(index)
        graph.add((iri, RDF.type, AGSO.ScheduleEntry))
        graph.add((iri, AGSO.scheduleSubject, subject_iri(entry.subject)))
        graph.add((iri, AGSO.dayOfWeek, Literal(entry.day_of_week, datatype=XSD.integer)))
        graph.add((iri, AGSO.startTime, Literal(entry.start_time, datatype=XSD.time)))
        graph.add((iri, AGSO.endTime, Literal(entry.end_time, datatype=XSD.time)))
        if entry.class_id in class_iris:
            graph.add((iri, AGSO.scheduleForClass, class_iris[entry.class_id]))
        if entry.teacher_id in user_iris:
            graph.add((iri, AGSO.taughtBy, user_iris[entry.teacher_id]))
        if entry.room:
            graph.add((iri, AGSO.room, Literal(entry.room)))
    print(f"    timetable      {len(schedule):>7,}")

    # --- announcements ----------------------------------------------------------------
    announcements = db.scalars(select(Announcement).order_by(Announcement.id)).all()
    for index, item in enumerate(announcements, start=1):
        iri = announcement_iri(index)
        graph.add((iri, RDF.type, AGSO.Announcement))
        graph.add((iri, AGSO["title"], Literal(item.title)))
        graph.add((iri, AGSO.description, Literal(item.body)))
        graph.add((iri, AGSO.audience, Literal(item.audience)))
        graph.add((iri, AGSO.publishedOn, Literal(item.published_on, datatype=XSD.date)))
        if item.class_id and item.class_id in class_iris:
            graph.add((iri, AGSO.announcementForClass, class_iris[item.class_id]))
    print(f"    announcements  {len(announcements):>7,}")

    return graph


def verify(graph: Graph) -> list[str]:
    """Checks that would otherwise fail silently as an empty result set at query time."""
    problems: list[str] = []

    def count(query: str) -> int:
        return int(list(graph.query(query))[0][0].toPython())

    prefix = f"PREFIX agso: <{AGSO}>\n"

    students = count(prefix + "SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a agso:Student }")
    if students == 0:
        problems.append("no students in the graph")

    unplaced = count(
        prefix
        + "SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE "
          "{ ?s a agso:Student . FILTER NOT EXISTS { ?s agso:enrolledIn ?c } }"
    )
    if unplaced:
        problems.append(f"{unplaced} student(s) not enrolled in any class")

    guardians = count(prefix + "SELECT (COUNT(*) AS ?n) WHERE { ?p agso:guardianOf ?s }")
    if guardians == 0:
        problems.append("no guardianOf edges — every parent would see nothing")

    teaches = count(prefix + "SELECT (COUNT(*) AS ?n) WHERE { ?t agso:teaches ?c }")
    if teaches == 0:
        problems.append("no teaches edges — every teacher would see nothing")

    summarised = count(
        prefix + "SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s agso:attendanceRate ?r }"
    )
    if summarised == 0:
        problems.append("no attendance summaries — attendance questions would return nothing")

    orphan_fees = count(
        prefix
        + "SELECT (COUNT(DISTINCT ?f) AS ?n) WHERE "
          "{ ?f a agso:Fee . FILTER NOT EXISTS { ?s agso:hasFee ?f } }"
    )
    if orphan_fees:
        problems.append(f"{orphan_fees} fee(s) not attached to a student")

    return problems


def upload(path: Path, dataset: str, base_url: str) -> None:
    """POST the graph into Fuseki's default graph, replacing what is there."""
    endpoint = f"{base_url.rstrip('/')}/{dataset}/data?default"
    content_type = "text/turtle" if path.suffix == ".ttl" else "application/n-triples"
    print(f"\nUploading {path.name} to {endpoint} ...")
    with httpx.Client(timeout=300.0) as client:
        response = client.put(
            endpoint, content=path.read_bytes(), headers={"Content-Type": content_type}
        )
    if response.status_code >= 400:
        print(f"  FAILED {response.status_code}: {response.text[:300]}")
        raise SystemExit(1)
    print(f"  OK {response.status_code}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=["ttl", "nt"], default="ttl")
    parser.add_argument("--upload", action="store_true", help="PUT into Fuseki when done")
    parser.add_argument("--dataset", default=None, help="Fuseki dataset name")
    args = parser.parse_args()

    settings = get_settings()
    db = SessionLocal()
    try:
        graph = build(db)
    finally:
        db.close()

    print(f"\n  TOTAL TRIPLES  {len(graph):>7,}   (target ~60,000)")

    print("\nGraph checks...")
    problems = verify(graph)
    if problems:
        for problem in problems:
            print(f"  FAIL  {problem}")
        return 1
    print("  PASS  every student enrolled in a class")
    print("  PASS  guardianOf and teaches edges present (parent and teacher scope)")
    print("  PASS  no orphaned fee nodes")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "ttl" if args.format == "ttl" else "nt"
    path = OUTPUT_DIR / f"agentsphere.{suffix}"
    graph.serialize(destination=path, format="turtle" if suffix == "ttl" else "nt")
    print(f"\nWrote {path} ({path.stat().st_size / 1_048_576:.1f} MB)")

    if args.upload:
        upload(path, args.dataset or settings.fuseki_dataset, settings.fuseki_base_url)

    return 0


if __name__ == "__main__":
    sys.exit(main())
