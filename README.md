# AgentSphere

A no-code platform for school staff to configure AI agents for parents, teachers and
administrators. Final-year project — **Phases 1-3b**: builder platform (RO1), local model
serving and agent framework (RO2), knowledge graph with role-based access control and
document retrieval with guardrails (RO3).

## What works today

- **Accounts and sessions (FR1)** — register, sign in, sign out. Every request carries a
  JWT whose role claim (`parent` / `teacher` / `administrator`) is re-validated server-side.
- **School records** — the eight tables from Figure 3.6, in MySQL, with reads scoped by
  role: a parent sees only their own children, a teacher only their own classes.
- **Agent builder (FR3)** — a six-step wizard covering name, role, knowledge sources, tools
  and guardrails. Every option is a dropdown or checkbox; no code, prompts or identifiers.
- **Chat interface** — agent rail, transcript and composer (Figure 3.7).
- **Local models (FR5)** — answers come from Ollama running on the school's own hardware;
  nothing leaves the machine. Each agent picks a model and a fallback in the wizard, and
  failover is automatic.
- **Agent framework (FR6-FR8)** — a ReAct reason/act/observe loop, four built-in tools
  (search, calculator, file reader, sandboxed code runner), intent routing, and memory
  (conversation buffer plus standing per-role preferences).
- **Latency logging (NFR3)** — every turn is timed and logged; `nfr3_report.py` reports
  mean and p95 against the 10s/20s targets.

- **Knowledge graph (FR9)** — 95,761 triples in Apache Jena Fuseki, built from MySQL via a
  domain ontology. Questions about fees, marks, attendance, homework and timetables are
  answered with parameterised SPARQL; the model picks a template and fills parameters, it
  never writes queries.
- **Role-based access control (FR10)** — a parent sees only their own children, a teacher
  only their own classes, an administrator everything. The constraint is injected into every
  SPARQL query server-side from the session token, so no prompt can widen it. Refused
  requests are logged for administrator review (FR12).

- **Document retrieval (FR4)** — ~50 school documents (PDF, DOCX, TXT) embedded locally
  with `nomic-embed-text` and searched by meaning. Staff upload through the app; nothing is
  sent to an outside service.
- **Dual grounding (FR9)** — answers combine verified database facts with document
  passages, labelled by source. Where they disagree, the verified record wins.
- **Guardrails (FR11)** — the assistant answers only from what it retrieved, treats
  documents as data rather than instructions, masks identifiers the reader may not see, and
  refuses out-of-scope requests. Three documents in the corpus carry deliberate prompt
  injections; the assistant does not follow them.

**Evaluation is not built yet** — the 100-question set and 30 probes are Phase 4.

## Setup

Requires Python 3.11+, Node.js 18+, MySQL 8.4, Java 11+ with
[Apache Jena Fuseki 4.x](https://jena.apache.org/download/), and
[Ollama](https://ollama.com) for the model and embedding layers.

### Backend

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` — set `DATABASE_URL` and a real `JWT_SECRET`. For MySQL, create the schema
first:

```bash
mysql -u root -p < backend/sql/schema.sql
```

Pull the models (about 4.5 GB total), then load demo data and start the API:

```bash
ollama pull llama3.2:3b
ollama pull qwen3:4b
ollama pull nomic-embed-text        # embeddings for document search
.venv/bin/python seed.py            # accounts, sample agent, preferences (run first)
.venv/bin/python generate_data.py   # 300 students and ~40,000 records (Table 3.4)
.venv/bin/python -m uvicorn app.main:app --reload
```

Then start Fuseki and load the graph:

```bash
mkdir -p ~/.local/fuseki/databases/agentsphere
cd ~/.local/fuseki && FUSEKI_BASE=$HOME/.local/fuseki/run ./fuseki-server \
    --update --loc=$HOME/.local/fuseki/databases/agentsphere /agentsphere
```

```bash
cd backend
.venv/bin/python build_graph.py --upload          # MySQL -> RDF -> Fuseki
.venv/bin/python generate_documents.py --ingest   # ~50 documents -> embeddings
```

API docs: <http://localhost:8000/docs>

### Frontend

Node 24 LTS lives at `~/.local/node` on this machine and is not on the default PATH, so
add it first (put the export in `~/.zshrc` to make it permanent):

```bash
export PATH="$HOME/.local/node/bin:$PATH"
cd frontend && npm install && npm run dev
```

Opens on <http://localhost:5173> and proxies `/api` to the backend.

## Demo accounts

Password `password123` for all three:

| Role | Email |
|---|---|
| Administrator | `admin@greenfield.edu` |
| Teacher | `teacher@greenfield.edu` |
| Parent | `parent@greenfield.edu` |

Sign in as the parent to see a published agent and role-scoped records; sign in as the
teacher or administrator to reach the builder.

## Tests

```bash
cd backend
.venv/bin/python -m pytest          # 235 tests, incl. access control and prompt injection
.venv/bin/python smoke_test.py      # end-to-end, needs the API running
.venv/bin/python nfr3_report.py     # latency against the NFR3 targets
```

The pytest suite runs offline; tests needing a real model skip themselves when Ollama is
not running.

## Repository

See [CLAUDE.md](CLAUDE.md) for the architecture, the role-scoping rules, and where Phases
2–4 plug in.
