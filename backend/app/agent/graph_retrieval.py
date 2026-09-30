"""The retrieval branch of the runtime (Phase 3a), replacing the Phase 2 placeholder.

Three steps, two of them model calls:

1. **Select** — the model reads the template catalogue for the caller's role and emits
   `{"template": "fees", "parameters": {...}}`. It never writes SPARQL.
2. **Retrieve** — `RetrievalService` validates, applies the role scope, queries, audits.
3. **Phrase** — the model turns the rows into a sentence for the user.

Step 3 is skipped on a refusal: the refusal wording is fixed text, written for a parent to
read, and handing it to a 3B model to rephrase risks it inventing a reason or apologising
its way into implying the data exists. A refusal is the one answer that must not be
paraphrased.

Nothing the model produces influences the scope. It picks *what kind* of record and *which
filters*; `rbac.py` decides *whose* records, from the session token alone.
"""

from __future__ import annotations

import json
import logging

from ..graph import Principal, RetrievalService, describe_templates
from ..graph.retrieval import RetrievalResult
from .action_parser import _THINK_BLOCK, _brace_blocks
from .ollama_client import OllamaClient, OllamaError

logger = logging.getLogger(__name__)

# Used when the model cannot pick a template. Deliberately plain.
CANNOT_ANSWER = (
    "I could not work out which records you need. Try naming one of: fees, marks, "
    "attendance, homework or the timetable."
)


def build_selection_prompt(question: str, role: str, history: list[tuple[str, str]]) -> str:
    catalogue = describe_templates(role)
    parts = []
    if history:
        turns = "\n".join(
            f"{'User' if who == 'user' else 'You'}: {text}" for who, text in history[-4:]
        )
        parts += ["Earlier in this conversation:", turns, ""]

    parts += [
        "The user is asking about school records. Choose which records to look up.",
        "",
        "Available record types:",
        catalogue,
        "",
        "Reply with ONE JSON object and nothing else:",
        '{"template": "<record type>", "parameters": {}}',
        "",
        "Rules:",
        "- Use only the record types listed above.",
        "- Leave `parameters` empty unless the user named something specific.",
        "- Do NOT invent a student number. Leave it out and the right pupil is found "
        "automatically.",
        '- Dates are "YYYY-MM-DD". Terms are "Term 1" or "Term 2".',
        "",
        f"Question: {question}",
        "",
        "Your JSON object:",
    ]
    return "\n".join(parts)


def build_phrasing_prompt(question: str, summary: str, role: str) -> str:
    return (
        f"The user ({role}) asked: {question}\n\n"
        f"These are the records found for them:\n{summary}\n\n"
        "Answer their question in plain English using only these records. Be brief. "
        "Quote the figures exactly as given. Do not add records that are not listed, and "
        "do not mention databases, queries or record types."
    )


def parse_selection(raw: str) -> tuple[str | None, dict]:
    """Pull `{"template": ..., "parameters": {...}}` out of whatever the model emitted.

    Reuses the brace scanner from the ReAct parser so code fences, prose and `<think>`
    blocks are handled the same way here as there.
    """
    if not raw or not raw.strip():
        return None, {}

    text = _THINK_BLOCK.sub("", raw).strip()

    candidates = [text, *(_brace_blocks(text))]
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue

        template = payload.get("template") or payload.get("record_type") or payload.get("action")
        if not isinstance(template, str) or not template.strip():
            continue

        parameters = payload.get("parameters") or payload.get("params") or {}
        if not isinstance(parameters, dict):
            parameters = {}
        # Models emit nulls for "not applicable"; drop them so validation sees absence.
        parameters = {k: v for k, v in parameters.items() if v not in (None, "", "null")}
        return template.strip().lower(), parameters

    return None, {}


def answer_from_graph(
    *,
    question: str,
    principal: Principal,
    service: RetrievalService,
    client: OllamaClient,
    model: str,
    fallback_model: str,
    system_prompt: str,
    history: list[tuple[str, str]] | None = None,
    session_id: int | None = None,
) -> tuple[str, dict]:
    """Return (answer, meta). Never raises: the caller is inside a user request."""
    history = history or []
    meta: dict = {
        "retrieval": True,
        "template": None,
        "refused": False,
        "row_count": 0,
        "citations": [],
        "model_s": 0.0,
        "graph_s": 0.0,
    }

    # --- 1. select -------------------------------------------------------------------
    try:
        selection = client.generate_with_fallback(
            model=model,
            fallback_model=fallback_model,
            prompt=build_selection_prompt(question, principal.role.value, history),
            system=system_prompt,
            temperature=0.0,  # this is a classification, not a creative task
            max_tokens=200,
        )
    except OllamaError as exc:
        logger.error("Template selection failed: %s", exc)
        meta["error"] = str(exc)[:255]
        return (
            "Sorry — the assistant is unavailable right now. Please try again shortly.",
            meta,
        )

    completion = selection.completion
    meta["model_s"] += completion.elapsed_s
    meta["model_used"] = completion.model
    meta["used_fallback"] = completion.used_fallback

    template_key, parameters = parse_selection(completion.text)
    if template_key is None:
        meta["stop_reason"] = "no_template_selected"
        return CANNOT_ANSWER, meta

    meta["template"] = template_key
    meta["parameters"] = parameters

    # --- 2. retrieve ------------------------------------------------------------------
    result: RetrievalResult = service.run(
        template_key=template_key,
        parameters=parameters,
        principal=principal,
        question=question,
        session_id=session_id,
    )
    meta["graph_s"] = round(result.elapsed_s, 3)
    meta["row_count"] = result.row_count
    meta["citations"] = result.citations
    meta["refused"] = result.refused

    if result.refused:
        # Returned verbatim. See the module docstring.
        meta["stop_reason"] = "access_refused"
        meta["refusal_reason"] = result.refusal_reason
        return result.summary, meta

    if not result.ok:
        meta["stop_reason"] = "retrieval_failed"
        meta["error"] = result.error[:255]
        # A bad template or parameter is the model's mistake, not the user's problem.
        return (
            "I could not look that up just now. Please try rephrasing, or contact the "
            "school office if it is urgent.",
            meta,
        )

    if result.is_empty:
        meta["stop_reason"] = "no_records"
        return result.summary, meta

    # --- 3. phrase --------------------------------------------------------------------
    try:
        phrasing = client.generate_with_fallback(
            model=model,
            fallback_model=fallback_model,
            prompt=build_phrasing_prompt(question, result.summary, principal.role.value),
            system=system_prompt,
            temperature=0.2,
            max_tokens=500,
        )
    except OllamaError:
        # The rows are already correct and readable; better to show them than to fail.
        meta["stop_reason"] = "phrasing_unavailable"
        return result.summary, meta

    meta["model_s"] += phrasing.completion.elapsed_s
    meta["model_used"] = phrasing.completion.model
    meta["stop_reason"] = "retrieved"
    answer = phrasing.completion.text.strip()
    return (answer or result.summary), meta
