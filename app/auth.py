"""Auth: bcrypt for passwords, signed cookies for sessions, magic links for borrowers.

Single file because all three share the same hash helpers."""
import hashlib, hmac, secrets, time, jwt
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
from fastapi import Request, Response, HTTPException, Depends, status
from sqlalchemy.orm import Session

from .config import config
from .db import get_db
from .models import Borrower, Session as SessionRow, MagicLink


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def hash_token(token: str) -> str:
    """sha256(token) — used so we never store the raw token at rest."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(plain: str) -> bytes:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12))


def verify_password(plain: str, hashed: bytes) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT session (broker side)
# ---------------------------------------------------------------------------

def issue_session(db: Session, email: str) -> tuple[str, datetime]:
    """Issue a new session for `email`. Returns (raw_token, expires_at)."""
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=config.SESSION_TTL_DAYS)
    db.add(SessionRow(user_email=email, token_hash=hash_token(raw), expires_at=expires))
    db.commit()
    return raw, expires


def resolve_session(db: Session, raw_token: str) -> Optional[str]:
    """Return the email for the session, or None if invalid/expired/revoked."""
    if not raw_token:
        return None
    h = hash_token(raw_token)
    row = db.query(SessionRow).filter_by(token_hash=h).one_or_none()
    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    # DB returns naive datetimes (SQLite). Compare in the same naive UTC frame.
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_naive = row.expires_at.replace(tzinfo=None) if row.expires_at.tzinfo else row.expires_at
    if expires_naive < now_utc_naive:
        return None
    return row.user_email


def revoke_session(db: Session, raw_token: str):
    h = hash_token(raw_token)
    row = db.query(SessionRow).filter_by(token_hash=h).one_or_none()
    if row:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()


# ---------------------------------------------------------------------------
# Magic link (borrower side)
# ---------------------------------------------------------------------------

def issue_magic_link(db: Session, borrower: Borrower, purpose: str = "login",
                     redirect_after: str = None) -> tuple[str, datetime]:
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=config.MAGIC_LINK_TTL_MINUTES)
    db.add(MagicLink(
        borrower_id=borrower.id,
        token_hash=hash_token(raw),
        purpose=purpose,
        redirect_after=redirect_after,
        expires_at=expires,
    ))
    db.commit()
    return raw, expires


def consume_magic_link(db: Session, raw_token: str) -> Optional[Borrower]:
    """Look up a magic link, burn it (mark used), return the borrower.
    Returns None on any failure: not found, expired, already used."""
    h = hash_token(raw_token)
    row = db.query(MagicLink).filter_by(token_hash=h).one_or_none()
    if row is None:
        return None
    if row.used_at is not None:
        return None
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_naive = row.expires_at.replace(tzinfo=None) if row.expires_at.tzinfo else row.expires_at
    if expires_naive < now_utc_naive:
        return None
    row.used_at = datetime.now(timezone.utc)
    db.commit()
    return db.query(Borrower).get(row.borrower_id)


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

COOKIE_BROKER = "ccc_broker"
COOKIE_BORROWER = "ccc_borrower"


def set_broker_cookie(response: Response, token: str, expires: datetime):
    response.set_cookie(
        key=COOKIE_BROKER,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        expires=expires,
        path="/",
    )


def clear_broker_cookie(response: Response):
    response.delete_cookie(COOKIE_BROKER, path="/")


def set_borrower_cookie(response: Response, token: str, expires: datetime):
    response.set_cookie(
        key=COOKIE_BORROWER,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        expires=expires,
        path="/portal",
    )


def clear_borrower_cookie(response: Response):
    response.delete_cookie(COOKIE_BORROWER, path="/portal")


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def require_broker(request: Request, db: Session = Depends(get_db)) -> str:
    """Return the broker email if signed in, else 401."""
    raw = request.cookies.get(COOKIE_BROKER)
    email = resolve_session(db, raw)
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Login required", headers={"WWW-Authenticate": "Cookie"})
    return email


def optional_broker(request: Request, db: Session = Depends(get_db)) -> Optional[str]:
    raw = request.cookies.get(COOKIE_BROKER)
    return resolve_session(db, raw) if raw else None


def require_borrower(request: Request, db: Session = Depends(get_db)) -> Borrower:
    """Return the Borrower object if signed in via magic-link cookie, else 401."""
    raw = request.cookies.get(COOKIE_BORROWER)
    if not raw:
        raise HTTPException(status_code=401, detail="Login required")
    # Borrower cookie holds a token; we re-resolve the borrower from it.
    # For simplicity we hash and look up the most recent unexpired magic link for this token.
    # (For real auth, store a borrower_session table; this is a v1.)
    h = hash_token(raw)
    link = db.query(MagicLink).filter_by(token_hash=h, purpose="login").one_or_none()
    if link is None or link.used_at is None:
        raise HTTPException(status_code=401, detail="Session expired")
    expires_naive = link.expires_at.replace(tzinfo=None) if link.expires_at.tzinfo else link.expires_at
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if expires_naive < now_utc_naive:
        raise HTTPException(status_code=401, detail="Session expired")
    return db.query(Borrower).get(link.borrower_id)


def optional_borrower(request: Request, db: Session = Depends(get_db)) -> Optional[Borrower]:
    raw = request.cookies.get(COOKIE_BORROWER)
    if not raw:
        return None
    h = hash_token(raw)
    link = db.query(MagicLink).filter_by(token_hash=h, purpose="login").one_or_none()
    if link is None or link.used_at is None:
        return None
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_naive = link.expires_at.replace(tzinfo=None) if link.expires_at.tzinfo else link.expires_at
    if expires_naive < now_utc_naive:
        return None
    return db.query(Borrower).get(link.borrower_id)