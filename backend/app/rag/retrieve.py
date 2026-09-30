"""Query-side document retrieval.

Thin on purpose: embed the question, search with the caller's role, return hits. The
interesting decisions live in `store.py` (role filter before ranking) and `grounding.py`
(how hits are combined with SPARQL facts).

A retrieved passage is **evidence, not an answer**. It is never returned to the user
directly — it goes into the context block that `guardrails.py` wraps as untrusted data.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import get_settings
from .embeddings import EmbeddingClient, EmbeddingError
from .store import Hit, VectorStore

logger = logging.getLogger(__name__)


@dataclass
class DocumentResult:
    ok: bool
    hits: list[Hit] = field(default_factory=list)
    error: str = ""
    elapsed_s: float = 0.0
    # True when the store held candidates but none cleared the score floor — worth
    # distinguishing from "no documents at all", which is a setup problem.
    below_threshold: bool = False

    @property
    def citations(self) -> list[str]:
        seen: list[str] = []
        for hit in self.hits:
            if hit.citation not in seen:
                seen.append(hit.citation)
        return seen


class DocumentRetriever:
    def __init__(
        self,
        db: Session,
        embedding_client: EmbeddingClient | None = None,
        top_k: int | None = None,
        min_score: float | None = None,
    ) -> None:
        settings = get_settings()
        self.db = db
        self.embeddings = embedding_client or EmbeddingClient()
        self.store = VectorStore(db)
        self.top_k = top_k if top_k is not None else settings.rag_top_k
        self.min_score = min_score if min_score is not None else settings.rag_min_score

    def retrieve(
        self,
        question: str,
        *,
        role: str,
        class_ids: list[int] | None = None,
        top_k: int | None = None,
    ) -> DocumentResult:
        started = time.perf_counter()
        if not question or not question.strip():
            return DocumentResult(ok=True, elapsed_s=time.perf_counter() - started)

        try:
            vector = self.embeddings.embed_one(question)
        except EmbeddingError as exc:
            logger.error("Query embedding failed: %s", exc)
            return DocumentResult(
                ok=False, error=str(exc), elapsed_s=time.perf_counter() - started
            )

        limit = top_k if top_k is not None else self.top_k

        # Search twice: once with the floor applied, and if that is empty, once without,
        # purely to tell "nothing relevant" apart from "nothing indexed". The unfiltered
        # hits are never returned — they only set `below_threshold`.
        hits = self.store.search(
            vector, role=role, limit=limit, min_score=self.min_score, class_ids=class_ids
        )
        below = False
        if not hits:
            probe = self.store.search(
                vector, role=role, limit=1, min_score=0.0, class_ids=class_ids
            )
            below = bool(probe)

        return DocumentResult(
            ok=True,
            hits=hits,
            below_threshold=below,
            elapsed_s=time.perf_counter() - started,
        )
