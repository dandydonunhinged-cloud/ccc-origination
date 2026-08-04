"""Seed the lender matrix from lender_product_matrix.md (and the inventory in
investor_underwriting_guide.md). Idempotent — safe to run on every boot."""
import logging
from sqlalchemy.orm import Session
from .models import Lender, Product, LoanType, RateSheetSnapshot
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lender seed data — lifted from lender_product_matrix.md + wholesale_lenders_2026.md
# ---------------------------------------------------------------------------

LENDERS = [
    {
        "slug": "kiavi", "name": "Kiavi",
        "website": "https://kiavi.com/broker",
        "channel": "wholesale",
        "states": ["AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
                    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
                    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA",
                    "WA","WV","WI","WY","DC"],
        "broker_comp_type": "lender_paid_ysp",
        "notes": "ML-driven decisions, fast submit, retail broker-friendly.",
        "products": [
            {"name": "DSCR 30-yr IO", "loan_type": LoanType.dscr_purchase,
             "min_fico": 680, "max_ltv": 80, "min_dscr": 1.0,
             "min_loan": 100000, "max_loan": 3000000,
             "rate_band": "7.00-7.75%", "comp_basis_pts": 1.50,
             "term": "30 year", "interest_only": True,
             "prepay_penalty": "5y step-down",
             "property_types": ["sfr","two_four","five_plus"],
             "docs_required": ["bank_statements","lease_or_rent_schedule","purchase_contract"]},
            {"name": "Bridge Fix & Flip", "loan_type": LoanType.fix_flip,
             "min_fico": 660, "max_ltv": 85, "max_ltc": 100,
             "min_loan": 100000, "max_loan": 2000000,
             "rate_band": "10.50-12.00%", "comp_basis_pts": 2.00,
             "term": "12 month", "interest_only": True,
             "prepay_penalty": "none",
             "property_types": ["sfr","two_four"],
             "docs_required": ["purchase_contract","rehab_scope","arv_comps"]},
        ],
    },
    {
        "slug": "rocket-pro", "name": "Rocket Pro",
        "website": "https://rocketprotogo.com",
        "channel": "wholesale",
        "states": ["AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
                    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
                    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA",
                    "WA","WV","WI","WY"],
        "broker_comp_type": "lender_paid_ysp",
        "notes": "Strong on DSCR refi, retail brand recognition.",
        "products": [
            {"name": "DSCR 30-yr IO", "loan_type": LoanType.dscr_purchase,
             "min_fico": 680, "max_ltv": 80, "min_dscr": 1.0,
             "min_loan": 75000, "max_loan": 3000000,
             "rate_band": "7.00-7.50%", "comp_basis_pts": 1.25,
             "term": "30 year", "interest_only": True,
             "prepay_penalty": "3y step-down",
             "property_types": ["sfr","two_four","five_plus","condo_townhome"]},
            {"name": "DSCR Cash-Out", "loan_type": LoanType.dscr_cashout,
             "min_fico": 700, "max_ltv": 75, "min_dscr": 1.10,
             "min_loan": 75000, "max_loan": 2000000,
             "rate_band": "7.25-7.75%", "comp_basis_pts": 1.25,
             "term": "30 year", "interest_only": True,
             "prepay_penalty": "3y step-down",
             "property_types": ["sfr","two_four","five_plus"]},
        ],
    },
    {
        "slug": "newfi", "name": "Newfi",
        "website": "https://newfi.com",
        "channel": "wholesale",
        "states": ["CA","CO","CT","FL","GA","IL","MA","MD","MI","NC","NJ","NV","NY","OH","OR",
                    "PA","SC","TN","TX","VA","WA","WI","DC"],
        "broker_comp_type": "lender_paid_ysp",
        "notes": "Non-QM, lower credit threshold (620), bridge product available.",
        "products": [
            {"name": "DSCR 30-yr", "loan_type": LoanType.dscr_purchase,
             "min_fico": 620, "max_ltv": 75, "min_dscr": 1.0,
             "min_loan": 100000, "max_loan": 2500000,
             "rate_band": "7.50-8.25%", "comp_basis_pts": 1.50,
             "term": "30 year", "interest_only": True,
             "prepay_penalty": "5y step-down",
             "property_types": ["sfr","two_four","condo_townhome"]},
            {"name": "Bridge 9-month", "loan_type": LoanType.bridge,
             "min_fico": 640, "max_ltv": 75,
             "min_loan": 100000, "max_loan": 2000000,
             "rate_band": "9.50-11.00%", "comp_basis_pts": 2.00,
             "term": "9 month", "interest_only": True,
             "prepay_penalty": "none",
             "property_types": ["sfr","two_four"]},
        ],
    },
    {
        "slug": "visio", "name": "Visio",
        "website": "https://visiolending.com",
        "channel": "wholesale",
        "states": ["AL","AZ","AR","CA","CO","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA",
                    "MD","MA","MI","MN","MS","MO","MT","NE","NV","NJ","NM","NY","NC","ND","OH","OK",
                    "OR","PA","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"],
        "broker_comp_type": "lender_paid_ysp",
        "notes": "Unique property types accepted (condotels, non-warrantable condos, rural).",
        "products": [
            {"name": "DSCR Portfolio", "loan_type": LoanType.dscr_purchase,
             "min_fico": 660, "max_ltv": 75, "min_dscr": 0.75,
             "min_loan": 100000, "max_loan": 2500000,
             "rate_band": "7.25-8.00%", "comp_basis_pts": 1.75,
             "term": "30 year", "interest_only": True,
             "prepay_penalty": "5y step-down",
             "property_types": ["sfr","two_four","condo_townhome","commercial_mixed_use"]},
        ],
    },
    {
        "slug": "lima-one", "name": "Lima One",
        "website": "https://limaone.com",
        "channel": "wholesale",
        "states": ["AL","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY",
                    "LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY",
                    "NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA",
                    "WV","WI","WY","DC"],
        "broker_comp_type": "lender_paid_ysp",
        "notes": "SFR portfolios, repeat borrower discounts.",
        "products": [
            {"name": "DSCR SFR 30-yr", "loan_type": LoanType.dscr_purchase,
             "min_fico": 680, "max_ltv": 80, "min_dscr": 1.0,
             "min_loan": 75000, "max_loan": 2500000,
             "rate_band": "6.95-7.50%", "comp_basis_pts": 1.50,
             "term": "30 year", "interest_only": True,
             "prepay_penalty": "5y step-down",
             "property_types": ["sfr"]},
            {"name": "DSCR Multi 5+", "loan_type": LoanType.dscr_purchase,
             "min_fico": 700, "max_ltv": 75, "min_dscr": 1.20,
             "min_loan": 200000, "max_loan": 5000000,
             "rate_band": "7.00-7.75%", "comp_basis_pts": 1.50,
             "term": "30 year", "interest_only": True,
             "prepay_penalty": "5y step-down",
             "property_types": ["five_plus"]},
        ],
    },
    {
        "slug": "anchor-loans", "name": "Anchor Loans",
        "website": "https://anchorloans.com",
        "channel": "wholesale",
        "states": ["AL","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY",
                    "LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY",
                    "NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA",
                    "WV","WI","WY"],
        "broker_comp_type": "lender_paid_ysp",
        "notes": "Heavy rehab, ground-up construction.",
        "products": [
            {"name": "Construction", "loan_type": LoanType.construction,
             "min_fico": 660, "max_ltc": 75,
             "min_loan": 100000, "max_loan": 3000000,
             "rate_band": "10.50-12.00%", "comp_basis_pts": 2.00,
             "term": "12-18 month", "interest_only": True,
             "prepay_penalty": "none",
             "property_types": ["sfr","condo_townhome","commercial_mixed_use"],
             "docs_required": ["construction_contract","builder_license","draw_schedule"]},
        ],
    },
    {
        "slug": "groundfloor", "name": "Groundfloor",
        "website": "https://groundfloor.com",
        "channel": "wholesale",
        "states": ["CA","FL","GA","IL","IN","LA","MD","MI","MO","NJ","NY","NC","OH","OK","OR","PA",
                    "SC","TN","TX","VA","WA"],
        "broker_comp_type": "lender_paid_ysp",
        "notes": "Crowdfunded, fast close, lighter rehab focus.",
        "products": [
            {"name": "Fix & Flip", "loan_type": LoanType.fix_flip,
             "min_fico": 660, "max_ltv": 80, "max_ltc": 95,
             "min_loan": 50000, "max_loan": 1500000,
             "rate_band": "11.00-12.50%", "comp_basis_pts": 2.50,
             "term": "12 month", "interest_only": True,
             "prepay_penalty": "none",
             "property_types": ["sfr","two_four"],
             "docs_required": ["purchase_contract","rehab_scope","arv_comps"]},
        ],
    },
    {
        "slug": "built", "name": "Built",
        "website": "https://built.ai",
        "channel": "wholesale",
        "states": ["CA","TX","FL","GA","NC","SC","TN","AZ","NV","CO"],
        "broker_comp_type": "lender_paid_ysp",
        "notes": "Spec/pre-sale construction lender.",
        "products": [
            {"name": "Spec Construction", "loan_type": LoanType.construction,
             "min_fico": 680, "max_ltc": 75,
             "min_loan": 250000, "max_loan": 5000000,
             "rate_band": "10.00-11.50%", "comp_basis_pts": 2.00,
             "term": "18 month", "interest_only": True,
             "prepay_penalty": "none",
             "property_types": ["sfr"],
             "docs_required": ["construction_contract","builder_license","draw_schedule","arv_comps"]},
        ],
    },
    {
        "slug": "bridgewell", "name": "BridgeWell",
        "website": "https://bridgewell.com",
        "channel": "wholesale",
        "states": ["CA","TX","FL","GA","NC","AZ","NV","CO","WA","OR"],
        "broker_comp_type": "lender_paid_ysp",
        "notes": "Acquisition + refi bridge.",
        "products": [
            {"name": "Bridge 12-month", "loan_type": LoanType.bridge,
             "min_fico": 680, "max_ltv": 75,
             "min_loan": 100000, "max_loan": 2500000,
             "rate_band": "10.00-11.50%", "comp_basis_pts": 1.75,
             "term": "12 month", "interest_only": True,
             "prepay_penalty": "none",
             "property_types": ["sfr","two_four","condo_townhome"]},
        ],
    },
    {
        "slug": "live-oak", "name": "Live Oak Bank",
        "website": "https://liveoakbank.com",
        "channel": "wholesale",
        "states": ["AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS",
                    "KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM",
                    "NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA",
                    "WA","WV","WI","WY","DC"],
        "broker_comp_type": "lender_paid_ysp",
        "notes": "SBA 7(a), CRE. Strong on commercial, slower than private.",
        "products": [
            {"name": "SBA 7(a)", "loan_type": LoanType.sba_7a,
             "min_fico": 680, "max_ltv": 80, "min_dscr": 1.20,
             "min_loan": 250000, "max_loan": 5000000,
             "rate_band": "prime+2.25-2.75%", "comp_basis_pts": 2.00,
             "term": "10-25 year", "interest_only": False,
             "prepay_penalty": "SBA standard",
             "property_types": ["commercial_mixed_use","five_plus"],
             "docs_required": ["bank_statements","tax_returns","entity_docs","purchase_contract"]},
        ],
    },
    {
        "slug": "byline", "name": "Byline Bank",
        "website": "https://bylinebank.com",
        "channel": "wholesale",
        "states": ["IL","WI","IN","MI","MN","MO","IA","TX","FL","AZ","CO","NV"],
        "broker_comp_type": "lender_paid_ysp",
        "notes": "SBA 504, franchise.",
        "products": [
            {"name": "SBA 504", "loan_type": LoanType.sba_504,
             "min_fico": 680, "max_ltv": 80, "min_dscr": 1.25,
             "min_loan": 250000, "max_loan": 5000000,
             "rate_band": "fixed rate, blended", "comp_basis_pts": 2.00,
             "term": "10-25 year", "interest_only": False,
             "prepay_penalty": "SBA standard",
             "property_types": ["commercial_mixed_use","five_plus"]},
        ],
    },
]


def seed_lenders(db: Session) -> dict:
    """Insert/update all lenders and their products. Returns a count."""
    inserted_lenders = 0
    inserted_products = 0
    for L in LENDERS:
        lender = db.query(Lender).filter_by(slug=L["slug"]).one_or_none()
        if lender is None:
            lender = Lender(
                slug=L["slug"], name=L["name"],
                website=L["website"], channel=L["channel"],
                states=L["states"],
                broker_comp_type=L["broker_comp_type"],
                notes=L["notes"], active=True,
            )
            db.add(lender)
            db.flush()
            inserted_lenders += 1
        else:
            # Update metadata
            lender.name = L["name"]
            lender.website = L["website"]
            lender.channel = L["channel"]
            lender.states = L["states"]
            lender.broker_comp_type = L["broker_comp_type"]
            lender.notes = L["notes"]
            lender.active = True
        for P in L["products"]:
            existing = (
                db.query(Product)
                .filter_by(lender_id=lender.id, name=P["name"])
                .one_or_none()
            )
            data = dict(
                lender_id=lender.id,
                name=P["name"],
                loan_type=P["loan_type"],
                term=P.get("term"),
                min_fico=P.get("min_fico"),
                max_ltv=P.get("max_ltv"),
                min_dscr=P.get("min_dscr"),
                max_ltc=P.get("max_ltc"),
                min_loan=P.get("min_loan"),
                max_loan=P.get("max_loan"),
                property_types=P.get("property_types"),
                occupancy="investor",
                interest_only=P.get("interest_only", True),
                prepay_penalty=P.get("prepay_penalty"),
                rate_band=P.get("rate_band"),
                comp_basis_pts=P.get("comp_basis_pts"),
                notes=P.get("notes", ""),
                docs_required=P.get("docs_required"),
                active=True,
            )
            if existing is None:
                db.add(Product(**data))
                inserted_products += 1
            else:
                for k, v in data.items():
                    setattr(existing, k, v)
    db.commit()
    return {"lenders_added": inserted_lenders, "products_added": inserted_products}


def seed_initial_admin(db: Session, email: str, password: str) -> bool:
    """If `email` doesn't exist as a borrower (admin has no Borrower row in
    v1; in v2 we'll add a Staff table), no-op. Kept for forward compatibility.
    Returns True if anything was done."""
    return False