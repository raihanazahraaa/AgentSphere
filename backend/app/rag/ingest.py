"""Document ingestion (FR4): file -> text -> chunks -> embeddings -> store.

One document is one transaction. A half-ingested document — some chunks embedded, some not
— would be worse than none: it retrieves partially, so an answer looks grounded while
silently missing the paragraph that mattered. Any failure marks the row `failed` with the
reason and leaves no chunks behind.

Re-ingesting an unchanged file is a no-op, decided by SHA-256 of the bytes. Embedding is the
slow part of the pipeline and there is no reason to repeat it for a file nobody edited.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Document, DocumentStatus
from .chunking import split_text
from .embeddings import EmbeddingClient, EmbeddingError
from .extract import ExtractionError, extract, media_type_for
from .store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    ok: bool
    document_id: int | None = None
    title: str = ""
    chunks: int = 0
    skipped: bool = False
    error: str = ""
    elapsed_s: float = 0.0


def corpus_root() -> Path:
    """Directory holding document files. Relative settings resolve against backend/."""
    configured = Path(get_settings().corpus_dir).expanduser()
    if not configured.is_absolute():
        configured = Path(__file__).resolve().parents[2] / configured
    configured.mkdir(parents=True, exist_ok=True)
    return configured


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class IngestService:
    def __init__(
        self,
        db: Session,
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self.db = db
        self.embeddings = embedding_client or EmbeddingClient()
        self.store = VectorStore(db)

    def ingest_file(
        self,
        path: str | Path,
        *,
        title: str | None = None,
        doc_type: str = "general",
        audience_roles: list[str] | None = None,
        class_id: int | None = None,
        uploaded_by: int | None = None,
        force: bool = False,
    ) -> IngestResult:
        started = time.perf_counter()
        path = Path(path)
        if not path.exists():
            return IngestResult(ok=False, error=f"No such file: {path}")

        digest = checksum(path)
        relative = self._relative_to_corpus(path)

        existing = self.db.scalar(select(Document).where(Document.source_path == relative))
        if (
            existing is not None
            and existing.checksum == digest
            and existing.status == DocumentStatus.indexed
            and not force
        ):
            return IngestResult(
                ok=True, document_id=existing.id, title=existing.title,
                chunks=existing.chunk_count, skipped=True,
                elapsed_s=time.perf_counter() - started,
            )

        document = existing or Document(source_path=relative)
        document.title = title or path.stem.replace("-", " ").replace("_", " ").title()
        document.filename = path.name
        document.doc_type = doc_type
        document.media_type = media_type_for(path)
        # Empty audience means "every role", which is the safe default only for documents
        # the caller explicitly declares as such; callers normally pass a list.
        document.audience_roles = audience_roles or []
        document.class_id = class_id
        document.checksum = digest
        document.byte_size = path.stat().st_size
        document.uploaded_by = uploaded_by
        document.status = DocumentStatus.pending
        document.error = None
        if existing is None:
            self.db.add(document)
        self.db.flush()

        try:
            text = extract(path)
        except ExtractionError as exc:
            return self._fail(document, str(exc), started)

        chunks = split_text(text, source_label=document.title)
        if not chunks:
            return self._fail(document, "No text could be chunked from this file", started)

        try:
            vectors = self.embeddings.embed([c.text for c in chunks])
        except EmbeddingError as exc:
            return self._fail(document, str(exc), started)

        self.store.replace_chunks(document, chunks, vectors, self.embeddings.model)
        document.status = DocumentStatus.indexed
        document.indexed_at = datetime.utcnow()
        self.db.commit()

        return IngestResult(
            ok=True, document_id=document.id, title=document.title,
            chunks=len(chunks), elapsed_s=time.perf_counter() - started,
        )

    def ingest_directory(
        self, directory: str | Path, *, manifest: dict | None = None, force: bool = False
    ) -> list[IngestResult]:
        """Ingest every supported file in a directory.

        `manifest` maps a filename to its metadata (doc_type, audience_roles, class_id).
        Without it every document would default to school-wide visibility, which is the
        wrong default for a staff handbook.
        """
        directory = Path(directory)
        manifest = manifest or {}
        results: list[IngestResult] = []

        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".pdf", ".docx", ".txt", ".md"}:
                continue
            meta = manifest.get(path.name, {})
            results.append(
                self.ingest_file(
                    path,
                    title=meta.get("title"),
                    doc_type=meta.get("doc_type", "general"),
                    audience_roles=meta.get("audience_roles"),
                    class_id=meta.get("class_id"),
                    force=force,
                )
            )
        return results

    def save_upload(self, filename: str, content: bytes) -> Path:
        """Write an uploaded file into the corpus directory, safely.

        The filename comes from an HTTP client, so only its basename is used — a name like
        `../../app/config.py` must not be able to write outside the corpus.
        """
        safe_name = Path(filename).name
        if not safe_name or safe_name.startswith("."):
            raise ValueError("Invalid filename")

        destination = corpus_root() / "uploads" / safe_name
        destination.parent.mkdir(parents=True, exist_ok=True)

        resolved = destination.resolve()
        if not resolved.is_relative_to(corpus_root().resolve()):
            raise ValueError("Invalid filename")

        destination.write_bytes(content)
        return destination

    def delete_document(self, document_id: int, *, remove_file: bool = False) -> bool:
        document = self.db.get(Document, document_id)
        if document is None:
            return False
        if remove_file:
            path = corpus_root() / document.source_path
            if path.exists() and path.is_relative_to(corpus_root()):
                path.unlink()
        self.db.delete(document)  # chunks cascade
        self.db.commit()
        return True

    # ------------------------------------------------------------------ internals

    def _relative_to_corpus(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(corpus_root().resolve()))
        except ValueError:
            return path.name

    def _fail(self, document: Document, error: str, started: float) -> IngestResult:
        logger.warning("Ingestion failed for %s: %s", document.filename, error)
        document.status = DocumentStatus.failed
        document.error = error[:500]
        document.chunk_count = 0
        for chunk in list(document.chunks):
            self.db.delete(chunk)
        self.db.commit()
        return IngestResult(
            ok=False, document_id=document.id, title=document.title,
            error=error, elapsed_s=time.perf_counter() - started,
        )


def copy_into_corpus(path: str | Path, subdirectory: str = "") -> Path:
    source = Path(path)
    destination = corpus_root() / subdirectory / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return destination
