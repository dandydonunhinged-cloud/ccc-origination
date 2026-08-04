"""Database engine + session factory + bootstrap."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from .config import config
from .models import Base


def make_engine():
    """Create the SQLAlchemy engine, with the right args for SQLite vs Postgres."""
    if config.DATABASE_URL.startswith("sqlite"):
        # SQLite: allow cross-thread access (FastAPI runs sync handlers in a threadpool)
        engine = create_engine(
            config.DATABASE_URL,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )
    else:
        # Postgres
        engine = create_engine(
            config.DATABASE_URL,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
db_session = scoped_session(SessionLocal)


def init_db():
    """Create all tables. Idempotent."""
    Base.metadata.create_all(engine)


def get_db():
    """FastAPI dependency that yields a session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()