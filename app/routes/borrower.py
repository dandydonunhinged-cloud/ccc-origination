"""Borrower-facing routes: intake form, portal, document upload.

The intake form is a single multi-step POST that creates a Deal.
The portal uses magic-link auth. Document upload uses presigned PUT to
Spaces from the browser.
"""
import os, secrets, json, logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request, Form, HTTPException, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..config import config
from ..db import get_db
from ..models import (
    Borrower, Entity, Property, Deal, DealStage, LoanType, PropertyType,
    Message, Event, Document, MagicLink,
)
from ..auth import (
    issue_magic_link, consume_magic_link, set_borrower_cookie,
    clear_borrower_cookie, require_borrower, optional_borrower, hash_token,
)
from .. import scenario_ai
from .. import storage
from .. import playbooks

logger = logging.getLogger(__name__)
router = APIRouter()

# Templates directory
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _gen_public_id() -> str:
    """8-char URL-safe id, no ambiguous chars."""
    return secrets.token_urlsafe(6).replace("_", "x").replace("/", "q")[:8]


# ---------------------------------------------------------------------------
# /submit/ — borrower intake form
# ---------------------------------------------------------------------------

@router.get("/submit/", response_class=HTMLResponse)
async def submit_form(request: Request):
    return templates.TemplateResponse("borrower/submit.html", {"request": request, "config": config})


@router.post("/submit/")
async def submit_form_post(
    request: Request,
    db: Session = Depends(get_db),

    # Borrower
    full_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    credit_score: int = Form(...),

    # Entity
    entity_name: str = Form(""),
    entity_type: str = Form("LLC"),
    entity_state: str = Form(""),

    # Property
    property_address: str = Form(...),
    property_city: str = Form(...),
    property_state: str = Form(...),
    property_zip: str = Form(""),
    property_type: str = Form(...),
    purchase_price: float = Form(...),
    target_loan_amount: float = Form(...),
    down_payment: float = Form(0),
    projected_rent: float = Form(0),
    arv: float = Form(0),
    rehab_budget: float = Form(0),

    # Loan
    loan_type: str = Form(...),
    target_close: str = Form(""),
    lead_source: str = Form("website"),
    ref: str = Form(""),
    notes: str = Form(""),
):
    """Create the Deal, kick off the AI scenario engine."""
    # Track referral from ref parameter
    if ref:
        lead_source = f"referral:{ref}"
    # Upsert the Borrower (by email)
    borrower = db.query(Borrower).filter_by(email=email).one_or_none()
    if borrower is None:
        borrower = Borrower(full_name=full_name, email=email, phone=phone, credit_score=credit_score)
        db.add(borrower)
    else:
        borrower.full_name = full_name
        borrower.phone = phone
        borrower.credit_score = credit_score

    # Upsert the Entity (optional)
    entity = None
    if entity_name.strip():
        entity = db.query(Entity).filter_by(legal_name=entity_name).one_or_none()
        if entity is None:
            entity = Entity(legal_name=entity_name, entity_type=entity_type, state_formed=entity_state)
            db.add(entity)
        else:
            entity.entity_type = entity_type
            entity.state_formed = entity_state

    # Create the Property
    prop = Property(
        address_line1=property_address,
        city=property_city,
        state=property_state,
        zip=property_zip,
        property_type=PropertyType(property_type),
        purchase_price=purchase_price,
        projected_rent=projected_rent,
        arv=arv,
        rehab_budget=rehab_budget,
    )
    db.add(prop)
    db.flush()

    # Create the Deal
    deal = Deal(
        public_id=_gen_public_id(),
        borrower_id=borrower.id,
        entity_id=entity.id if entity else None,
        property_id=prop.id,
        loan_type=LoanType(loan_type),
        target_loan_amount=target_loan_amount,
        down_payment=down_payment,
        target_close=datetime.fromisoformat(target_close) if target_close else None,
        lead_source=lead_source,
        notes=notes,
        stage=DealStage.lead_acquired,
    )
    db.add(deal)
    db.flush()

    # Initial event
    db.add(Event(
        deal_id=deal.id,
        kind="deal_created",
        actor="borrower",
        payload={"loan_type": loan_type, "lead_source": lead_source, "public_id": deal.public_id},
    ))
    db.commit()

    # Kick off the AI scenario engine (best-effort; don't fail the intake if
    # the AI engine is down — the local scorer always runs as fallback)
    try:
        scenario_ai.run_scenario(deal.id, db)
    except Exception as e:
        logger.exception(f"Scenario engine failed at intake: {e}")

    # Send the borrower a magic link so they can see the deal in the portal
    raw, _ = issue_magic_link(db, borrower, purpose="login",
                              redirect_after=f"/portal/deal/{deal.public_id}/")
    login_url = f"{request.url.scheme}://{request.url.netloc}/portal/login?token={raw}"

    # In production: email the link. In dev: log it.
    logger.info(f"MAGIC LINK for {email}: {login_url}")

    return RedirectResponse(url=f"/submit/thanks/{deal.public_id}/", status_code=303)


@router.get("/submit/thanks/{public_id}/", response_class=HTMLResponse)
async def submit_thanks(public_id: str, request: Request, db: Session = Depends(get_db)):
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if not deal:
        raise HTTPException(404, "Deal not found")
    return templates.TemplateResponse("borrower/thanks.html",
                                      {"request": request, "deal": deal, "config": config})


# ---------------------------------------------------------------------------
# /portal/ — borrower portal (magic-link auth)
# ---------------------------------------------------------------------------

@router.get("/portal/", response_class=HTMLResponse)
async def portal_root(request: Request, db: Session = Depends(get_db)):
    borrower = optional_borrower(request, db)
    if not borrower:
        return RedirectResponse(url="/portal/login/", status_code=303)
    # Send the borrower to their latest deal
    latest = (
        db.query(Deal)
        .filter_by(borrower_id=borrower.id)
        .order_by(Deal.created_at.desc())
        .first()
    )
    if latest is None:
        return templates.TemplateResponse("borrower/no_deals.html",
                                          {"request": request, "borrower": borrower, "config": config})
    return RedirectResponse(url=f"/portal/deal/{latest.public_id}/", status_code=303)


@router.get("/portal/login/", response_class=HTMLResponse)
async def portal_login_get(request: Request, token: str = "", error: str = "",
                            db: Session = Depends(get_db)):
    """Either show the login form, or consume a token from the URL."""
    if token:
        borrower = consume_magic_link(db, token)
        if borrower is None:
            error = "This link has expired or been used. Request a new one below."
        else:
            # Re-issue a long-lived session magic link for the cookie
            raw, expires = issue_magic_link(db, borrower, purpose="login")
            response = RedirectResponse(url="/portal/", status_code=303)
            set_borrower_cookie(response, raw, expires)
            return response
    return templates.TemplateResponse("borrower/login.html",
                                      {"request": request, "error": error, "config": config})


@router.post("/portal/login/")
async def portal_login_post(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    borrower = db.query(Borrower).filter_by(email=email).one_or_none()
    if borrower is None:
        # Don't leak which emails are known
        return templates.TemplateResponse("borrower/login.html",
                                          {"request": request,
                                           "error": "If that email matches a deal, a sign-in link is on its way.",
                                           "config": config},
                                          status_code=200)
    raw, expires = issue_magic_link(db, borrower, purpose="login")
    link = f"{request.url.scheme}://{request.url.netloc}/portal/login/?token={raw}"
    logger.info(f"MAGIC LINK for {email}: {link}")
    return templates.TemplateResponse("borrower/login.html",
                                      {"request": request,
                                       "info": f"Sign-in link generated (dev: see log). In prod, this is emailed.",
                                       "config": config})


@router.get("/portal/logout/", response_class=HTMLResponse)
async def portal_logout(request: Request):
    response = RedirectResponse(url="/portal/login/", status_code=303)
    clear_borrower_cookie(response)
    return response


# ---------------------------------------------------------------------------
# /portal/deal/<public_id>/ — the borrower view of their deal
# ---------------------------------------------------------------------------

@router.get("/portal/deal/{public_id}/", response_class=HTMLResponse)
async def portal_deal(public_id: str, request: Request, db: Session = Depends(get_db)):
    borrower = require_borrower(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if deal is None or deal.borrower_id != borrower.id:
        raise HTTPException(404, "Deal not found")
    docs = db.query(Document).filter_by(deal_id=deal.id).order_by(Document.created_at.desc()).all()
    from ..models import Condition
    conditions = db.query(Condition).filter_by(deal_id=deal.id).order_by(Condition.requested_at.desc()).all()
    messages = db.query(Message).filter_by(deal_id=deal.id, visible_to_borrower=True).order_by(Message.created_at.asc()).all()
    return templates.TemplateResponse("borrower/deal.html",
                                      {"request": request, "deal": deal, "docs": docs,
                                       "conditions": conditions, "messages": messages,
                                       "config": config, "playbooks": playbooks})


@router.post("/portal/deal/{public_id}/message/")
async def portal_deal_message(
    public_id: str,
    request: Request,
    body: str = Form(...),
    db: Session = Depends(get_db),
):
    borrower = require_borrower(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if deal is None or deal.borrower_id != borrower.id:
        raise HTTPException(404)
    if not body.strip():
        raise HTTPException(400, "Empty message")
    db.add(Message(
        deal_id=deal.id,
        from_party="borrower",
        from_email=borrower.email,
        body=body.strip()[:4000],
        visible_to_borrower=True,
    ))
    db.add(Event(deal_id=deal.id, kind="borrower_message", actor="borrower",
                  payload={"preview": body.strip()[:120]}))
    db.commit()
    return RedirectResponse(url=f"/portal/deal/{public_id}/", status_code=303)


# ---------------------------------------------------------------------------
# /portal/upload/ — presigned PUT URL issuance (the browser uploads directly)
# ---------------------------------------------------------------------------

@router.post("/portal/upload/presign/")
async def portal_upload_presign(
    request: Request,
    filename: str = Form(...),
    content_type: str = Form("application/octet-stream"),
    purpose: str = Form("borrower_doc"),     # what the upload is for
    clears_condition_id: int = Form(None),
    db: Session = Depends(get_db),
):
    """Issue a presigned PUT URL. The browser uploads directly to Spaces.

    The borrower can upload against their own deal. We check the deal
    ownership before issuing the URL.
    """
    borrower = require_borrower(request, db)
    deal_id = int(request.headers.get("X-Deal-Id", "0"))
    deal = db.query(Deal).filter_by(id=deal_id).one_or_none()
    if not deal or deal.borrower_id != borrower.id:
        raise HTTPException(404, "Deal not found")
    presigned = storage.presign_upload(
        deal_public_id=deal.public_id,
        purpose=purpose,
        filename=filename,
        content_type=content_type,
    )
    return JSONResponse(presigned)


@router.post("/portal/upload/commit/")
async def portal_upload_commit(
    request: Request,
    deal_id: int = Form(...),
    spaces_key: str = Form(...),
    filename: str = Form(...),
    content_type: str = Form("application/octet-stream"),
    purpose: str = Form("borrower_doc"),
    clears_condition_id: int = Form(None),
    db: Session = Depends(get_db),
):
    """After the browser PUTs the file to Spaces, it calls this to record
    the document in our DB and (optionally) link it to a condition.
    """
    borrower = require_borrower(request, db)
    deal = db.query(Deal).filter_by(id=deal_id).one_or_none()
    if not deal or deal.borrower_id != borrower.id:
        raise HTTPException(404)
    meta = storage.head_object(spaces_key)
    size = meta["size_bytes"] if meta else None
    doc = Document(
        deal_id=deal.id,
        filename=filename,
        content_type=content_type,
        size_bytes=size,
        spaces_key=spaces_key,
        spaces_url=storage.public_url(spaces_key),
        purpose=purpose,
        uploaded_by="borrower",
        uploader_email=borrower.email,
        clears_condition_id=clears_condition_id,
        version=1,
    )
    db.add(doc)
    db.flush()
    if clears_condition_id:
        from ..models import Condition, ConditionStatus
        cond = db.query(Condition).get(clears_condition_id)
        if cond and cond.deal_id == deal.id:
            cond.status = ConditionStatus.received
            cond.received_at = datetime.now(timezone.utc)
            db.add(Event(deal_id=deal.id, kind="condition_received",
                         actor="borrower", payload={"condition_id": clears_condition_id}))
    db.add(Event(deal_id=deal.id, kind="doc_uploaded", actor="borrower",
                 payload={"purpose": purpose, "filename": filename, "size": size}))
    db.commit()
    return {"document_id": doc.id, "spaces_url": doc.spaces_url}