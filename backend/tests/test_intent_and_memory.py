"""Intent routing (FR6) and memory (FR8)."""

from __future__ import annotations

import pytest

from app.agent.intent import Route, route_query

ALL_TOOLS = ["search", "calculator", "file_reader", "code_runner"]


@pytest.mark.parametrize(
    "question",
    [
        "Has my child missed any school this week?",
        "How much do I owe in fees?",
        "What were my son's exam results?",
        "What homework was set today?",
        "What is on the timetable tomorrow?",
        "Why was my daughter marked absent?",
    ],
)
def test_school_record_questions_go_to_retrieval(question):
    # These must not be handed to the calculator, which has no idea what the fees are.
    assert route_query(question, ALL_TOOLS).route is Route.RETRIEVAL


@pytest.mark.parametrize(
    ("question", "expected_tool"),
    [
        ("What is 15% of 320?", "calculator"),
        ("Write some python to sort a list", "code_runner"),
        ("What does the handbook say about uniform?", "file_reader"),
    ],
)
def test_tool_wording_is_recognised(question, expected_tool):
    decision = route_query(question, ALL_TOOLS)
    assert decision.route is Route.TOOLS
    assert expected_tool in decision.suggested_tools


@pytest.mark.parametrize("greeting", ["Hello", "hi", "Thanks!", "Good morning"])
def test_greetings_answer_directly(greeting):
    assert route_query(greeting, ALL_TOOLS).route is Route.DIRECT


def test_agent_without_tools_always_answers_directly():
    assert route_query("What is 2 + 2?", []).route is Route.DIRECT


def test_agent_with_tools_defaults_to_the_loop():
    # No keyword matched, but the model is a better judge than these patterns.
    assert route_query("Tell me about the school", ALL_TOOLS).route is Route.TOOLS


def test_only_available_tools_are_suggested():
    decision = route_query("Write some python for me", ["calculator"])
    assert "code_runner" not in decision.suggested_tools


def test_retrieval_stub_is_gone():
    """Phase 3a replaced the placeholder with real SPARQL retrieval.

    Kept as a regression guard: if the stub ever reappears, record questions would start
    being answered with an apology instead of data.
    """
    import app.agent.intent as intent

    assert not hasattr(intent, "retrieval_stub")
    assert not hasattr(intent, "RETRIEVAL_PLACEHOLDER")


# ------------------------------------------------------------------------ memory (FR8)


def test_conversation_buffer_is_bounded_and_ordered(db_session, chat_session):
    from app.agent.memory import conversation_buffer
    from app.models import ChatMessage, MessageSender

    for index in range(10):
        db_session.add(
            ChatMessage(
                session_id=chat_session.id,
                sender=MessageSender.user if index % 2 == 0 else MessageSender.agent,
                content=f"message {index}",
                meta={},
            )
        )
    db_session.flush()

    buffer = conversation_buffer(db_session, chat_session.id, max_turns=2)

    assert len(buffer) == 4                      # 2 turns == 4 messages
    assert buffer[-1][1] == "message 9"          # newest last
    assert buffer[0][1] == "message 6"           # oldest of the window first


def test_conversation_buffer_excludes_system_messages(db_session, chat_session):
    from app.agent.memory import conversation_buffer
    from app.models import ChatMessage, MessageSender

    db_session.add_all(
        [
            ChatMessage(session_id=chat_session.id, sender=MessageSender.user,
                        content="real question", meta={}),
            ChatMessage(session_id=chat_session.id, sender=MessageSender.system,
                        content="bookkeeping", meta={}),
        ]
    )
    db_session.flush()

    contents = [text for _, text in conversation_buffer(db_session, chat_session.id, 5)]
    assert "real question" in contents
    assert "bookkeeping" not in contents


def test_zero_turns_returns_nothing(db_session, chat_session):
    from app.agent.memory import conversation_buffer

    assert conversation_buffer(db_session, chat_session.id, max_turns=0) == []


def test_role_preferences_round_trip_and_order(db_session):
    from app.agent.memory import role_preferences, set_role_preference
    from app.models import UserRole

    set_role_preference(db_session, role=UserRole.parent, key="tone",
                        preference="No jargon.", priority=10)
    set_role_preference(db_session, role=UserRole.parent, key="length",
                        preference="Be brief.", priority=5)
    set_role_preference(db_session, role=UserRole.teacher, key="tone",
                        preference="Terminology is fine.", priority=10)

    parent = role_preferences(db_session, UserRole.parent)
    assert parent == ["No jargon.", "Be brief."]      # highest priority first
    # Preferences must not leak across roles.
    assert role_preferences(db_session, UserRole.teacher) == ["Terminology is fine."]


def test_setting_the_same_key_updates_rather_than_duplicates(db_session):
    from app.agent.memory import role_preferences, set_role_preference
    from app.models import UserRole

    set_role_preference(db_session, role=UserRole.parent, key="tone", preference="First.")
    set_role_preference(db_session, role=UserRole.parent, key="tone", preference="Second.")

    assert role_preferences(db_session, UserRole.parent) == ["Second."]
