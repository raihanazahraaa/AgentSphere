"""Splitting extracted text into retrievable passages.

Paragraph-first, not fixed-width: school documents are structured as headed sections and
short paragraphs, and cutting mid-sentence every N characters produces chunks that retrieve
badly and read worse when shown as a citation.

Each chunk carries the heading it sat under. A passage reading "The fee is payable within
30 days" is ambiguous on its own; "Fees > Payment terms: The fee is payable within 30 days"
is not, and the heading costs a handful of tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Roughly 4 characters per token for English prose. Only used for budgeting, never for
# anything that has to be exact.
CHARS_PER_TOKEN = 4

TARGET_CHARS = 900
MAX_CHARS = 1400
MIN_CHARS = 120
OVERLAP_CHARS = 150

# A line that is short, has no terminal punctuation, and is titled or numbered.
_HEADING = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s+)?[A-Z][^.!?]{2,70}$"
)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


@dataclass
class Chunk:
    text: str
    index: int
    location: str = ""
    token_estimate: int = 0


def looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return False
    if _BULLET.match(stripped):
        return False
    if stripped.endswith((".", "!", "?", ",", ";", ":")) and not stripped.isupper():
        return False
    return bool(_HEADING.match(stripped)) or stripped.isupper()


def split_text(text: str, *, source_label: str = "") -> list[Chunk]:
    """Split into chunks, each tagged with the heading it belongs to."""
    if not text or not text.strip():
        return []

    blocks = _blocks_with_headings(text)
    chunks: list[Chunk] = []

    for heading, body in blocks:
        location = " > ".join(part for part in (source_label, heading) if part)
        for piece in _split_block(body):
            chunks.append(
                Chunk(
                    text=(f"[{location}]\n{piece}" if location else piece),
                    index=len(chunks),
                    location=location or source_label,
                    token_estimate=max(1, len(piece) // CHARS_PER_TOKEN),
                )
            )

    # A document with no paragraph structure at all still has to be retrievable.
    if not chunks:
        for piece in _hard_split(text.strip()):
            chunks.append(
                Chunk(
                    text=piece, index=len(chunks), location=source_label,
                    token_estimate=max(1, len(piece) // CHARS_PER_TOKEN),
                )
            )
    return chunks


def _blocks_with_headings(text: str) -> list[tuple[str, str]]:
    """Group the document into (heading, body) pairs."""
    lines = text.splitlines()
    blocks: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []

    for line in lines:
        if looks_like_heading(line):
            if buffer and any(b.strip() for b in buffer):
                blocks.append((heading, "\n".join(buffer).strip()))
            heading = line.strip()
            buffer = []
        else:
            buffer.append(line)

    if buffer and any(b.strip() for b in buffer):
        blocks.append((heading, "\n".join(buffer).strip()))
    return blocks


def _split_block(body: str) -> list[str]:
    """Pack paragraphs up to TARGET_CHARS, splitting any paragraph that alone exceeds MAX."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    pieces: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > MAX_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_hard_split(paragraph))
            continue

        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= TARGET_CHARS:
            current = candidate
        else:
            if current:
                pieces.append(current)
            current = paragraph

    if current:
        pieces.append(current)

    # Fold a stub tail into its predecessor rather than emitting a chunk of three words.
    if len(pieces) > 1 and len(pieces[-1]) < MIN_CHARS:
        pieces[-2] = f"{pieces[-2]}\n\n{pieces[-1]}"
        pieces.pop()
    return pieces


def _hard_split(text: str) -> list[str]:
    """Last resort for a paragraph with no internal breaks: split on sentences.

    Overlaps consecutive pieces so a fact spanning the boundary is still retrievable from
    at least one of them.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces: list[str] = []
    current = ""

    for sentence in sentences:
        if len(sentence) > MAX_CHARS:
            # A single sentence longer than the cap: cut on width, with overlap.
            for start in range(0, len(sentence), MAX_CHARS - OVERLAP_CHARS):
                pieces.append(sentence[start : start + MAX_CHARS])
            continue
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= TARGET_CHARS:
            current = candidate
        else:
            if current:
                pieces.append(current)
            current = sentence

    if current:
        pieces.append(current)
    return pieces or [text]
