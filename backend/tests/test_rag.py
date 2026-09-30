"""Chunking, the vector store and document-level RBAC.

No model and no server: embeddings are supplied as fixed vectors so ranking is checkable by
inspection rather than by whatever the embedding model happens to think today.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.models import Document, DocumentChunk, DocumentStatus
from app.rag.chunking import looks_like_heading, split_text
from app.rag.embeddings import from_blob, to_blob
from app.rag.store import VectorStore


# ======================================================================================
# Chunking
# ======================================================================================


def test_short_document_becomes_one_chunk():
    chunks = split_text("Half term runs from 27 to 31 October 2025.", source_label="Handbook")
    assert len(chunks) == 1
    assert "27 to 31 October" in chunks[0].text


def test_headings_are_detected():
    assert looks_like_heading("Term dates")
    assert looks_like_heading("3.1 Attendance")
    assert looks_like_heading("UNIFORM")
    assert not looks_like_heading("The school day ends at 15:30.")
    assert not looks_like_heading("- Chess Club on Monday")


def test_chunks_carry_their_heading_so_they_stand_alone():
    """A passage reading 'payable within 30 days' is useless without its heading."""
    text = """Fees

Tuition is payable within 30 days of the invoice date.

Uniform

Pupils wear the school blazer."""
    chunks = split_text(text, source_label="Handbook")
    fee_chunk = next(c for c in chunks if "30 days" in c.text)
    assert "Fees" in fee_chunk.location
    assert "Fees" in fee_chunk.text


def test_long_document_splits_into_several_chunks():
    body = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(12))
    chunks = split_text(body, source_label="Long")
    assert len(chunks) > 1
    assert all(len(c.text) < 2500 for c in chunks)


def test_chunk_indices_are_contiguous():
    body = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(8))
    chunks = split_text(body)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_empty_input_produces_no_chunks():
    assert split_text("") == []
    assert split_text("   \n  \n") == []


def test_a_wall_of_text_with_no_paragraphs_still_chunks():
    body = " ".join(f"This is sentence number {i}." for i in range(200))
    chunks = split_text(body)
    assert len(chunks) > 1


# ======================================================================================
# Embedding round trip
# ======================================================================================


def test_vector_survives_the_blob_round_trip():
    vector = np.array([0.1, -0.25, 0.75], dtype=np.float32)
    restored = from_blob(to_blob(vector))
    assert np.allclose(vector, restored)


def test_blob_is_little_endian_float32():
    """Stored bytes must be portable between machines."""
    assert len(to_blob(np.zeros(768, dtype=np.float32))) == 768 * 4


# ======================================================================================
# Vector store: ranking and role filtering
# ======================================================================================


def unit(*values: float) -> np.ndarray:
    vector = np.array(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


@pytest.fixture
def store(db_session):
    """Three documents with hand-chosen orthogonal-ish vectors, one per audience."""
    specs = [
        ("Parent Handbook", ["parent", "teacher", "administrator"], unit(1, 0, 0), "handbook"),
        ("Staff Handbook", ["teacher", "administrator"], unit(0, 1, 0), "handbook"),
        ("Fee Circular", ["parent", "administrator"], unit(0, 0, 1), "fee_circular"),
    ]
    for index, (title, roles, vector, doc_type) in enumerate(specs, start=1):
        document = Document(
            title=title, filename=f"{title}.txt", doc_type=doc_type,
            audience_roles=roles, source_path=f"{title}.txt", checksum=f"c{index}",
            status=DocumentStatus.indexed, chunk_count=1,
        )
        db_session.add(document)
        db_session.flush()
        db_session.add(
            DocumentChunk(
                document_id=document.id, chunk_index=0, text=f"Body of {title}",
                embedding=to_blob(vector), embedding_dim=3, embedding_model="test",
            )
        )
    db_session.flush()
    return VectorStore(db_session)


def titles(hits) -> list[str]:
    return [h.document_title for h in hits]


def test_search_ranks_by_similarity(store):
    hits = store.search(unit(1, 0, 0), role="parent", limit=3)
    assert hits[0].document_title == "Parent Handbook"
    assert hits[0].score > 0.9


def test_a_parent_never_sees_the_staff_handbook(store):
    """The document-level half of RBAC.

    The query vector points straight at the staff handbook, so this fails loudly if the
    audience filter is not applied.
    """
    hits = store.search(unit(0, 1, 0), role="parent", limit=5)
    assert "Staff Handbook" not in titles(hits)


def test_a_teacher_does_see_the_staff_handbook(store):
    hits = store.search(unit(0, 1, 0), role="teacher", limit=5)
    assert hits[0].document_title == "Staff Handbook"


def test_a_teacher_does_not_see_fee_circulars(store):
    """Fees are withheld from teachers on every path — REST, graph, and now documents."""
    hits = store.search(unit(0, 0, 1), role="teacher", limit=5)
    assert "Fee Circular" not in titles(hits)


def test_an_administrator_sees_everything(store):
    assert len(store.search(unit(1, 1, 1), role="administrator", limit=5)) == 3


def test_the_score_floor_excludes_weak_matches(store):
    # unit(1,1,0) is a 45-degree match to the Parent Handbook: cosine 0.707, which is a
    # real but weak hit. It survives a low floor and is cut by a high one.
    assert store.search(unit(1, 1, 0), role="parent", limit=5, min_score=0.5)
    assert store.search(unit(1, 1, 0), role="parent", limit=5, min_score=0.9) == []


def test_an_exact_match_is_not_cut_by_the_floor(store):
    hits = store.search(unit(1, 0, 0), role="parent", limit=5, min_score=0.99)
    assert len(hits) == 1
    assert hits[0].score == pytest.approx(1.0, abs=1e-5)


def test_limit_is_respected(store):
    assert len(store.search(unit(1, 1, 1), role="administrator", limit=2)) == 2


def test_unindexed_documents_are_not_searched(db_session, store):
    document = Document(
        title="Draft", filename="d.txt", doc_type="general",
        audience_roles=["parent"], source_path="d.txt", checksum="draft",
        status=DocumentStatus.pending, chunk_count=1,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        DocumentChunk(
            document_id=document.id, chunk_index=0, text="Draft body",
            embedding=to_blob(unit(1, 0, 0)), embedding_dim=3, embedding_model="test",
        )
    )
    db_session.flush()
    assert "Draft" not in titles(store.search(unit(1, 0, 0), role="parent", limit=5))


def test_dimension_mismatch_returns_nothing_rather_than_nonsense(store):
    """Happens when the embedding model changes without re-ingesting.

    Comparing across models silently produces garbage rankings, so returning nothing — and
    logging — is the safer failure.
    """
    assert store.search(np.ones(768, dtype=np.float32), role="parent", limit=3) == []


def test_stats_report_the_corpus(store):
    stats = store.stats()
    assert stats["documents"] == 3
    assert stats["indexed"] == 3
    assert stats["chunks"] == 3
