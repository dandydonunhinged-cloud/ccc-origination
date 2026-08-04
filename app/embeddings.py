"""Vector embeddings + similarity search.

Three layers:
  1. Build a deal narrative from structured fields (the embedder)
  2. Generate a 1024-d vector using Ollama (nomic-embed-text by default;
     bge-large, mxbai-embed-large, all-minilm are also fine)
  3. Store in sqlite-vec (or pgvector on Postgres), search by cosine distance

The model runs locally via Ollama at http://localhost:11434, or via the
Ollama Cloud API if OLLAMA_CLOUD_KEY is set. Falls back to a deterministic
hash-based "embedding" if no embedding model is available — the math still
works, the semantic quality is zero.
"""
import os, json, hashlib, math, time, logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)


OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")


def _safe_dim() -> int:
    """Try to introspect the embed model's dim; default 1024."""
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/show", json={"name": EMBED_MODEL}, timeout=5)
        if r.ok:
            data = r.json()
            # nomic-embed-text is 768; bge-large is 1024; mxbai-embed-large is 1024
            return 768 if "nomic" in EMBED_MODEL else 1024
    except Exception:
        pass
    return 768


EMBED_DIM   = int(os.environ.get("EMBED_DIM", str(_safe_dim())))


# ---------------------------------------------------------------------------
# Deal narrative builder (the thing that gets embedded)
# ---------------------------------------------------------------------------

def build_deal_narrative(deal) -> str:
    """Compose a plain-text narrative of the deal that captures the *whole*
    situation, not just the rule-checked fields. This is what gets embedded
    for similarity search.
    """
    b = deal.borrower
    p = deal.property
    e = deal.entity
    loan_type = deal.loan_type.value.replace("_", " ")
    prop_type = p.property_type.value.replace("_", " ")
    state = p.state or "?"
    city = p.city or "?"

    parts = [
        f"{loan_type.title()} loan request in {city}, {state}.",
        f"Property: {prop_type} at {p.address_line1}.",
        f"Purchase price ${p.purchase_price or 0:,.0f}, target loan ${deal.target_loan_amount or 0:,.0f}, down payment ${deal.down_payment or 0:,.0f}.",
    ]
    if p.projected_rent:
        parts.append(f"Projected monthly rent ${p.projected_rent:,.0f}.")
    if p.arv:
        parts.append(f"After-repair value ${p.arv:,.0f}.")
    if p.rehab_budget:
        parts.append(f"Rehab budget ${p.rehab_budget:,.0f}.")
    if b.credit_score:
        parts.append(f"Borrower FICO {b.credit_score}.")
    if e:
        parts.append(f"Borrowing entity: {e.legal_name} ({e.entity_type}, formed in {e.state_formed or '?'}).")
    if deal.target_close:
        parts.append(f"Target close: {deal.target_close.strftime('%Y-%m-%d')}.")
    if deal.lead_source:
        parts.append(f"Lead source: {deal.lead_source}.")
    if deal.notes:
        parts.append(f"Borrower notes: {deal.notes[:500]}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Embedding generation
# ---------------------------------------------------------------------------

def embed_text(text: str, model: Optional[str] = None) -> list[float]:
    """Generate a single embedding. Returns a list of floats.

    Tries Ollama first. Falls back to a deterministic hash-based pseudo-embedding
    so the rest of the system still functions offline / when the embed model
    is unavailable.
    """
    m = model or EMBED_MODEL
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/embeddings",
            json={"model": m, "prompt": text[:8000]},  # truncate long narratives
            timeout=20,
        )
        r.raise_for_status()
        vec = r.json().get("embedding", [])
        if vec:
            return vec
    except Exception as e:
        logger.warning(f"Ollama embed failed ({e}); falling back to hash embedding")

    # Deterministic fallback: hash the text into N floats.
    # Not semantically meaningful but lets the system function for testing.
    n = EMBED_DIM
    h = hashlib.sha512(text.encode("utf-8")).digest()
    # Re-hash repeatedly to fill N
    raw = b""
    while len(raw) < n * 4:
        raw += hashlib.sha512(raw + h).digest()
    return [
        (int.from_bytes(raw[i*4:i*4+4], "big") / 0xFFFFFFFF) - 0.5
        for i in range(n)
    ]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Returns 0 if either vector is zero."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(x*x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# sqlite-vec integration (no Postgres dependency, runs anywhere)
# ---------------------------------------------------------------------------

def ensure_vec_table(conn):
    """Create the vector table if it doesn't exist."""
    try:
        import sqlite_vec
        # Try sqlite-vec's loadable extension
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.executescript(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS deal_vec USING vec0(
                deal_id INTEGER PRIMARY KEY,
                embedding float[{EMBED_DIM}]
            );
        """)
        return True
    except Exception as e:
        logger.warning(f"sqlite-vec unavailable ({e}); falling back to in-Python cosine")
        return False


def index_deal(conn, deal_id: int, embedding: list[float], vec_ok: bool):
    """Insert/replace a deal's vector in the vec table, OR keep it in
    DealEmbedding.embedding_json for Python cosine."""
    if vec_ok:
        vec_str = "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO deal_vec (deal_id, embedding) VALUES (?, ?)",
                (deal_id, vec_str),
            )
            conn.commit()
            return
        except Exception as e:
            logger.warning(f"vec insert failed: {e}")


def search_similar(conn, embedding: list[float], k: int = 10,
                   exclude_deal_id: Optional[int] = None) -> list[tuple[int, float]]:
    """Return [(deal_id, similarity)] for the k most similar deals."""
    # Try sqlite-vec KNN
    try:
        vec_str = "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"
        sql = f"""
            SELECT deal_id, distance
            FROM deal_vec
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
        """
        rows = conn.execute(sql, (vec_str, k * 3 if exclude_deal_id else k)).fetchall()
        results = [(r[0], 1.0 - r[1]) for r in rows if r[0] != exclude_deal_id][:k]
        return results
    except Exception:
        pass

    # Fallback: Python cosine over DealEmbedding
    rows = conn.execute("SELECT deal_id, embedding_json FROM deal_embeddings").fetchall()
    scored = []
    for did, ej in rows:
        if did == exclude_deal_id:
            continue
        try:
            vec = json.loads(ej)
            scored.append((did, cosine(embedding, vec)))
        except Exception:
            continue
    scored.sort(key=lambda x: -x[1])
    return scored[:k]