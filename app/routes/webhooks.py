"""Webhook + API endpoints (no UI)."""
import logging
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..db import get_db
from .. import snapshots, storage

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------

@router.get("/api/version")
async def api_version():
    return {
        "service": "CCC Origination",
        "version": "0.1.0",
        "features": {
            "ai_lender_matching": True,
            "vector_similarity": True,
            "5_pass_rerank": True,
            "outcome_loop": True,
            "rate_sheets": True,
            "plaid_stub": True,
            "borrower_portal": True,
            "broker_command_surface": True,
        },
    }


# ---------------------------------------------------------------------------
# /webhooks/plaid — stub for reserve verification
# ---------------------------------------------------------------------------

@router.post("/webhooks/plaid/")
async def webhook_plaid(request: Request, db: Session = Depends(get_db)):
    """Plaid webhook stub. When PLAID_CLIENT_ID is set and the real webhook
    fires, this verifies the link status and pulls fresh balances."""
    payload = await request.json()
    webhook_type = payload.get("webhook_type")
    webhook_code = payload.get("webhook_code")
    item_id = payload.get("item_id")
    logger.info(f"PLAID webhook: type={webhook_type} code={webhook_code} item={item_id}")
    # In production: pull /accounts/balance/get and update the PlaidLink row
    # + post a message to the deal's borrower-portal feed.
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# /api/rates/current.json — current rate bands per lender (for any external
# widget that wants to embed "today's pricing")
# ---------------------------------------------------------------------------

@router.get("/api/rates/current.json")
async def api_rates_current(db: Session = Depends(get_db)):
    out = []
    for lender in db.query(__import__(f"{__package__}..models", fromlist=["Lender"]).Lender).filter_by(active=True).all():
        latest = snapshots.latest_for_lender(db, lender.id)
        for snap in latest:
            out.append({
                "lender": lender.name,
                "lender_slug": lender.slug,
                "product_id": snap.product_id,
                "rate_low": snap.rate_low,
                "rate_high": snap.rate_high,
                "rate_band": f"{snap.rate_low:.2f}-{snap.rate_high:.2f}%",
                "points": snap.points,
                "captured_at": snap.captured_at.isoformat() if snap.captured_at else None,
            })
    return {"rates": out, "count": len(out)}