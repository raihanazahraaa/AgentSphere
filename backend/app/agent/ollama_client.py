"""Ollama HTTP client — the only place the backend talks to a model (FR5).

Two things callers get from here:

* `generate()` — one completion, with timing broken out so NFR3 can distinguish time
  spent loading a model from time spent actually generating.
* `generate_with_fallback()` — tries the agent's default model, and on timeout or any
  Ollama-side failure transparently retries on its fallback model.

**qwen3 is a thinking model.** Left alone it returns its reasoning in a separate
`thinking` field and puts clean output in `response`. Setting `think: false` makes it
reason *in-band* instead, which corrupts ReAct JSON parsing — so never disable it. The
`thinking` text is captured for debugging and kept out of the user-visible transcript.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

# A thinking model that exhausts its budget reasoning gets one retry with this much more
# room before it is treated as a failure.
_THINKING_BUDGET_MULTIPLIER = 4
_MAX_THINKING_TOKENS = 3000


class OllamaError(RuntimeError):
    """Base class for every failure that should trigger a fallback."""


class OllamaUnavailable(OllamaError):
    """Ollama is not reachable — not running, or the wrong host/port."""


class OllamaTimeout(OllamaError):
    """The model did not answer inside the configured timeout."""


class ModelNotFound(OllamaError):
    """The requested model tag is not pulled on this machine."""


class EmptyCompletion(OllamaError):
    """The model returned no text.

    Thinking models do this when they exhaust the token budget reasoning and never start
    their answer. Treating it as a failure means `generate_with_fallback` moves on to the
    other model instead of handing the user an empty reply.
    """


@dataclass
class Completion:
    """One model response plus the timings NFR3 needs."""

    text: str
    model: str
    thinking: str = ""
    # Wall-clock seconds for the whole HTTP call.
    elapsed_s: float = 0.0
    # Ollama's own breakdown, in seconds.
    load_s: float = 0.0
    eval_s: float = 0.0
    prompt_eval_s: float = 0.0
    eval_tokens: int = 0
    # True when this came from the fallback model rather than the default.
    used_fallback: bool = False
    fallback_reason: str = ""


@dataclass
class ModelAttempt:
    """Record of one model try, so a failover is visible in the logs and in meta."""

    model: str
    ok: bool
    elapsed_s: float
    error: str = ""


@dataclass
class GenerationResult:
    completion: Completion
    attempts: list[ModelAttempt] = field(default_factory=list)


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float | None = None,
        connect_timeout_s: float = 5.0,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.timeout_s = timeout_s if timeout_s is not None else settings.ollama_timeout_s
        self._timeout = httpx.Timeout(self.timeout_s, connect=connect_timeout_s)

    # ------------------------------------------------------------------ discovery

    def list_models(self) -> list[str]:
        """Model tags pulled on this machine. Empty list if Ollama is not reachable.

        The wizard calls this so staff pick from what actually exists rather than a
        hardcoded list that drifts the moment someone pulls a new model.
        """
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                return sorted(m["name"] for m in response.json().get("models", []))
        except (httpx.HTTPError, KeyError, ValueError):
            logger.warning("Could not list Ollama models at %s", self.base_url)
            return []

    def is_available(self) -> bool:
        return bool(self.list_models())

    # ----------------------------------------------------------------- generation

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 800,
        stop: list[str] | None = None,
    ) -> Completion:
        """One completion. Raises an `OllamaError` subclass on any failure.

        Thinking models (qwen3) count reasoning tokens against `num_predict`, so a budget
        that is ample for the answer can be consumed entirely by the reasoning, returning
        empty text. When that happens the same model is retried once with a larger budget
        rather than failing over — the model is working, it just needed more room.
        """
        try:
            return self._call(
                model=model, prompt=prompt, system=system, temperature=temperature,
                max_tokens=max_tokens, stop=stop,
            )
        except EmptyCompletion as exc:
            if not getattr(exc, "was_thinking", False):
                raise
            widened = min(max_tokens * _THINKING_BUDGET_MULTIPLIER, _MAX_THINKING_TOKENS)
            logger.info("%s ran out of budget thinking; retrying with %d tokens", model, widened)
            return self._call(
                model=model, prompt=prompt, system=system, temperature=temperature,
                max_tokens=widened, stop=stop,
            )

    def _call(
        self,
        *,
        model: str,
        prompt: str,
        system: str | None,
        temperature: float,
        max_tokens: int,
        stop: list[str] | None,
    ) -> Completion:
        options: dict = {"temperature": temperature, "num_predict": max_tokens}
        if stop:
            options["stop"] = stop

        body: dict = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": options,
            "keep_alive": get_settings().ollama_keep_alive,
        }
        if system:
            body["system"] = system

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(f"{self.base_url}/api/generate", json=body)
        except httpx.TimeoutException as exc:
            raise OllamaTimeout(f"{model} timed out after {self.timeout_s:.0f}s") from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc

        elapsed = time.perf_counter() - started

        if response.status_code == 404:
            raise ModelNotFound(f"Model '{model}' is not pulled. Run: ollama pull {model}")
        if response.status_code >= 400:
            raise OllamaError(f"Ollama returned {response.status_code}: {response.text[:200]}")

        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaError(f"Ollama returned a non-JSON body: {response.text[:200]}") from exc

        text = (data.get("response") or "").strip()
        thinking = (data.get("thinking") or "").strip()
        if not text:
            detail = "it spent the whole token budget thinking" if thinking else "no output"
            empty = EmptyCompletion(f"{model} returned nothing ({detail})")
            # Distinguishes "needs more room" from "genuinely produced nothing".
            empty.was_thinking = bool(thinking)
            raise empty

        return Completion(
            text=text,
            model=model,
            thinking=thinking,
            elapsed_s=elapsed,
            load_s=data.get("load_duration", 0) / 1e9,
            eval_s=data.get("eval_duration", 0) / 1e9,
            prompt_eval_s=data.get("prompt_eval_duration", 0) / 1e9,
            eval_tokens=data.get("eval_count", 0) or 0,
        )

    def generate_with_fallback(
        self,
        *,
        model: str,
        fallback_model: str | None = None,
        **kwargs,
    ) -> GenerationResult:
        """Try `model`; on any Ollama failure, try `fallback_model` once (FR5).

        Only Ollama-side failures trigger the fallback. A malformed *answer* is the
        orchestrator's problem, not a reason to switch models.
        """
        attempts: list[ModelAttempt] = []

        candidates: list[str] = [model]
        if fallback_model and fallback_model != model:
            candidates.append(fallback_model)

        last_error: OllamaError | None = None
        for index, candidate in enumerate(candidates):
            started = time.perf_counter()
            try:
                completion = self.generate(model=candidate, **kwargs)
            except OllamaError as exc:
                elapsed = time.perf_counter() - started
                attempts.append(
                    ModelAttempt(model=candidate, ok=False, elapsed_s=elapsed, error=str(exc))
                )
                last_error = exc
                logger.warning("Model %s failed (%s)", candidate, exc)
                continue

            attempts.append(
                ModelAttempt(model=candidate, ok=True, elapsed_s=completion.elapsed_s)
            )
            if index > 0:
                completion.used_fallback = True
                completion.fallback_reason = str(last_error) if last_error else "default failed"
                logger.info("Fell back to %s", candidate)
            return GenerationResult(completion=completion, attempts=attempts)

        raise last_error or OllamaError("No model could be reached")


def get_client() -> OllamaClient:
    return OllamaClient()
