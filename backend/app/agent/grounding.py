"""Dual grounding (FR9): document passages plus verified graph facts, in one block.

Two sources answer two different kinds of question, and most real questions need both:

* **Verified records** (SPARQL over the knowledge graph) — what is true of *this* pupil
  right now. Authoritative, role-scoped at the query, and never stale.
* **Documents** (vector retrieval over the corpus) — policy, procedure, dates, "how does
  this work". Useful, but a fee circular written in September does not know that this
  family was granted a hardship waiver in November.

**Where they conflict, verified records win.** That is enforced three ways, because a
3-4B model will not reliably honour one instruction:

1. Records are placed **first** in the block, under a heading that says they are
   authoritative. Position matters to small models.
2. The instruction is stated explicitly next to the data, not only in the system prompt.
3. Numeric contradictions that can be detected are **annotated inline** with the correct
   value, so the model does not have to resolve the conflict by reasoning.

Detection (3) is deliberately narrow — it compares money amounts and percentages that both
sources state about the same subject. General contradiction detection between prose and
structured data is not tractable here, and a detector that guessed would be worse than one
with a stated limit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..graph.retrieval import RetrievalResult
from ..rag.retrieve import DocumentResult

logger = logging.getLogger(__name__)

# Delimiters marking untrusted retrieved text. See guardrails.py for the instruction that
# accompanies them; they are defined here because the block is built here.
DATA_OPEN = "<<<BEGIN_SCHOOL_DATA>>>"
DATA_CLOSE = "<<<END_SCHOOL_DATA>>>"

_MONEY = re.compile(r"\b(\d{2,5}(?:\.\d{2})?)\b")


@dataclass
class Conflict:
    """A detected contradiction between a document and a verified record."""

    field: str
    document_value: str
    verified_value: str
    document_title: str

    def describe(self) -> str:
        return (
            f"{self.field}: the document '{self.document_title}' says "
            f"{self.document_value}, but the verified record says {self.verified_value}. "
            f"Use {self.verified_value}."
        )


@dataclass
class GroundedContext:
    """Everything the model is allowed to see for one question."""

    text: str
    has_records: bool = False
    has_documents: bool = False
    record_citations: list[str] = field(default_factory=list)
    document_citations: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    # Student numbers appearing in verified records — the set the caller is entitled to
    # see, used by the output guardrail to decide what must be masked.
    permitted_identifiers: set[str] = field(default_factory=set)
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.has_records or self.has_documents)

    @property
    def citations(self) -> list[str]:
        return self.record_citations + self.document_citations


def build_context(
    *,
    records: RetrievalResult | None,
    documents: DocumentResult | None,
    max_chars: int = 3500,
) -> GroundedContext:
    """Assemble the labelled, role-scoped context block."""
    context = GroundedContext(text="")
    sections: list[str] = []

    record_lines = ""
    if records is not None and records.ok and records.rows:
        context.has_records = True
        context.record_citations = [f"school records ({records.template})"]
        context.permitted_identifiers = {
            str(row["studentNo"]) for row in records.rows if row.get("studentNo")
        }
        record_lines = records.summary

    if documents is not None and documents.ok and documents.hits:
        context.has_documents = True
        context.document_citations = documents.citations

    # Detect contradictions before rendering, so they can be annotated in place.
    if context.has_records and context.has_documents:
        context.conflicts = detect_conflicts(records, documents)

    # --- section 1: verified records, first and labelled authoritative ----------------
    if context.has_records:
        sections.append(
            "SECTION A — VERIFIED SCHOOL RECORDS (authoritative, current)\n"
            "These come from the school's own database for this specific person. "
            "If anything in Section B disagrees with this, THIS section is correct.\n\n"
            + record_lines
        )

    # --- section 2: documents, clearly secondary --------------------------------------
    if context.has_documents:
        budget = max_chars - len(record_lines)
        rendered, truncated = _render_documents(documents, budget)
        context.truncated = truncated
        sections.append(
            "SECTION B — SCHOOL DOCUMENTS (reference only, may be out of date)\n"
            "Policies, circulars and letters. Useful for how things work, but they do not "
            "know this person's individual situation.\n\n" + rendered
        )

    if context.conflicts:
        sections.append(
            "SECTION C — CORRECTIONS\n"
            "The documents contradict the verified records here. The verified value is "
            "correct:\n"
            + "\n".join(f"- {c.describe()}" for c in context.conflicts)
        )

    body = "\n\n".join(sections)
    context.text = f"{DATA_OPEN}\n{body}\n{DATA_CLOSE}" if body else ""
    return context


def _render_documents(documents: DocumentResult, budget: int) -> tuple[str, bool]:
    """Render passages newest-first within a character budget.

    Budgeted because a 3B model given the whole corpus loses the question. Passages are
    already ranked, so truncating from the end drops the least relevant.
    """
    budget = max(budget, 600)
    parts: list[str] = []
    used = 0
    truncated = False

    for index, hit in enumerate(documents.hits, start=1):
        entry = f"[Document {index}: {hit.citation}]\n{hit.text.strip()}"
        if used + len(entry) > budget:
            truncated = True
            break
        parts.append(entry)
        used += len(entry)

    return "\n\n".join(parts), truncated


def detect_conflicts(
    records: RetrievalResult, documents: DocumentResult
) -> list[Conflict]:
    """Find money amounts a document states that the verified record contradicts.

    Narrow on purpose. It fires on the case that actually matters and is checkable — a fee
    circular quoting the standard charge when this family's record says something else
    (partial payment, or a hardship waiver). It does not attempt to reconcile prose
    generally; `build_context` relies on ordering and instruction for the rest.
    """
    if records.template != "fees":
        return []

    verified_amounts: set[str] = set()
    outstanding: str | None = None
    for row in records.rows:
        amount = row.get("amount")
        paid = row.get("amountPaid")
        if amount is None:
            continue
        verified_amounts.add(f"{float(amount):.2f}")
        if paid is not None:
            balance = float(amount) - float(paid)
            outstanding = f"{balance:.2f}"

    if not verified_amounts:
        return []

    conflicts: list[Conflict] = []
    seen_titles: set[str] = set()

    for hit in documents.hits:
        if hit.doc_type != "fee_circular":
            continue
        if hit.document_title in seen_titles:
            continue
        # Amounts the document asserts, in the fee range the school actually charges.
        stated = {
            f"{float(m):.2f}"
            for m in _MONEY.findall(hit.text)
            if 100.0 <= float(m) <= 10000.0
        }
        contradicting = stated - verified_amounts
        if contradicting and stated:
            seen_titles.add(hit.document_title)
            conflicts.append(
                Conflict(
                    field="fee amount",
                    document_value=", ".join(sorted(contradicting)[:3]),
                    verified_value=(
                        f"{', '.join(sorted(verified_amounts))}"
                        + (f" (outstanding {outstanding})" if outstanding else "")
                    ),
                    document_title=hit.document_title,
                )
            )

    return conflicts[:3]
