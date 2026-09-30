"""Document retrieval (Phase 3b, FR4/FR9).

Pipeline: extract -> chunk -> embed -> store, then embed the query and search.

Role filtering happens inside `VectorStore.search`, before ranking, so text a caller may
not read never enters the candidate set — and therefore never reaches a prompt.
"""

from .chunking import Chunk, split_text
from .embeddings import EmbeddingClient, EmbeddingError, EMBEDDING_DIM
from .extract import ExtractionError, extract, media_type_for
from .ingest import IngestResult, IngestService
from .retrieve import DocumentRetriever
from .store import Hit, VectorStore

__all__ = [
    "EMBEDDING_DIM",
    "Chunk",
    "DocumentRetriever",
    "EmbeddingClient",
    "EmbeddingError",
    "ExtractionError",
    "Hit",
    "IngestResult",
    "IngestService",
    "VectorStore",
    "extract",
    "media_type_for",
    "split_text",
]
