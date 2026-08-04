"""Partner portal — referral partners submit deals, track commissions, get API keys."""
import logging, secrets
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Form, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..config import config
from ..db import get_db
from ..models import Partner, Deal, DealStage, LoanType, PropertyType, Borrower, Entity, Property, Event
from ..auth import hash_token

logger = logging.getLogger(__name__)
router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _gen_api_key() -> str:
    return "ccc_" + secrets.token_urlsafe(32)


def _require_partner(request: Request, db: Session) -> Partner:
    """Authenticate via API key in header, or session cookie."""
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        partner = db.query(Partner).filter_by(api_key=hash_token(api_key), active=True).one_or_none()
        if partner:
            return partner
    raise HTTPException(401, "Invalid or missing API key")


# ---------------------------------------------------------------------------
# Partner dashboard
# ---------------------------------------------------------------------------

@router.get("/partner/", response_class=HTMLResponse)
async def partner_dashboard(request: Request, db: Session = Depends(get_db)):
    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        return templates.TemplateResponse("partner/login.html", {"request": request, "config": config})
    partner = _require_partner(request, db)
    deals = db.query(Deal).filter_by(referral_partner_id=partner.id).order_by(Deal.created_at.desc()).all()
    return templates.TemplateResponse("partner/dashboard.html",
                                      {"request": request, "partner": partner, "deals": deals, "config": config})


@router.get("/partner/login/", response_class=HTMLResponse)
async def partner_login_get(request: Request, error: str = ""):
    return templates.TemplateResponse("partner/login.html",
                                      {"request": request, "error": error, "config": config})


@router.post("/partner/login/")
async def partner_login_post(
    request: Request,
    email: str = Form(...),
    api_key: str = Form(...),
    db: Session = Depends(get_db),
):
    partner = db.query(Partner).filter_by(email=email, api_key=hash_token(api_key), active=True).one_or_none()
    if not partner:
        return templates.TemplateResponse("partner/login.html",
                                          {"request": request, "error": "Invalid credentials.", "config": config},
                                          status_code=401)
    return templates.TemplateResponse("partner/dashboard.html",
                                      {"request": request, "partner": partner,
                                       "deals": db.query(Deal).filter_by(referral_partner_id=partner.id)
                                       .order_by(Deal.created_at.desc()).all(),
                                       "config": config})


# ---------------------------------------------------------------------------
# Partner submits a deal (simplified intake)
# ---------------------------------------------------------------------------

@router.post("/partner/submit/")
async def partner_submit(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    credit_score: int = Form(...),
    property_address: str = Form(...),
    property_city: str = Form(...),
    property_state: str = Form(...),
    property_type: str = Form(...),
    purchase_price: float = Form(...),
    target_loan_amount: float = Form(...),
    loan_type: str = Form(...),
    projected_rent: float = Form(0),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    api_key: str = Header(None),
):
    partner = _require_partner(request, db) if api_key else None
    if not partner:
        raise HTTPException(401, "Partner API key required")

    borrower = db.query(Borrower).filter_by(email=email).one_or_none()
    if borrower is None:
        borrower = Borrower(full_name=full_name, email=email, phone=phone, credit_score=credit_score)
        db.add(borrower)
    else:
        borrower.full_name = full_name
        borrower.phone = phone
        borrower.credit_score = credit_score

    prop = Property(
        address_line1=property_address, city=property_city, state=property_state,
        property_type=PropertyType(property_type), purchase_price=purchase_price,
        projected_rent=projected_rent,
    )
    db.add(prop)
    db.flush()

    deal = Deal(
        public_id=secrets.token_urlsafe(6).replace("_", "x").replace("/", "q")[:8],
        borrower_id=borrower.id, property_id=prop.id,
        loan_type=LoanType(loan_type), target_loan_amount=target_loan_amount,
        lead_source=f"partner:{partner.id}", notes=notes,
        referral_partner_id=partner.id,
        stage=DealStage.lead_acquired,
    )
    db.add(deal)
    db.flush()
    db.add(Event(deal_id=deal.id, kind="deal_created", actor="partner",
                 payload={"partner_id": partner.id, "partner_name": partner.name}))
    db.commit()

    return JSONResponse({"status": "ok", "deal_id": deal.public_id, "partner": partner.name})


# ---------------------------------------------------------------------------
# Partner API key management
# ---------------------------------------------------------------------------

@router.post("/partner/api-key/rotate/")
async def partner_rotate_key(
    request: Request,
    email: str = Form(...),
    current_key: str = Form(...),
    db: Session = Depends(get_db),
):
    partner = db.query(Partner).filter_by(email=email, api_key=hash_token(current_key), active=True).one_or_none()
    if not partner:
        raise HTTPException(401, "Invalid credentials")
    new_key = _gen_api_key()
    partner.api_key = hash_token(new_key)
    db.commit()
    return JSONResponse({"api_key": new_key, "note": "Save this — it won't be shown again."})


# ---------------------------------------------------------------------------
# Public partner registration (self-serve)
# ---------------------------------------------------------------------------

@router.get("/partner/register/", response_class=HTMLResponse)
async def partner_register_page(request: Request, error: str = "", success: str = "", api_key: str = ""):
    return templates.TemplateResponse("partner/register.html",
                                      {"request": request, "error": error, "success": success,
                                       "api_key": api_key, "config": config})


@router.post("/partner/register/")
async def partner_register(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    company: str = Form(""),
    db: Session = Depends(get_db),
):
    existing = db.query(Partner).filter_by(email=email).one_or_none()
    if existing:
        return templates.TemplateResponse("partner/register.html",
                                          {"request": request, "error": "Partner with this email already exists. <a href='/partner/login/'>Sign in</a>.",
                                           "success": "", "api_key": "", "config": config},
                                          status_code=409)
    api_key = _gen_api_key()
    partner = Partner(
        name=name, email=email, phone=phone or None, company=company or None,
        api_key=hash_token(api_key), active=True,
    )
    db.add(partner)
    db.commit()
    return templates.TemplateResponse("partner/register.html",
                                      {"request": request, "error": "", "success": "true",
                                       "api_key": api_key, "config": config})
