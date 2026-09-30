"""Local embeddings via Ollama (FR4).

No external embedding API is ever called: the whole point of the project is that a school's
documents stay on the school's hardware. `OllamaClient` in the agent package handles
generation; this is the embedding half, kept separate because it has different batching,
timeout and failure behaviour — an embedding failure during ingestion should stop the
ingest, whereas a generation failure during chat should fall back to another model.

`nomic-embed-text` returns 768-dimension vectors that are already L2-normalised, so cosine
similarity reduces to a dot product. `store.py` relies on that; if the model is swapped for
one that does not normalise, normalise here rather than there.
"""

from __future__ import annotations

import logging

import httpx
import numpy as np

from ..config import get_settings

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768


class EmbeddingError(RuntimeError):
    """The embedding model could not be reached or returned something unusable."""


class EmbeddingClient:
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        settings = get_settings()
        self.model = model or settings.embedding_model
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.timeout_s = timeout_s
        self.batch_size = settings.embedding_batch_size

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                names = {m["name"] for m in response.json().get("models", [])}
            # Ollama reports "nomic-embed-text:latest" for a bare "nomic-embed-text" pull.
            return any(n == self.model or n.startswith(f"{self.model}:") for n in names)
        except (httpx.HTTPError, KeyError, ValueError):
            return False

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed a list of texts. Returns shape (len(texts), EMBEDDING_DIM), float32.

        Batched because per-call overhead dominates: 32 chunks in one request take about
        as long as two chunks in two requests.
        """
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors.extend(self._embed_batch(batch))

        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.shape[0] != len(texts):
            raise EmbeddingError(
                f"Expected {len(texts)} embeddings, got {matrix.shape[0]}"
            )
        return matrix

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                response = client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": batch},
                )
        except httpx.TimeoutException as exc:
            raise EmbeddingError(f"{self.model} timed out embedding {len(batch)} chunks") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc

        if response.status_code == 404:
            raise EmbeddingError(
                f"Embedding model '{self.model}' is not pulled. "
                f"Run: ollama pull {self.model}"
            )
        if response.status_code >= 400:
            raise EmbeddingError(
                f"Ollama returned {response.status_code}: {response.text[:200]}"
            )

        try:
            embeddings = response.json()["embeddings"]
        except (ValueError, KeyError) as exc:
            raise EmbeddingError("Ollama returned an unexpected embedding payload") from exc

        return embeddings


def to_blob(vector: np.ndarray) -> bytes:
    """float32 little-endian, so the stored bytes are portable across machines."""
    return np.asarray(vector, dtype="<f4").tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


def get_client() -> EmbeddingClient:
    return EmbeddingClient()
