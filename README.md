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