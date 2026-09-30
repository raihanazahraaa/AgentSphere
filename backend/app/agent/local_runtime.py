"""LocalModelRuntime — the Phase 2 replacement for the Phase 1 stub.

Assembles the pieces: route the query (FR6), build the agent's tool registry from its
wizard config (FR7), run the ReAct loop against Ollama with fallback (FR5), and return
timings the caller logs for NFR3.

Memory (FR8) is read by the caller and passed in, so this module stays free of database
imports and can be tested with no database at all.
"""

from __future__ import annotations

import time

from ..config import get_settings
from ..graph import Principal
from ..graph.retrieval import RetrievalService
from ..models import UserRole
from .graph_retrieval import answer_from_graph
from .intent import Route, route_query
from .ollama_client import OllamaClient, OllamaError
from .prompts import build_direct_prompt, build_system_prompt
from .react import SAFE_FALLBACK, ReActOrchestrator
from .runtime import AgentInvocation, AgentReply, RoleContext
from .tools import build_registry


class LocalModelRuntime:
    """Runs agents against locally-served models via Ollama."""

    name = "local"

    def __init__(
        self,
        client: OllamaClient | None = None,
        retrieval: RetrievalService | None = None,
        pipeline=None,
    ) -> None:
        self.client = client or OllamaClient()
        # Supplied by the chat router, which owns the database session. None means the
        # retrieval branch is unavailable rather than silently answering without scope.
        self.retrieval = retrieval
        # Phase 3b: the grounded + guarded path. The chat UI and the Phase 4 evaluation
        # harness go through the same `AnswerPipeline`, so what is measured is what users
        # get. Without one, the runtime falls back to the Phase 3a graph-only branch.
        self.pipeline = pipeline
        self.settings = get_settings()

    def generate(
        self,
        *,
        invocation: AgentInvocation,
        history: list[tuple[str, str]],
        message: str,
        role: RoleContext,
    ) -> AgentReply:
        started = time.perf_counter()

        model = invocation.model or self.settings.default_model
        fallback = invocation.fallback_model or self.settings.fallback_model

        registry = build_registry(invocation.tools)
        decision = route_query(message, registry.keys)

        system_prompt = build_system_prompt(
            agent_name=invocation.agent_name,
            role_instructions=invocation.role_instructions,
            role=role.role,
            user_name=role.full_name,
            preferences=invocation.preferences,
        )

        base_meta = {
            "runtime": self.name,
            "stub": False,
            "agent_id": invocation.agent_id,
            "role": role.role,
            "route": decision.route.value,
            "route_reason": decision.reason,
            "model_requested": model,
            "tools_available": registry.keys,
            # Phase 3 fills this in on the retrieval path.
            "citations": [],
        }

        # Phase 3b: anything that is not a tool-using question goes through the grounded
        # pipeline, which retrieves documents *and* records, labels them, and applies the
        # step-9 guardrails. The ReAct branch below keeps the tool path unchanged.
        if self.pipeline is not None and decision.route is not Route.TOOLS:
            return self._pipeline_answer(
                message, history, role, invocation, base_meta, started
            )

        if decision.route is Route.RETRIEVAL:
            return self._retrieval_answer(
                message, history, system_prompt, model, fallback, role, base_meta, started
            )

        if decision.route is Route.DIRECT or not registry.keys:
            return self._direct_answer(
                message, history, system_prompt, model, fallback, base_meta, started
            )

        return self._react_answer(
            message, history, system_prompt, model, fallback, registry, base_meta, started,
            suggested_tools=decision.suggested_tools,
        )

    # ------------------------------------------------------------------ branches

    def _pipeline_answer(
        self,
        message: str,
        history: list[tuple[str, str]],
        role: RoleContext,
        invocation: AgentInvocation,
        meta: dict,
        started: float,
    ) -> AgentReply:
        """Phase 3b: dual-grounded, guardrailed answer via the shared pipeline."""
        principal = Principal(
            user_id=role.user_id,
            role=UserRole(role.role),
            email=role.email,
            full_name=role.full_name,
        )
        outcome = self.pipeline.answer(
            message,
            principal=principal,
            agent_name=invocation.agent_name,
            role_instructions=invocation.role_instructions,
            history=history,
            session_id=role.session_id,
            preferences=invocation.preferences,
        )

        meta = {
            **meta,
            **outcome.to_meta(),
            "iterations": 1,
            "tool_calls": [],
            "stop_reason": outcome.stages.get("stopped_at", "grounded"),
        }
        return AgentReply(content=outcome.answer, meta=meta)

    def _retrieval_answer(
        self,
        message: str,
        history: list[tuple[str, str]],
        system_prompt: str,
        model: str,
        fallback: str,
        role: RoleContext,
        meta: dict,
        started: float,
    ) -> AgentReply:
        """Phase 3a: answer from the knowledge graph, scoped by the caller's role.

        The runtime holds no database session, so the caller supplies the retrieval
        service. Without one — a unit test, or a deployment with no graph — this falls
        back to saying so rather than guessing.
        """
        if self.retrieval is None:
            meta = {
                **meta, "model_used": "none", "used_fallback": False, "iterations": 0,
                "tool_calls": [], "stop_reason": "retrieval_unavailable",
                "total_s": round(time.perf_counter() - started, 3),
                "model_s": 0.0, "tool_s": 0.0, "succeeded": False,
            }
            return AgentReply(
                content=(
                    "I cannot look up school records at the moment. Please contact the "
                    "school office."
                ),
                meta=meta,
            )

        principal = Principal(
            user_id=role.user_id,
            role=UserRole(role.role),
            email=role.email,
            full_name=role.full_name,
        )
        answer, retrieval_meta = answer_from_graph(
            question=message,
            principal=principal,
            service=self.retrieval,
            client=self.client,
            model=model,
            fallback_model=fallback,
            system_prompt=system_prompt,
            history=history,
            session_id=role.session_id,
        )

        meta = {
            **meta,
            **retrieval_meta,
            "model_used": retrieval_meta.get("model_used", "none"),
            "used_fallback": retrieval_meta.get("used_fallback", False),
            "iterations": 1,
            "tool_calls": [],
            "total_s": round(time.perf_counter() - started, 3),
            "model_s": round(retrieval_meta.get("model_s", 0.0), 3),
            "tool_s": round(retrieval_meta.get("graph_s", 0.0), 3),
            # A refusal is a correct outcome, not a failure. Counting refusals as failures
            # would make the NFR3 success rate fall every time RBAC did its job.
            "succeeded": not bool(retrieval_meta.get("error")),
        }
        return AgentReply(content=answer, meta=meta)

    def _direct_answer(
        self,
        message: str,
        history: list[tuple[str, str]],
        system_prompt: str,
        model: str,
        fallback: str,
        meta: dict,
        started: float,
    ) -> AgentReply:
        prompt = build_direct_prompt(question=message, history=history)
        try:
            generation = self.client.generate_with_fallback(
                model=model, fallback_model=fallback, prompt=prompt, system=system_prompt,
                temperature=0.3,
            )
        except OllamaError as exc:
            return self._unavailable(meta, started, str(exc), model)

        completion = generation.completion
        meta = {
            **meta,
            "model_used": completion.model,
            "used_fallback": completion.used_fallback,
            "fallback_reason": completion.fallback_reason,
            "iterations": 1,
            "tool_calls": [],
            "stop_reason": "direct",
            "total_s": round(time.perf_counter() - started, 3),
            "model_s": round(completion.elapsed_s, 3),
            "tool_s": 0.0,
            "succeeded": True,
        }
        return AgentReply(content=completion.text or SAFE_FALLBACK, meta=meta)

    def _react_answer(
        self,
        message: str,
        history: list[tuple[str, str]],
        system_prompt: str,
        model: str,
        fallback: str,
        registry,
        meta: dict,
        started: float,
        suggested_tools: list[str] | None = None,
    ) -> AgentReply:
        orchestrator = ReActOrchestrator(
            client=self.client,
            registry=registry,
            model=model,
            fallback_model=fallback,
            max_iterations=self.settings.react_max_iterations,
        )
        result = orchestrator.run(
            question=message,
            system_prompt=system_prompt,
            history=history,
            suggested_tools=suggested_tools,
        )

        meta = {
            **meta,
            "model_used": result.model,
            "used_fallback": result.used_fallback,
            "fallback_reason": result.fallback_reason,
            "iterations": result.iterations,
            "tool_calls": result.tool_calls,
            "stop_reason": result.stop_reason,
            "total_s": round(time.perf_counter() - started, 3),
            "model_s": round(result.model_s, 3),
            "tool_s": round(result.tool_s, 3),
            "succeeded": result.succeeded,
            # Trimmed trace: enough to debug a bad answer without bloating the row.
            "steps": [
                {
                    "iteration": step.iteration,
                    "action": step.action,
                    "input": step.action_input[:200],
                    "observation": step.observation[:300],
                    "ok": step.ok,
                    "repaired": step.repaired,
                }
                for step in result.steps
            ],
        }
        return AgentReply(content=result.answer, meta=meta)

    def _unavailable(
        self, meta: dict, started: float, error: str, model: str
    ) -> AgentReply:
        meta = {
            **meta,
            "model_used": "none",
            "used_fallback": False,
            "iterations": 0,
            "tool_calls": [],
            "stop_reason": "model_unavailable",
            "total_s": round(time.perf_counter() - started, 3),
            "model_s": 0.0,
            "tool_s": 0.0,
            "succeeded": False,
            "error": error[:255],
        }
        return AgentReply(
            content=(
                "Sorry — the assistant is unavailable right now. Please try again shortly."
            ),
            meta=meta,
        )
