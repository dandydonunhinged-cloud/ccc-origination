"""Condition playbooks: per-condition-type response templates + clear-criteria.

The 12 condition types in the PHASE_6 underwriting playbook, each with:
  - what it usually asks for
  - the borrower's most common failure modes
  - the response template (LOE wording, doc checklist, etc.)
  - what 'cleared' looks like for the lender

Used by:
  - the broker command surface (when adding a condition, surface the playbook)
  - the borrower portal (when we send them a checklist)
  - the daily monitoring job (when a condition is overdue)
"""
from typing import Optional


PLAYBOOKS: dict[str, dict] = {
    "bank_statements": {
        "title": "Additional bank statements",
        "what_they_want": "More months, missing pages, or a large-deposit explanation.",
        "borrower_failure_modes": [
            "Sent the wrong month's statement",
            "Missing pages on a multi-page statement",
            "Large unexplained deposit triggers 'source of funds' ask",
        ],
        "response_template": (
            "Ask the borrower for the specific months/pages the lender flagged. "
            "If it's a large deposit, get a Letter of Explanation (LOE) with the source: "
            "sale of vehicle, insurance proceeds, tax refund, gift, business income, etc. "
            "Attach supporting documentation when available."
        ),
        "loe_template": (
            '"The deposit of $[AMOUNT] on [DATE] in my [BANK] account was from '
            '[SOURCE: sale of vehicle / insurance proceeds / tax refund / gift / '
            'business income / other]. Documentation: [attach if available]."'
        ),
        "cleared_when": "Lender acknowledges receipt in portal. Same-day if before 2pm CT.",
        "sla_hours": 4,
    },

    "rent_comps": {
        "title": "Rent comparables",
        "what_they_want": "Third-party confirmation of the rent estimate.",
        "borrower_failure_modes": [
            "Comps too far from subject (> 1 mile)",
            "Different bedroom count",
            "Using Airbnb comps for non-STR properties",
        ],
        "response_template": (
            "Pull 3–5 MLS or AirDNA comps within 1 mile, similar bedroom count, "
            "with rent history. Avoid Airbnb comps for non-short-term properties. "
            "If subject is a condo, comp against condos in the same complex first."
        ),
        "cleared_when": "Lender's UW system re-runs DSCR with the new comps and shows ≥ their floor.",
        "sla_hours": 24,
    },

    "rehab_scope": {
        "title": "Rehab scope of work (line items)",
        "what_they_want": "Scope broken to line items with cost estimates.",
        "borrower_failure_modes": [
            "Scope is a paragraph, not line items",
            "No unit counts (sqft, fixture count, etc.)",
            "Cost estimates missing or vague ('TBD')",
        ],
        "response_template": (
            "Re-issue the scope as a line-item sheet with categories: "
            "demo, framing, electrical, plumbing, HVAC, finishes, landscaping. "
            "Each line has a unit count and a unit cost. Contractor must sign off."
        ),
        "cleared_when": "Lender desk underwriter approves the scope and cost basis.",
        "sla_hours": 24,
    },

    "entity_docs": {
        "title": "Entity documentation (LLC)",
        "what_they_want": "Full chain: operating agreement, EIN, certificate of good standing, articles of organization.",
        "borrower_failure_modes": [
            "Operating agreement never updated since formation",
            "Good standing expired",
            "Missing EIN letter (just IRS confirmation)",
        ],
        "response_template": (
            "Pull from the Secretary of State website for the good standing certificate "
            "(most states: $10–25, 5 minutes online). Operating agreement: check the signing page "
            "and the membership interest table — many LLCs never updated these after formation."
        ),
        "cleared_when": "Lender signs off on entity docs and the borrowing entity is on file.",
        "sla_hours": 4,
    },

    "insurance": {
        "title": "Hazard insurance binder",
        "what_they_want": "Insurance bound (not just quoted) before funding.",
        "borrower_failure_modes": [
            "Quote is bound but the binder hasn't been issued",
            "Coverage amount is wrong (replacement cost vs loan amount)",
            "Lender not listed as mortgagee / loss payee",
        ],
        "response_template": (
            "Coordinate with insurance agent 48 hours before closing. Confirm: "
            "(1) coverage ≥ loan amount, (2) effective date on or before closing, "
            "(3) lender listed as mortgagee with their loan ID, "
            "(4) binder — not just quote — is in the lender's portal."
        ),
        "cleared_when": "Lender sees the binder in their insurance verification system.",
        "sla_hours": 48,
    },

    "appraisal": {
        "title": "Appraisal",
        "what_they_want": "An appraisal order placed through their AMC.",
        "borrower_failure_modes": [
            "Borrower ordered their own appraisal (lender won't accept it)",
            "Appraisal came in low — deal dies unless borrower brings more cash",
        ],
        "response_template": (
            "Appraisal must be ordered through the lender's AMC. Cost $400–700. "
            "Turn time 5–10 days. If it comes in low, the borrower's options are: "
            "(a) bring more cash to close the LTV gap, (b) restructure to a lower LTV program, "
            "(c) walk. Don't fight the appraiser."
        ),
        "cleared_when": "Lender accepts the appraisal value and the file moves to CTC.",
        "sla_hours": 168,  # 7 days for the AMC
    },

    "title_commitment": {
        "title": "Title commitment",
        "what_they_want": "Title binder/commitment issued by the lender's title company.",
        "borrower_failure_modes": [
            "Existing liens the borrower didn't disclose",
            "Undisclosed heirs on inherited property",
            "Survey defect (encroachment, missing easement)",
        ],
        "response_template": (
            "Title work is the lender's title company's job. Don't get your own. "
            "The commitment comes 5–10 days before closing. Read the Schedule B "
            "(exceptions) carefully. Anything unexpected = call Don immediately, "
            "not the day before closing."
        ),
        "cleared_when": "Title commitment issued with no unacceptable exceptions.",
        "sla_hours": 168,
    },

    "survey": {
        "title": "Survey",
        "what_they_want": "ALTA survey, especially for commercial or rural properties.",
        "borrower_failure_modes": [
            "Borrower doesn't have a recent survey",
            "Survey shows an encroachment that wasn't disclosed",
        ],
        "response_template": (
            "Survey cost $400–800. Turn time 5–10 days. Required for most commercial "
            "and some rural residential. If an encroachment surfaces, you have three "
            "options: (a) get an easement from the neighbor, (b) remove the encroachment, "
            "(c) accept that the title policy will except to it (lender usually won't accept this)."
        ),
        "cleared_when": "Lender accepts the survey with no unacceptable exceptions.",
        "sla_hours": 168,
    },

    "flood_cert": {
        "title": "Flood certification",
        "what_they_want": "FEMA flood zone determination for the property.",
        "borrower_failure_modes": [
            "Property is in a Special Flood Hazard Area and flood insurance isn't bound",
        ],
        "response_template": (
            "FEMA cert is $20–50, instant. If the property is in Zone A or V, "
            "flood insurance is required and the borrower must bind it (separate from "
            "hazard). Cost is higher than standard hazard. Confirm before signing the "
            "purchase contract."
        ),
        "cleared_when": "Cert shows zone X (or equivalent non-SFHA), or flood insurance is bound.",
        "sla_hours": 4,
    },

    "purchase_contract": {
        "title": "Purchase contract (fully executed)",
        "what_they_want": "Signed contract with all addenda.",
        "borrower_failure_modes": [
            "Missing signatures (buyer, seller, or both)",
            "Missing earnest money receipt",
            "Financing contingency not properly worded",
        ],
        "response_template": (
            "Contract must be fully signed and dated by both parties. Lender wants "
            "the receipt of earnest money deposit (cashed check or wire confirmation). "
            "If there's a financing contingency, it should be explicit — "
            "'This contract is contingent on buyer obtaining financing within X days.'"
        ),
        "cleared_when": "Lender sees the fully-executed contract with all addenda.",
        "sla_hours": 24,
    },

    "lease": {
        "title": "Lease (for DSCR)",
        "what_they_want": "Existing or projected lease for the property.",
        "borrower_failure_modes": [
            "Month-to-month lease (lender wants 12-month minimum)",
            "No lease at all (lender requires rent schedule with comps)",
        ],
        "response_template": (
            "If you have a tenant, send the signed lease. If not, get a 1007 rent "
            "schedule from a local appraiser. If short-term rental, get an AirDNA "
            "report with 12-month projection."
        ),
        "cleared_when": "Lender accepts the lease/rent schedule and calculates DSCR ≥ their floor.",
        "sla_hours": 48,
    },

    "arv_comps": {
        "title": "After-repair value comps",
        "what_they_want": "3+ comps within 1 mile for the post-repair value.",
        "borrower_failure_modes": [
            "Comps are from a different neighborhood",
            "Comps are too old (> 6 months)",
            "No comps for the renovated bedroom/bath count",
        ],
        "response_template": (
            "Pull 3–5 comps within 1 mile, sold in the last 6 months, "
            "with bedroom/bath count matching the post-repair scope. "
            "If no comps exist, you may need to use a subject-to-value "
            "approach or change programs."
        ),
        "cleared_when": "Lender accepts the ARV and calculates LTC/ARV within their program limits.",
        "sla_hours": 24,
    },

    "construction_contract": {
        "title": "Construction contract + builder license",
        "what_they_want": "Fixed-price construction contract, builder license, insurance.",
        "borrower_failure_modes": [
            "Cost-plus contract (lender wants fixed-price)",
            "Builder license expired or not in the subject state",
            "Builder insurance missing",
        ],
        "response_template": (
            "Construction lender requires a fixed-price contract with a licensed + insured "
            "builder. Cost-plus is a deal-breaker for most construction programs. "
            "If you can't get fixed-price, you're probably in a bridge program, not construction."
        ),
        "cleared_when": "Lender accepts the contract and builder credentials.",
        "sla_hours": 48,
    },

    "builder_license": {
        "title": "Builder license",
        "what_they_want": "Active builder/general contractor license in the subject property's state.",
        "borrower_failure_modes": [
            "License expired",
            "License doesn't cover the property state",
            "Borrower is their own builder (often disqualifies)",
        ],
        "response_template": (
            "Pull the license verification from the state contractor's board website. "
            "Most states have an online lookup. If the borrower is doing owner-builder, "
            "that disqualifies for most construction programs."
        ),
        "cleared_when": "Lender confirms builder is licensed in the subject state.",
        "sla_hours": 4,
    },

    "other": {
        "title": "Other (custom)",
        "what_they_want": "Whatever the lender specified.",
        "borrower_failure_modes": ["Vague or non-specific ask"],
        "response_template": "Read the lender's condition carefully. If unclear, call the AE.",
        "cleared_when": "Lender confirms receipt and acceptance.",
        "sla_hours": 24,
    },
}


def get_playbook(condition_type: str) -> dict:
    """Return the playbook for a condition type. Falls back to 'other'."""
    return PLAYBOOKS.get(condition_type, PLAYBOOKS["other"])


def list_condition_types() -> list[str]:
    """List all known condition types."""
    return list(PLAYBOOKS.keys())


def format_borrower_checklist(condition_types: list[str]) -> list[dict]:
    """Build the borrower-facing checklist for a set of conditions."""
    out = []
    for ct in condition_types:
        p = get_playbook(ct)
        out.append({
            "type": ct,
            "title": p["title"],
            "what_they_want": p["what_they_want"],
            "response_template": p["response_template"],
            "loe_template": p.get("loe_template", ""),
            "sla_hours": p["sla_hours"],
        })
    return out