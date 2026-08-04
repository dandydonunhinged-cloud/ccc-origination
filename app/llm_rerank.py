"""5-pass LLM re-ranker with full corpus context.

This is the bleeding-edge lender-matching layer. The flow:

  1. Embed the deal narrative (embeddings.py)
  2. Retrieve the K most similar PAST funded deals (sqlite-vec KNN)
  3. Retrieve the lender matrix (Product rows)
  4. Compose a prompt that includes:
     - The deal narrative
     - The K similar past deals with their funded lender, rate, comp, days-to-fund
     - The full lender matrix
     - The condition playbooks (so the model knows what each UW looks for)
     - The investor underwriting guide (so the model knows the real UW criteria)
  5. Run the 5-pass RPCCP-style analysis on it
  6. Return ranked + reasoned recommendations

If Ollama is reachable, uses a local model (qwen3.5:32b or similar).
Falls back to a deterministic corpus-aware ranking if no LLM.
"""
import os, json, logging
from typing import Optional
import requests

from .config import config
from . import embeddings as emb
from .models import Deal, Product, Lender, DealOutcome

logger = logging.getLogger(__name__)


OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "qwen3.5:32b")  # good local reasoning model
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "llama3.1:8b")


def _load_corpus_excerpts(db) -> dict[str, str]:
    """Load short excerpts of the relevant corpus docs to inline into the
    prompt. Bounded total size so the prompt doesn't blow up."""
    paths = {
        "lender_product_matrix":       config.CORPUS_DIR + "/lender_product_matrix.md" if hasattr(config, "CORPUS_DIR") else None,
        "investor_underwriting_guide": config.CORPUS_DIR + "/investor_underwriting_guide.md" if hasattr(config, "CORPUS_DIR") else None,
        "dscr_pain_points":             config.CORPUS_DIR + "/dscr_pain_points.md" if hasattr(config, "CORPUS_DIR") else None,
        "broker_partnerships":          config.CORPUS_DIR + "/broker_partnerships.md" if hasattr(config, "CORPUS_DIR") else None,
        "underwriting_guide":           config.CORPUS_DIR + "/underwriting_guide.md" if hasattr(config, "CORPUS_DIR") else None,
    }
    out = {}
    # We don't have CORPUS_DIR in v1; load from the configured filesystem path if set.
    corpus_root = os.environ.get("CCC_CORPUS_DIR")
    if corpus_root:
        for key, _ in paths.items():
            p = os.path.join(corpus_root, f"{key}.md")
            if os.path.exists(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                # Truncate each to ~4k chars so total fits ~20k
                out[key] = text[:4000]
    return out


def _load_lender_matrix(db, loan_type) -> list[dict]:
    """Pull the active lender matrix as a list of dicts for the prompt."""
    products = db.query(Product).filter_by(active=True, loan_type=loan_type).all()
    out = []
    for p in products:
        if not p.lender.active:
            continue
        out.append({
            "lender": p.lender.name,
            "product": p.name,
            "loan_type": p.loan_type.value,
            "min_fico": p.min_fico,
            "max_ltv": p.max_ltv,
            "min_dscr": p.min_dscr,
            "min_loan": p.min_loan,
            "max_loan": p.max_loan,
            "rate_band": p.rate_band,
            "comp_basis_pts": p.comp_basis_pts,
            "term": p.term,
            "interest_only": p.interest_only,
            "prepay_penalty": p.prepay_penalty,
            "states": p.lender.states,
            "notes": (p.notes or "")[:200],
        })
    return out


def _load_similar_paid_deals(db, narrative_embedding: list[float],
                             deal_id: int, k: int = 5) -> list[dict]:
    """Pull the k most similar *funded* deals so the model can reason by analogy."""
    from sqlalchemy import text
    # Use the embeddings.py search
    results = emb.search_similar(db.connection().connection.dbapi_connection, narrative_embedding, k=k, exclude_deal_id=deal_id)
    if not results:
        return []
    out = []
    for other_id, similarity in results:
        outcome = db.query(DealOutcome).filter_by(deal_id=other_id).one_or_none()
        if not outcome or outcome.outcome != "funded":
            continue
        d = db.query(Deal).get(other_id)
        if not d:
            continue
        out.append({
            "similarity": round(similarity, 3),
            "loan_type": d.loan_type.value,
            "state": d.property.state,
            "loan_amount": d.target_loan_amount,
            "lender": outcome.chosen_lender.name if outcome.chosen_lender else None,
            "product": outcome.chosen_product.name if outcome.chosen_product else None,
            "rate": outcome.rate_at_close,
            "comp_cents": outcome.comp_at_close_cents,
            "days_to_fund": outcome.days_to_fund,
            "borrower_fico": d.borrower.credit_score,
        })
    return out


# ---------------------------------------------------------------------------
# 5-pass prompt composition
# ---------------------------------------------------------------------------

RERANK_SYSTEM = """You are a senior mortgage broker who has closed 1,000+ DSCR, bridge, fix-and-flip, construction, and commercial loans. You work with 32+ wholesale lenders. You think in terms of:
  - Real-world underwriting criteria (often different from the published matrix)
  - Comp economics (every basis point matters)
  - Speed-to-close (days, not weeks)
  - Borrower fit (not just deal fit)

You respond with structured reasoning. You never invent numbers. If you don't know, you say "I don't have data on that" and explain what data you'd need.

When given historical funded deals, you use them as evidence. When the historical pattern differs from the matrix, you trust the historical pattern — it's the ground truth.
"""


def _compose_prompt(deal: Deal, matrix: list[dict], similar: list[dict],
                    corpus: dict[str, str]) -> str:
    """Build the prompt for the 5-pass re-ranker."""
    narrative = emb.build_deal_narrative(deal)

    matrix_text = "\n".join(
        f"  {i+1}. {m['lender']} — {m['product']} — FICO ≥ {m['min_fico']}, LTV ≤ {m['max_ltv']}%, "
        f"DSCR ≥ {m['min_dscr']}, rate {m['rate_band']}, comp {m['comp_basis_pts']} pts, "
        f"states {m['states']}, term {m['term']}, IO {m['interest_only']}, "
        f"prepay {m['prepay_penalty']}. Notes: {m['notes']}"
        for i, m in enumerate(matrix[:40])
    )

    similar_text = ""
    if similar:
        similar_text = "HISTORICAL FUNDED DEALS (most similar to this one, ranked by similarity):\n"
        for s in similar[:7]:
            similar_text += (
                f"  • similarity={s['similarity']}, {s['loan_type']} in {s['state']}, "
                f"${s['loan_amount']:,.0f}, FICO {s['borrower_fico']}, "
                f"funded by {s['lender']} ({s['product']}), "
                f"rate {s['rate']}%, comp {s['comp_cents']/100:.2f}, "
                f"days-to-fund {s['days_to_fund']}\n"
            )
        similar_text += "\n"
    else:
        similar_text = "No historical funded deals yet (this is a new program). Use the matrix + your expertise.\n\n"

    corpus_text = ""
    if corpus:
        corpus_text = "REFERENCE CORPUS EXCERPTS (background — use when relevant):\n\n"
        for key, text in corpus.items():
            if text.strip():
                corpus_text += f"### {key}\n{text}\n\n"

    prompt = f"""DEAL UNDER ANALYSIS:
{narrative}

{matrix_text if matrix else "NO LENDER MATRIX LOADED"}

{similar_text}

{corpus_text}

TASK — 5-PASS ANALYSIS:

Pass 1: Program match. Which 5 lenders from the matrix are the best fit? List them in order. For each, give a 1-line reason tied to the deal.

Pass 2: Weakness analysis. What are the 3 underwriting weaknesses the desk underwriter will ask about? For each, name the playbook action (see /app/playbooks.py in your head: bank_statements, rent_comps, rehab_scope, entity_docs, etc.).

Pass 3: Structure optimize. Is there a structural change (cross-collateralization, entity restructure, term adjustment) that improves the fit? Yes/no with reasoning.

Pass 4: Hidden dimensions. Flood zone? HOA estoppel? License requirements in {deal.property.state}? Seasoning for cash-out? Entity good-standing? Be specific.

Pass 5: Paradigm shift. Is this even the right loan type? If it's a flip with hold intent, bridge-then-DSCR refi can save 200+ bps. If it's a construction with long stabilization, SBA 504 may be better. State the case if applicable, else say "no paradigm shift."

CLOSING: Return a JSON object with this exact shape:
{{
  "top5": [{{"lender": "<name>", "product": "<name>", "score": <0-100>, "reason": "<1 line>"}}, ... 5 entries],
  "weaknesses": ["<weakness1>", "<weakness2>", "<weakness3>"],
  "structure_recommendation": "<string or null>",
  "hidden_dimensions": ["<dim1>", "<dim2>", ...],
  "paradigm_shift": "<string or null>",
  "client_report": "<plain-English 5-line summary, no lender names, no rate sheets>",
  "admin_report": "<full report including lender names, rate bands, comp, and the lender you'll lead with>"
}}
"""
    return prompt


# ---------------------------------------------------------------------------
# Ollama call
# ---------------------------------------------------------------------------

def _call_ollama(prompt: str, model: str) -> Optional[str]:
    """Call Ollama chat. Returns None on failure."""
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": RERANK_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")
    except Exception as e:
        logger.warning(f"Ollama chat failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def rerank(deal_id: int, db) -> dict:
    """Run the full 5-pass re-rank for a deal. Returns the structured report."""
    deal = db.query(Deal).get(deal_id)
    if deal is None:
        raise ValueError(f"deal {deal_id} not found")

    narrative = emb.build_deal_narrative(deal)
    matrix = _load_lender_matrix(db, deal.loan_type)
    narrative_vec = emb.embed_text(narrative)
    similar = _load_similar_paid_deals(db, narrative_vec, deal_id=deal_id, k=7)
    corpus = _load_corpus_excerpts(db)

    prompt = _compose_prompt(deal, matrix, similar, corpus)

    # Try the model chain: prefer rerank model, fall back to lighter model.
    raw = None
    for m in (RERANK_MODEL, FALLBACK_MODEL):
        raw = _call_ollama(prompt, m)
        if raw:
            break

    if raw:
        try:
            parsed = json.loads(raw)
            parsed["_engine"] = f"ollama:{m}"
            parsed["_model_used"] = m
            parsed["_narrative"] = narrative
            parsed["_similar_paid_count"] = len(similar)
            return parsed
        except json.JSONDecodeError:
            # Try to recover by trimming to the first { and last }
            try:
                start = raw.index("{")
                end = raw.rindex("}") + 1
                parsed = json.loads(raw[start:end])
                parsed["_engine"] = f"ollama:{m}"
                parsed["_raw"] = raw
                return parsed
            except Exception:
                pass

    # Fallback: deterministic ranking
    return _fallback_rerank(deal, matrix, similar)


def _fallback_rerank(deal: Deal, matrix: list[dict], similar: list[dict]) -> dict:
    """When no LLM is reachable, return a structured output that uses the
    historical-similarity signal we have."""
    if not matrix:
        return {
            "top5": [],
            "client_report": "No active products match this loan type in this state.",
            "admin_report": "Empty matrix.",
            "_engine": "fallback",
        }

    # Score: similarity-weighted count of similar funded deals at each lender
    lender_score = {}
    for s in similar:
        lender = s.get("lender")
        if lender:
            lender_score[lender] = lender_score.get(lender, 0) + s["similarity"]

    # Simple matrix score (lower confidence without LLM)
    top = matrix[:5]
    top5 = []
    for i, m in enumerate(top):
        score = max(20, 80 - i * 10) + (lender_score.get(m["lender"], 0) * 20)
        top5.append({
            "lender": m["lender"],
            "product": m["product"],
            "score": round(min(100, score), 1),
            "reason": f"Matrix fit + historical similarity ({lender_score.get(m['lender'], 0):.2f})",
        })
    top5.sort(key=lambda x: -x["score"])

    return {
        "top5": top5,
        "weaknesses": ["Unable to call LLM — manual review recommended"],
        "structure_recommendation": None,
        "hidden_dimensions": [],
        "paradigm_shift": None,
        "client_report": "We're matching this deal against our active programs. The full analysis will be available once the matching engine completes.",
        "admin_report": "Fallback ranking — no LLM reachable. Top 5 from matrix + historical similarity.",
        "_engine": "fallback",
        "_narrative": "(see embeddings.py)",
        "_similar_paid_count": len(similar),
    }