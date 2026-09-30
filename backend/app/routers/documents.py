"""Document upload and management (FR4).

Staff-only: a parent may read documents aimed at them but may not add to the corpus.
Ingestion runs inline rather than in a background worker — embedding 5-10 chunks takes well
under a second locally, and a school uploading a circular would rather wait for it than be
told "processing" with no way to know whether it worked.

**`audience_roles` is a security decision, not metadata.** It is what stops the staff
handbook being retrievable for parents, so it is required on upload rather than defaulted.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import CurrentUser, DbSession, StaffUser
from ..models import Document, DocumentStatus, UserRole
from ..rag.extract import SUPPORTED_SUFFIXES
from ..rag.ingest import IngestService
from ..rag.store import VectorStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class DocumentOut(BaseModel):
    id: int
    title: str
    filename: str
    doc_type: str
    audience_roles: list[str]
    class_id: int | None
    status: str
    chunk_count: int
    byte_size: int
    error: str | None


def _to_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id, title=document.title, filename=document.filename,
        doc_type=document.doc_type, audience_roles=document.audience_roles or [],
        class_id=document.class_id, status=document.status.value,
        chunk_count=document.chunk_count, byte_size=document.byte_size,
        error=document.error,
    )


@router.get("", response_model=list[DocumentOut])
def list_documents(user: CurrentUser, db: DbSession) -> list[DocumentOut]:
    """Staff see every document; a parent sees only those aimed at them."""
    documents = db.scalars(select(Document).order_by(Document.id.desc())).all()
    if user.role == UserRole.parent:
        documents = [
            d for d in documents
            if not d.audience_roles or UserRole.parent.value in d.audience_roles
        ]
    return [_to_out(d) for d in documents]


@router.get("/stats")
def document_stats(staff: StaffUser, db: DbSession) -> dict:
    return VectorStore(db).stats()


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    staff: StaffUser,
    db: DbSession,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    doc_type: str = Form(default="general"),
    # Comma-separated, e.g. "parent,administrator". Required: see the module docstring.
    audience_roles: str = Form(...),
    class_id: int | None = Form(default=None),
) -> DocumentOut:
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No filename given")

    suffix = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type '{suffix}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}",
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File is larger than {MAX_UPLOAD_BYTES // 1_048_576} MB",
        )
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")

    roles = [r.strip() for r in audience_roles.split(",") if r.strip()]
    valid = {r.value for r in UserRole}
    unknown = set(roles) - valid
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unknown role(s): {', '.join(sorted(unknown))}. Valid: {', '.join(sorted(valid))}",
        )
    if not roles:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "At least one audience role is required — it decides who can retrieve this.",
        )

    service = IngestService(db)
    if not service.embeddings.is_available():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"The embedding model '{service.embeddings.model}' is not available. "
            f"Run: ollama pull {service.embeddings.model}",
        )

    try:
        path = service.save_upload(file.filename, content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    result = service.ingest_file(
        path, title=title, doc_type=doc_type, audience_roles=roles,
        class_id=class_id, uploaded_by=staff.id,
    )
    if not result.ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, result.error)

    document = db.get(Document, result.document_id)
    return _to_out(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: int, staff: StaffUser, db: DbSession) -> None:
    if not IngestService(db).delete_document(document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")


@router.post("/{document_id}/reindex", response_model=DocumentOut)
def reindex_document(document_id: int, staff: StaffUser, db: DbSession) -> DocumentOut:
    """Re-embed a document, e.g. after changing the embedding model."""
    from ..rag.ingest import corpus_root

    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    path = corpus_root() / document.source_path
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "The original file is no longer on disk")

    service = IngestService(db)
    result = service.ingest_file(
        path, title=document.title, doc_type=document.doc_type,
        audience_roles=document.audience_roles, class_id=document.class_id,
        uploaded_by=staff.id, force=True,
    )
    if not result.ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, result.error)
    db.refresh(document)
    return _to_out(document)
