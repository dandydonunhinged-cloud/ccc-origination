"""CCC Origination — FastAPI app entry point.

Routes are split across modules for clarity:
  - borrower.py:   /submit/*, /portal/*      (borrower-facing)
  - broker.py:     /admin/*                  (Don's command surface)
  - webhooks.py:   /webhooks/plaid, /api/health, /api/snapshots
  - static.py:     the /mortgage/* pages from the old static hub

The app.py here is just the FastAPI() + middleware + router include.
"""
import os, logging, secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import config
from .db import init_db
from . import seed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="CCC Origination",
    version="0.1.0",
    description="Investor mortgage origination system. From lead to close to next deal.",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


# ---------------------------------------------------------------------------
# HTTPS redirect (when behind a proxy that sends X-Forwarded-Proto)
# ---------------------------------------------------------------------------

@app.middleware("http")
async def https_redirect(request: Request, call_next):
    proto = request.headers.get("x-forwarded-proto", "")
    if proto == "http" and not request.url.hostname.startswith("localhost"):
        url = request.url.replace(scheme="https")
        return RedirectResponse(url=str(url), status_code=301)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "engine": "CCC Origination", "version": app.version}


# ---------------------------------------------------------------------------
# Startup: seed
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    init_db()
    from .db import SessionLocal
    db = SessionLocal()
    try:
        counts = seed.seed_lenders(db)
        if counts["lenders_added"] or counts["products_added"]:
            logger.info(f"Seeded {counts['lenders_added']} lenders + {counts['products_added']} products")
    finally:
        db.close()
    # Schedule daily jobs (stub for v1; APScheduler wired in v2)
    logger.info("CCC Origination started")


# ---------------------------------------------------------------------------
# Static + route registration
# ---------------------------------------------------------------------------

# Static files: app CSS + the marketing /mortgage/ pages
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
STATIC_MORTGAGE = STATIC_DIR / "mortgage"

# The /mortgage/ pages themselves — register them as a Jinja2-free static
# file mount at the top of the tree. This serves the marketing front door.
# In production we'll move them to a CDN; for now they're served by FastAPI
# so the whole monolith is one process.

from .routes import borrower, broker, webhooks, static_pages, partners, widget
app.include_router(borrower.router)
app.include_router(broker.router)
app.include_router(webhooks.router)
app.include_router(static_pages.router)
app.include_router(partners.router)
app.include_router(widget.router)


# ---------------------------------------------------------------------------
# Default route: marketing landing
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    """Default landing — bounces to /mortgage/ if it exists, otherwise a
    tiny landing that says "this is the API; /mortgage/ for the front door,
    /admin/ for the broker command surface, /portal/ for the borrower."""
    index_html = STATIC_MORTGAGE / "index.html"
    if index_html.exists():
        from fastapi.responses import FileResponse
        return FileResponse(str(index_html))
    return JSONResponse({
        "service": "CCC Origination",
        "version": app.version,
        "endpoints": {
            "marketing": "/mortgage/",
            "borrower_portal": "/portal/",
            "broker_command_surface": "/admin/",
            "health": "/api/health",
        },
    })