"""Keyword search across the workspace documents.

Deliberately *not* a web search: this runs offline, the corpus is the school's own files,
and nothing untrusted from the open internet reaches the model's context.

Phase 3 replaces the ranking here with semantic retrieval over the indexed
`agent_knowledge_source` rows. The tool key and schema stay the same, so agents already
configured with `search` pick up the better implementation without being re-saved.
"""

from __future__ import annotations

import re

from .base import BaseTool, ToolResult
from .workspace import MAX_FILE_BYTES, list_readable_files, relative_name

MAX_MATCHES = 5
CONTEXT_CHARS = 240
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "of", "to", "in", "on", "for", "and",
    "or", "what", "when", "where", "who", "how", "does", "do", "did", "my", "me", "i",
}


class SearchTool(BaseTool):
    key = "search"
    label = "Search documents"
    description = "Searches the school's documents for a keyword or phrase."
    input_hint = "the words to look for, e.g. half term dates"

    def _run(self, tool_input: str) -> ToolResult:
        query = tool_input.strip()
        if not query:
            return ToolResult.failure("No search terms given.")

        terms = [t for t in re.findall(r"[\w']+", query.lower()) if t not in _STOPWORDS]
        if not terms:
            return ToolResult.failure("Those are all common words — try more specific terms.")

        scored: list[tuple[int, str, str]] = []
        for path in list_readable_files():
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            lowered = text.lower()

            score = sum(lowered.count(term) for term in terms)
            if not score:
                continue
            # Documents matching more distinct terms beat documents repeating one term.
            score += 10 * sum(1 for term in terms if term in lowered)
            scored.append((score, relative_name(path), self._snippet(text, lowered, terms)))

        if not scored:
            return ToolResult.failure(f"Nothing in the documents mentions '{query}'.")

        scored.sort(key=lambda row: row[0], reverse=True)
        top = scored[:MAX_MATCHES]
        lines = [f"[{name}] {snippet}" for _, name, snippet in top]
        return ToolResult.success(
            "\n".join(lines),
            matches=[name for _, name, _ in top],
            searched=len(list_readable_files()),
        )

    def _snippet(self, text: str, lowered: str, terms: list[str]) -> str:
        """Window of text around the earliest matching term."""
        positions = [lowered.find(term) for term in terms if lowered.find(term) != -1]
        centre = min(positions) if positions else 0
        start = max(0, centre - CONTEXT_CHARS // 3)
        end = min(len(text), start + CONTEXT_CHARS)
        snippet = " ".join(text[start:end].split())
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(text) else ""
        return f"{prefix}{snippet}{suffix}"
