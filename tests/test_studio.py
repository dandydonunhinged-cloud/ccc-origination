"""Studio pipeline tests. The model is mocked so every stage runs on
realistic-shaped output without network access."""
import json
import os
import shutil
import subprocess
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="studio-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["ADMIN_PASSWORD"] = "pw"
os.environ["STUDIO_MEDIA_DIR"] = f"{_TMP}/media"
os.environ["STUDIO_LLM_PROVIDER"] = "offline"

from fastapi.testclient import TestClient  # noqa: E402

from app.app import app  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.studio import engine, llm, integrations  # noqa: E402
from app.studio.models import StudioProject, StudioTask, StudioSource, StudioAsset  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="https://testserver") as c:
        r = c.post("/admin/login/", data={"email": "don@dandydon.media", "password": "pw", "next": "/studio/"},
                   follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/studio/"
        yield c


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Canned model output per output contract
# ---------------------------------------------------------------------------

SEGMENT_SCRIPT = """[VISUAL: S{sid} — highlight "Sec. 4(b)"]
HOST: So the bill says one thing {{S{sid}}}.
[BEAT]
CORRESPONDENT (Jordan): And does another, obviously. {{S{sid}}}
[SFX: record scratch]
HOST: Right."""


def fake_complete(system, messages, model="", tools=None, effort="high", max_tokens=0):
    prompt = messages[-1]["content"]
    contract = prompt.split("OUTPUT_CONTRACT:")[-1].strip().split()[0] if "OUTPUT_CONTRACT:" in prompt else "text"
    cites = [{"url": "https://www.congress.gov/bill/1", "title": "Bill"}]
    if contract == "investigate":
        body = {"verdict": "substantiated", "confidence": 80, "summary": "Well documented.",
                "evidence": [{"claim": "c1", "status": "proven", "title": "Bill", "publisher": "Congress",
                              "date": "2025", "kind": "bill", "url": "https://www.congress.gov/bill/1",
                              "quote": "Sec. 4(b)"},
                             {"claim": "c2", "status": "alleged", "title": "Made up", "url": "https://example.com/x"}],
                "counter_evidence": [],
                "topics": [{"title": "Topic A", "angle": "a", "why_it_matters": "w",
                            "key_documents": ["https://www.congress.gov/bill/1"], "strength": "strong"},
                           {"title": "Topic B", "angle": "b", "key_documents": [], "strength": "thin"}],
                "gaps": ["g"]}
    elif contract == "research":
        body = {"summary": "Evidence file.", "sources": [
            {"title": "IG report", "publisher": "OIG", "date": "2025", "kind": "gov_report",
             "url": "https://oig.example.gov/r.pdf", "quote": "found violations", "supports": "c1"}],
            "timeline": [{"date": "2025-01-01", "event": "e", "url": ""}],
            "key_facts": [{"fact": "f", "status": "proven", "url": "https://oig.example.gov/r.pdf"}],
            "open_questions": []}
    elif contract == "story":
        body = {"logline": "L", "thesis": "T", "bomb": "B", "segments": [
            {"title": "Cold open", "format": "cold_open", "minutes": 1, "angle": "hook", "beats": ["b"]},
            {"title": "Deep dive", "format": "deep_dive", "minutes": 8, "angle": "docs", "beats": ["b"]},
            {"title": "Correspondent", "format": "correspondent", "minutes": 3, "angle": "bit", "beats": []},
            {"title": "Convergence", "format": "convergence", "minutes": 1.5, "angle": "bomb", "beats": []}]}
    elif contract == "segment" or contract == "edit":
        sid = prompt.split("[S")[1].split("]")[0] if "[S" in prompt else "1"
        text = SEGMENT_SCRIPT.format(sid=sid)
        if contract == "edit":
            text = "===== SEGMENT 1: Cold open =====\n" + text
        return llm.LLMResult(text=text, model="mock", provider="mock")
    elif contract == "standards":
        body = {"verdict": "clear", "summary": "ok", "issues": [{"quote": "x", "problem": "p", "severity": "low", "fix": "f"}]}
    elif contract == "visuals":
        sid = prompt.split("[S")[1].split("]")[0]
        body = {"shots": [{"segment_index": 1, "cue": "bill", "visual_type": "document_highlight", "source_id": f"S{sid}",
                           "highlight_text": "Sec. 4(b)", "caption": "The bill"},
                          {"segment_index": 1, "cue": "money", "visual_type": "chart", "source_id": None, "caption": "gap"}]}
    elif contract == "animation":
        body = {"pieces": [{"after_segment_index": 0, "duration_sec": 3, "concept": "wipe", "prompt": "p"}]}
    elif contract == "music":
        body = {"cues": [{"placement": "bed", "mood": "tense", "bpm": 90, "level_db": -22}]}
    elif contract == "social":
        body = {"youtube": {"title": "YT", "caption": "cap", "hashtags": "#a"}, "x": {"caption": "short"}}
    else:
        return llm.LLMResult(text="free text reply", model="mock", provider="mock")
    return llm.LLMResult(text="Here you go:\n```json\n" + json.dumps(body) + "\n```", model="mock",
                         provider="mock", citations=cites)


@pytest.fixture
def mock_llm(monkeypatch):
    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(llm, "provider", lambda: "mock")
    # Don't run tasks in background threads during tests; run_stage() runs them inline.
    monkeypatch.setattr(engine, "_run_in_thread", lambda task_id: engine._running.discard(task_id))


def run_stage(db, project_id, stage):
    t = db.query(StudioTask).filter_by(project_id=project_id, stage=stage).order_by(StudioTask.rank).first()
    engine.run_task(db, t.id)
    db.expire_all()
    return db.get(StudioTask, t.id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_requires_login():
    with TestClient(app, base_url="https://testserver") as anon:
        r = anon.get("/studio/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/admin/login/?next=/studio/")
        assert anon.post("/studio/api/reorder", json={}).status_code == 401


def test_login_next_rejects_offsite():
    with TestClient(app, base_url="https://testserver") as c:
        r = c.post("/admin/login/", data={"email": "a@b.c", "password": "pw", "next": "//evil.com"},
                   follow_redirects=False)
        assert r.headers["location"] == "/admin/"


def test_pages_render(client):
    for path in ["/studio/", "/studio/new/series/", "/studio/new/episode/", "/studio/new/documentary/",
                 "/studio/new/project/", "/studio/specialists/", "/studio/settings/"]:
        assert client.get(path).status_code == 200, path


def test_intake_question_flow(client):
    answers = []
    seen = []
    for _ in range(20):
        nxt = client.post("/studio/api/intake/next", json={"kind": "series", "answers": answers}).json()
        if nxt.get("done"):
            break
        seen.append(nxt["question"])
        answers.append({"q": nxt["question"], "a": "answer", "followup": nxt["followup"]})
    assert len(seen) == 8 and "series about" in seen[0]


def test_series_to_episodes(client, db, mock_llm):
    r = client.post("/studio/api/intake/create", json={"kind": "series", "answers": [
        {"q": "What's the series about?", "a": "The corruption of the Trump administration"},
        {"q": "Claim?", "a": "Officials used office for private gain"}]})
    sid = r.json()["id"]
    # the investigation auto-starts in a thread; run it synchronously to make the test deterministic
    engine._running.clear()
    inv = run_stage(db, sid, "investigate")
    assert inv.status == "review", inv.error
    series = db.get(StudioProject, sid)
    assert series.title == "The corruption of the Trump administration"
    assert series.data["investigation"]["verdict"] == "substantiated"
    assert [t["title"] for t in series.data["topics"]] == ["Topic A", "Topic B"]
    flagged = [s for s in series.sources if s.url == "https://example.com/x"][0]
    assert flagged.supports.startswith("[URL not seen in search results")

    # Spock ranks → reverse order won't break; then exclude Topic B and lock
    topics = series.data["topics"]
    topics[1]["include"] = False
    assert client.post(f"/studio/api/project/{sid}/data", json={"topics": topics}).status_code == 200
    r = client.post(f"/studio/api/project/{sid}/lock-episodes")
    created = r.json()["created"]
    assert [c["title"] for c in created] == ["Topic A"]
    db.expire_all()
    ep = db.get(StudioProject, created[0]["id"])
    assert ep.parent_id == sid and ep.kind == "episode"
    assert [s.url for s in ep.sources] == ["https://www.congress.gov/bill/1"]  # seeded from key_documents
    assert all(t.status == "done" for t in db.get(StudioProject, sid).tasks)
    # locking again doesn't duplicate
    assert client.post(f"/studio/api/project/{sid}/lock-episodes").json()["created"] == []


def test_full_episode_pipeline(client, db, mock_llm, monkeypatch):
    r = client.post("/studio/api/intake/create", json={"kind": "episode", "answers": [
        {"q": "About?", "a": "The bill that does the opposite"}]})
    pid = r.json()["id"]
    engine._running.clear()

    assert run_stage(db, pid, "research").status == "review"
    story = run_stage(db, pid, "story")
    assert story.status == "review"
    ep = db.get(StudioProject, pid)
    assert [s.format for s in ep.segments] == ["cold_open", "deep_dive", "correspondent", "convergence"]
    writers = [t for t in ep.tasks if t.stage == "write"]
    assert [t.specialist for t in writers] == ["headline_writer", "segment_writer", "correspondent_writer",
                                               "closer_writer"]
    # writer tasks sit between story and edit
    edit = next(t for t in ep.tasks if t.stage == "edit")
    assert all(story.rank < w.rank < edit.rank for w in writers)

    # edit refuses until every segment has a script
    t = run_stage(db, pid, "edit")
    assert t.status == "blocked" and "without a script" in t.error
    for w in writers:
        engine.run_task(db, w.id)
    db.expire_all()
    assert all(s.script for s in db.get(StudioProject, pid).segments)
    assert run_stage(db, pid, "edit").status == "review"
    assert run_stage(db, pid, "standards").status == "review"

    scripts = run_stage(db, pid, "scripts")
    assert scripts.status == "done"
    ep = db.get(StudioProject, pid)
    clean = next(a for a in ep.assets if a.kind == "script_clean").body
    stage = next(a for a in ep.assets if a.kind == "script_stage").body
    assert "[VISUAL" in stage and "{S" in stage
    assert "[" not in clean and "{" not in clean and "HOST:" not in clean and "=====" not in clean
    assert "So the bill says one thing." in clean

    # voice without OmniVoice → blocked with a clear message
    monkeypatch.delenv("OMNIVOICE_URL", raising=False)
    v = run_stage(db, pid, "voice")
    assert v.status == "blocked" and "OMNIVOICE_URL" in v.error

    vis = run_stage(db, pid, "visuals")
    assert vis.status == "review"
    shots = [a for a in db.get(StudioProject, pid).assets if a.kind == "visual"]
    assert sorted(a.status for a in shots) == ["needs_sourcing", "ready"]
    assert all(a.segment_id for a in shots)

    assert run_stage(db, pid, "animation").status == "review"
    assert run_stage(db, pid, "music").status == "review"
    tr = run_stage(db, pid, "transparency")
    assert "/studio/p/" in tr.output

    rend = run_stage(db, pid, "render")
    manifest = json.loads(next(a for a in db.get(StudioProject, pid).assets if a.kind == "manifest").body)
    assert manifest["layout"]["main"] == [0, 0, 1440, 810]
    assert len(manifest["visuals"]) == 1  # the unsourced shot is excluded
    assert any("no verified source" in w for w in manifest["warnings"])
    assert rend.status in ("review", "blocked")

    pub = run_stage(db, pid, "publish")
    posts = db.get(StudioProject, pid).posts
    assert len(posts) == 11 and pub.status == "review"
    assert next(p for p in posts if p.platform == "youtube").title == "YT"

    # sending without OnlySocial configured → clear error
    for p in posts[:2]:
        client.patch(f"/studio/api/post/{p.id}", json={"status": "approved"})
    r = client.post(f"/studio/api/project/{pid}/publish")
    assert r.status_code == 409

    # pages render with a fully-populated episode
    assert client.get(f"/studio/project/{pid}/").status_code == 200
    assert client.get(f"/studio/episode/{pid}/player/").status_code == 200
    pub_id = db.get(StudioProject, pid).public_id
    with TestClient(app, base_url="https://testserver") as anon:
        page = anon.get(f"/studio/p/{pub_id}/sources/")
        assert page.status_code == 200 and "oig.example.gov" in page.text


def test_reorder_and_move_between_columns(client, db):
    pid = client.post("/studio/api/project", json={"kind": "project", "title": "Website relaunch"}).json()["id"]
    ids = [client.post(f"/studio/api/project/{pid}/task", json={"title": f"T{i}"}).json()["id"] for i in range(3)]
    # move T2 in front of T0
    client.post("/studio/api/reorder", json={"entity": "task", "ids": [ids[2], ids[0], ids[1]]})
    db.expire_all()
    ordered = [t.title for t in sorted(db.get(StudioProject, pid).tasks, key=lambda t: t.rank)]
    assert ordered == ["T2", "T0", "T1"]
    # drag T0 into the review column
    client.post("/studio/api/reorder", json={"entity": "task", "ids": [ids[0]], "status": "review", "moved": ids[0]})
    db.expire_all()
    assert db.get(StudioTask, ids[0]).status == "review"
    assert db.get(StudioTask, ids[1]).status == "todo"
    # projects: prioritise to top + priority label
    other = client.post("/studio/api/project", json={"kind": "project", "title": "Other"}).json()["id"]
    client.post("/studio/api/reorder", json={"entity": "project", "ids": [other, pid]})
    client.patch(f"/studio/api/project/{other}", json={"priority": "P1"})
    db.expire_all()
    assert db.get(StudioProject, other).rank < db.get(StudioProject, pid).rank
    assert db.get(StudioProject, other).priority == "P1"


def test_edit_validation(client):
    pid = client.post("/studio/api/project", json={"kind": "project", "title": "X"}).json()["id"]
    assert client.patch(f"/studio/api/project/{pid}", json={"status": "nope"}).status_code == 400
    assert client.patch(f"/studio/api/project/{pid}", json={"public_id": "x"}).status_code == 400
    assert client.patch(f"/studio/api/project/{pid}", json={"parent_id": pid}).status_code == 400
    assert client.patch("/studio/api/specialist/investigator", json={"model": "claude-sonnet-5"}).status_code == 200
    assert client.delete(f"/studio/api/project/{pid}").status_code == 200
    assert client.get(f"/studio/api/project/{pid}").status_code == 404


def test_media_path_traversal_blocked(client):
    assert client.get("/studio/media/../../etc/passwd").status_code == 404
    assert client.get("/studio/media/%2e%2e/test.db").status_code == 404


def test_media_auth(client):
    secret = integrations.media_path("proj123", "narration.mp3")
    secret.write_bytes(b"x")
    logo = integrations.media_path("_brand", "logo.png")
    logo.write_bytes(b"x")
    assert client.get("/studio/media/proj123/narration.mp3").status_code == 200
    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.get("/studio/media/_brand/logo.png").status_code == 200
        r = anon.get("/studio/media/_brand/%2e%2e/proj123/narration.mp3", follow_redirects=False)
        assert r.status_code == 303  # escaping _brand/ requires login
        assert anon.get("/studio/media/proj123/narration.mp3", follow_redirects=False).status_code == 303


def test_extract_json():
    assert llm.extract_json('blah ```json\n{"a": 1}\n``` tail') == {"a": 1}
    assert llm.extract_json('prefix {"a": [1, 2]} suffix') == {"a": [1, 2]}
    assert llm.extract_json("no json here") is None


def test_chunk_text():
    text = ("Sentence one. " * 300).strip()
    chunks = integrations.chunk_text(text, limit=500)
    assert all(len(c) <= 500 for c in chunks) and "".join(chunks).count("Sentence") == 300


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_ffmpeg_render(db, tmp_path):
    from app.studio import render
    p = StudioProject(kind="episode", title="Render test", data={})
    db.add(p)
    db.flush()
    voice = tmp_path / "voice.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(voice)],
                   check=True, capture_output=True)
    img = tmp_path / "doc.png"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=white:s=800x600", "-frames:v", "1", str(img)],
                   check=True, capture_output=True)
    src = StudioSource(project_id=p.id, url="https://example.gov/doc", title="Doc")
    db.add(src)
    db.flush()
    db.add(StudioAsset(project_id=p.id, kind="audio", path=str(voice), url="/x"))
    db.add(StudioAsset(project_id=p.id, kind="visual", path=str(img), source_id=src.id, status="ready",
                       title="The doc"))
    db.commit()
    db.refresh(p)
    m = render.build_manifest(p, {"name": "DanDon Media"}, [{"label": "YouTube", "url": "https://yt"}],
                              "https://x/studio/p/abc/sources/")
    assert m["duration_source"] == "audio" and 2.5 < m["duration_sec"] < 3.5
    out = tmp_path / "ep.mp4"
    report = render.render_mp4(m, out)
    assert out.exists() and out.stat().st_size > 1000 and report["visuals_used"] == 1
    assert 2.5 < integrations.probe_duration(out) < 3.6


# ---------------------------------------------------------------------------
# Image model (Qwen-Image-2.1 via ComfyUI) — animation keyframes & thumbnails only
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_comfyui(tmp_path, monkeypatch):
    """A tiny stand-in for ComfyUI's HTTP API: POST /prompt, GET /history/<id>, GET /view."""
    import http.server
    import threading
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                        "0000000d49444154789c6360f8cfc00000030101009c2d2a6e0000000049454e44ae426082")
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append(body["prompt"])
            self._json({"prompt_id": "p1"})

        def do_GET(self):
            if self.path.startswith("/history/"):
                self._json({"p1": {"status": {"status_str": "success"},
                                   "outputs": {"9": {"images": [{"filename": "out.png", "type": "output"}]}}}})
            elif self.path.startswith("/view"):
                self.send_response(200)
                self.send_header("content-type", "image/png")
                self.end_headers()
                self.wfile.write(png)

        def _json(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    wf = tmp_path / "qwen_image_api.json"
    wf.write_text(json.dumps({
        "3": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "steps": "{{steps}}"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": "{{width}}", "height": "{{height}}"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative}}"}},
    }))
    monkeypatch.setenv("IMAGEGEN_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("IMAGEGEN_COMFY_WORKFLOW", str(wf))
    monkeypatch.delenv("IMAGEGEN_API", raising=False)
    yield seen
    srv.shutdown()


def test_fill_workflow_escapes_prompt():
    wf = integrations.fill_workflow('{"a": {"text": "{{prompt}}", "w": "{{width}}", "s": {{seed}}}}',
                                    {"prompt": 'He said "no"\nthen left', "negative": "", "width": 1024,
                                     "height": 576, "seed": 7, "steps": 30})
    assert wf == {"a": {"text": 'He said "no"\nthen left', "w": 1024, "s": 7}}


def test_animation_keyframes_on_image_model(client, db, mock_llm, fake_comfyui):
    pid = client.post("/studio/api/intake/create", json={"kind": "episode", "answers": [
        {"q": "About?", "a": "Keyframe test"}]}).json()["id"]
    engine._running.clear()
    run_stage(db, pid, "story")
    anim = run_stage(db, pid, "animation")
    assert "1 keyframes generated" in anim.output, anim.output + anim.error
    piece = next(a for a in db.get(StudioProject, pid).assets if a.kind == "animation")
    assert piece.meta["generated"] and piece.path.endswith(".png") and piece.status == "ready"
    sent = fake_comfyui[0]
    assert sent["5"]["inputs"] == {"width": 1024, "height": 576}
    assert "DanDon Media" in sent["6"]["inputs"]["text"] and sent["6"]["inputs"]["text"].endswith("p")
    assert client.get(piece.url).status_code == 200

    # regenerate through the API; fact visuals are refused
    assert client.post(f"/studio/api/asset/{piece.id}/generate").status_code == 200
    src = StudioSource(project_id=pid, url="https://example.gov/d", title="d")
    db.add(src)
    db.flush()
    vis = StudioAsset(project_id=pid, kind="visual", title="doc", body="a document", source_id=src.id)
    db.add(vis)
    db.commit()
    r = client.post(f"/studio/api/asset/{vis.id}/generate")
    assert r.status_code == 400 and "real documents" in r.json()["detail"]


def test_generated_images_never_fact_visuals(db):
    from app.studio import render
    p = StudioProject(kind="episode", title="Guard", data={})
    db.add(p)
    db.flush()
    src = StudioSource(project_id=p.id, url="https://example.gov/x", title="x")
    db.add(src)
    db.flush()
    db.add(StudioAsset(project_id=p.id, kind="visual", title="sneaky", source_id=src.id, status="ready",
                       meta={"generated": True}))
    db.commit()
    db.refresh(p)
    m = render.build_manifest(p, {}, [], "u")
    assert m["visuals"] == [] and any("AI-generated" in w for w in m["warnings"])


def test_imagegen_not_configured(client, db, monkeypatch):
    monkeypatch.delenv("IMAGEGEN_URL", raising=False)
    pid = client.post("/studio/api/project", json={"kind": "episode", "title": "No GPU"}).json()["id"]
    aid = client.post(f"/studio/api/project/{pid}/asset", data={"kind": "thumbnail", "title": "t",
                                                                 "body": "a red gavel"}).json()["id"]
    r = client.post(f"/studio/api/asset/{aid}/generate")
    assert r.status_code == 409 and "IMAGEGEN_URL" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Document capture (real PDF pages) and the DaVinci Resolve / Blender package
# ---------------------------------------------------------------------------

def make_pdf(lines: list[str]) -> bytes:
    """A minimal one-page text PDF."""
    esc = lambda t: t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    content = "BT /F1 14 Tf 72 700 Td " + " ".join(f"({esc(l)}) Tj 0 -22 Td" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


BILL = make_pdf(["H.R. 1234 - Accountability Act", "", "Sec. 4(b) The Secretary may waive the",
                 "disclosure requirement for any contractor.", "", "Sec. 5 Effective date."])


def test_capture_pdf_highlights_passage(tmp_path):
    from PIL import Image
    from app.studio.capture import capture_pdf
    out = tmp_path / "frame.png"
    report = capture_pdf(BILL, "The Secretary may waive the disclosure requirement", out)
    assert report == {"page": 1, "highlighted": True, "passage_found": True}
    img = Image.open(out).convert("RGB")
    assert img.size == (1440, 810)
    yellowish = sum(1 for p in img.getdata() if p[0] > 200 and p[1] > 180 and p[2] < 150)
    assert yellowish > 500  # the highlight is on the frame
    report = capture_pdf(BILL, "words that are not in the bill", tmp_path / "miss.png")
    assert report["passage_found"] is False and report["highlighted"] is False


def test_visuals_capture_source_pdf(client, db, mock_llm, monkeypatch):
    monkeypatch.setattr(engine, "fetch_pdf", lambda url, cache: BILL)
    pid = client.post("/studio/api/intake/create", json={"kind": "episode", "answers": [
        {"q": "About?", "a": "Capture test"}]}).json()["id"]
    engine._running.clear()
    run_stage(db, pid, "research")
    run_stage(db, pid, "story")
    vis = run_stage(db, pid, "visuals")
    assert "1 document frames captured" in vis.output, vis.output
    shot = next(a for a in db.get(StudioProject, pid).assets if a.kind == "visual" and a.status == "ready")
    assert shot.path.endswith(".png") and shot.meta["capture"]["page"] == 1
    assert client.post(f"/studio/api/asset/{shot.id}/capture").status_code == 200


class _FakeResolve:
    """Just enough of DaVinciResolveScript to run resolve_build.py."""

    def __init__(self):
        self.appended, self.markers, self.props, self.settings = [], [], [], {}
        fake = self

        class Item:
            def __init__(self, path):
                self.path = path

            def GetClipProperty(self, key):
                return self.path

        class TLItem:
            def SetProperty(self, k, v):
                fake.props.append((k, v))
                return True

        class Timeline:
            tracks = {"video": 1, "audio": 1}

            def GetTrackCount(self, kind):
                return self.tracks[kind]

            def AddTrack(self, kind, *a):
                self.tracks[kind] += 1

            def SetTrackName(self, *a):
                return True

            def GetStartFrame(self):
                return 86400

            def AddMarker(self, *a):
                fake.markers.append(a)

        class Pool:
            def GetRootFolder(self):
                return "root"

            def AddSubFolder(self, *a):
                return "folder"

            def SetCurrentFolder(self, *a):
                pass

            def ImportMedia(self, paths):
                return [Item(p) for p in paths]

            def CreateEmptyTimeline(self, name):
                return Timeline()

            def AppendToTimeline(self, infos):
                fake.appended.extend(infos)
                return [TLItem()]

        class Project:
            def SetSetting(self, k, v):
                fake.settings[k] = v

            def GetMediaPool(self):
                return Pool()

            def SetCurrentTimeline(self, t):
                pass

        class PM:
            def CreateProject(self, name):
                return Project()

        self.pm = PM()

    def GetProjectManager(self):
        return self.pm


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_resolve_package(db, tmp_path):
    import runpy
    import zipfile
    from app.studio import handoff, render
    p = StudioProject(kind="episode", title="Package Test", data={})
    db.add(p)
    db.flush()
    voice = tmp_path / "v.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=duration=4", str(voice)], check=True, capture_output=True)
    doc = tmp_path / "doc.png"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=white:s=800x600", "-frames:v", "1", str(doc)],
                   check=True, capture_output=True)
    from app.studio.models import StudioSegment
    seg = StudioSegment(project_id=p.id, title="Deep dive", format="deep_dive", minutes=1, script="HOST: hi")
    src = StudioSource(project_id=p.id, url="https://example.gov/x", title="The bill", quote="q")
    db.add_all([seg, src])
    db.flush()
    db.add_all([
        StudioAsset(project_id=p.id, kind="audio", path=str(voice), url="/x"),
        StudioAsset(project_id=p.id, kind="visual", segment_id=seg.id, source_id=src.id, status="ready",
                    title="Bill page", path=str(doc)),
        StudioAsset(project_id=p.id, kind="visual", segment_id=seg.id, source_id=src.id, status="ready",
                    title="Floor speech", url="https://example.gov/video", meta={"timestamp_in": "00:41"}),
        StudioAsset(project_id=p.id, kind="animation", title="wipe", path=str(doc),
                    meta={"after_segment_index": 0, "duration_sec": 1, "on_screen_text": "FOLLOW THE MONEY"}),
        StudioAsset(project_id=p.id, kind="script_clean", body="hi"),
    ])
    db.commit()
    db.refresh(p)
    brand = {"name": "DanDon Media", "accent": "#e63946"}
    manifest = render.build_manifest(p, brand, [{"label": "YouTube", "url": "https://yt"}], "https://x/s/")
    zpath = handoff.build_package(p, manifest, brand)
    root = tmp_path / "unzipped"
    zipfile.ZipFile(zpath).extractall(root)
    pkg = next(root.iterdir())
    for rel in ("resolve_build.py", "plan.json", "render_bumpers.bat", "blender/bumper.py", "blender/anim_01.json",
                "media/plate.mp4", "media/visual_01.mp4", "media/anim_01.mp4", "media/narration.mp3",
                "scripts/script_clean.txt", "sources.csv", "NEEDS_FRAME_GRAB.txt", "README.txt"):
        assert (pkg / rel).exists(), rel
    assert json.loads((pkg / "blender/anim_01.json").read_text())["on_screen_text"] == "FOLLOW THE MONEY"

    fake = _FakeResolve()
    runpy.run_path(str(pkg / "resolve_build.py"), init_globals={"resolve": fake}, run_name="__main__")
    tracks = sorted((i["trackIndex"], i["mediaType"]) for i in fake.appended)
    assert tracks == [(1, 1), (1, 2), (2, 1), (3, 1)]  # plate, narration, visual, bumper
    plate = next(i for i in fake.appended if i["trackIndex"] == 1 and i["mediaType"] == 1)
    assert plate["recordFrame"] == 86400 and plate["endFrame"] == round(manifest["duration_sec"] * 30) - 1
    assert ("ZoomX", 0.75) in fake.props and ("Pan", -240) in fake.props
    assert fake.settings["timelineResolutionWidth"] == "1920"
    colors = sorted(m[1] for m in fake.markers)
    assert colors == ["Blue", "Red"]  # one segment start, one shot needing a frame grab
