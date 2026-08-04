"""Config loaded from env vars. Single source of truth for everything."""
import os
from pathlib import Path


def load_dotenv():
    """Load .env from the project's own directory ONLY (app/.env or cwd/.env).
    We do NOT fall back to a shared project-root .env because that file
    (C:/DandyDon/.env) contains settings for OTHER apps (hopium_studios, etc.)
    that would conflict with ours. Each app owns its own .env."""
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",  # C:/DandyDon/ccc-origination/.env
        Path.cwd() / ".env",
    ]
    for p in candidates:
        if p.exists():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        if k and v and not v.startswith("<"):
                            os.environ.setdefault(k, v)
            except Exception:
                pass
            return  # only use the FIRST one found


load_dotenv()


class Config:
    # Database
    DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./ccc_origination.db")
    # Postgres URLs on Render start with postgres:// — SQLAlchemy wants postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    # Server
    HOST = os.environ.get("HOST", "0.0.0.0")
    PORT = int(os.environ.get("PORT", "8081"))  # 8081 to avoid clashing with RPCCP on 8080

    # Cookies / sessions
    SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-secret-change-in-prod")
    SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))
    MAGIC_LINK_TTL_MINUTES = int(os.environ.get("MAGIC_LINK_TTL_MINUTES", "30"))

    # CORS — production restricts to clickclickclose.click
    ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "https://clickclickclose.click,https://www.clickclickclose.click,http://localhost:8081"
    ).split(",") if o.strip()]

    # DO Spaces (document storage)
    SPACES_REGION = os.environ.get("SPACES_REGION", "nyc3")
    SPACES_BUCKET = os.environ.get("SPACES_BUCKET", "dandydon-hub")
    SPACES_ACCESS_KEY = os.environ.get("SPACES_ACCESS_KEY", "")
    SPACES_SECRET_KEY = os.environ.get("SPACES_SECRET_KEY", "")
    SPACES_ENDPOINT = f"https://{SPACES_REGION}.digitaloceanspaces.com"
    SPACES_CDN_ENDPOINT = f"https://{SPACES_BUCKET}.{SPACES_REGION}.cdn.digitaloceanspaces.com"

    # RPCCP engine (the existing 5-pass engine at clickclickclose.help)
    RPCCP_BASE_URL = os.environ.get("RPCCP_BASE_URL", "")
    RPCCP_API_KEY = os.environ.get("RPCCP_API_KEY", "")

    # Brand
    BRAND_NAME = "ClickClickClose"
    BRAND_DOMAIN = "clickclickclose.click"
    BROKER_NAME = "Don Brown"
    BROKER_EMAIL = os.environ.get("BROKER_EMAIL", "don@dandydon.media")
    BROKER_PHONE = os.environ.get("BROKER_PHONE", "(409) 332-9313")
    BROKER_CITY  = os.environ.get("BROKER_CITY",  "Trinity, Texas")

    # Admin bootstrap — set to create the first broker user on first run
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "don@dandydon.media")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")  # if set, ensures admin exists


config = Config()