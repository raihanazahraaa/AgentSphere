"""Dual grounding (FR9): documents plus verified facts, and who wins a conflict."""

from __future__ import annotations

from app.agent.grounding import DATA_CLOSE, DATA_OPEN, build_context, detect_conflicts
from app.graph.retrieval import RetrievalResult
from app.rag.retrieve import DocumentResult
from app.rag.store import Hit


def hit(text: str, title: str = "Fee Circular Term 1", doc_type: str = "fee_circular",
        score: float = 0.8) -> Hit:
    return Hit(
        chunk_id=1, document_id=1, document_title=title, doc_type=doc_type,
        location="Tuition", text=text, score=score,
    )


def fees_records(amount: float = 560.0, paid: float = 100.0) -> RetrievalResult:
    return RetrievalResult(
        ok=True,
        template="fees",
        rows=[{
            "studentNo": "S-2026-0001", "studentName": "Alice",
            "amount": amount, "amountPaid": paid, "status": "partial",
        }],
        summary=f"Alice (S-2026-0001): Term 1 tuition — {amount} invoiced, {paid} paid",
        row_count=1,
    )


# ======================================================================================
# Assembly
# ======================================================================================


def test_context_is_wrapped_in_data_delimiters():
    context = build_context(records=None, documents=DocumentResult(ok=True, hits=[hit("x")]))
    assert context.text.startswith(DATA_OPEN)
    assert context.text.endswith(DATA_CLOSE)


def test_empty_context_is_empty_not_a_stray_delimiter():
    context = build_context(records=None, documents=None)
    assert context.is_empty
    assert context.text == ""


def test_verified_records_come_first_and_are_labelled_authoritative():
    context = build_context(
        records=fees_records(), documents=DocumentResult(ok=True, hits=[hit("The fee is 450.00")])
    )
    assert context.has_records and context.has_documents
    # Position matters to small models, so Section A must precede Section B.
    assert context.text.index("SECTION A") < context.text.index("SECTION B")
    assert "authoritative" in context.text
    assert "THIS section is correct" in context.text


def test_documents_are_labelled_as_possibly_out_of_date():
    context = build_context(records=None, documents=DocumentResult(ok=True, hits=[hit("x")]))
    assert "may be out of date" in context.text
    assert "reference only" in context.text


def test_permitted_identifiers_come_from_the_verified_records():
    """These are what the output guardrail treats as safe to show."""
    context = build_context(records=fees_records(), documents=None)
    assert context.permitted_identifiers == {"S-2026-0001"}


def test_citations_cover_both_sources():
    context = build_context(
        records=fees_records(), documents=DocumentResult(ok=True, hits=[hit("x")])
    )
    assert any("school records" in c for c in context.citations)
    assert any("Fee Circular" in c for c in context.citations)


def test_document_text_is_budgeted():
    long_hits = [hit("word " * 400, title=f"Doc {i}") for i in range(10)]
    context = build_context(
        records=None, documents=DocumentResult(ok=True, hits=long_hits), max_chars=1200
    )
    assert context.truncated
    assert len(context.text) < 3000


# ======================================================================================
# Conflicts — verified facts win
# ======================================================================================


def test_conflicting_fee_amount_is_detected():
    conflicts = detect_conflicts(
        fees_records(amount=560.0),
        DocumentResult(ok=True, hits=[hit("The tuition charge for Term 1 is 450.00.")]),
    )
    assert len(conflicts) == 1
    assert "450.00" in conflicts[0].document_value
    assert "560.00" in conflicts[0].verified_value


def test_agreeing_amounts_produce_no_conflict():
    conflicts = detect_conflicts(
        fees_records(amount=450.0),
        DocumentResult(ok=True, hits=[hit("The tuition charge for Term 1 is 450.00.")]),
    )
    assert conflicts == []


def test_a_conflict_adds_a_corrections_section_telling_the_model_which_to_use():
    context = build_context(
        records=fees_records(amount=560.0),
        documents=DocumentResult(ok=True, hits=[hit("The tuition charge is 450.00.")]),
    )
    assert context.conflicts
    assert "SECTION C" in context.text
    assert "The verified value is" in context.text
    assert "Use 560.00" in context.text


def test_conflict_detection_is_limited_to_fee_records():
    """Deliberately narrow: a detector that guessed would be worse than a stated limit."""
    attendance = RetrievalResult(
        ok=True, template="attendance",
        rows=[{"studentNo": "S-2026-0001", "attendanceRate": 0.95}],
        summary="Alice: attendance 95%", row_count=1,
    )
    assert detect_conflicts(
        attendance, DocumentResult(ok=True, hits=[hit("Attendance must exceed 99.00")])
    ) == []


def test_non_fee_documents_are_not_scanned_for_amounts():
    conflicts = detect_conflicts(
        fees_records(amount=560.0),
        DocumentResult(ok=True, hits=[hit("450.00", doc_type="announcement")]),
    )
    assert conflicts == []


def test_documents_alone_still_ground_an_answer():
    context = build_context(
        records=None,
        documents=DocumentResult(ok=True, hits=[hit("Half term is in October.", doc_type="handbook")]),
    )
    assert context.has_documents and not context.has_records
    assert "SECTION A" not in context.text
    assert "Half term" in context.text


def test_records_alone_still_ground_an_answer():
    context = build_context(records=fees_records(), documents=None)
    assert context.has_records and not context.has_documents
    assert "SECTION A" in context.text
    assert "SECTION B" not in context.text
