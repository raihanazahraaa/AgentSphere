"""Text extraction from uploaded files.

Three formats, matching the corpus a school actually has: PDF (circulars, timetables),
DOCX (letters, handbooks) and plain text.

Extraction is treated as untrusted input throughout. A PDF is a file someone uploaded; its
text may contain anything, including instructions aimed at the model. Nothing here tries to
sanitise that — stripping "ignore previous instructions" would be a losing game of
whack-a-mole. Instead the text is passed through verbatim and `guardrails.py` wraps it in
delimiters that mark it as data. Sanitising here would also corrupt legitimate documents
that happen to quote such phrases.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}
MAX_BYTES = 20 * 1024 * 1024


class ExtractionError(RuntimeError):
    pass


def extract(path: str | Path) -> str:
    """Return the plain text of a document, or raise `ExtractionError`."""
    path = Path(path)
    if not path.exists():
        raise ExtractionError(f"No such file: {path.name}")
    if path.stat().st_size > MAX_BYTES:
        raise ExtractionError(f"{path.name} is larger than {MAX_BYTES // 1_048_576} MB")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace")

    raise ExtractionError(
        f"{path.name}: unsupported type '{suffix}'. "
        f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError("pypdf is not installed") from exc

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types on damaged files
        raise ExtractionError(f"{path.name} could not be opened: {exc}") from exc

    if reader.is_encrypted:
        # Try the empty password, which covers PDFs that are "protected" but not secret.
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"{path.name} is password protected") from exc

    pages: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page must not lose the document
            logger.warning("Could not extract page %d of %s", number, path.name)
            text = ""
        if text.strip():
            # Page markers survive into chunk locations, so a citation can name a page.
            pages.append(f"Page {number}\n{text.strip()}")

    if not pages:
        raise ExtractionError(
            f"{path.name} has no extractable text — it may be a scan needing OCR"
        )
    return "\n\n".join(pages)


def _extract_docx(path: Path) -> str:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError("python-docx is not installed") from exc

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"{path.name} could not be opened: {exc}") from exc

    parts: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        # Word headings become headings in the extracted text, which chunking keys on.
        if paragraph.style is not None and paragraph.style.name.startswith("Heading"):
            parts.append(f"\n{text}")
        else:
            parts.append(text)

    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))

    if not parts:
        raise ExtractionError(f"{path.name} contains no text")
    return "\n\n".join(parts)


def media_type_for(path: str | Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(Path(path).suffix.lower(), "application/octet-stream")
