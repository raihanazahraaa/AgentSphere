"""Application settings, loaded from environment / backend/.env."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved against this file, not the working directory, so the app and the test suite
# find the same .env whether they are started from backend/ or the repository root.
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    app_name: str = "AgentSphere"
    database_url: str = "mysql+pymysql://agentsphere:changeme@localhost:3306/agentsphere"

    # HS256 needs at least 32 bytes of key material (RFC 7518 §3.2).
    jwt_secret: str = "agentsphere-dev-secret-change-me-before-deploying"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480

    cors_origins: str = "http://localhost:5173"

    # "local" routes chat through Ollama; "stub" keeps the Phase 1 placeholder.
    agent_runtime: str = "local"

    # --- Phase 2: local model serving (FR5) -------------------------------------------
    ollama_base_url: str = "http://localhost:11434"
    # Generous: a cold model load on an 8 GB machine can take ~5s before a token appears.
    ollama_timeout_s: float = 90.0

    # Defaults when an agent has not picked its own (the wizard normally sets both).
    # Measured on this machine (M3, 8 GB): llama3.2:3b answers a ReAct step in ~2.3s,
    # qwen3:4b in ~10s because it is a thinking model and spends its whole token budget
    # reasoning. A 7-8B model would also swap against 8 GB of shared memory. So the fast
    # non-thinking model leads and the slower, stronger one is the fallback (FR5, NFR3).
    default_model: str = "llama3.2:3b"
    fallback_model: str = "qwen3:4b"

    # Holds a loaded model in memory between turns, saving ~2s of reload per request.
    ollama_keep_alive: str = "10m"

    # --- Phase 2: ReAct orchestrator ---------------------------------------------------
    react_max_iterations: int = 4
    # Turns of history kept in the per-session buffer (FR8).
    memory_buffer_turns: int = 6

    # Documents the search and file_reader tools may read. Relative to backend/.
    workspace_dir: str = "workspace"

    # --- Phase 3a: knowledge graph (FR9) ------------------------------------------------
    fuseki_base_url: str = "http://localhost:3030"
    fuseki_dataset: str = "agentsphere"
    sparql_timeout_s: float = 20.0
    # Hard ceiling on rows returned to the model, whatever a template asks for. Protects
    # both the context window and the NFR3 latency budget.
    sparql_max_rows: int = 50

    # --- Phase 3b: document RAG (FR4, FR9) ----------------------------------------------
    # Local embeddings only; no external API is ever called with school documents.
    embedding_model: str = "nomic-embed-text"
    embedding_batch_size: int = 32
    # Where uploaded and generated documents live, relative to backend/.
    corpus_dir: str = "corpus"
    # Retrieval: how many passages reach the context block, and the floor below which a
    # match is treated as noise rather than evidence.
    rag_top_k: int = 4
    rag_min_score: float = 0.45
    # Hard cap on document text in one prompt. A 3B model given 8k characters of context
    # loses the question in the middle of it.
    rag_max_context_chars: int = 3500

    # --- Phase 2: code_runner sandbox --------------------------------------------------
    code_runner_timeout_s: float = 5.0
    code_runner_max_output_chars: int = 4000

    @field_validator("jwt_secret")
    @classmethod
    def _reject_short_secret(cls, value: str) -> str:
        if len(value.encode()) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 bytes for HS256. "
                "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
            )
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
