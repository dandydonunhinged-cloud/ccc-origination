"""Rate sheet snapshots: daily capture of lender pricing.

Each lender has a `RateSheetSnapshot` row per day per product. Source can be:
  - 'manual'  : a broker pastes a rate sheet from an email or call
  - 'scrape'  : (TODO) automated public rate sheet scrape
  - 'lender_api' : (TODO) direct lender API integration (Kiavi, Arix, Newfi)

Until those integrations ship, the snapshot is manual. The daily task
prompts the broker for fresh rate sheets.
"""
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from .models import RateSheetSnapshot, Product, Lender, Event


def record_snapshot(db: Session, lender_id: int, product_id: Optional[int],
                    rate_low: float, rate_high: float,
                    points: Optional[float] = None,
                    rate_lock_days: Optional[int] = None,
                    source: str = "manual",
                    raw_payload: Optional[dict] = None,
                    notes: Optional[str] = None) -> RateSheetSnapshot:
    snap = RateSheetSnapshot(
        lender_id=lender_id,
        product_id=product_id,
        rate_low=rate_low,
        rate_high=rate_high,
        points=points,
        rate_lock_days=rate_lock_days,
        source=source,
        raw_payload=raw_payload,
        notes=notes,
    )
    db.add(snap)
    db.commit()
    return snap


def latest_for_lender(db: Session, lender_id: int) -> list[RateSheetSnapshot]:
    """Most recent snapshot per product for a lender."""
    from sqlalchemy import func
    sub = (
        db.query(
            RateSheetSnapshot.product_id,
            func.max(RateSheetSnapshot.captured_at).label("max_at"),
        )
        .filter(RateSheetSnapshot.lender_id == lender_id)
        .group_by(RateSheetSnapshot.product_id)
        .subquery()
    )
    return (
        db.query(RateSheetSnapshot)
        .join(sub,
              (RateSheetSnapshot.lender_id == lender_id) &
              (RateSheetSnapshot.product_id == sub.c.product_id) &
              (RateSheetSnapshot.captured_at == sub.c.max_at))
        .all()
    )


def current_rate_band_for_product(db: Session, product_id: int) -> Optional[str]:
    """Return the latest rate band as a string, e.g. '7.00-7.50%'."""
    from sqlalchemy import desc
    snap = (
        db.query(RateSheetSnapshot)
        .filter_by(product_id=product_id)
        .order_by(desc(RateSheetSnapshot.captured_at))
        .first()
    )
    if snap is None:
        return None
    if snap.rate_low is None or snap.rate_high is None:
        return None
    return f"{snap.rate_low:.2f}-{snap.rate_high:.2f}%"


# ---------------------------------------------------------------------------
# Plaid stub: reserve verification structure (no real API call yet)
# ---------------------------------------------------------------------------

def create_plaid_link(db: Session, borrower_id: int, deal_id: Optional[int],
                      link_token: str, item_id: Optional[str] = None,
                      access_token: Optional[str] = None,
                      accounts_json: Optional[dict] = None) -> "PlaidLink":
    """Persist a Plaid link. The full Plaid flow is stubbed for now — when
    PLAID_CLIENT_ID + PLAID_SECRET are set, the auth route will do the
    real link-token exchange."""
    from .models import PlaidLink
    pl = PlaidLink(
        borrower_id=borrower_id,
        deal_id=deal_id,
        link_token=link_token,
        access_token=access_token,
        item_id=item_id,
        accounts_json=accounts_json,
    )
    db.add(pl)
    db.commit()
    return pl


def mark_plaid_verified(db: Session, plaid_link_id: int):
    from .models import PlaidLink
    from datetime import datetime, timezone
    pl = db.query(PlaidLink).get(plaid_link_id)
    if pl:
        pl.verified_at = datetime.now(timezone.utc)
        pl.expires_at = datetime.now(timezone.utc).replace(hour=23, minute=59, second=0)
        db.commit()