"""Broker command surface — Don's daily working surface.

Endpoints:
  GET  /admin/                       — dashboard (pipeline summary)
  GET  /admin/login/                 — login form
  POST /admin/login/                 — login (returns JWT in cookie)
  GET  /admin/logout/                — logout
  GET  /admin/pipeline/              — full pipeline (all deals by stage)
  GET  /admin/queue/                 — today's queue (15-min calls due, conditions overdue, etc.)
  GET  /admin/deal/<public_id>/      — deal detail
  POST /admin/deal/<public_id>/stage/        — change stage
  POST /admin/deal/<public_id>/condition/    — add a UW condition
  POST /admin/deal/<public_id>/submission/   — record a lender submission
  POST /admin/deal/<public_id>/outcome/      — record funded/declined/withdrew
  POST /admin/deal/<public_id>/note/         — internal note
  POST /admin/deal/<public_id>/message/      — message to borrower
  POST /admin/deal/<public_id>/scenario/     — re-run scenario engine
  POST /admin/deal/<public_id>/closing/       — save closing record
  POST /admin/deal/<public_id>/comp/          — record a comp payment
  POST /admin/rates/snapshot/                 — record a rate sheet snapshot
  GET  /admin/rates/                          — latest rate sheet snapshots
"""
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from ..config import config
from ..db import get_db
from ..models import (
    Deal, DealStage, LoanType, PropertyType, Condition, ConditionType, ConditionStatus,
    Submission, SubmissionStatus, Product, Lender, Closing, CompPayment, CompType,
    Document, Note, Event, Message, Borrower, Entity, Property,
    DealOutcome,
)
from ..auth import require_broker, issue_session, resolve_session, set_broker_cookie, clear_broker_cookie
from .. import scenario_ai, outcomes, snapshots, playbooks

logger = logging.getLogger(__name__)
router = APIRouter()

TEMPLATES_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@router.get("/admin/login/", response_class=HTMLResponse)
async def admin_login_get(request: Request, error: str = ""):
    return templates.TemplateResponse("broker/login.html",
                                      {"request": request, "error": error, "config": config})


@router.post("/admin/login/")
async def admin_login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """In v1 we use a simple password check against config.ADMIN_PASSWORD.
    In v2 we'll move to bcrypt-hashed staff passwords with a Staff table.
    """
    expected = config.ADMIN_PASSWORD
    if not expected:
        return templates.TemplateResponse("broker/login.html",
                                          {"request": request,
                                           "error": "ADMIN_PASSWORD not configured on server.",
                                           "config": config},
                                          status_code=500)
    if password != expected:
        return templates.TemplateResponse("broker/login.html",
                                          {"request": request,
                                           "error": "Invalid credentials.",
                                           "config": config},
                                          status_code=401)
    token, expires = issue_session(db, email)
    response = RedirectResponse(url="/admin/", status_code=303)
    set_broker_cookie(response, token, expires)
    return response


@router.get("/admin/logout/")
async def admin_logout(request: Request, db: Session = Depends(get_db)):
    from ..auth import revoke_session, COOKIE_BROKER
    raw = request.cookies.get(COOKIE_BROKER)
    if raw:
        revoke_session(db, raw)
    response = RedirectResponse(url="/admin/login/", status_code=303)
    clear_broker_cookie(response)
    return response


def require_admin(request: Request, db: Session = Depends(get_db)) -> str:
    """Convenience wrapper that returns the broker email."""
    return require_broker(request, db)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("/admin/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    email = require_admin(request, db)
    summary = outcomes.pipeline_summary(db)
    # Today's queue
    queue = _todays_queue(db)
    return templates.TemplateResponse("broker/dashboard.html",
                                      {"request": request, "summary": summary,
                                       "queue": queue, "email": email, "config": config})


def _todays_queue(db: Session) -> list[dict]:
    """The 15-min-calls-due + conditions-overdue + CD-review-due buckets."""
    items = []
    now = datetime.now(timezone.utc)

    # Deals in first_contact stage for > 24h (15-min rule broken)
    fc_threshold = now - timedelta(hours=24)
    fc_deals = (
        db.query(Deal)
        .filter_by(stage=DealStage.scenario_analyzed)
        .filter(Deal.stage_entered_at <= fc_threshold)
        .all()
    )
    fc_deals += (
        db.query(Deal)
        .filter_by(stage=DealStage.first_contact)
        .filter(Deal.stage_entered_at <= fc_threshold)
        .all()
    )
    for d in fc_deals:
        items.append({
            "kind": "call_due",
            "label": f"Call due: {d.borrower.full_name} ({d.public_id})",
            "url": f"/admin/deal/{d.public_id}/",
            "severity": "high",
        })

    # Conditions overdue (> 4 days in requested status)
    cond_threshold = now - timedelta(days=4)
    overdue = (
        db.query(Condition)
        .filter_by(status=ConditionStatus.requested)
        .filter(Condition.requested_at <= cond_threshold)
        .all()
    )
    for c in overdue:
        items.append({
            "kind": "condition_overdue",
            "label": f"Condition overdue: {c.condition_type.value} on deal {c.deal.public_id}",
            "url": f"/admin/deal/{c.deal.public_id}/",
            "severity": "med",
        })

    # CTC review pending: deals in clear_to_close stage
    ctc_deals = db.query(Deal).filter_by(stage=DealStage.clear_to_close).all()
    for d in ctc_deals:
        items.append({
            "kind": "ctc_review",
            "label": f"CTC review: {d.borrower.full_name} ({d.public_id})",
            "url": f"/admin/deal/{d.public_id}/",
            "severity": "high",
        })

    # 30/90/180 day rate-reset check
    rs_threshold = now - timedelta(days=30)
    rs_threshold_90 = now - timedelta(days=90)
    rs_deals = (
        db.query(Deal)
        .filter(Deal.closed_at.isnot(None))
        .filter(or_(
            Deal.closed_at <= rs_threshold,
            Deal.closed_at <= rs_threshold_90,
        ))
        .all()
    )
    for d in rs_deals:
        days = (now - d.closed_at).days
        if 25 <= days <= 35 or 85 <= days <= 95:
            items.append({
                "kind": "rate_reset",
                "label": f"Rate-reset check: {d.borrower.full_name} ({days}d post-close, {d.public_id})",
                "url": f"/admin/deal/{d.public_id}/",
                "severity": "low",
            })

    # Sort by severity (high first)
    sev = {"high": 0, "med": 1, "low": 2}
    items.sort(key=lambda x: sev.get(x["severity"], 9))
    return items[:30]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

@router.get("/admin/pipeline/", response_class=HTMLResponse)
async def admin_pipeline(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    deals = db.query(Deal).order_by(Deal.created_at.desc()).limit(200).all()
    by_stage = {}
    for d in deals:
        by_stage.setdefault(d.stage.value, []).append(d)
    return templates.TemplateResponse("broker/pipeline.html",
                                      {"request": request, "by_stage": by_stage, "config": config})


# ---------------------------------------------------------------------------
# Deal detail
# ---------------------------------------------------------------------------

@router.get("/admin/deal/{public_id}/", response_class=HTMLResponse)
async def admin_deal(public_id: str, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if not deal:
        raise HTTPException(404, "Deal not found")
    conditions = db.query(Condition).filter_by(deal_id=deal.id).order_by(Condition.requested_at.desc()).all()
    submissions = db.query(Submission).filter_by(deal_id=deal.id).order_by(Submission.created_at.desc()).all()
    documents = db.query(Document).filter_by(deal_id=deal.id).order_by(Document.created_at.desc()).all()
    notes = db.query(Note).filter_by(deal_id=deal.id).order_by(Note.created_at.desc()).all()
    messages = db.query(Message).filter_by(deal_id=deal.id).order_by(Message.created_at.asc()).all()
    events = db.query(Event).filter_by(deal_id=deal.id).order_by(Event.created_at.desc()).limit(100).all()
    closing = db.query(Closing).filter_by(deal_id=deal.id).order_by(Closing.created_at.desc()).first()
    comps = db.query(CompPayment).filter_by(deal_id=deal.id).order_by(CompPayment.received_at.desc()).all()
    outcome = db.query(DealOutcome).filter_by(deal_id=deal.id).one_or_none()
    products = db.query(Product).filter_by(active=True, loan_type=deal.loan_type).all()
    return templates.TemplateResponse("broker/deal.html",
                                      {"request": request, "deal": deal,
                                       "conditions": conditions, "submissions": submissions,
                                       "documents": documents, "notes": notes, "messages": messages,
                                       "events": events, "closing": closing, "comps": comps,
                                       "outcome": outcome, "products": products,
                                       "playbooks": playbooks, "config": config})


# ---------------------------------------------------------------------------
# Stage change
# ---------------------------------------------------------------------------

@router.post("/admin/deal/{public_id}/stage/")
async def admin_stage_change(
    public_id: str,
    request: Request,
    new_stage: str = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if not deal:
        raise HTTPException(404)
    try:
        target = DealStage(new_stage)
    except ValueError:
        raise HTTPException(400, "Invalid stage")
    old = deal.stage
    deal.stage = target
    deal.stage_entered_at = datetime.now(timezone.utc)
    if note:
        db.add(Note(deal_id=deal.id, author="don", body=f"Stage {old.value} → {target.value}: {note}"))
    db.add(Event(deal_id=deal.id, kind="stage_change", actor="don",
                 payload={"from": old.value, "to": target.value, "note": note}))
    db.commit()
    return RedirectResponse(url=f"/admin/deal/{public_id}/", status_code=303)


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

@router.post("/admin/deal/{public_id}/condition/")
async def admin_condition_add(
    public_id: str,
    request: Request,
    condition_type: str = Form(...),
    description: str = Form(""),
    submission_id: int = Form(None),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if not deal:
        raise HTTPException(404)
    try:
        ct = ConditionType(condition_type)
    except ValueError:
        raise HTTPException(400, "Invalid condition type")
    cond = Condition(
        deal_id=deal.id,
        condition_type=ct,
        description=description or None,
        submission_id=submission_id,
        status=ConditionStatus.requested,
    )
    db.add(cond)
    db.flush()
    db.add(Event(deal_id=deal.id, kind="condition_added", actor="don",
                 payload={"condition_id": cond.id, "type": ct.value}))
    db.commit()
    return RedirectResponse(url=f"/admin/deal/{public_id}/", status_code=303)


@router.post("/admin/deal/{public_id}/condition/{condition_id}/status/")
async def admin_condition_status(
    public_id: str,
    condition_id: int,
    request: Request,
    new_status: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    cond = db.query(Condition).get(condition_id)
    if not cond or cond.deal.public_id != public_id:
        raise HTTPException(404)
    try:
        ns = ConditionStatus(new_status)
    except ValueError:
        raise HTTPException(400, "Invalid status")
    cond.status = ns
    if ns == ConditionStatus.received and cond.received_at is None:
        cond.received_at = datetime.now(timezone.utc)
    if ns == ConditionStatus.cleared and cond.cleared_at is None:
        cond.cleared_at = datetime.now(timezone.utc)
    db.add(Event(deal_id=cond.deal_id, kind="condition_status",
                 actor="don", payload={"condition_id": condition_id, "status": ns.value}))
    db.commit()
    return RedirectResponse(url=f"/admin/deal/{public_id}/", status_code=303)


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------

@router.post("/admin/deal/{public_id}/submission/")
async def admin_submission_add(
    public_id: str,
    request: Request,
    product_id: int = Form(...),
    is_primary: bool = Form(True),
    ae_name: str = Form(""),
    ae_email: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if not deal:
        raise HTTPException(404)
    product = db.query(Product).get(product_id)
    if not product or not product.active:
        raise HTTPException(400, "Unknown product")
    # If marking this primary, unmark others
    if is_primary:
        for s in deal.submissions:
            s.is_primary = False
    sub = Submission(
        deal_id=deal.id,
        product_id=product_id,
        status=SubmissionStatus.submitted,
        submitted_at=datetime.now(timezone.utc),
        ae_name=ae_name or None,
        ae_email=ae_email or None,
        is_primary=is_primary,
    )
    db.add(sub)
    db.flush()
    if deal.stage in (DealStage.scenario_analyzed, DealStage.first_contact, DealStage.documents_collected):
        deal.stage = DealStage.submitted
        db.add(Event(deal_id=deal.id, kind="stage_change", actor="don",
                     payload={"from": deal.stage.value, "to": "submitted", "trigger": "first_submission"}))
    db.add(Event(deal_id=deal.id, kind="submission_added", actor="don",
                 payload={"submission_id": sub.id, "lender": product.lender.name, "product": product.name}))
    db.commit()
    return RedirectResponse(url=f"/admin/deal/{public_id}/", status_code=303)


@router.post("/admin/deal/{public_id}/submission/{submission_id}/status/")
async def admin_submission_status(
    public_id: str,
    submission_id: int,
    request: Request,
    new_status: str = Form(...),
    lender_loan_id: str = Form(""),
    declined_reason: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    sub = db.query(Submission).get(submission_id)
    if not sub or sub.deal.public_id != public_id:
        raise HTTPException(404)
    try:
        ns = SubmissionStatus(new_status)
    except ValueError:
        raise HTTPException(400, "Invalid status")
    sub.status = ns
    sub.last_status_at = datetime.now(timezone.utc)
    if lender_loan_id:
        sub.lender_loan_id = lender_loan_id
    if declined_reason:
        sub.declined_reason = declined_reason
    db.add(Event(deal_id=sub.deal_id, kind="submission_status", actor="don",
                 payload={"submission_id": submission_id, "status": ns.value}))
    # If approved, advance to UW
    if ns == SubmissionStatus.conditional:
        sub.deal.stage = DealStage.underwriting
        db.add(Event(deal_id=sub.deal_id, kind="stage_change", actor="don",
                     payload={"to": "underwriting", "trigger": "submission_conditional"}))
    elif ns == SubmissionStatus.approved:
        sub.deal.ctc_at = datetime.now(timezone.utc)
        sub.deal.stage = DealStage.clear_to_close
        db.add(Event(deal_id=sub.deal_id, kind="stage_change", actor="don",
                     payload={"to": "clear_to_close", "trigger": "submission_approved"}))
    db.commit()
    return RedirectResponse(url=f"/admin/deal/{public_id}/", status_code=303)


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

@router.post("/admin/deal/{public_id}/outcome/")
async def admin_outcome(
    public_id: str,
    request: Request,
    outcome: str = Form(...),
    chosen_lender_id: int = Form(None),
    chosen_product_id: int = Form(None),
    rate_at_close: float = Form(None),
    comp_at_close_cents: int = Form(None),
    days_to_fund: int = Form(None),
    declined_reason: str = Form(""),
    fellout_reason: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if not deal:
        raise HTTPException(404)
    out = outcomes.record_outcome(
        deal_id=deal.id, outcome=outcome, db=db,
        chosen_lender_id=chosen_lender_id,
        chosen_product_id=chosen_product_id,
        rate_at_close=rate_at_close,
        comp_at_close_cents=comp_at_close_cents,
        days_to_fund=days_to_fund,
        declined_reason=declined_reason or None,
        fellout_reason=fellout_reason or None,
        notes=notes or None,
    )
    return RedirectResponse(url=f"/admin/deal/{public_id}/", status_code=303)


# ---------------------------------------------------------------------------
# Note + message
# ---------------------------------------------------------------------------

@router.post("/admin/deal/{public_id}/note/")
async def admin_note(
    public_id: str,
    request: Request,
    body: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if not deal:
        raise HTTPException(404)
    db.add(Note(deal_id=deal.id, author="don", body=body.strip()))
    db.commit()
    return RedirectResponse(url=f"/admin/deal/{public_id}/", status_code=303)


@router.post("/admin/deal/{public_id}/message/")
async def admin_message(
    public_id: str,
    request: Request,
    body: str = Form(...),
    visible_to_borrower: bool = Form(True),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if not deal:
        raise HTTPException(404)
    db.add(Message(
        deal_id=deal.id, from_party="broker", from_email="don@dandydon.media",
        body=body.strip()[:4000], visible_to_borrower=visible_to_borrower,
    ))
    db.add(Event(deal_id=deal.id, kind="broker_message", actor="don",
                 payload={"preview": body.strip()[:120], "visible": visible_to_borrower}))
    db.commit()
    return RedirectResponse(url=f"/admin/deal/{public_id}/", status_code=303)


# ---------------------------------------------------------------------------
# Re-run scenario
# ---------------------------------------------------------------------------

@router.post("/admin/deal/{public_id}/scenario/")
async def admin_scenario(
    public_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if not deal:
        raise HTTPException(404)
    try:
        scenario_ai.run_scenario(deal.id, db)
    except Exception as e:
        logger.exception(f"Scenario rerun failed: {e}")
    return RedirectResponse(url=f"/admin/deal/{public_id}/", status_code=303)


# ---------------------------------------------------------------------------
# Closing + comp
# ---------------------------------------------------------------------------

@router.post("/admin/deal/{public_id}/closing/")
async def admin_closing(
    public_id: str,
    request: Request,
    title_company: str = Form(""),
    title_agent: str = Form(""),
    insurance_agent: str = Form(""),
    insurance_bound: bool = Form(False),
    closing_date: str = Form(""),
    loan_amount_cents: int = Form(0),
    rate: float = Form(0),
    term_months: int = Form(360),
    borrower_origination_cents: int = Form(0),
    lender_ysp_cents: int = Form(0),
    processing_cents: int = Form(0),
    admin_cents: int = Form(0),
    cash_to_close_cents: int = Form(0),
    recorded_at: str = Form(""),
    recording_number: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if not deal:
        raise HTTPException(404)
    closing = db.query(Closing).filter_by(deal_id=deal.id).first()
    if closing is None:
        closing = Closing(deal_id=deal.id)
        db.add(closing)
    closing.title_company = title_company or None
    closing.title_agent = title_agent or None
    closing.insurance_agent = insurance_agent or None
    closing.insurance_bound = insurance_bound
    closing.closing_date = datetime.fromisoformat(closing_date) if closing_date else None
    closing.loan_amount_cents = loan_amount_cents
    closing.rate = rate
    closing.term_months = term_months
    closing.borrower_origination_cents = borrower_origination_cents
    closing.lender_ysp_cents = lender_ysp_cents
    closing.processing_cents = processing_cents
    closing.admin_cents = admin_cents
    closing.cash_to_close_cents = cash_to_close_cents
    closing.recorded_at = datetime.fromisoformat(recorded_at) if recorded_at else None
    closing.recording_number = recording_number or None
    closing.notes = notes or None
    # Auto-advance the deal
    if closing.closing_date and deal.stage not in (DealStage.closing, DealStage.post_close):
        deal.stage = DealStage.closing
        db.add(Event(deal_id=deal.id, kind="stage_change", actor="don",
                     payload={"to": "closing", "trigger": "closing_date_set"}))
    if closing.recorded_at:
        deal.stage = DealStage.post_close
        deal.closed_at = closing.recorded_at
        deal.recording_number = recording_number or None
        deal.comp_paid_cents = (closing.borrower_origination_cents or 0) + (closing.lender_ysp_cents or 0)
        db.add(Event(deal_id=deal.id, kind="stage_change", actor="don",
                     payload={"to": "post_close", "trigger": "recorded"}))
    db.commit()
    return RedirectResponse(url=f"/admin/deal/{public_id}/", status_code=303)


@router.post("/admin/deal/{public_id}/comp/")
async def admin_comp(
    public_id: str,
    request: Request,
    comp_type: str = Form(...),
    amount_cents: int = Form(...),
    payer: str = Form(""),
    paid_to: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    deal = db.query(Deal).filter_by(public_id=public_id).one_or_none()
    if not deal:
        raise HTTPException(404)
    try:
        ct = CompType(comp_type)
    except ValueError:
        raise HTTPException(400, "Invalid comp type")
    db.add(CompPayment(
        deal_id=deal.id, comp_type=ct, amount_cents=amount_cents,
        payer=payer or None, paid_to=paid_to or None, notes=notes or None,
    ))
    db.commit()
    return RedirectResponse(url=f"/admin/deal/{public_id}/", status_code=303)


# ---------------------------------------------------------------------------
# Rate sheet snapshot
# ---------------------------------------------------------------------------

@router.get("/admin/rates/", response_class=HTMLResponse)
async def admin_rates(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    lenders = db.query(Lender).filter_by(active=True).all()
    snaps_by_lender = {l.id: snapshots.latest_for_lender(db, l.id) for l in lenders}
    return templates.TemplateResponse("broker/rates.html",
                                      {"request": request, "lenders": lenders,
                                       "snaps_by_lender": snaps_by_lender, "config": config})


@router.post("/admin/rates/snapshot/")
async def admin_rates_snapshot(
    request: Request,
    lender_id: int = Form(...),
    product_id: int = Form(None),
    rate_low: float = Form(...),
    rate_high: float = Form(...),
    points: float = Form(None),
    rate_lock_days: int = Form(None),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    snapshots.record_snapshot(
        db, lender_id=lender_id, product_id=product_id or None,
        rate_low=rate_low, rate_high=rate_high,
        points=points, rate_lock_days=rate_lock_days,
        source="manual", notes=notes or None,
    )
    return RedirectResponse(url="/admin/rates/", status_code=303)