"""Static pages — serves the marketing /mortgage/ pages from disk.

In production these will move to a CDN, but for the monolith deploy they're
served by FastAPI as file responses. This is fast (kernel cache), cheap, and
eliminates the need for a separate static host.
"""
import logging
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# /mortgage/ root + sub-pages live here
STATIC_MORTGAGE = Path(__file__).resolve().parent.parent.parent / "static" / "mortgage"


def _serve(relative: str) -> FileResponse:
    """Serve a file under /mortgage/. Resolves safely."""
    # Strip leading slash
    rel = relative.lstrip("/")
    target = (STATIC_MORTGAGE / rel).resolve()
    # Block path traversal
    try:
        target.relative_to(STATIC_MORTGAGE.resolve())
    except ValueError:
        raise HTTPException(404, "Not found")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(str(target))


@router.get("/mortgage/", response_class=HTMLResponse)
async def mortgage_home():
    return _serve("index.html")


@router.get("/mortgage/{rest:path}", response_class=HTMLResponse)
async def mortgage_sub(rest: str):
    # rest may include slashes (e.g. lead-acquisition/index.html)
    if not rest.endswith(".html") and not rest.endswith(".css") and not rest.endswith(".js") \
       and not rest.endswith(".svg") and not rest.endswith(".png") and not rest.endswith(".ico") \
       and not rest.endswith(".txt") and not rest.endswith(".xml"):
        # Treat as a directory — append index.html
        rest = rest + "/index.html"
    return _serve(rest)