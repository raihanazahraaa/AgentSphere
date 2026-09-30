"""ReAct orchestrator: reason -> act -> observe, until an answer or the iteration cap.

Failure handling is the whole point of this module. A 3-4B model will, routinely:
  * wrap its JSON in prose or code fences        -> tolerated by action_parser
  * emit something unparseable                   -> one repair prompt, then bail out safely
  * invent a tool that does not exist            -> fed back as an Observation to correct
  * loop on the same failing call                -> repetition guard ends the turn
  * never converge                               -> iteration cap, then summarise honestly

Every one of those paths ends with a usable reply to the user rather than an exception,
because this runs inside a request the user is waiting on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .action_parser import Action, ActionParseError, parse_action
from .ollama_client import Completion, OllamaClient, OllamaError
from .prompts import build_react_prompt, build_repair_prompt
from .tools.base import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

# Shown when the model cannot produce anything usable. Deliberately plain: a parent
# should not see "malformed JSON".
SAFE_FALLBACK = (
    "Sorry — I could not work that out just now. Please try rephrasing your question, "
    "or contact the school office if it is urgent."
)


@dataclass
class Step:
    """One pass through the loop, kept for the transcript and for Phase 4 evaluation."""

    iteration: int
    action: str
    action_input: str
    observation: str = ""
    ok: bool = True
    model: str = ""
    model_s: float = 0.0
    tool_s: float = 0.0
    parse_error: str = ""
    repaired: bool = False


@dataclass
class ReActResult:
    answer: str
    steps: list[Step] = field(default_factory=list)
    # Seconds spent inside the model, as opposed to inside tools (NFR3).
    model_s: float = 0.0
    tool_s: float = 0.0
    iterations: int = 0
    # The model that produced the final answer.
    model: str = ""
    used_fallback: bool = False
    fallback_reason: str = ""
    stop_reason: str = "final_answer"
    tool_calls: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """True when the user got a real answer, however the loop arrived at it.

        Judged on the answer rather than the stop reason: `repetition_guard` and
        `max_iterations` both salvage a genuine answer from what the tools returned, and
        counting those as failures would overstate the failure rate in the NFR3 report.
        """
        if self.stop_reason in {"model_unavailable", "unparseable_output"}:
            return False
        return bool(self.answer.strip()) and self.answer != SAFE_FALLBACK


class ReActOrchestrator:
    def __init__(
        self,
        *,
        client: OllamaClient,
        registry: ToolRegistry,
        model: str,
        fallback_model: str | None = None,
        max_iterations: int = 4,
        temperature: float = 0.1,
    ) -> None:
        self.client = client
        self.registry = registry
        self.model = model
        self.fallback_model = fallback_model
        self.max_iterations = max_iterations
        self.temperature = temperature

    def run(
        self,
        *,
        question: str,
        system_prompt: str,
        history: list[tuple[str, str]] | None = None,
        suggested_tools: list[str] | None = None,
    ) -> ReActResult:
        history = history or []
        result = ReActResult(answer="", model=self.model)
        scratchpad: list[str] = []
        recent_calls: list[str] = []

        for iteration in range(1, self.max_iterations + 1):
            result.iterations = iteration
            prompt = build_react_prompt(
                registry=self.registry,
                question=question,
                history=history,
                scratchpad="\n".join(scratchpad),
                # Only on the first pass: once tools have run, the scratchpad is the
                # better guide and a stale hint just pushes it to repeat itself.
                suggested_tools=suggested_tools if iteration == 1 else None,
            )

            step = Step(iteration=iteration, action="", action_input="")
            try:
                completion = self._generate(prompt, system_prompt, result)
            except OllamaError as exc:
                # Both models are down. Nothing to salvage; tell the user plainly.
                logger.error("All models failed: %s", exc)
                result.answer = (
                    "Sorry — the assistant is unavailable right now. Please try again "
                    "shortly."
                )
                result.stop_reason = "model_unavailable"
                return result

            step.model = completion.model
            step.model_s = completion.elapsed_s

            action, parse_error, repaired = self._parse_with_repair(
                completion, system_prompt, result, step
            )
            step.repaired = repaired

            if action is None:
                # Two failed parses in a row: stop rather than burn more iterations.
                step.action = "unparseable"
                step.parse_error = parse_error
                step.ok = False
                result.steps.append(step)
                result.answer = SAFE_FALLBACK
                result.stop_reason = "unparseable_output"
                return result

            step.action = action.name
            step.action_input = action.input

            if action.is_final:
                result.steps.append(step)
                result.answer = action.input.strip() or SAFE_FALLBACK
                result.stop_reason = "final_answer"
                return result

            # --- act -------------------------------------------------------------------
            signature = f"{action.name}::{action.input}"
            repeats = recent_calls.count(signature)

            if repeats >= 2:
                # Nudged once already and still looping. Salvage what the tools found.
                step.observation = "(stopped — the same tool call was repeated)"
                step.ok = False
                result.steps.append(step)
                result.answer = self._summarise(question, scratchpad, system_prompt, result)
                result.stop_reason = "repetition_guard"
                return result

            if repeats == 1:
                # Don't re-run the tool: the result is identical by construction, and the
                # call costs latency the NFR3 budget cannot spare. Nudge instead, and let
                # the model spend its next iteration on something useful.
                nudge = (
                    f"You already ran {action.name} with exactly that input and the result "
                    "is above. Do not repeat it. Either use a different tool, or give your "
                    "final_answer using what you already have."
                )
                step.observation = nudge
                step.ok = False
                recent_calls.append(signature)
                result.steps.append(step)
                scratchpad.append(f"Step {iteration}: {nudge}")
                continue

            recent_calls.append(signature)

            tool_result: ToolResult = self.registry.run(action.name, action.input)
            step.observation = tool_result.output
            step.ok = tool_result.ok
            step.tool_s = tool_result.elapsed_s
            result.tool_s += tool_result.elapsed_s
            result.tool_calls.append(action.name)
            result.steps.append(step)

            scratchpad.append(
                f'Step {iteration}: used {action.name} with input "{action.input}"\n'
                f"Result: {tool_result.output}"
            )

        # --- ran out of iterations ------------------------------------------------------
        result.answer = self._summarise(question, scratchpad, system_prompt, result)
        result.stop_reason = (
            "max_iterations_summarised" if result.answer != SAFE_FALLBACK else "max_iterations"
        )
        return result

    # ------------------------------------------------------------------ internals

    def _generate(self, prompt: str, system_prompt: str, result: ReActResult) -> Completion:
        """One generation, with model failover, accumulating timings onto `result`."""
        generation = self.client.generate_with_fallback(
            model=self.model,
            fallback_model=self.fallback_model,
            prompt=prompt,
            system=system_prompt,
            temperature=self.temperature,
        )
        completion = generation.completion
        result.model_s += completion.elapsed_s
        result.model = completion.model
        if completion.used_fallback:
            result.used_fallback = True
            result.fallback_reason = completion.fallback_reason
        return completion

    def _parse_with_repair(
        self,
        completion: Completion,
        system_prompt: str,
        result: ReActResult,
        step: Step,
    ) -> tuple[Action | None, str, bool]:
        """Parse the completion; on failure ask once for valid JSON, then give up."""
        try:
            return parse_action(completion.text), "", False
        except ActionParseError as first_error:
            logger.info("Unparseable model output, retrying once: %s", first_error)

        try:
            repair = self.client.generate_with_fallback(
                model=self.model,
                fallback_model=self.fallback_model,
                prompt=build_repair_prompt(completion.text),
                system=system_prompt,
                temperature=0.0,  # deterministic: we want the format, not creativity
            )
        except OllamaError as exc:
            return None, f"repair generation failed: {exc}", True

        repaired_completion = repair.completion
        result.model_s += repaired_completion.elapsed_s
        step.model_s += repaired_completion.elapsed_s
        if repaired_completion.used_fallback:
            result.used_fallback = True
            result.fallback_reason = repaired_completion.fallback_reason

        try:
            return parse_action(repaired_completion.text), "", True
        except ActionParseError as second_error:
            return None, str(second_error), True

    def _summarise(
        self, question: str, scratchpad: list[str], system_prompt: str, result: ReActResult
    ) -> str:
        """Last chance: ask for a plain answer from what the tools already returned.

        Better than SAFE_FALLBACK when the tools did useful work but the model never
        declared `final_answer`.
        """
        if not scratchpad:
            return SAFE_FALLBACK

        prompt = (
            f"Question: {question}\n\n"
            "What you found:\n" + "\n".join(scratchpad) + "\n\n"
            "Answer the question in plain English using only what you found above. "
            "Reply with the answer itself — no JSON, no tool calls."
        )
        try:
            generation = self.client.generate_with_fallback(
                model=self.model,
                fallback_model=self.fallback_model,
                prompt=prompt,
                system=system_prompt,
                temperature=self.temperature,
            )
        except OllamaError:
            return SAFE_FALLBACK

        completion = generation.completion
        result.model_s += completion.elapsed_s
        answer = completion.text.strip()
        return answer or SAFE_FALLBACK
