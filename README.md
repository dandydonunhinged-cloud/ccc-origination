# CCC Origination

Full investor-loan origination system. From lead to close to next deal.

## What it is

The system of record for an investor-loan business (DSCR, bridge, fix & flip,
construction, commercial, SBA). Not a contact pipeline — a deal pipeline.

## Stack

- **FastAPI** + SQLAlchemy 2.0 + SQLite (dev) / Postgres (prod)
- **Server-rendered** Jinja2 templates (no SPA, no build step)
- **bcrypt** for passwords + signed cookies + magic links for borrower auth
- **DO Spaces** for document storage (presigned PUT uploads from the browser)
- **Ollama** for local embedding (nomic-embed-text, bge-large) and 5-pass rerank
- **sqlite-vec** for vector similarity (no Postgres dependency)
- Bleeding-edge AI matching: vector similarity over historical funded deals +
  LLM re-rank with full corpus context + outcome feedback loop

## Architecture

```
app/
├── app.py              # FastAPI app, middleware, route registration
├── config.py           # env vars + paths
├── db.py               # SQLAlchemy engine + session factory
├── auth.py             # bcrypt, signed cookies, magic links
├── models.py           # full data model (Deal, Borrower, Property, Entity,
│                       #   Document, Condition, Note, Event, Lender, Product,
│                       #   Submission, Closing, CompPayment, DealEmbedding,
│                       #   DealOutcome, RateSheetSnapshot, PlaidLink, Message,
│                       #   ScheduleTask, MagicLink, Session)
├── scenario.py         # legacy deterministic local scorer
├── scenario_ai.py      # the entry point: embed + vector KNN + 5-pass rerank + merge
├── embeddings.py       # Ollama embed + sqlite-vec + Python fallback
├── llm_rerank.py       # 5-pass LLM rerank with full corpus context
├── playbooks.py        # per-condition-type response playbooks
├── outcomes.py         # outcome loop: DealOutcome writes feed back the embedding corpus
├── snapshots.py        # rate sheet snapshots + Plaid stub
├── storage.py          # DO Spaces presigned upload/download
├── seed.py             # lender matrix seed (11 lenders, 15 products)
└── routes/
    ├── borrower.py     # /submit/, /portal/* (borrower-facing)
    ├── broker.py       # /admin/* (Don's command surface)
    ├── webhooks.py     # /webhooks/plaid, /api/health, /api/version
    └── static_pages.py # /mortgage/* (marketing pages served as static)
```

## Key endpoints

### Borrower
- `GET  /submit/` — intake form
- `POST /submit/` — creates the Deal, kicks off AI scenario engine
- `GET  /portal/` — borrower dashboard (requires magic-link auth)
- `GET  /portal/login/?token=...` — consume magic link
- `POST /portal/upload/presign/` — get a presigned PUT URL for Spaces
- `POST /portal/upload/commit/` — record the uploaded Document

### Broker (Don)
- `GET  /admin/` — dashboard with pipeline summary + today's queue
- `GET  /admin/pipeline/` — all deals by stage
- `GET  /admin/deal/<id>/` — deal detail (full record + conditions + submissions + closing + timeline)
- `POST /admin/deal/<id>/stage/` — change stage
- `POST /admin/deal/<id>/condition/` — add a UW condition
- `POST /admin/deal/<id>/submission/` — record a lender submission
- `POST /admin/deal/<id>/outcome/` — record funded/declined/withdrew (feeds the outcome loop)
- `POST /admin/deal/<id>/scenario/` — re-run the 5-pass engine
- `GET  /admin/rates/` — current rate sheets per lender
- `POST /admin/rates/snapshot/` — record a new rate snapshot

### API
- `GET /api/health` — liveness
- `GET /api/version` — service metadata + feature flags
- `GET /api/rates/current.json` — current rate bands (for external widgets)
- `POST /webhooks/plaid/` — Plaid webhook stub for reserve verification

### Marketing (served as static)
- `GET /mortgage/` and all sub-pages

## Local dev

```bash
pip install -r requirements.txt
uvicorn app.app:app --host 0.0.0.0 --port 8080
```

Then:
- http://localhost:8080/api/health
- http://localhost:8080/submit/
- http://localhost:8080/admin/login/

## Deploy

See `render.yaml`. Deploys to Render as a free-tier web service.

## Env vars

| Name | Purpose | Required |
|------|---------|----------|
| `ADMIN_PASSWORD` | broker login | yes |
| `BROKER_EMAIL` | sender address for borrower emails | optional |
| `SPACES_ACCESS_KEY` / `SPACES_SECRET_KEY` | DO Spaces credentials for document uploads | optional |
| `OLLAMA_BASE_URL` | Ollama server for embeddings + rerank | optional |
| `RPCCP_BASE_URL` / `RPCCP_API_KEY` | call into the RPCCP engine at clickclickclose.help | optional |
| `PLAID_CLIENT_ID` / `PLAID_SECRET` | Plaid auth for reserve verification | optional |
| `DATABASE_URL` | Postgres URL on prod; defaults to SQLite locally | optional |

The service degrades gracefully when optional deps are missing:
- No Ollama → hash-based pseudo-embeddings + deterministic local scorer
- No DO Spaces → document upload endpoint still works (records the metadata)
- No Plaid → reserve verification is a stub (UI shows the structure)

## Studio — DanDon Media content production pipeline (`/studio/`)

A project tracker and an AI production pipeline for satirical-investigative
video. It sits behind the same admin login as `/admin/`.

### What it does

- **Mission control** (`/studio/`): every project ranked in one list. Drag to
  reorder, or use ⤒ ▲ ▼ ⤓ to prioritize or deprioritize. Each row has a P1–P4
  priority, a status, the current stage and a progress bar. Episodes nest under
  their series. The work queue shows everything running, waiting for review or blocked.
- **Start buttons**: *Let's find a series*, *Let's create an episode*,
  *Let's create a documentary*, *Track a project*. Each starts an intake
  interview with **Spock**. Spock asks leading questions and follows up on vague
  answers, then the project is built and its first model step starts.
- **Series flow**: the Investigator checks the claim against the public record
  and returns a verdict, evidence, counter-evidence and candidate topics. You fine-tune
  and rank the topics with Spock (or have Spock rank them), then lock the order,
  which creates the episodes.
- **Episode / documentary pipeline**. Every step is a task assigned to a specialist:
  research → story & rundown (Stewart Doctrine) → one writer per segment →
  head writer/editor (12–15 min) → fact-check → two scripts (stage directions +
  clean TTS) → OmniVoice narration → real-document visuals (every shot tied to a
  source) → animated connective tissue → music bed → public transparency page →
  render in the DanDon layout → per-platform posts → OnlySocial.
- **Board** per project: To do / In progress / Review / Done / Blocked. Drag
  cards between columns or use ◀ ▶ to move them back or forward. ▲ ▼ moves a task in
  front of or behind another. Every field is editable, and tasks can be added or deleted.
- **Autopilot**: approves each model step automatically and starts the next one.
  It stops on errors, on high-severity fact-check issues and on human steps.
- **Specialists** (`/studio/specialists/`): the model roster. Model, effort,
  tools and system prompt are editable for each role.
- **Broadcast player** (`/studio/episode/<id>/player/`): the render layout
  (3/4 visual, links bar under it, host animation or logo upper right, clickable
  sources link under that) playing the edit list against the narration.
- **Transparency page** (`/studio/p/<public_id>/sources/`, public): every source
  used, with the exact passage shown on screen.

### Configuration

| Env var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude for all specialists (research roles use live web search/fetch). Without it the pipeline still runs with clearly labelled placeholder drafts. |
| `OMNIVOICE_URL`, `OMNIVOICE_API_KEY` | OmniVoice server with the OpenAI-compatible `POST /v1/audio/speech` endpoint |
| `ONLYSOCIAL_TOKEN`, `ONLYSOCIAL_WORKSPACE` | OnlySocial API token and workspace UUID. Link each platform to its account id in Studio → Settings |
| `STUDIO_MEDIA_DIR` | Where narration, uploads and renders are stored (default `./studio_media`). Point it at a persistent disk in production |

The MP4 render uses `ffmpeg`, which is installed in the Docker image.

### Tests

```bash
pip install -r requirements.txt pytest
python -m pytest tests -q
```
