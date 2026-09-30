"""The answering pipeline, callable without the UI.

Phase 4's evaluation harness needs to run a few hundred questions and inspect what happened
at each stage. Going through HTTP and the chat router would drag in sessions, agents and
transcript rows that have nothing to do with answer quality, so the whole path is exposed
here as one function:

    from app.pipeline import AnswerPipeline, principal_for_user

    pipeline = AnswerPipeline(db)
    outcome = pipeline.answer("When is half term?", principal=principal_for_user(user))
    outcome.answer, outcome.citations, outcome.guardrails_triggered, outcome.stages

`PipelineOutcome` carries the intermediate state — which route was taken, what the graph
returned, which passages were retrieved, what the model said before guardrails ran — so an
evaluation can score retrieval and generation separately instead of only seeing the final
string. `routers/chat.py` uses the same class, so what is evaluated is what users get.

Figure 3.4, steps 4 and 9: retrieval (4) is `_gather`, guardrails (9) is
`apply_output_guardrails` in `_generate`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .agent.grounding import GroundedContext, build_context
from .agent.guardrails import (
    NO_INFORMATION,
    GuardrailReport,
    apply_output_guardrails,
    build_grounded_prompt,
    context_contains_injection,
    safe_mode_preamble,
)
from .agent.intent import Route, route_query
from .agent.ollama_client import OllamaClient, OllamaError
from .agent.prompts import build_system_prompt
from .config import get_settings
from .graph.rbac import Principal
from .graph.retrieval import RetrievalResult, RetrievalService
from .models import AppUser
from .rag.retrieve import DocumentResult, DocumentRetriever

logger = logging.getLogger(__name__)


def principal_for_user(user: AppUser) -> Principal:
    return Principal(
        user_id=user.id, role=user.role, email=user.email, full_name=user.full_name
    )


@dataclass
class PipelineOutcome:
    """Everything one question produced, stage by stage."""

    answer: str
    question: str = ""
    route: str = ""
    ok: bool = True

    # --- retrieval (Figure 3.4 step 4) -------------------------------------------------
    records: RetrievalResult | None = None
    documents: DocumentResult | None = None
    context: GroundedContext | None = None
    used_records: bool = False
    used_documents: bool = False

    # --- generation --------------------------------------------------------------------
    raw_answer: str = ""
    model_used: str = ""
    used_fallback: bool = False

    # --- guardrails (Figure 3.4 step 9) ------------------------------------------------
    refused: bool = False
    refusal_reason: str = ""
    guardrails_triggered: list[str] = field(default_factory=list)
    masked_identifiers: list[str] = field(default_factory=list)
    injection_in_context: bool = False

    citations: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    error: str = ""

    # --- timings ------------------------------------------------------------------------
    total_s: float = 0.0
    graph_s: float = 0.0
    documents_s: float = 0.0
    model_s: float = 0.0

    stages: dict = field(default_factory=dict)

    @property
    def said_no_information(self) -> bool:
        return NO_INFORMATION.lower().rstrip(".") in self.answer.lower()

    def to_meta(self) -> dict:
        """Flattened for `chat_message.meta` and the NFR3 log."""
        return {
            "route": self.route,
            # Which SPARQL template answered, when the graph was consulted.
            "template": self.records.template if self.records else None,
            "used_records": self.used_records,
            "used_documents": self.used_documents,
            "citations": self.citations,
            "conflicts": self.conflicts,
            "refused": self.refused,
            "refusal_reason": self.refusal_reason,
            "guardrails": self.guardrails_triggered,
            "masked": len(self.masked_identifiers),
            "injection_in_context": self.injection_in_context,
            "model_used": self.model_used,
            "used_fallback": self.used_fallback,
            "total_s": round(self.total_s, 3),
            "graph_s": round(self.graph_s, 3),
            "documents_s": round(self.documents_s, 3),
            "model_s": round(self.model_s, 3),
            "succeeded": self.ok and not self.error,
        }


class AnswerPipeline:
    """Route -> retrieve (graph and/or documents) -> ground -> generate -> guard."""

    def __init__(
        self,
        db: Session,
        *,
        client: OllamaClient | None = None,
        retrieval: RetrievalService | None = None,
        documents: DocumentRetriever | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
    ) -> None:
        settings = get_settings()
        self.db = db
        self.client = client or OllamaClient()
        self.retrieval = retrieval
        self.documents = documents
        self.model = model or settings.default_model
        self.fallback_model = fallback_model or settings.fallback_model
        self.max_context_chars = settings.rag_max_context_chars

    # ------------------------------------------------------------------ public

    def answer(
        self,
        question: str,
        *,
        principal: Principal,
        agent_name: str = "School Assistant",
        role_instructions: str = "Answer questions about the school.",
        history: list[tuple[str, str]] | None = None,
        session_id: int | None = None,
        preferences: list[str] | None = None,
    ) -> PipelineOutcome:
        started = time.perf_counter()
        outcome = PipelineOutcome(answer="", question=question)

        decision = route_query(question, [])
        outcome.route = decision.route.value
        outcome.stages["routing"] = decision.reason

        records, documents = self._gather(question, principal, decision.route, session_id)
        outcome.records = records
        outcome.documents = documents
        outcome.graph_s = records.elapsed_s if records else 0.0
        outcome.documents_s = documents.elapsed_s if documents else 0.0

        # A refusal from the graph layer is final and is never re-phrased: the wording is
        # written for a parent to read, and a 3B model asked to rephrase it may invent a
        # reason or imply the data exists.
        if records is not None and records.refused:
            outcome.answer = records.summary
            outcome.refused = True
            outcome.refusal_reason = records.refusal_reason
            outcome.guardrails_triggered.append("graph_rbac_refusal")
            outcome.total_s = time.perf_counter() - started
            outcome.stages["stopped_at"] = "graph_refusal"
            return outcome

        context = build_context(
            records=records, documents=documents, max_chars=self.max_context_chars
        )
        outcome.context = context
        outcome.used_records = context.has_records
        outcome.used_documents = context.has_documents
        outcome.citations = context.citations
        outcome.conflicts = [c.describe() for c in context.conflicts]
        outcome.injection_in_context = context_contains_injection(context.text)
        outcome.stages["context_chars"] = len(context.text)

        if context.is_empty:
            outcome.answer = NO_INFORMATION
            outcome.guardrails_triggered.append("no_context")
            outcome.total_s = time.perf_counter() - started
            outcome.stages["stopped_at"] = "no_context"
            return outcome

        self._generate(question, principal, context, outcome, agent_name,
                       role_instructions, preferences)
        outcome.total_s = time.perf_counter() - started
        return outcome

    # ------------------------------------------------------------------ stages

    def _gather(
        self,
        question: str,
        principal: Principal,
        route: Route,
        session_id: int | None,
    ) -> tuple[RetrievalResult | None, DocumentResult | None]:
        """Figure 3.4 step 4.

        Documents are retrieved for *every* question, not only policy ones: "how much do I
        owe and when is it due" needs the balance from the graph and the payment terms from
        the circular. The graph is queried only when the router says the question is about
        records, because a template selection costs a model call.
        """
        records: RetrievalResult | None = None
        documents: DocumentResult | None = None

        if route is Route.RETRIEVAL and self.retrieval is not None:
            records = self._query_graph(question, principal, session_id)

        if self.documents is not None:
            documents = self.documents.retrieve(question, role=principal.role.value)

        return records, documents

    def _query_graph(
        self, question: str, principal: Principal, session_id: int | None
    ) -> RetrievalResult | None:
        """Pick a template with the model, then run it through the scoped, audited path."""
        from .agent.graph_retrieval import build_selection_prompt, parse_selection

        try:
            selection = self.client.generate_with_fallback(
                model=self.model,
                fallback_model=self.fallback_model,
                prompt=build_selection_prompt(question, principal.role.value, []),
                system="You choose which school records to look up.",
                temperature=0.0,
                max_tokens=200,
            )
        except OllamaError as exc:
            logger.warning("Template selection failed: %s", exc)
            return None

        template_key, parameters = parse_selection(selection.completion.text)
        if template_key is None:
            return None

        return self.retrieval.run(
            template_key=template_key,
            parameters=parameters,
            principal=principal,
            question=question,
            session_id=session_id,
        )

    def _generate(
        self,
        question: str,
        principal: Principal,
        context: GroundedContext,
        outcome: PipelineOutcome,
        agent_name: str,
        role_instructions: str,
        preferences: list[str] | None,
    ) -> None:
        system_prompt = (
            build_system_prompt(
                agent_name=agent_name,
                role_instructions=role_instructions,
                role=principal.role.value,
                user_name=principal.full_name or "the user",
                preferences=preferences,
            )
            + "\n\n"
            + safe_mode_preamble(principal)
        )
        prompt = build_grounded_prompt(question, context.text, principal)

        try:
            generation = self.client.generate_with_fallback(
                model=self.model,
                fallback_model=self.fallback_model,
                prompt=prompt,
                system=system_prompt,
                temperature=0.1,
                max_tokens=500,
            )
        except OllamaError as exc:
            outcome.ok = False
            outcome.error = str(exc)[:255]
            outcome.answer = (
                "Sorry — the assistant is unavailable right now. Please try again shortly."
            )
            outcome.stages["stopped_at"] = "model_unavailable"
            return

        completion = generation.completion
        outcome.raw_answer = completion.text
        outcome.model_used = completion.model
        outcome.used_fallback = completion.used_fallback
        outcome.model_s = completion.elapsed_s

        # --- Figure 3.4 step 9 ---------------------------------------------------------
        report: GuardrailReport = apply_output_guardrails(
            completion.text,
            principal=principal,
            permitted_identifiers=context.permitted_identifiers,
            context_text=context.text,
        )
        outcome.answer = report.answer
        outcome.refused = report.refused
        outcome.refusal_reason = report.refusal_reason
        outcome.guardrails_triggered.extend(report.triggered)
        outcome.masked_identifiers = report.masked_identifiers
        outcome.stages["guardrails"] = report.triggered

        if report.refused:
            self._audit_guardrail_refusal(principal, question, report)

    def _audit_guardrail_refusal(
        self, principal: Principal, question: str, report: GuardrailReport
    ) -> None:
        """Log a step-9 refusal to the same table Phase 3a writes (FR11 + FR12).

        One table, so an administrator reviewing refusals sees graph-scope refusals and
        guardrail refusals together rather than having to know there are two kinds.
        """
        from .models import AccessAudit

        try:
            self.db.add(
                AccessAudit(
                    user_id=principal.user_id,
                    role=principal.role.value,
                    template="guardrail",
                    question=question,
                    parameters={"triggered": report.triggered},
                    allowed=False,
                    refusal_reason=report.refusal_reason[:255],
                    result_count=0,
                )
            )
            self.db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Could not write guardrail refusal to access_audit")
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass


def build_pipeline(db: Session) -> AnswerPipeline:
    """Wire the pipeline with the real graph and document backends."""
    from .graph import get_client as graph_client

    return AnswerPipeline(
        db,
        retrieval=RetrievalService(graph_client(), db),
        documents=DocumentRetriever(db),
    )
