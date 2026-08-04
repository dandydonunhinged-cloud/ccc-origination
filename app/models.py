"""CCC Origination — data model.

The full origination domain for an investor-loan business.

Entities:
  Borrower     — the person on the loan (or the entity rep)
  Entity       — the borrowing entity (LLC, trust, corp)
  Property     — the subject real estate
  Deal         — the work order. One per property+loan combo. Stages 1-10.
  Document     — uploaded file (per deal or per borrower)
  Condition    — outstanding UW condition on a submission
  Note         — internal note on a deal
  Event        — append-only audit log (every state change)
  Lender       — wholesale lender
  Product      — a loan product (per lender)
  Submission   — a deal sent to a lender (one deal can have multiple submissions)
  Closing      — the settlement statement + recording
  CompPayment  — the broker compensation event
  MagicLink    — borrower one-time login token
  Session      — broker session

Stages (on Deal.stage):
  1 = lead_acquired
  2 = scenario_analyzed
  3 = first_contact
  4 = documents_collected
  5 = submitted
  6 = underwriting
  7 = clear_to_close
  8 = closing
  9 = post_close
  10 = pipeline_growth (next deal sourced from this one)
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float, Boolean, ForeignKey,
    UniqueConstraint, Index, JSON, Enum as SAEnum
)
from sqlalchemy.orm import relationship, declarative_base, backref
import enum

Base = declarative_base()


def now_utc():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums (stored as strings for readability + schema-evolution safety)
# ---------------------------------------------------------------------------

class DealStage(str, enum.Enum):
    lead_acquired      = "lead_acquired"
    scenario_analyzed  = "scenario_analyzed"
    first_contact      = "first_contact"
    documents_collected = "documents_collected"
    submitted          = "submitted"
    underwriting       = "underwriting"
    clear_to_close     = "clear_to_close"
    closing            = "closing"
    post_close         = "post_close"
    pipeline_growth    = "pipeline_growth"


class LoanType(str, enum.Enum):
    dscr_purchase      = "dscr_purchase"
    dscr_refi          = "dscr_refi"
    dscr_cashout       = "dscr_cashout"
    bridge             = "bridge"
    fix_flip           = "fix_flip"
    construction       = "construction"
    commercial         = "commercial"
    sba_7a             = "sba_7a"
    sba_504            = "sba_504"
    portfolio          = "portfolio"


class PropertyType(str, enum.Enum):
    sfr      = "sfr"
    two_four = "2-4_unit"
    five_plus = "5+_multifamily"
    condo    = "condo_townhome"
    commercial = "commercial_mixed_use"
    land     = "land_with_plans"


class ConditionStatus(str, enum.Enum):
    requested  = "requested"    # we asked borrower for it
    received   = "received"     # borrower uploaded / lender received
    in_review  = "in_review"    # lender is reviewing
    cleared    = "cleared"      # satisfied
    rejected   = "rejected"     # unsatisfiable


class ConditionType(str, enum.Enum):
    bank_statements      = "bank_statements"
    rent_comps           = "rent_comps"
    rehab_scope          = "rehab_scope"
    entity_docs          = "entity_docs"
    insurance             = "insurance"
    appraisal             = "appraisal"
    title_commitment      = "title_commitment"
    survey                 = "survey"
    flood_cert            = "flood_cert"
    purchase_contract     = "purchase_contract"
    lease                  = "lease"
    arv_comps              = "arv_comps"
    construction_contract  = "construction_contract"
    builder_license        = "builder_license"
    other                  = "other"


class SubmissionStatus(str, enum.Enum):
    draft       = "draft"
    submitted   = "submitted"
    in_review   = "in_review"
    conditional = "conditional"
    suspended    = "suspended"   # underwriter paused
    declined    = "declined"
    approved    = "approved"
    withdrawn   = "withdrawn"


class CompType(str, enum.Enum):
    borrower_origination = "borrower_origination"
    lender_ysp            = "lender_ysp"
    processing             = "processing"
    admin                  = "admin"
    referral               = "referral"


# ---------------------------------------------------------------------------
# Core tables
# ---------------------------------------------------------------------------

class Borrower(Base):
    """A natural person who is the contact for a deal (the entity's rep, the
    individual borrower, the guarantor). One Borrower can have many Deals."""
    __tablename__ = "borrowers"

    id            = Column(Integer, primary_key=True)
    full_name     = Column(String(200), nullable=False, index=True)
    email         = Column(String(254), nullable=False, index=True, unique=True)
    phone         = Column(String(40))
    gov_id_type   = Column(String(40))      # drivers_license, passport
    gov_id_last4  = Column(String(8))
    credit_score  = Column(Integer)        # borrower-stated at intake
    created_at    = Column(DateTime, default=now_utc, nullable=False)
    updated_at    = Column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)

    deals         = relationship("Deal", back_populates="borrower", cascade="all, delete-orphan")
    magic_links   = relationship("MagicLink", back_populates="borrower", cascade="all, delete-orphan")


class Entity(Base):
    """Borrowing entity. Many-to-one with Deal (one deal, one entity)."""
    __tablename__ = "entities"

    id           = Column(Integer, primary_key=True)
    legal_name   = Column(String(200), nullable=False, index=True)
    entity_type  = Column(String(40), nullable=False)  # LLC, corp, trust, individual
    ein          = Column(String(20))
    state_formed = Column(String(4))
    good_standing_until = Column(DateTime)
    notes        = Column(Text)
    created_at   = Column(DateTime, default=now_utc, nullable=False)

    deals        = relationship("Deal", back_populates="entity")


class Property(Base):
    """The subject real estate."""
    __tablename__ = "properties"

    id             = Column(Integer, primary_key=True)
    address_line1  = Column(String(200), nullable=False)
    address_line2  = Column(String(200))
    city           = Column(String(120), nullable=False, index=True)
    state          = Column(String(4), nullable=False, index=True)
    zip            = Column(String(12))
    county         = Column(String(120))
    property_type  = Column(SAEnum(PropertyType), nullable=False)
    year_built     = Column(Integer)
    sqft           = Column(Integer)
    beds           = Column(Integer)
    baths          = Column(Float)
    purchase_price = Column(Float)
    as_is_value    = Column(Float)
    arv            = Column(Float)             # after-repair value (flips)
    projected_rent = Column(Float)             # for DSCR
    rehab_budget   = Column(Float)
    notes          = Column(Text)
    created_at     = Column(DateTime, default=now_utc, nullable=False)

    deals          = relationship("Deal", back_populates="property")


class Deal(Base):
    """The work order. One per property+loan+borrower combo."""
    __tablename__ = "deals"

    id              = Column(Integer, primary_key=True)
    public_id       = Column(String(12), unique=True, nullable=False, index=True)  # short URL-safe id
    borrower_id     = Column(Integer, ForeignKey("borrowers.id"), nullable=False)
    entity_id       = Column(Integer, ForeignKey("entities.id"))
    property_id     = Column(Integer, ForeignKey("properties.id"), nullable=False)
    stage           = Column(SAEnum(DealStage), nullable=False, default=DealStage.lead_acquired, index=True)
    loan_type       = Column(SAEnum(LoanType), nullable=False)
    target_loan_amount = Column(Float, nullable=False)
    down_payment    = Column(Float)
    target_close    = Column(DateTime)
    lead_source     = Column(String(80), index=True)   # biggerpockets, facebook, referral, etc.
    referral_partner_id = Column(Integer)  # plain int — no FK to avoid ambiguous-join with borrower_id
    notes           = Column(Text)          # broker's free-form notes on the deal
    # Stage timestamps
    stage_entered_at = Column(DateTime, default=now_utc, nullable=False)
    created_at      = Column(DateTime, default=now_utc, nullable=False)
    updated_at      = Column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)
    # Scenario analysis output
    scenario_report  = Column(JSON)            # full 5-pass output (see scenario.py)
    scenario_score   = Column(Float)           # 0-100, overall fit score
    # Closing
    ctc_at           = Column(DateTime)
    closed_at        = Column(DateTime, index=True)
    recording_number = Column(String(80))
    comp_paid_cents  = Column(Integer)         # total cents
    # Post-close
    next_rate_reset  = Column(DateTime)         # 30/90/180 day check

    borrower       = relationship("Borrower", back_populates="deals", foreign_keys=[borrower_id])
    entity         = relationship("Entity", back_populates="deals")
    property       = relationship("Property", back_populates="deals")
    # referral_partner_id is a plain FK column; we look up the Borrower manually.
    # Avoids the ambiguous-FK problem with two FKs from deals to borrowers.
    documents      = relationship("Document", back_populates="deal", cascade="all, delete-orphan")
    conditions     = relationship("Condition", back_populates="deal", cascade="all, delete-orphan")
    submissions    = relationship("Submission", back_populates="deal", cascade="all, delete-orphan")
    closings       = relationship("Closing", back_populates="deal", cascade="all, delete-orphan")
    comp_payments  = relationship("CompPayment", back_populates="deal", cascade="all, delete-orphan")
    # NOTE: the `Note` relationship (free-form broker notes) is accessed via
    # db.query(Note).filter_by(deal_id=...) — we don't expose it as `Deal.notes`
    # because the field name collides with the notes Column.

    __table_args__ = (
        Index("idx_deal_stage_created", "stage", "created_at"),
    )


class Document(Base):
    """A file attached to a deal (or a borrower). Uploaded by borrower via
    presigned URL to DO Spaces. Versioned."""
    __tablename__ = "documents"

    id              = Column(Integer, primary_key=True)
    deal_id         = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    filename        = Column(String(200), nullable=False)
    content_type    = Column(String(80))
    size_bytes      = Column(Integer)
    spaces_key      = Column(String(400), nullable=False)  # bucket path
    spaces_url      = Column(String(500))                  # public URL if known
    purpose         = Column(String(80), nullable=False, index=True)  # bank_stmt, rent_comp, etc.
    version         = Column(Integer, default=1, nullable=False)
    uploaded_by     = Column(String(40), default="borrower")  # borrower, broker
    uploader_email  = Column(String(254))
    # Link to a Condition (when the doc was uploaded to clear a condition)
    clears_condition_id = Column(Integer, ForeignKey("conditions.id"))
    created_at      = Column(DateTime, default=now_utc, nullable=False)

    deal          = relationship("Deal", back_populates="documents")
    clears_condition = relationship("Condition", foreign_keys=[clears_condition_id])


class Condition(Base):
    """An outstanding underwriting condition on a submission. Has its own
    playbook (see condition_playbooks.py)."""
    __tablename__ = "conditions"

    id              = Column(Integer, primary_key=True)
    deal_id         = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    submission_id   = Column(Integer, ForeignKey("submissions.id"), index=True)
    condition_type  = Column(SAEnum(ConditionType), nullable=False)
    status          = Column(SAEnum(ConditionStatus), nullable=False, default=ConditionStatus.requested, index=True)
    description     = Column(Text)
    requested_at    = Column(DateTime, default=now_utc, nullable=False)
    received_at     = Column(DateTime)
    cleared_at      = Column(DateTime)
    due_by          = Column(DateTime)
    notes           = Column(Text)

    deal          = relationship("Deal", back_populates="conditions")
    submission    = relationship("Submission", back_populates="conditions")
    documents     = relationship("Document", foreign_keys=[Document.clears_condition_id], overlaps="clears_condition")


class Note(Base):
    """Internal note on a deal. Borrower never sees this."""
    __tablename__ = "notes"

    id           = Column(Integer, primary_key=True)
    deal_id      = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    author       = Column(String(120), nullable=False)  # "don" or a staff email
    body         = Column(Text, nullable=False)
    created_at   = Column(DateTime, default=now_utc, nullable=False)

# Note: no back_populates — we don't expose Deal.notes as a relationship
    # (the field name collides with the notes Column). Look up notes via
    # db.query(Note).filter_by(deal_id=deal.id).order_by(Note.created_at.desc()).
    deal         = relationship("Deal")


class Event(Base):
    """Append-only audit log. Every state change, every submission,
    every condition added/cleared, every email sent."""
    __tablename__ = "events"

    id           = Column(Integer, primary_key=True)
    deal_id      = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    kind         = Column(String(60), nullable=False, index=True)  # stage_change, condition_added, doc_uploaded, email_sent, etc.
    actor        = Column(String(120))                              # don, borrower, system, lender_name
    payload      = Column(JSON)                                     # structured details
    created_at   = Column(DateTime, default=now_utc, nullable=False, index=True)

    deal         = relationship("Deal")


# ---------------------------------------------------------------------------
# Lender matrix
# ---------------------------------------------------------------------------

class Lender(Base):
    """A wholesale lender. seed from lender_product_matrix.md."""
    __tablename__ = "lenders"

    id              = Column(Integer, primary_key=True)
    slug            = Column(String(80), unique=True, nullable=False)
    name            = Column(String(200), nullable=False)
    website         = Column(String(200))
    channel         = Column(String(40))   # broker, retail, wholesale
    states          = Column(JSON)         # list of state codes
    broker_comp_type = Column(String(80))
    notes           = Column(Text)
    active          = Column(Boolean, default=True, nullable=False)
    created_at      = Column(DateTime, default=now_utc, nullable=False)

    products        = relationship("Product", back_populates="lender", cascade="all, delete-orphan")


class Product(Base):
    """A loan product from a lender. The matcher scores deals against these."""
    __tablename__ = "products"

    id               = Column(Integer, primary_key=True)
    lender_id        = Column(Integer, ForeignKey("lenders.id"), nullable=False, index=True)
    name             = Column(String(200), nullable=False)
    loan_type        = Column(SAEnum(LoanType), nullable=False, index=True)
    term             = Column(String(40))
    min_fico         = Column(Integer)
    max_ltv          = Column(Float)
    max_cltv         = Column(Float)
    min_dscr         = Column(Float)
    max_ltc          = Column(Float)             # for construction
    min_loan         = Column(Integer)
    max_loan         = Column(Integer)
    property_types   = Column(JSON)
    occupancy        = Column(String(40))        # investor, owner_occ, both
    interest_only    = Column(Boolean, default=True)
    prepay_penalty   = Column(String(80))
    rate_band        = Column(String(40))        # "7.00-7.50%"
    rate_lock_days   = Column(Integer)
    docs_required    = Column(JSON)             # per-loan-type doc list
    comp_basis_pts   = Column(Float)            # points to broker
    notes            = Column(Text)
    active           = Column(Boolean, default=True, nullable=False)
    created_at       = Column(DateTime, default=now_utc, nullable=False)

    lender           = relationship("Lender", back_populates="products")
    submissions      = relationship("Submission", back_populates="product")

    __table_args__ = (
        UniqueConstraint("lender_id", "name", name="uq_product_lender_name"),
    )


# ---------------------------------------------------------------------------
# Submissions + closing + comp
# ---------------------------------------------------------------------------

class Submission(Base):
    """A deal sent to a specific lender. One deal can have multiple submissions
    (primary + fallback). Tracks UW status, the lender's AE, the comp basis."""
    __tablename__ = "submissions"

    id              = Column(Integer, primary_key=True)
    deal_id         = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    product_id      = Column(Integer, ForeignKey("products.id"), nullable=False)
    status          = Column(SAEnum(SubmissionStatus), nullable=False, default=SubmissionStatus.draft, index=True)
    is_primary      = Column(Boolean, default=True, nullable=False)
    lender_loan_id  = Column(String(120))      # the lender's internal ID
    ae_name         = Column(String(120))       # lender's account exec
    ae_email        = Column(String(254))
    submitted_at    = Column(DateTime)
    last_status_at  = Column(DateTime)
    ctc_at          = Column(DateTime)
    declined_reason = Column(Text)
    notes           = Column(Text)
    created_at      = Column(DateTime, default=now_utc, nullable=False)
    updated_at      = Column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)

    deal            = relationship("Deal", back_populates="submissions")
    product         = relationship("Product", back_populates="submissions")
    conditions      = relationship("Condition", back_populates="submission")


class Closing(Base):
    """The settlement statement / CD review. CTC + funding + recording all live here."""
    __tablename__ = "closings"

    id              = Column(Integer, primary_key=True)
    deal_id         = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    closing_date    = Column(DateTime)
    cdc_received_at = Column(DateTime)
    title_company   = Column(String(200))
    title_agent     = Column(String(200))
    insurance_agent = Column(String(200))
    insurance_bound = Column(Boolean, default=False)
    cash_to_close_cents = Column(Integer)
    loan_amount_cents   = Column(Integer)
    rate            = Column(Float)
    term_months     = Column(Integer)
    borrower_origination_cents = Column(Integer)
    lender_ysp_cents  = Column(Integer)
    processing_cents = Column(Integer)
    admin_cents       = Column(Integer)
    # Final recording
    recorded_at     = Column(DateTime)
    recording_number = Column(String(80))
    wire_confirmed_at = Column(DateTime)
    notes           = Column(Text)
    created_at      = Column(DateTime, default=now_utc, nullable=False)
    updated_at      = Column(DateTime, default=now_utc, onupdate=now_utc, nullable=False)

    deal            = relationship("Deal", back_populates="closings")


class CompPayment(Base):
    """A broker compensation payment. Multiple per deal (origination + YSP + referral)."""
    __tablename__ = "comp_payments"

    id          = Column(Integer, primary_key=True)
    deal_id     = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    comp_type   = Column(SAEnum(CompType), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    payer       = Column(String(200))    # lender name, borrower, or referral
    received_at = Column(DateTime, default=now_utc, nullable=False)
    paid_to     = Column(String(200))    # broker entity
    notes       = Column(Text)
    created_at  = Column(DateTime, default=now_utc, nullable=False)

    deal        = relationship("Deal", back_populates="comp_payments")


# ---------------------------------------------------------------------------
# Auth: broker sessions + borrower magic links
# ---------------------------------------------------------------------------

class Session(Base):
    """A broker's signed session token (server-side reference for revocation)."""
    __tablename__ = "sessions"

    id          = Column(Integer, primary_key=True)
    user_email  = Column(String(254), nullable=False, index=True)
    token_hash  = Column(String(64), nullable=False, unique=True, index=True)  # sha256 of cookie value
    created_at  = Column(DateTime, default=now_utc, nullable=False)
    expires_at  = Column(DateTime, nullable=False, index=True)
    revoked_at  = Column(DateTime)


class MagicLink(Base):
    """A one-time login token sent to a borrower's email. Burned on use."""
    __tablename__ = "magic_links"

    id            = Column(Integer, primary_key=True)
    borrower_id   = Column(Integer, ForeignKey("borrowers.id"), nullable=False, index=True)
    token_hash    = Column(String(64), nullable=False, unique=True, index=True)
    purpose       = Column(String(40), default="login")  # login, doc_request, message
    redirect_after = Column(String(400))
    created_at    = Column(DateTime, default=now_utc, nullable=False)
    expires_at    = Column(DateTime, nullable=False, index=True)
    used_at       = Column(DateTime)

    borrower      = relationship("Borrower", back_populates="magic_links")


# ---------------------------------------------------------------------------
# AI matching: embeddings, outcomes, rate sheets
# ---------------------------------------------------------------------------

class DealEmbedding(Base):
    """Vector embedding of a deal's full narrative + structured fields.
    Used for similarity search against historical funded deals.
    Stored as a JSON array of floats in SQLite (sqlite-vec handles it natively;
    on Postgres, swap to the pgvector column type)."""
    __tablename__ = "deal_embeddings"

    id             = Column(Integer, primary_key=True)
    deal_id        = Column(Integer, ForeignKey("deals.id"), nullable=False, unique=True, index=True)
    embedding_json = Column(Text, nullable=False)   # JSON array of floats
    model_name     = Column(String(80), nullable=False)
    dim            = Column(Integer, nullable=False)
    # Cached narrative (so we can re-embed on model change without recomputing)
    narrative      = Column(Text, nullable=False)
    created_at     = Column(DateTime, default=now_utc, nullable=False)

    deal           = relationship("Deal")


class DealOutcome(Base):
    """The terminal state of a deal — funded, declined, withdrawn, etc.
    Feeds the outcome loop: similar past deals inform future matches."""
    __tablename__ = "deal_outcomes"

    id             = Column(Integer, primary_key=True)
    deal_id        = Column(Integer, ForeignKey("deals.id"), nullable=False, unique=True, index=True)
    outcome        = Column(String(40), nullable=False, index=True)
    # outcome is one of: funded, declined, withdrawn, fell_out, partial
    chosen_lender_id     = Column(Integer, ForeignKey("lenders.id"))
    chosen_product_id    = Column(Integer, ForeignKey("products.id"))
    rate_at_close        = Column(Float)
    comp_at_close_cents  = Column(Integer)
    days_to_fund          = Column(Integer)
    declined_reason      = Column(Text)
    fellout_reason       = Column(Text)
    notes                = Column(Text)
    recorded_at          = Column(DateTime, default=now_utc, nullable=False)

    deal                 = relationship("Deal")
    chosen_lender        = relationship("Lender")
    chosen_product       = relationship("Product")


class RateSheetSnapshot(Base):
    """A snapshot of a lender's rate sheet at a point in time. Used to
    build rate-band history + verify current pricing."""
    __tablename__ = "rate_sheet_snapshots"

    id              = Column(Integer, primary_key=True)
    lender_id       = Column(Integer, ForeignKey("lenders.id"), nullable=False, index=True)
    product_id      = Column(Integer, ForeignKey("products.id"), index=True)
    captured_at     = Column(DateTime, default=now_utc, nullable=False, index=True)
    rate_low        = Column(Float)
    rate_high       = Column(Float)
    points          = Column(Float)
    rate_lock_days  = Column(Integer)
    source          = Column(String(80))   # manual, scrape, lender_api
    raw_payload     = Column(JSON)
    notes           = Column(Text)

    lender          = relationship("Lender")
    product         = relationship("Product")


class PlaidLink(Base):
    """A borrower's linked bank account, used for reserve verification.
    Stores the Plaid link_token / access_token + the snapshot of accounts."""
    __tablename__ = "plaid_links"

    id              = Column(Integer, primary_key=True)
    borrower_id     = Column(Integer, ForeignKey("borrowers.id"), nullable=False, index=True)
    deal_id         = Column(Integer, ForeignKey("deals.id"), index=True)
    link_token      = Column(String(200))
    access_token    = Column(String(200))
    item_id         = Column(String(80))
    accounts_json   = Column(JSON)             # {account_id: {name, mask, type, balances}}
    verified_at     = Column(DateTime)
    expires_at      = Column(DateTime)
    created_at      = Column(DateTime, default=now_utc, nullable=False)

    borrower        = relationship("Borrower")
    deal            = relationship("Deal")


class Message(Base):
    """A message on a deal — between borrower and broker, or broker-only.
    Borrower-portal messages are visible to the borrower; internal_notes are not."""
    __tablename__ = "messages"

    id           = Column(Integer, primary_key=True)
    deal_id      = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    from_party   = Column(String(40), nullable=False)  # borrower, broker, system
    from_email   = Column(String(254))
    body         = Column(Text, nullable=False)
    visible_to_borrower = Column(Boolean, default=True, nullable=False)
    created_at   = Column(DateTime, default=now_utc, nullable=False, index=True)

    deal         = relationship("Deal")


class ScheduleTask(Base):
    """A scheduled task (APScheduler-backed). The 30/90/180 day rate-reset
    checks, daily UW monitoring, condition-overdue alerts."""
    __tablename__ = "schedule_tasks"

    id           = Column(Integer, primary_key=True)
    kind         = Column(String(40), nullable=False, index=True)  # rate_reset, daily_uw, condition_overdue
    deal_id      = Column(Integer, ForeignKey("deals.id"), index=True)
    run_at       = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime, index=True)
    payload      = Column(JSON)
    result       = Column(JSON)
    created_at   = Column(DateTime, default=now_utc, nullable=False)

    deal         = relationship("Deal")