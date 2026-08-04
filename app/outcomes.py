"""Outcome loop: every terminal deal state feeds back into the matching corpus.

When a deal is funded, declined, or withdrawn, we record:
  - outcome (funded | declined | withdrawn | fell_out | partial)
  - chosen lender + product (for funded)
  - rate at close, comp at close, days-to-fund (for funded)
  - the reason (for declined/fell_out)

This becomes training data for future matching. Similar deals will surface
this outcome as evidence.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .models import Deal, DealOutcome, DealEmbedding, Submission, Lender, Product, Event


def record_outcome(deal_id: int, outcome: str, db: Session,
                   chosen_lender_id: Optional[int] = None,
                   chosen_product_id: Optional[int] = None,
                   rate_at_close: Optional[float] = None,
                   comp_at_close_cents: Optional[int] = None,
                   days_to_fund: Optional[int] = None,
                   declined_reason: Optional[str] = None,
                   fellout_reason: Optional[str] = None,
                   notes: Optional[str] = None) -> DealOutcome:
    """Record the terminal outcome of a deal. Idempotent — overwrites the
    existing outcome row if present.
    """
    deal = db.query(Deal).get(deal_id)
    if deal is None:
        raise ValueError(f"deal {deal_id} not found")

    out = db.query(DealOutcome).filter_by(deal_id=deal_id).one_or_none()
    if out is None:
        out = DealOutcome(deal_id=deal_id, outcome=outcome)
        db.add(out)

    out.outcome = outcome
    out.chosen_lender_id = chosen_lender_id or out.chosen_lender_id
    out.chosen_product_id = chosen_product_id or out.chosen_product_id
    out.rate_at_close = rate_at_close if rate_at_close is not None else out.rate_at_close
    out.comp_at_close_cents = comp_at_close_cents if comp_at_close_cents is not None else out.comp_at_close_cents
    out.days_to_fund = days_to_fund if days_to_fund is not None else out.days_to_fund
    out.declined_reason = declined_reason or out.declined_reason
    out.fellout_reason = fellout_reason or out.fellout_reason
    out.notes = notes or out.notes
    out.recorded_at = datetime.now(timezone.utc)

    # Append to event log
    db.add(Event(
        deal_id=deal_id,
        kind="outcome_recorded",
        actor="broker",
        payload={"outcome": outcome, "lender_id": chosen_lender_id, "rate": rate_at_close},
    ))

    # If funded, also mark the deal closed (if not already)
    if outcome == "funded" and deal.closed_at is None:
        deal.closed_at = datetime.now(timezone.utc)
        deal.stage = "post_close"
        db.add(Event(
            deal_id=deal_id,
            kind="stage_change",
            actor="system",
            payload={"from": deal.stage, "to": "post_close", "trigger": "outcome_funded"},
        ))

    db.commit()
    return out


def get_outcome(deal_id: int, db: Session) -> Optional[DealOutcome]:
    return db.query(DealOutcome).filter_by(deal_id=deal_id).one_or_none()


# ---------------------------------------------------------------------------
# Bulk queries for the broker command surface
# ---------------------------------------------------------------------------

def pipeline_summary(db: Session) -> dict:
    """Counts by stage + outcomes. Used by the broker dashboard."""
    from sqlalchemy import func
    from .models import DealStage

    by_stage = dict(
        db.query(Deal.stage, func.count(Deal.id))
        .group_by(Deal.stage)
        .all()
    )
    by_outcome = dict(
        db.query(DealOutcome.outcome, func.count(DealOutcome.id))
        .group_by(DealOutcome.outcome)
        .all()
    )
    total_funded = by_outcome.get("funded", 0)
    total_deal_attempts = sum(by_stage.values()) + total_funded
    total_comp_cents = (
        db.query(func.coalesce(func.sum(Deal.comp_paid_cents), 0))
        .filter(Deal.comp_paid_cents.isnot(None))
        .scalar() or 0
    )

    return {
        "by_stage": {s.value: by_stage.get(s, 0) for s in DealStage},
        "by_outcome": by_outcome,
        "total_deal_attempts": total_deal_attempts,
        "total_funded": total_funded,
        "total_comp_cents": int(total_comp_cents),
        "fund_rate_pct": (
            round(100 * total_funded / total_deal_attempts, 1)
            if total_deal_attempts > 0 else 0
        ),
    }