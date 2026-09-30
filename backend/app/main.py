"""AgentSphere API entry point (Phase 1).

Run from the backend/ directory:  uvicorn app.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import agents, audit, auth, chat, documents, preferences, school

settings = get_settings()

app = FastAPI(
    title="AgentSphere API",
    version="0.3.0",
    description=(
        "No-code agent builder for school staff. Phase 1: builder platform (RO1). "
        "Phase 2: local model serving and agent framework (RO2). "
        "Phase 3a/3b: knowledge graph, RBAC, document RAG and guardrails (RO3)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(chat.router)
app.include_router(preferences.router)
app.include_router(audit.router)
app.include_router(documents.router)
app.include_router(school.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "phase": 3, "agent_runtime": settings.agent_runtime}
