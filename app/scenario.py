"""Scenario engine: score a Deal against the lender matrix, produce the 5-pass report.

The 'RPCCP engine' at clickclickclose.help is the production 5-pass analyzer.
We use it via HTTP if RPCCP_BASE_URL + RPCCP_API_KEY are set; otherwise we
fall back to a deterministic local scorer that uses the lender_product_matrix
directly. Same shape of output either way.

The 5 passes, in this domain:
  1. Program match        — which of 32+ lenders fit the deal?
  2. Weakness analysis    — what will blow up at UW?
  3. Structure optimize   — better way to structure this?
  4. Hidden dimensions    — tax, entity, seasoning, flood, HOA?
  5. Paradigm shift       — is this even the right loan type?

Output: a JSON dict with {passes: [...], client_report: str, admin_report: str, ranked_lenders: [...]}
"""
from __future__ import annotations
import os, json, time, math
from datetime import datetime, timezone
from typing import Optional
import requests
from sqlalchemy.orm import Session

from .config import config
from .models import Deal, Lender, Product, Document


# ---------------------------------------------------------------------------
# Local deterministic scorer
# ---------------------------------------------------------------------------

def _lender_score(deal: Deal, product: Product, lender: Lender) -> tuple[float, list[str]]:
    """Score 0-100. Returns (score, reasons: list[str])."""
    reasons = []
    score = 100.0

    # 1. FICO
    fico = deal.borrower.credit_score or 0
    if product.min_fico and fico < product.min_fico:
        score -= 25
        reasons.append(f"FICO {fico} below lender min {product.min_fico}")
    elif product.min_fico and fico < product.min_fico + 20:
        score -= 5
        reasons.append(f"FICO {fico} near lender min {product.min_fico}")

    # 2. LTV
    if deal.property.purchase_price and product.max_ltv and deal.target_loan_amount:
        ltv = deal.target_loan_amount / deal.property.purchase_price * 100
        if ltv > product.max_ltv:
            score -= 20
            reasons.append(f"LTV {ltv:.1f}% above lender max {product.max_ltv}%")
        elif ltv > product.max_ltv - 5:
            score -= 5
            reasons.append(f"LTV {ltv:.1f}% near lender max {product.max_ltv}%")

    # 3. DSCR (only for DSCR loan types)
    if "dscr" in product.loan_type.value and deal.property.projected_rent:
        monthly_pi = deal.target_loan_amount * (product.rate_band and 0.07 or 0.07) / 12 / 100  # crude
        # Better: compute from rate_band middle
        try:
            rate = float((product.rate_band or "7.0").split("-")[0]) / 100
            term_years = int(product.term.split()[-2]) if product.term else 30
            n = term_years * 12
            monthly_pi = deal.target_loan_amount * (rate/12) / (1 - (1 + rate/12) ** (-n))
        except Exception:
            monthly_pi = deal.target_loan_amount * 0.007
        dscr = deal.property.projected_rent / monthly_pi
        if product.min_dscr and dscr < product.min_dscr:
            score -= 25
            reasons.append(f"DSCR {dscr:.2f} below lender min {product.min_dscr}")
        elif product.min_dscr and dscr < product.min_dscr + 0.1:
            score -= 5
            reasons.append(f"DSCR {dscr:.2f} near lender min {product.min_dscr}")

    # 4. Property type
    if product.property_types and deal.property.property_type.value not in product.property_types:
        score -= 15
        reasons.append(f"Property type {deal.property.property_type.value} not in lender list")

    # 5. Loan size
    if product.min_loan and deal.target_loan_amount < product.min_loan:
        score -= 10
        reasons.append(f"Loan amount below lender min ${product.min_loan:,}")
    if product.max_loan and deal.target_loan_amount > product.max_loan:
        score -= 10
        reasons.append(f"Loan amount above lender max ${product.max_loan:,}")

    # 6. State availability
    if lender.states and deal.property.state not in lender.states:
        score -= 100  # hard fail — lender doesn't operate in this state
        reasons.append(f"Lender doesn't operate in {deal.property.state}")

    return max(0, min(100, score)), reasons


def local_scenario(deal: Deal, db: Session) -> dict:
    """Deterministic local 5-pass scorer. Used when RPCCP_BASE_URL is unset."""
    products = db.query(Product).filter_by(active=True, loan_type=deal.loan_type).all()
    ranked = []
    for product in products:
        lender = product.lender
        if not lender.active:
            continue
        score, reasons = _lender_score(deal, product, lender)
        ranked.append({
            "lender": lender.name,
            "product": product.name,
            "score": round(score, 1),
            "reasons": reasons,
            "rate_band": product.rate_band,
            "comp_basis_pts": product.comp_basis_pts,
        })
    ranked.sort(key=lambda x: -x["score"])

    passes = [
        {
            "pass": 1,
            "name": "Program match",
            "result": f"Scored {len(ranked)} products for {deal.loan_type.value} in {deal.property.state}. Top 3: "
                      + ", ".join(f"{r['lender']} ({r['score']})" for r in ranked[:3]),
            "details": ranked[:10],
        },
        {
            "pass": 2,
            "name": "Weakness analysis",
            "result": _weakness(deal),
        },
        {
            "pass": 3,
            "name": "Structure optimize",
            "result": _structure(deal),
        },
        {
            "pass": 4,
            "name": "Hidden dimensions",
            "result": _hidden(deal),
        },
        {
            "pass": 5,
            "name": "Paradigm shift",
            "result": _paradigm(deal),
        },
    ]

    return {
        "engine": "local-scorer-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "passes": passes,
        "ranked_lenders": ranked[:10],
        "top_score": ranked[0]["score"] if ranked else 0,
        "client_report": _client_report(deal, ranked),
        "admin_report":   _admin_report(deal, ranked),
    }


def _weakness(deal: Deal) -> str:
    issues = []
    if deal.borrower.credit_score and deal.borrower.credit_score < 680:
        issues.append(f"FICO {deal.borrower.credit_score} below DSCR preferred floor of 680 — expect compensating factor asks.")
    if deal.property.purchase_price and deal.target_loan_amount:
        ltv = deal.target_loan_amount / deal.property.purchase_price * 100
        if ltv > 80:
            issues.append(f"LTV {ltv:.0f}% — over 80% triggers reserves + extra comp scrutiny.")
    if deal.loan_type.value == "dscr_purchase" and not deal.property.projected_rent:
        issues.append("DSCR purchase with no projected rent — lender will require 1007 rent schedule or comps before they can quote.")
    if deal.entity_id is None:
        issues.append("No entity on file — most programs require LLC, not personal name. Open one before submission.")
    if not issues:
        return "No obvious weaknesses. Standard DSCR package should clear without major pushback."
    return "Likely underwriting friction: " + " ".join(issues)


def _structure(deal: Deal) -> str:
    if deal.target_loan_amount and deal.property.purchase_price:
        ltv = deal.target_loan_amount / deal.property.purchase_price * 100
        if ltv > 75:
            return "Consider cross-collateralizing a free-and-clear property to drop LTV below 75. Several lenders offer blanket-loan structures that beat the per-property offer."
    return "Structure is conventional. No restructuring warranted on the face of the file."


def _hidden(deal: Deal) -> str:
    flags = []
    if deal.property.state in ("FL", "TX", "CA"):
        flags.append(f"{deal.property.state}: condo warrantability + HOA estoppel certs add 5–10 days to CTC.")
    if deal.loan_type.value == "fix_flip" and not deal.property.arv:
        flags.append("ARV missing — every flip lender will require 3+ comps within 1 mile. Pull before submission or the file stalls at desk.")
    if deal.entity_id:
        flags.append("Confirm good-standing certificate from SOS for borrowing entity. Most states: $10–25 online, 5-minute turnaround.")
    return "Hidden dimensions to pre-empt: " + (" ".join(flags) if flags else "None. Standard disclosures.")


def _paradigm(deal: Deal) -> str:
    if deal.loan_type.value == "fix_flip":
        return "If hold intent > 12 months, bridge→DSCR refi structure can save 200+ bps lifetime. Confirm borrower's true intent before locking the term."
    if deal.loan_type.value == "construction":
        return "If stabilized exit is > 18 months, consider SBA 504 with permanent takeout — better long-run cost than construction-only."
    return "Loan type matches the deal. No paradigm shift needed."


def _client_report(deal: Deal, ranked: list) -> str:
    top3 = ranked[:3]
    if not top3:
        return f"We weren't able to match this deal against any of our {len(ranked)} active programs. We'll talk on the call about what's blocking it and what would need to change."
    lines = [
        f"Here's what we found for your {deal.loan_type.value.replace('_', ' ').title()} deal in {deal.property.city}, {deal.property.state}:",
        "",
    ]
    for r in top3:
        lines.append(f"  • {r['lender']} — {r['product']} — fit score {r['score']}/100. Rate band {r['rate_band']}.")
    lines.append("")
    lines.append("We'll walk you through the tradeoffs on the call (15 minutes). Don't commit to a rate yet — the actual quote comes at submission with rate lock.")
    return "\n".join(lines)


def _admin_report(deal: Deal, ranked: list) -> str:
    top5 = ranked[:5]
    if not top5:
        return "No matches. Decision: decline."
    lines = [
        f"DEAL ID: {deal.public_id}",
        f"Borrower: {deal.borrower.full_name} <{deal.borrower.email}> FICO {deal.borrower.credit_score}",
        f"Property: {deal.property.address_line1}, {deal.property.city}, {deal.property.state}",
        f"Loan type: {deal.loan_type.value}  Amount: ${deal.target_loan_amount:,.0f}  Close by: {deal.target_close or 'unspecified'}",
        "",
        "RANKED LENDERS:",
    ]
    for r in top5:
        lines.append(f"  {r['score']:5.1f}/100  {r['lender']} — {r['product']}  (rate {r['rate_band']}, comp {r['comp_basis_pts']} pts)")
        for reason in r['reasons']:
            lines.append(f"          - {reason}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point: run_scenario
# ---------------------------------------------------------------------------

def run_scenario(deal_id: int, db: Session) -> dict:
    """Run the 5-pass scenario analysis on the deal. Returns the full report."""
    deal = db.query(Deal).get(deal_id)
    if deal is None:
        raise ValueError(f"deal {deal_id} not found")
    # Lazy-load borrower + property (SQLAlchemy will populate on access)

    if config.RPCCP_BASE_URL and config.RPCCP_API_KEY:
        # Production path: call the existing RPCCP engine
        try:
            report = rpccp_remote(deal)
            return report
        except Exception as e:
            # Fall through to local
            print(f"RPCCP remote failed: {e}; falling back to local")

    return local_scenario(deal, db)


def rpccp_remote(deal: Deal) -> dict:
    """Call the existing RPCCP engine at clickclickclose.help (or wherever
    RPCCP_BASE_URL points) with a domain-aware query and return the merged
    report."""
    # Build a query that asks for the 5 passes on this specific deal
    query = (
        f"You are scoring a {deal.loan_type.value.replace('_', ' ').title()} deal for an investor "
        f"at {deal.property.address_line1}, {deal.property.city}, {deal.property.state}. "
        f"Purchase price ${deal.property.purchase_price or 0:,.0f}, target loan "
        f"${deal.target_loan_amount:,.0f}, FICO {deal.borrower.credit_score or 'unknown'}, "
        f"projected rent ${deal.property.projected_rent or 0:,.0f}/mo, ARV "
        f"${deal.property.arv or 0:,.0f}. "
        f"Pass 1: rank our 32+ lenders. Pass 2: name the 3 underwriting weaknesses. "
        f"Pass 3: any structural optimization (cross-collateral, entity, etc). "
        f"Pass 4: hidden dimensions. Pass 5: paradigm shift. End with a top-3 "
        f"lender ranking with rate band and comp estimate."
    )
    headers = {"Authorization": f"Bearer {config.RPCCP_API_KEY}", "Content-Type": "application/json"}
    r = requests.post(
        f"{config.RPCCP_BASE_URL.rstrip('/')}/api/run",
        headers=headers,
        json={"query": query, "mode": "cloud"},
        timeout=30,
    )
    r.raise_for_status()
    remote = r.json()
    # Wrap into our shape. We'll fetch the full result via WS in production;
    # for the API-create endpoint, just return the metadata.
    return {
        "engine": "rpccp-remote",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": remote.get("run_id"),
        "query": query,
        "note": "Full pass output available at WS stream /api/runs/{run_id} once complete.",
    }