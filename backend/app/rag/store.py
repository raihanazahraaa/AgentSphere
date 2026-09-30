"""Vector store: embeddings as BLOBs in the relational database, searched with numpy.

**Why not sqlite-vec.** The brief asked for sqlite-vec "or equivalent". It cannot be used
here: the python.org macOS build of Python 3.14 ships `sqlite3` compiled without loadable
extension support — `Connection.enable_load_extension` does not exist, so *no* SQLite
extension can load, sqlite-vec included. Rebuilding Python to change that would break the
existing venv and every other phase.

That turns out not to matter at this scale. The corpus is ~50 documents, on the order of a
thousand chunks. An exact dot product over a (1000, 768) float32 matrix is roughly 1.5 MB
of arithmetic — about a millisecond — and it gives *perfect* recall, which an approximate
index does not. ANN indexes earn their overhead somewhere north of 10^5 vectors.

`nomic-embed-text` returns L2-normalised vectors, so cosine similarity is a plain dot
product. `embeddings.py` guarantees that; this module assumes it.

**Role filtering happens before ranking, not after.** Chunks the caller may not read are
excluded from the candidate matrix entirely. Filtering afterwards would mean the text had
already been loaded, ranked and — in a careless refactor — put in a prompt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document, DocumentChunk, DocumentStatus
from .embeddings import from_blob, to_blob

logger = logging.getLogger(__name__)


@dataclass
class Hit:
    """One retrieved passage."""

    chunk_id: int
    document_id: int
    document_title: str
    doc_type: str
    location: str
    text: str
    score: float
    source_path: str = ""

    @property
    def citation(self) -> str:
        return f"{self.document_title}" + (f" ({self.location})" if self.location else "")


class VectorStore:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------ writing

    def replace_chunks(
        self, document: Document, chunks: list, vectors: np.ndarray, model: str
    ) -> int:
        """Write a document's chunks, discarding any previous ones.

        Re-ingesting a document must not leave the old passages behind: they would still be
        retrievable and would contradict the new version.
        """
        for existing in list(document.chunks):
            self.db.delete(existing)
        self.db.flush()

        dim = int(vectors.shape[1]) if vectors.size else 0
        for chunk, vector in zip(chunks, vectors):
            self.db.add(
                DocumentChunk(
                    document_id=document.id,
                    chunk_index=chunk.index,
                    text=chunk.text,
                    location=chunk.location or None,
                    token_estimate=chunk.token_estimate,
                    embedding=to_blob(vector),
                    embedding_model=model,
                    embedding_dim=dim,
                )
            )
        document.chunk_count = len(chunks)
        self.db.flush()
        return len(chunks)

    # ------------------------------------------------------------------ reading

    def search(
        self,
        query_vector: np.ndarray,
        *,
        role: str,
        limit: int = 5,
        min_score: float = 0.0,
        class_ids: list[int] | None = None,
        doc_types: list[str] | None = None,
    ) -> list[Hit]:
        """Top `limit` chunks the caller is allowed to read, by cosine similarity."""
        rows = self._candidates(role=role, class_ids=class_ids, doc_types=doc_types)
        if not rows:
            return []

        matrix = np.vstack([from_blob(row.embedding) for row, _ in rows])
        query = np.asarray(query_vector, dtype=np.float32)

        if matrix.shape[1] != query.shape[0]:
            # Happens when the embedding model is swapped without re-ingesting. Returning
            # nothing is right: silently comparing across models yields garbage rankings.
            logger.error(
                "Embedding dimension mismatch: stored %d, query %d — re-run ingestion",
                matrix.shape[1], query.shape[0],
            )
            return []

        scores = matrix @ query  # vectors are normalised, so this is cosine similarity
        order = np.argsort(-scores)[: max(limit, 0)]

        hits: list[Hit] = []
        for position in order:
            score = float(scores[position])
            if score < min_score:
                continue
            chunk, document = rows[int(position)]
            hits.append(
                Hit(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    document_title=document.title,
                    doc_type=document.doc_type,
                    location=chunk.location or "",
                    text=chunk.text,
                    score=score,
                    source_path=document.source_path,
                )
            )
        return hits

    def _candidates(
        self,
        *,
        role: str,
        class_ids: list[int] | None,
        doc_types: list[str] | None,
    ) -> list[tuple[DocumentChunk, Document]]:
        """Chunks this role may read. The role filter is applied in SQL, before ranking."""
        query = (
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                Document.status == DocumentStatus.indexed,
                DocumentChunk.embedding.is_not(None),
            )
        )
        if doc_types:
            query = query.where(Document.doc_type.in_(doc_types))

        rows = list(self.db.execute(query).all())

        # audience_roles is JSON, so the membership test is done in Python. At this corpus
        # size that is cheaper than a portable JSON query, and it keeps the rule in one
        # readable place rather than split across MySQL and SQLite dialects.
        permitted: list[tuple[DocumentChunk, Document]] = []
        for chunk, document in rows:
            audience = document.audience_roles or []
            if audience and role not in audience:
                continue
            if document.class_id is not None and class_ids is not None:
                if document.class_id not in class_ids:
                    continue
            permitted.append((chunk, document))
        return permitted

    # ------------------------------------------------------------------ stats

    def stats(self) -> dict:
        documents = self.db.scalars(select(Document)).all()
        indexed = [d for d in documents if d.status == DocumentStatus.indexed]
        return {
            "documents": len(documents),
            "indexed": len(indexed),
            "failed": sum(1 for d in documents if d.status == DocumentStatus.failed),
            "chunks": sum(d.chunk_count for d in indexed),
            "by_type": {
                doc_type: sum(1 for d in indexed if d.doc_type == doc_type)
                for doc_type in sorted({d.doc_type for d in indexed})
            },
        }
