"""Scenario engine — the entry point that the borrower intake hits.

Pipeline:
  1. Run local deterministic scorer (matrix rules)  → fast baseline
  2. Run AI re-ranker (embeddings + LLM)             → reasoning + historical
  3. Merge into one report, save on the Deal

Returns a JSON-serializable dict (gets stored on Deal.scenario_report).
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from .models import Deal, DealEmbedding
from . import embeddings as emb
from . import llm_rerank

logger = logging.getLogger(__name__)


def run_scenario(deal_id: int, db: Session) -> dict:
    """The full pipeline. Saves DealEmbedding + Deal.scenario_report + scenario_score."""
    deal = db.query(Deal).get(deal_id)
    if deal is None:
        raise ValueError(f"deal {deal_id} not found")

    # Step 1: build narrative + embed
    narrative = emb.build_deal_narrative(deal)
    vec = emb.embed_text(narrative)

    # Save the embedding
    emb_row = db.query(DealEmbedding).filter_by(deal_id=deal_id).one_or_none()
    if emb_row is None:
        emb_row = DealEmbedding(
            deal_id=deal_id,
            embedding_json="[]",
            model_name="auto",
            dim=len(vec),
            narrative=narrative,
        )
        db.add(emb_row)
    emb_row.embedding_json = emb.json_dumps(vec)
    emb_row.model_name = "auto"
    emb_row.dim = len(vec)
    emb_row.narrative = narrative

    # Step 2: try the LLM re-ranker
    try:
        rerank = llm_rerank.rerank(deal_id, db)
    except Exception as e:
        logger.exception(f"LLM rerank failed for deal {deal_id}: {e}")
        rerank = {"_engine": "error", "error": str(e)}

    # Step 3: also run the deterministic local scorer (always — fallback)
    from .scenario import local_scenario
    local = local_scenario(deal, db)

    # Merge: AI top5 (with reasoning) wins, but local gives the deterministic ranked list
    ai_top5 = rerank.get("top5", [])
    local_ranked = local.get("ranked_lenders", [])
    ai_lender_names = {t.get("lender", "").lower() for t in ai_top5 if isinstance(t, dict)}

    report = {
        "engine": "ai-pipeline-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "narrative": narrative,
        "ai": rerank,
        "local": local,
        "ranked_lenders": _merge_rankings(ai_top5, local_ranked),
        "top_score": (ai_top5[0].get("score", 0) if ai_top5 else local.get("top_score", 0)),
        "client_report": rerank.get("client_report", local.get("client_report", "")),
        "admin_report": rerank.get("admin_report", local.get("admin_report", "")),
        "weaknesses": rerank.get("weaknesses", []),
        "structure_recommendation": rerank.get("structure_recommendation"),
        "hidden_dimensions": rerank.get("hidden_dimensions", []),
        "paradigm_shift": rerank.get("paradigm_shift"),
    }

    # Score: average AI top1 + local top1
    ai_s = ai_top5[0].get("score", 0) if ai_top5 and isinstance(ai_top5[0], dict) else 0
    local_s = local.get("top_score", 0)
    score = round((ai_s + local_s) / 2, 1) if (ai_s or local_s) else 0

    deal.scenario_report = report
    deal.scenario_score = score
    if deal.stage.value == "lead_acquired":
        deal.stage = "scenario_analyzed"

    db.commit()
    return report


def _merge_rankings(ai_top5: list, local_ranked: list) -> list[dict]:
    """Merge the AI top5 (reasoned) with the local deterministic ranked list.
    AI entries appear first; local entries that aren't in the AI list follow."""
    out = []
    seen = set()
    for entry in ai_top5:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("lender", ""), entry.get("product", ""))
        if key in seen:
            continue
        out.append({
            "lender": entry.get("lender", ""),
            "product": entry.get("product", ""),
            "score": entry.get("score", 0),
            "reason": entry.get("reason", ""),
            "source": "ai",
        })
        seen.add(key)
    for entry in local_ranked:
        key = (entry.get("lender", ""), entry.get("product", ""))
        if key in seen:
            continue
        out.append({
            "lender": entry.get("lender", ""),
            "product": entry.get("product", ""),
            "score": entry.get("score", 0),
            "reason": "; ".join(entry.get("reasons", [])),
            "rate_band": entry.get("rate_band", ""),
            "source": "local",
        })
        seen.add(key)
    return out[:10]