"""Studio routes — /studio/*.

HTML pages (admin login required):
  GET /studio/                          Mission control: ranked projects + work queue
  GET /studio/new/{kind}/               Guided intake with Spock (series|episode|documentary|project)
  GET /studio/project/{id}/             Project workspace: pipeline, board, Spock, segments, sources, assets, publish
  GET /studio/episode/{id}/player/      Broadcast-layout player (preview of the render)
  GET /studio/specialists/              Model roster (editable prompts/models/tools)
  GET /studio/settings/                 Brand, platforms, links bar, voice, integrations

Public:
  GET /studio/p/{public_id}/sources/    Transparency page linked from every video

JSON API under /studio/api/* — every row is editable; see the handlers below.
"""
import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..db import get_db
from ..auth import resolve_session, COOKIE_BROKER
from . import engine, integrations, llm, render
from .models import (
    StudioProject, StudioTask, StudioSegment, StudioSpecialist, StudioSource, StudioAsset, StudioPost,
    PROJECT_KINDS, PROJECT_STATUSES, PRIORITIES, TASK_STATUSES,
)
from .pipeline import (
    STAGE_LABELS, INTAKE_QUESTIONS, DEFAULT_SETTINGS, get_setting, set_setting, populate_tasks, stages_for,
)

router = APIRouter(prefix="/studio")
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals.update(STAGE_LABELS=STAGE_LABELS, PROJECT_KINDS=PROJECT_KINDS,
                             PROJECT_STATUSES=PROJECT_STATUSES, PRIORITIES=PRIORITIES,
                             TASK_STATUSES=TASK_STATUSES)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class _LoginRedirect(Exception):
    def __init__(self, next_url: str):
        self.next_url = next_url


def page_user(request: Request, db: Session = Depends(get_db)) -> str:
    email = resolve_session(db, request.cookies.get(COOKIE_BROKER))
    if not email:
        raise _LoginRedirect(str(request.url.path))
    return email


def api_user(request: Request, db: Session = Depends(get_db)) -> str:
    email = resolve_session(db, request.cookies.get(COOKIE_BROKER))
    if not email:
        raise HTTPException(status_code=401, detail="Login required")
    return email


def install(app):
    """Register the router and the login-redirect handler on the main app."""
    @app.exception_handler(_LoginRedirect)
    async def _redirect_to_login(request: Request, exc: _LoginRedirect):
        return RedirectResponse(url=f"/admin/login/?next={exc.next_url}", status_code=303)

    app.include_router(router)


def _page(request, name, email, **ctx):
    return templates.TemplateResponse(f"studio/{name}", {"request": request, "email": email,
                                                         "llm_provider": llm.provider(), **ctx})


def _get(db, model, key):
    row = db.get(model, key)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {key} not found")
    return row


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def studio_home(request: Request, db: Session = Depends(get_db), email: str = Depends(page_user)):
    top = (db.query(StudioProject).filter(StudioProject.parent_id.is_(None))
           .order_by(StudioProject.rank, StudioProject.id).all())
    active = [p for p in top if p.status not in ("killed", "published")]
    closed = [p for p in top if p.status in ("killed", "published")]
    queue = (db.query(StudioTask).join(StudioProject)
             .filter(StudioTask.status.in_(["running", "review", "blocked"]))
             .order_by(StudioProject.rank, StudioTask.rank).all())
    return _page(request, "home.html", email, active=active, closed=closed, queue=queue,
                 progress=engine.progress)


@router.get("/new/{kind}/", response_class=HTMLResponse)
def studio_new(kind: str, request: Request, parent: int | None = None, db: Session = Depends(get_db),
               email: str = Depends(page_user)):
    if kind not in PROJECT_KINDS:
        raise HTTPException(404)
    series = db.query(StudioProject).filter_by(kind="series").order_by(StudioProject.rank).all()
    return _page(request, "new.html", email, kind=kind, parent=parent, series=series,
                 first_question=INTAKE_QUESTIONS.get(kind, INTAKE_QUESTIONS["project"])[0])


@router.get("/project/{pid}/", response_class=HTMLResponse)
def studio_project(pid: int, request: Request, db: Session = Depends(get_db), email: str = Depends(page_user)):
    p = _get(db, StudioProject, pid)
    specialists = db.query(StudioSpecialist).order_by(StudioSpecialist.rank).all()
    children = sorted(p.children, key=lambda c: (c.rank, c.id))
    return _page(request, "project.html", email, p=p, specialists=specialists, children=children,
                 spec_names={s.key: s.name for s in specialists}, pr=engine.progress(p),
                 progress=engine.progress, stages=stages_for(p.kind),
                 platforms=get_setting(db, "platforms") or [], sources_url=engine.sources_url(db, p),
                 onlysocial=integrations.onlysocial_configured(),
                 imagegen=integrations.imagegen_configured(),
                 chat=[m for m in p.messages if m.channel == "showrunner"])


@router.get("/episode/{pid}/player/", response_class=HTMLResponse)
def studio_player(pid: int, request: Request, db: Session = Depends(get_db), email: str = Depends(page_user)):
    p = _get(db, StudioProject, pid)
    manifest = render.build_manifest(p, get_setting(db, "brand") or {}, get_setting(db, "links_bar") or [],
                                     engine.sources_url(db, p))
    return _page(request, "player.html", email, p=p, manifest=manifest)


@router.get("/specialists/", response_class=HTMLResponse)
def studio_specialists(request: Request, db: Session = Depends(get_db), email: str = Depends(page_user)):
    specs = db.query(StudioSpecialist).order_by(StudioSpecialist.rank).all()
    return _page(request, "specialists.html", email, specialists=specs)


@router.get("/settings/", response_class=HTMLResponse)
def studio_settings(request: Request, db: Session = Depends(get_db), email: str = Depends(page_user)):
    settings = {k: get_setting(db, k) for k in DEFAULT_SETTINGS}
    status = {
        "llm": llm.provider(),
        "omnivoice": integrations.omnivoice_configured(),
        "onlysocial": integrations.onlysocial_configured(),
        "ffmpeg": bool(integrations.ffmpeg()),
        "imagegen": integrations.imagegen_configured(),
    }
    return _page(request, "settings.html", email, settings=settings, status=status)


@router.get("/p/{public_id}/sources/", response_class=HTMLResponse)
def studio_public_sources(public_id: str, request: Request, db: Session = Depends(get_db)):
    p = db.query(StudioProject).filter_by(public_id=public_id).one_or_none()
    if p is None:
        raise HTTPException(404)
    return templates.TemplateResponse("studio/sources_public.html", {
        "request": request, "p": p, "brand": get_setting(db, "brand") or {},
        "links_bar": [l for l in (get_setting(db, "links_bar") or []) if l.get("url")]})


@router.get("/media/{path:path}")
def studio_media(path: str, request: Request, db: Session = Depends(get_db)):
    target = (integrations.MEDIA_DIR / path).resolve()
    if integrations.MEDIA_DIR not in target.parents or not target.is_file():
        raise HTTPException(404)
    if (integrations.MEDIA_DIR / "_brand") not in target.parents:
        page_user(request, db)  # only brand assets (logo, host loop) are public — used on the sources page
    return FileResponse(str(target))


# ---------------------------------------------------------------------------
# API: generic editing
# ---------------------------------------------------------------------------

EDITABLE = {
    StudioProject: {"title", "premise", "kind", "status", "priority", "notes", "parent_id", "brief"},
    StudioTask: {"title", "instructions", "specialist", "status", "auto", "output", "stage"},
    StudioSegment: {"title", "format", "minutes", "angle", "beats", "writer", "script", "source_urls"},
    StudioSource: {"url", "title", "publisher", "kind", "published", "quote", "supports", "verified"},
    StudioAsset: {"title", "url", "body", "status", "kind", "segment_id", "source_id"},
    StudioPost: {"title", "caption", "hashtags", "status", "scheduled_for"},
    StudioSpecialist: {"name", "role", "description", "model", "effort", "tools", "system_prompt", "enabled"},
}

ENTITIES = {"project": StudioProject, "task": StudioTask, "segment": StudioSegment, "source": StudioSource,
            "asset": StudioAsset, "post": StudioPost, "specialist": StudioSpecialist}


def _apply(row, payload: dict):
    allowed = EDITABLE[type(row)]
    for k, v in payload.items():
        if k not in allowed:
            raise HTTPException(400, f"Field '{k}' is not editable")
        if k == "status" and isinstance(row, StudioProject) and v not in PROJECT_STATUSES:
            raise HTTPException(400, "bad status")
        if k == "status" and isinstance(row, StudioTask) and v not in TASK_STATUSES:
            raise HTTPException(400, "bad status")
        if k == "priority" and v not in PRIORITIES:
            raise HTTPException(400, "bad priority")
        if k == "kind" and isinstance(row, StudioProject) and v not in PROJECT_KINDS:
            raise HTTPException(400, "bad kind")
        if k == "parent_id" and v is not None and int(v) == getattr(row, "id", None):
            raise HTTPException(400, "A project can't be its own parent")
        if k == "minutes":
            v = float(v or 0)
        setattr(row, k, v)


def _project_of(row) -> StudioProject | None:
    if isinstance(row, StudioProject):
        return row
    return getattr(row, "project", None)


@router.patch("/api/{entity}/{key}")
def api_patch(entity: str, key: str, payload: dict[str, Any], db: Session = Depends(get_db),
              email: str = Depends(api_user)):
    model = ENTITIES.get(entity)
    if model is None:
        raise HTTPException(404)
    row = _get(db, model, key if model is StudioSpecialist else int(key))
    _apply(row, payload)
    if isinstance(row, StudioTask) and "status" in payload:
        engine.refresh_project(row.project)
    db.commit()
    return {"ok": True}


@router.delete("/api/{entity}/{key}")
def api_delete(entity: str, key: str, db: Session = Depends(get_db), email: str = Depends(api_user)):
    model = ENTITIES.get(entity)
    if model is None:
        raise HTTPException(404)
    row = _get(db, model, key if model is StudioSpecialist else int(key))
    project = _project_of(row)
    db.delete(row)
    db.flush()
    if project is not None and not isinstance(row, StudioProject):
        db.refresh(project)
        engine.refresh_project(project)
    db.commit()
    return {"ok": True}


@router.post("/api/reorder")
def api_reorder(payload: dict[str, Any], db: Session = Depends(get_db), email: str = Depends(api_user)):
    """{entity, ids: [ordered ids], status?: new column for tasks, parent_id?: for projects}.

    Ranks are rewritten 0..n in the given order, so "move up", "move down",
    drag-and-drop and "move to top" are all the same call from the client.
    """
    model = ENTITIES.get(payload.get("entity"))
    if model is None:
        raise HTTPException(400, "bad entity")
    ids = payload.get("ids") or []
    touched = set()
    for i, rid in enumerate(ids):
        row = _get(db, model, rid if model is StudioSpecialist else int(rid))
        row.rank = float(i)
        if model is StudioTask and payload.get("status"):
            if payload["status"] not in TASK_STATUSES:
                raise HTTPException(400, "bad status")
            if int(rid) == int(payload.get("moved", -1)):
                row.status = payload["status"]
            touched.add(row.project)
        if model is StudioProject and "parent_id" in payload and int(rid) == int(payload.get("moved", -1)):
            row.parent_id = payload["parent_id"]
    for p in touched:
        engine.refresh_project(p)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: projects
# ---------------------------------------------------------------------------

def _project_json(p: StudioProject) -> dict:
    return {
        "id": p.id, "title": p.title, "kind": p.kind, "status": p.status, "priority": p.priority,
        "progress": engine.progress(p),
        "tasks": [{"id": t.id, "title": t.title, "status": t.status, "stage": t.stage, "error": t.error}
                  for t in p.tasks],
    }


@router.get("/api/project/{pid}")
def api_project(pid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    return _project_json(_get(db, StudioProject, pid))


@router.post("/api/project")
def api_project_create(payload: dict[str, Any], db: Session = Depends(get_db), email: str = Depends(api_user)):
    kind = payload.get("kind", "project")
    if kind not in PROJECT_KINDS:
        raise HTTPException(400, "bad kind")
    parent_id = payload.get("parent_id")
    siblings = db.query(StudioProject).filter(StudioProject.parent_id == parent_id).all()
    p = StudioProject(kind=kind, title=(payload.get("title") or f"New {kind}")[:300], parent_id=parent_id,
                      premise=payload.get("premise", ""), priority=payload.get("priority", "P2"),
                      rank=max([s.rank for s in siblings] or [0]) + 1, data={})
    db.add(p)
    db.flush()
    populate_tasks(db, p, skip_intake=True)
    db.refresh(p)
    engine.refresh_project(p)
    db.commit()
    return {"id": p.id, "url": f"/studio/project/{p.id}/"}


@router.post("/api/project/{pid}/data")
def api_project_data(pid: int, payload: dict[str, Any], db: Session = Depends(get_db),
                     email: str = Depends(api_user)):
    """Merge keys into project.data (topics list, autopilot, runtime targets, ...)."""
    p = _get(db, StudioProject, pid)
    allowed = {"topics", "autopilot", "runtime_min", "runtime_max"}
    bad = set(payload) - allowed
    if bad:
        raise HTTPException(400, f"Not editable: {', '.join(bad)}")
    d = dict(p.data or {})
    d.update(payload)
    p.data = d
    db.commit()
    return {"ok": True}


@router.post("/api/project/{pid}/next")
def api_project_next(pid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    p = _get(db, StudioProject, pid)
    t = engine.next_task(p)
    if t is None:
        raise HTTPException(409, "Nothing to run: finish or approve the task in review first.")
    if not engine.can_run(t):
        raise HTTPException(409, f"Next step '{t.title}' is a human step — do it, then mark it done.")
    engine.start_task(db, t)
    return {"ok": True, "task": t.id}


@router.post("/api/project/{pid}/populate")
def api_project_populate(pid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    """Re-add any missing template tasks (e.g. after changing the project kind)."""
    p = _get(db, StudioProject, pid)
    populate_tasks(db, p, skip_intake=True)
    db.refresh(p)
    engine.refresh_project(p)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: tasks
# ---------------------------------------------------------------------------

@router.post("/api/project/{pid}/task")
def api_task_create(pid: int, payload: dict[str, Any], db: Session = Depends(get_db),
                    email: str = Depends(api_user)):
    p = _get(db, StudioProject, pid)
    t = StudioTask(project_id=p.id, title=(payload.get("title") or "New task")[:300],
                   stage=payload.get("stage") or "custom", specialist=payload.get("specialist") or None,
                   instructions=payload.get("instructions", ""), auto=bool(payload.get("auto", False)),
                   status="todo", rank=max([x.rank for x in p.tasks] or [0]) + 1)
    db.add(t)
    db.flush()
    db.refresh(p)
    engine.refresh_project(p)
    db.commit()
    return {"id": t.id}


@router.get("/api/task/{tid}")
def api_task(tid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    t = _get(db, StudioTask, tid)
    return {"id": t.id, "title": t.title, "stage": t.stage, "specialist": t.specialist, "status": t.status,
            "instructions": t.instructions, "auto": t.auto, "output": t.output, "error": t.error,
            "model_used": t.model_used, "segment": t.segment.title if t.segment else None,
            "citations": (t.output_data or {}).get("citations", [])}


@router.post("/api/task/{tid}/run")
def api_task_run(tid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    t = _get(db, StudioTask, tid)
    if not engine.can_run(t):
        raise HTTPException(409, "This is a human task — no specialist model runs it.")
    if not engine.start_task(db, t):
        raise HTTPException(409, "Already running.")
    return {"ok": True}


@router.post("/api/task/{tid}/approve")
def api_task_approve(tid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    engine.approve(db, _get(db, StudioTask, tid))
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: segments, sources, assets, posts
# ---------------------------------------------------------------------------

@router.post("/api/project/{pid}/segment")
def api_segment_create(pid: int, payload: dict[str, Any], db: Session = Depends(get_db),
                       email: str = Depends(api_user)):
    p = _get(db, StudioProject, pid)
    fmt = payload.get("format", "deep_dive")
    from .pipeline import WRITER_FOR_FORMAT
    seg = StudioSegment(project_id=p.id, title=(payload.get("title") or "New segment")[:300], format=fmt,
                        minutes=float(payload.get("minutes") or 2), angle=payload.get("angle", ""),
                        writer=WRITER_FOR_FORMAT.get(fmt, "segment_writer"),
                        rank=max([s.rank for s in p.segments] or [0]) + 1)
    db.add(seg)
    db.flush()
    story = next((t for t in p.tasks if t.stage == "story"), None)
    edit = next((t for t in p.tasks if t.stage == "edit"), None)
    rank = ((story.rank + edit.rank) / 2 + 0.001 * seg.id) if story and edit else seg.rank
    db.add(StudioTask(project_id=p.id, segment_id=seg.id, stage="write", title=f"Write segment: {seg.title}",
                      specialist=seg.writer, auto=True, rank=rank))
    db.flush()
    db.refresh(p)
    engine.refresh_project(p)
    db.commit()
    return {"id": seg.id}


@router.post("/api/project/{pid}/source")
def api_source_create(pid: int, payload: dict[str, Any], db: Session = Depends(get_db),
                      email: str = Depends(api_user)):
    p = _get(db, StudioProject, pid)
    s = StudioSource(project_id=p.id, url=payload.get("url", ""), title=payload.get("title", ""),
                     publisher=payload.get("publisher", ""), kind=payload.get("kind", "article"),
                     quote=payload.get("quote", ""), supports=payload.get("supports", ""), added_by=email,
                     verified=bool(payload.get("verified", False)),
                     rank=max([x.rank for x in p.sources] or [0]) + 1)
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.post("/api/project/{pid}/asset")
async def api_asset_create(pid: int, kind: str = Form(...), title: str = Form(""), url: str = Form(""),
                           body: str = Form(""), file: UploadFile | None = File(None),
                           db: Session = Depends(get_db), email: str = Depends(api_user)):
    p = _get(db, StudioProject, pid)
    a = StudioAsset(project_id=p.id, kind=kind, title=title or (file.filename if file else kind), url=url,
                    body=body, status="ready" if (file or url) else "draft", meta={"manual": True},
                    rank=max([x.rank for x in p.assets] or [0]) + 1)
    if file is not None and file.filename:
        _store_upload(p, a, file)
    db.add(a)
    db.commit()
    return {"id": a.id}


def _store_upload(p: StudioProject, a: StudioAsset, file: UploadFile):
    name = Path(file.filename).name.replace(" ", "_")
    out = integrations.media_path(p.public_id, f"upload_{a.kind}_{name}")
    with out.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    a.path = str(out)
    a.url = integrations.media_url(out)


@router.post("/api/asset/{aid}/upload")
async def api_asset_upload(aid: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                           email: str = Depends(api_user)):
    a = _get(db, StudioAsset, aid)
    _store_upload(a.project, a, file)
    if a.status in ("draft", "needs_sourcing") and a.kind != "visual":
        a.status = "ready"
    db.commit()
    return {"ok": True, "url": a.url}


@router.post("/api/asset/{aid}/capture")
def api_asset_capture(aid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    """Grab the real document frame for a fact visual from its source PDF."""
    from .capture import CaptureError
    a = _get(db, StudioAsset, aid)
    if a.kind != "visual":
        raise HTTPException(400, "Capture is for fact visuals.")
    try:
        if not engine.capture_visual(db, a):
            raise HTTPException(409, "Source isn't a PDF — take a screenshot and attach it.")
    except CaptureError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return {"ok": True, "url": a.url}


@router.get("/api/project/{pid}/handoff.zip")
def api_handoff(pid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    """Download the DaVinci Resolve Studio + Blender package for an episode."""
    from . import handoff
    p = _get(db, StudioProject, pid)
    brand = get_setting(db, "brand") or {}
    manifest = render.build_manifest(p, brand, get_setting(db, "links_bar") or [], engine.sources_url(db, p))
    try:
        out = handoff.build_package(p, manifest, brand)
    except integrations.IntegrationError as e:
        raise HTTPException(409, str(e))
    return FileResponse(str(out), filename=out.name, media_type="application/zip")


@router.post("/api/asset/{aid}/generate")
def api_asset_generate(aid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    """(Re)generate an animation keyframe or thumbnail on the local image model."""
    a = _get(db, StudioAsset, aid)
    try:
        engine.generate_asset_image(db, a)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except integrations.IntegrationError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return {"ok": True, "url": a.url}


@router.post("/api/project/{pid}/publish")
def api_publish(pid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    p = _get(db, StudioProject, pid)
    try:
        result = engine.publish_posts(db, p)
    except (ValueError, integrations.IntegrationError) as e:
        raise HTTPException(409, str(e))
    return result


# ---------------------------------------------------------------------------
# API: Spock
# ---------------------------------------------------------------------------

@router.post("/api/intake/next")
def api_intake_next(payload: dict[str, Any], db: Session = Depends(get_db), email: str = Depends(api_user)):
    return engine.intake_next(db, payload.get("kind", "project"), payload.get("answers", []))


@router.post("/api/intake/create")
def api_intake_create(payload: dict[str, Any], db: Session = Depends(get_db), email: str = Depends(api_user)):
    kind = payload.get("kind", "project")
    if kind not in PROJECT_KINDS:
        raise HTTPException(400, "bad kind")
    parent_id = payload.get("parent_id") or None
    p = engine.create_from_intake(db, kind, payload.get("answers", []), payload.get("title", ""),
                                  int(parent_id) if parent_id else None)
    # Kick off the first model step immediately (investigation for series/docs, research for episodes).
    first = engine.next_task(p)
    if first is not None and engine.can_run(first):
        engine.start_task(db, first)
    return {"id": p.id, "url": f"/studio/project/{p.id}/"}


@router.post("/api/project/{pid}/chat")
def api_chat(pid: int, payload: dict[str, Any], db: Session = Depends(get_db), email: str = Depends(api_user)):
    p = _get(db, StudioProject, pid)
    text = (payload.get("message") or "").strip()
    if not text:
        raise HTTPException(400, "Empty message")
    msg = engine.chat(db, p, text)
    return {"reply": msg.content}


@router.post("/api/project/{pid}/rank-topics")
def api_rank_topics(pid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    try:
        return {"reasoning": engine.rank_topics(db, _get(db, StudioProject, pid))}
    except (ValueError, llm.LLMError) as e:
        raise HTTPException(409, str(e))


@router.post("/api/project/{pid}/lock-episodes")
def api_lock_episodes(pid: int, db: Session = Depends(get_db), email: str = Depends(api_user)):
    try:
        created = engine.lock_episodes(db, _get(db, StudioProject, pid))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"created": [{"id": e.id, "title": e.title} for e in created]}


# ---------------------------------------------------------------------------
# API: specialists + settings
# ---------------------------------------------------------------------------

@router.post("/api/specialist")
def api_specialist_create(payload: dict[str, Any], db: Session = Depends(get_db), email: str = Depends(api_user)):
    key = (payload.get("key") or "").strip().lower().replace(" ", "_")
    if not key or db.get(StudioSpecialist, key):
        raise HTTPException(400, "Key missing or already used")
    db.add(StudioSpecialist(key=key, name=payload.get("name") or key, role=payload.get("role", ""),
                            model=payload.get("model") or "claude-opus-5", tools=[], system_prompt="",
                            rank=db.query(StudioSpecialist).count()))
    db.commit()
    return {"key": key}


@router.post("/api/settings/{key}")
def api_setting(key: str, payload: Any = Body(None), db: Session = Depends(get_db), email: str = Depends(api_user)):
    if key not in DEFAULT_SETTINGS:
        raise HTTPException(404)
    set_setting(db, key, payload)
    return {"ok": True}


@router.post("/api/settings/brand/upload/{field}")
async def api_brand_upload(field: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                           email: str = Depends(api_user)):
    if field not in ("logo", "host_animation"):
        raise HTTPException(404)
    out = integrations.media_path("_brand", f"{field}{Path(file.filename).suffix.lower()}")
    with out.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    brand = dict(get_setting(db, "brand") or {})
    brand[f"{field}_path"] = str(out)
    brand[f"{field}_url"] = integrations.media_url(out)
    set_setting(db, "brand", brand)
    return {"ok": True, "url": brand[f"{field}_url"]}


@router.get("/api/onlysocial/accounts")
def api_onlysocial_accounts(email: str = Depends(api_user)):
    try:
        return {"accounts": integrations.onlysocial_accounts()}
    except integrations.IntegrationError as e:
        raise HTTPException(409, str(e))
