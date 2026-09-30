"""Create tables and seed the *platform* fixtures.

    cd backend && python seed.py          # accounts, sample agent, role preferences
    cd backend && python generate_data.py # the school records themselves (Table 3.4)

Run seed.py first, then generate_data.py. School records belong to generate_data.py from
Phase 3a onwards; this script only creates the three documented demo logins, one sample
agent and the standing role preferences. generate_data.py preserves these accounts and
joins them into the generated dataset, so the demo parent has a real child and the demo
teacher has real classes.

Safe to re-run: it does nothing if admin@greenfield.edu already exists.
"""

from __future__ import annotations


from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import (
    Agent,
    AgentGuardrail,
    AgentKnowledgeSource,
    AgentStatus,
    AgentTool,
    AppUser,
    GuardrailType,
    KnowledgeSourceType,
    UserRole,
)
from app.agent.memory import set_role_preference
from app.security import hash_password

DEMO_PASSWORD = "password123"


def seed() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.scalar(select(AppUser).where(AppUser.email == "admin@greenfield.edu")):
            print("Seed accounts already present — nothing to do.")
            return

        admin = AppUser(
            email="admin@greenfield.edu",
            password_hash=hash_password(DEMO_PASSWORD),
            full_name="Aisha Rahman",
            role=UserRole.administrator,
        )
        teacher = AppUser(
            email="teacher@greenfield.edu",
            password_hash=hash_password(DEMO_PASSWORD),
            full_name="Daniel Okafor",
            role=UserRole.teacher,
        )
        parent = AppUser(
            email="parent@greenfield.edu",
            password_hash=hash_password(DEMO_PASSWORD),
            full_name="Mei Lin Tan",
            role=UserRole.parent,
        )
        db.add_all([admin, teacher, parent])
        db.flush()

        # School records (classes, students, fees, marks, attendance, homework,
        # timetable) are owned by generate_data.py from Phase 3a onwards. Seeding a second,
        # smaller set of them here meant whichever script ran last silently won.

        # A sample agent so the chat screen has something to talk to on first run.
        helper = Agent(
            name="Parent Helpdesk",
            description="Answers everyday questions from parents about their child.",
            role_instructions=(
                "You are the school's front desk assistant for parents. Answer questions about "
                "attendance, fees, homework and timetables for the parent's own children only. "
                "Be brief and warm. If you are unsure, say so and point them to the school office."
            ),
            audience_roles=[UserRole.parent.value],
            status=AgentStatus.published,
            model_settings={},
            created_by=admin.id,
        )
        helper.knowledge_sources = [
            AgentKnowledgeSource(
                source_type=KnowledgeSourceType.database_table,
                label="Attendance records",
                location="attendance",
            ),
            AgentKnowledgeSource(
                source_type=KnowledgeSourceType.document,
                label="Parent handbook 2025/2026",
                location="parent-handbook-2026.pdf",
            ),
        ]
        helper.tools = [
            AgentTool(tool_key="check_attendance", enabled=True, config={}),
            AgentTool(tool_key="check_fees", enabled=True, config={}),
            AgentTool(tool_key="list_homework", enabled=True, config={}),
        ]
        helper.guardrails = [
            AgentGuardrail(
                guardrail_type=GuardrailType.blocked_topic,
                value="Other pupils' records",
            ),
            AgentGuardrail(
                guardrail_type=GuardrailType.required_disclaimer,
                value="For anything urgent, please call the school office on 01234 567890.",
            ),
        ]
        # Give the sample agent explicit models so the wizard round-trip is visible.
        helper.model_settings = {"model": "qwen3:4b", "fallback_model": "llama3.2:3b"}
        db.add(helper)

        # Long-term memory (FR8): standing preferences per role.
        set_role_preference(
            db, role=UserRole.parent, key="tone",
            preference="Avoid school jargon and abbreviations; write for a busy parent.",
            priority=10,
        )
        set_role_preference(
            db, role=UserRole.parent, key="length",
            preference="Keep answers to a few sentences unless asked for detail.",
            priority=5,
        )
        set_role_preference(
            db, role=UserRole.teacher, key="tone",
            preference="Be concise and practical; school terminology is fine.",
            priority=10,
        )
        set_role_preference(
            db, role=UserRole.administrator, key="detail",
            preference="Include specifics and figures where they are available.",
            priority=10,
        )

        db.commit()
        print("Seeded demo data.")
        print(f"  administrator  admin@greenfield.edu    / {DEMO_PASSWORD}")
        print(f"  teacher        teacher@greenfield.edu  / {DEMO_PASSWORD}")
        print(f"  parent         parent@greenfield.edu   / {DEMO_PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
