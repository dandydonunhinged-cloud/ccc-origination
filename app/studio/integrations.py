"""External services: OmniVoice (text-to-speech) and OnlySocial (publishing).

Both are optional. When a service isn't configured the pipeline stops at that
step with a clear "configure X" message instead of failing silently.

OmniVoice  — any OmniVoice server exposing the OpenAI-compatible
             POST /v1/audio/speech endpoint (omnivoice-server, OmniVoice-local…).
             Env: OMNIVOICE_URL (e.g. http://gpu-box:8000), OMNIVOICE_API_KEY (optional).
OnlySocial — REST API at https://app.onlysocial.io/os/api/{workspaceUuid}/...
             Env: ONLYSOCIAL_TOKEN, ONLYSOCIAL_WORKSPACE (workspace UUID),
             ONLYSOCIAL_API_BASE (optional override).
Image gen  — Qwen-Image-2.1 (or any model) on your own GPU box, for the
             non-factual art only: animation keyframes, bumpers, thumbnails.
             Env: IMAGEGEN_URL, IMAGEGEN_API = "comfyui" (default) | "openai",
             IMAGEGEN_COMFY_WORKFLOW (path to a ComfyUI API-format workflow JSON),
             IMAGEGEN_MODEL (openai mode), IMAGEGEN_API_KEY (optional).
"""
import base64
import json
import random
import time
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx

MEDIA_DIR = Path(os.environ.get("STUDIO_MEDIA_DIR", "./studio_media")).resolve()


class IntegrationError(Exception):
    pass


def media_path(project_public_id: str, name: str) -> Path:
    d = MEDIA_DIR / project_public_id
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def media_url(path: Path) -> str:
    return "/studio/media/" + path.resolve().relative_to(MEDIA_DIR).as_posix()


def ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def probe_duration(path: Path) -> float | None:
    probe = shutil.which("ffprobe")
    if not probe or not path.exists():
        return None
    try:
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OmniVoice
# ---------------------------------------------------------------------------

def omnivoice_configured() -> bool:
    return bool(os.environ.get("OMNIVOICE_URL"))


def chunk_text(text: str, limit: int = 2500) -> list[str]:
    """Split on paragraph, then sentence boundaries so no chunk exceeds `limit`."""
    chunks, cur = [], ""
    for para in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        pieces = [para] if len(para) <= limit else re.split(r"(?<=[.!?])\s+", para)
        for piece in pieces:
            if len(cur) + len(piece) + 2 > limit and cur:
                chunks.append(cur)
                cur = ""
            cur = f"{cur}\n\n{piece}".strip() if cur else piece
    if cur:
        chunks.append(cur)
    return chunks


def synthesize(text: str, out: Path, voice: dict) -> Path:
    """Voice `text` with OmniVoice into `out`. Returns the output path."""
    base = os.environ.get("OMNIVOICE_URL", "").rstrip("/")
    if not base:
        raise IntegrationError("OmniVoice is not configured — set OMNIVOICE_URL to your OmniVoice server.")
    headers = {}
    if os.environ.get("OMNIVOICE_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['OMNIVOICE_API_KEY']}"
    fmt = voice.get("format", "mp3")
    parts = []
    with httpx.Client(timeout=600) as client:
        for i, chunk in enumerate(chunk_text(text)):
            resp = client.post(f"{base}/v1/audio/speech", headers=headers, json={
                "model": voice.get("model", "omnivoice"),
                "input": chunk,
                "voice": voice.get("voice", "default"),
                "speed": voice.get("speed", 1.0),
                "response_format": fmt,
            })
            if resp.status_code >= 400:
                raise IntegrationError(f"OmniVoice returned {resp.status_code}: {resp.text[:300]}")
            part = out.with_name(f"{out.stem}.part{i:03d}.{fmt}")
            part.write_bytes(resp.content)
            parts.append(part)
    _concat_audio(parts, out)
    for p in parts:
        p.unlink(missing_ok=True)
    return out


def _concat_audio(parts: list[Path], out: Path):
    if len(parts) == 1:
        shutil.copyfile(parts[0], out)
        return
    ff = ffmpeg()
    if ff:
        listing = out.with_suffix(".txt")
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
        subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)],
                       check=True, capture_output=True, timeout=600)
        listing.unlink(missing_ok=True)
    else:
        # MP3 is frame-based, so byte concatenation plays back correctly.
        with out.open("wb") as fh:
            for p in parts:
                fh.write(p.read_bytes())


# ---------------------------------------------------------------------------
# OnlySocial
# ---------------------------------------------------------------------------

def onlysocial_configured() -> bool:
    return bool(os.environ.get("ONLYSOCIAL_TOKEN") and os.environ.get("ONLYSOCIAL_WORKSPACE"))


def _os_base() -> str:
    base = os.environ.get("ONLYSOCIAL_API_BASE", "https://app.onlysocial.io/os/api").rstrip("/")
    return f"{base}/{os.environ.get('ONLYSOCIAL_WORKSPACE', '')}"


def _os_headers() -> dict:
    return {"Authorization": f"Bearer {os.environ.get('ONLYSOCIAL_TOKEN', '')}", "Accept": "application/json"}


def _os_require():
    if not onlysocial_configured():
        raise IntegrationError("OnlySocial is not configured — set ONLYSOCIAL_TOKEN and ONLYSOCIAL_WORKSPACE.")


def onlysocial_accounts() -> list[dict]:
    _os_require()
    resp = httpx.get(f"{_os_base()}/accounts", headers=_os_headers(), timeout=60)
    if resp.status_code >= 400:
        raise IntegrationError(f"OnlySocial accounts: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    return data.get("data", data) if isinstance(data, dict) else data


def onlysocial_upload(path: Path) -> str:
    _os_require()
    with path.open("rb") as fh:
        resp = httpx.post(f"{_os_base()}/media", headers=_os_headers(),
                          files={"file": (path.name, fh)}, timeout=900)
    if resp.status_code >= 400:
        raise IntegrationError(f"OnlySocial media upload: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    data = data.get("data", data)
    return str(data.get("id") or data.get("uuid"))


def onlysocial_post(entries: list[dict], media_ids: list[str], schedule_for: str = "") -> dict:
    """Create one OnlySocial post covering several accounts.

    `entries` = [{"account_id": ..., "body": ...}]; the first body is the
    original version and every account gets its own platform-specific version.
    `schedule_for` = "YYYY-MM-DD HH:MM" (UTC) or "" to publish now.
    """
    _os_require()
    if not entries:
        raise IntegrationError("No platforms have an OnlySocial account linked (Settings → Platforms).")
    when = None
    if schedule_for:
        when = datetime.strptime(schedule_for, "%Y-%m-%d %H:%M")
    else:
        when = datetime.now(timezone.utc)
    versions = [{"account_id": 0, "is_original": True,
                 "content": [{"body": entries[0]["body"], "media": media_ids, "url": ""}], "options": {}}]
    for e in entries:
        versions.append({"account_id": e["account_id"], "is_original": False,
                         "content": [{"body": e["body"], "media": media_ids, "url": ""}], "options": {}})
    payload = {
        "accounts": [e["account_id"] for e in entries],
        "tags": [],
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M"),
        "schedule": bool(schedule_for),
        "schedule_now": not schedule_for,
        "queue": False,
        "versions": versions,
    }
    resp = httpx.post(f"{_os_base()}/posts", headers=_os_headers(), json=payload, timeout=120)
    if resp.status_code >= 400:
        raise IntegrationError(f"OnlySocial post: {resp.status_code} {resp.text[:500]}")
    return resp.json()


# ---------------------------------------------------------------------------
# Image generation (Qwen-Image-2.1 on a local GPU)
# ---------------------------------------------------------------------------
#
# Two ways to reach the box:
#   comfyui — ComfyUI's HTTP API. Export your Qwen-Image-2.1 workflow with
#             "Save (API Format)" and put {{prompt}}, {{negative}}, {{width}},
#             {{height}}, {{seed}} and {{steps}} where those values go.
#   openai  — an OpenAI-compatible POST /v1/images/generations server
#             (e.g. vLLM-Omni / SGLang serving Qwen-Image-2.1).

def imagegen_configured() -> bool:
    if not os.environ.get("IMAGEGEN_URL"):
        return False
    if _imagegen_api() == "comfyui":
        return bool(os.environ.get("IMAGEGEN_COMFY_WORKFLOW"))
    return True


def _imagegen_api() -> str:
    return os.environ.get("IMAGEGEN_API", "comfyui").strip().lower()


def _imagegen_headers() -> dict:
    key = os.environ.get("IMAGEGEN_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def generate_image(prompt: str, out: Path, opts: dict) -> Path:
    """Generate one image from `prompt` into `out` (PNG). `opts` = imagegen settings."""
    if not imagegen_configured():
        raise IntegrationError("Image generation is not configured — set IMAGEGEN_URL "
                               "(and IMAGEGEN_COMFY_WORKFLOW for ComfyUI).")
    style = (opts.get("style_prefix") or "").strip()
    full_prompt = f"{style}, {prompt}" if style else prompt
    params = {
        "prompt": full_prompt,
        "negative": opts.get("negative_prompt", ""),
        "width": int(opts.get("width", 1024)),
        "height": int(opts.get("height", 576)),
        "steps": int(opts.get("steps", 30)),
        "seed": int(opts.get("seed") or random.randint(1, 2**31 - 1)),
    }
    if _imagegen_api() == "openai":
        data = _imagegen_openai(params)
    else:
        data = _imagegen_comfyui(params)
    out.write_bytes(data)
    return out


def _imagegen_openai(p: dict) -> bytes:
    base = os.environ["IMAGEGEN_URL"].rstrip("/")
    resp = httpx.post(f"{base}/v1/images/generations", headers=_imagegen_headers(), timeout=900, json={
        "model": os.environ.get("IMAGEGEN_MODEL", "Qwen/Qwen-Image-2.1"),
        "prompt": p["prompt"], "negative_prompt": p["negative"], "n": 1,
        "size": f"{p['width']}x{p['height']}", "seed": p["seed"],
        "num_inference_steps": p["steps"], "response_format": "b64_json",
    })
    if resp.status_code >= 400:
        raise IntegrationError(f"Image server returned {resp.status_code}: {resp.text[:300]}")
    item = (resp.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        return httpx.get(item["url"], timeout=300).content
    raise IntegrationError("Image server response had no image.")


def fill_workflow(template: str, p: dict) -> dict:
    """Substitute {{placeholders}} in an API-format ComfyUI workflow."""
    text = template
    for key in ("prompt", "negative"):
        # json.dumps gives a safely escaped string; strip its quotes to drop it inside the template's quotes
        text = text.replace("{{%s}}" % key, json.dumps(p[key])[1:-1])
    for key in ("width", "height", "seed", "steps"):
        text = text.replace('"{{%s}}"' % key, str(p[key])).replace("{{%s}}" % key, str(p[key]))
    return json.loads(text)


def _imagegen_comfyui(p: dict) -> bytes:
    base = os.environ["IMAGEGEN_URL"].rstrip("/")
    wf_path = Path(os.environ["IMAGEGEN_COMFY_WORKFLOW"])
    if not wf_path.exists():
        raise IntegrationError(f"ComfyUI workflow file not found: {wf_path}")
    workflow = fill_workflow(wf_path.read_text(encoding="utf-8"), p)
    with httpx.Client(timeout=60, headers=_imagegen_headers()) as client:
        resp = client.post(f"{base}/prompt", json={"prompt": workflow})
        if resp.status_code >= 400:
            raise IntegrationError(f"ComfyUI rejected the workflow: {resp.text[:300]}")
        prompt_id = resp.json()["prompt_id"]
        deadline = time.time() + 900  # a 4060 Ti can take a few minutes per image
        while time.time() < deadline:
            hist = client.get(f"{base}/history/{prompt_id}").json().get(prompt_id)
            if hist:
                for node in (hist.get("outputs") or {}).values():
                    for img in node.get("images", []):
                        view = client.get(f"{base}/view", params={
                            "filename": img["filename"], "subfolder": img.get("subfolder", ""),
                            "type": img.get("type", "output")})
                        if view.status_code == 200:
                            return view.content
                status = (hist.get("status") or {}).get("status_str")
                if status == "error":
                    raise IntegrationError("ComfyUI reported an error running the workflow.")
            time.sleep(2)
    raise IntegrationError("Timed out waiting for ComfyUI to finish the image.")
