"""Composition: the DanDon Media broadcast layout and the render.

Layout (1920x1080 canvas):

    +-------------------------------+-----------+
    |                               |  host     |
    |   MAIN VISUAL (3/4)           |  animation|
    |   1440 x 810                  |  / logo   |
    |   documents, clips, animation |  480x540  |
    +-------------------------------+-----------+
    |   LINKS BAR  1440 x 270       |  SOURCES  |
    |   our other platforms         |  link     |
    +-------------------------------+-----------+

`build_manifest` turns an episode's assets into a timed edit list. The web
player (/studio/episode/<id>/player/) plays that manifest live in the browser
with clickable links; `render_mp4` burns it to a video file with ffmpeg.
"""
import logging
import mimetypes
import re
import subprocess
from pathlib import Path

import httpx

from . import integrations
from .pipeline import WORDS_PER_MINUTE

logger = logging.getLogger(__name__)

CANVAS = (1920, 1080)
LAYOUT = {
    "main": (0, 0, 1440, 810),
    "links_bar": (0, 810, 1440, 270),
    "host": (1440, 0, 480, 540),
    "sources": (1440, 540, 480, 540),
}
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w[\w'’-]*\b", text or ""))


def build_manifest(project, brand: dict, links_bar: list, sources_url: str) -> dict:
    assets = project.assets
    voice = next((a for a in assets if a.kind == "audio" and (a.path or a.url)), None)
    duration, duration_source = None, "estimate"
    if voice and voice.path:
        duration = integrations.probe_duration(Path(voice.path))
        if duration:
            duration_source = "audio"

    segs = list(project.segments)
    weights = [max(word_count(s.script), 1) if s.script else max(int((s.minutes or 1) * WORDS_PER_MINUTE), 1)
               for s in segs]
    total_words = sum(weights) or 1
    if not duration:
        duration = round(total_words / WORDS_PER_MINUTE * 60, 1) if segs else 60.0

    timeline, t = [], 0.0
    for s, w in zip(segs, weights):
        span = duration * w / total_words
        timeline.append({"segment_id": s.id, "title": s.title, "format": s.format,
                         "start": round(t, 2), "end": round(t + span, 2)})
        t += span

    warnings = []
    visuals = []
    by_seg: dict = {}
    for a in [a for a in assets if a.kind == "visual"]:
        by_seg.setdefault(a.segment_id, []).append(a)
    for seg_id, shots in by_seg.items():
        window = next((w for w in timeline if w["segment_id"] == seg_id), None)
        start, end = (window["start"], window["end"]) if window else (0.0, duration)
        step = (end - start) / max(len(shots), 1)
        for i, a in enumerate(sorted(shots, key=lambda x: x.rank)):
            meta = a.meta or {}
            if a.status == "needs_sourcing":
                warnings.append(f"Visual '{a.title}' has no verified source — excluded from render.")
                continue
            if meta.get("generated"):
                warnings.append(f"Visual '{a.title}' is AI-generated — fact visuals must be real; excluded.")
                continue
            visuals.append({
                "asset_id": a.id, "title": a.title, "type": meta.get("visual_type", "document"),
                "url": a.url, "path": a.path, "caption": meta.get("caption", ""),
                "highlight": meta.get("highlight_text", ""),
                "source_title": a.source.title if a.source else meta.get("source_title", ""),
                "source_url": a.source.url if a.source else meta.get("source_url", ""),
                "clip_in": meta.get("timestamp_in", ""), "clip_out": meta.get("timestamp_out", ""),
                "start": round(start + i * step, 2), "end": round(start + (i + 1) * step, 2),
            })

    animation = []
    for a in [a for a in assets if a.kind == "animation"]:
        meta = a.meta or {}
        idx = meta.get("after_segment_index")
        at = timeline[idx]["end"] if isinstance(idx, int) and 0 <= idx < len(timeline) else 0.0
        dur = float(meta.get("duration_sec") or 3)
        animation.append({"asset_id": a.id, "title": a.title, "concept": meta.get("concept", a.body),
                          "url": a.url, "path": a.path, "generated": bool(meta.get("generated")),
                          "on_screen_text": meta.get("on_screen_text", ""),
                          "start": round(max(at - dur / 2, 0), 2),
                          "end": round(min(at + dur / 2, duration), 2)})

    music = [{"asset_id": a.id, "title": a.title, "url": a.url, "path": a.path,
              "level_db": (a.meta or {}).get("level_db", -22)} for a in assets if a.kind == "music"]

    if not voice:
        warnings.append("No narration audio yet — timing is estimated from word counts.")
    if not visuals:
        warnings.append("No sourced visuals yet.")

    return {
        "episode": {"id": project.id, "public_id": project.public_id, "title": project.title},
        "duration_sec": round(duration, 2),
        "duration_source": duration_source,
        "canvas": CANVAS,
        "layout": LAYOUT,
        "brand": brand,
        "links_bar": [l for l in links_bar if l.get("url")],
        "sources_url": sources_url,
        "voice": {"url": voice.url, "path": voice.path} if voice else None,
        "music": music,
        "segments": timeline,
        "visuals": visuals,
        "animation": animation,
        "warnings": warnings,
    }


def _local_image(item: dict, workdir: Path) -> Path | None:
    """Return a local image file for a visual, downloading it if it's a remote image."""
    if item.get("path") and Path(item["path"]).exists():
        p = Path(item["path"])
        return p if (mimetypes.guess_type(p.name)[0] or "").startswith("image/") and p.suffix != ".svg" else None
    url = item.get("url") or ""
    if not url.startswith("http"):
        return None
    try:
        resp = httpx.get(url, timeout=60, follow_redirects=True)
        ctype = resp.headers.get("content-type", "")
        if resp.status_code == 200 and ctype.startswith("image/") and "svg" not in ctype:
            ext = mimetypes.guess_extension(ctype.split(";")[0]) or ".img"
            out = workdir / f"visual_{item['asset_id']}{ext}"
            out.write_bytes(resp.content)
            return out
    except Exception as e:
        logger.info("visual %s not downloadable: %s", item.get("asset_id"), e)
    return None


def render_mp4(manifest: dict, out: Path, plate_only: bool = False) -> dict:
    """Burn the manifest into an MP4 with ffmpeg. Returns a report dict.

    plate_only=True renders just the layout frame (links bar, host/logo panel,
    sources link) with no main visuals and no audio — the V1 plate for Resolve.
    """
    ff = integrations.ffmpeg()
    if not ff:
        raise integrations.IntegrationError("ffmpeg is not installed on this server — the web player still works.")
    if plate_only:
        manifest = {**manifest, "visuals": [], "animation": []}
    voice = manifest.get("voice") or {}
    if not plate_only and (not voice.get("path") or not Path(voice["path"]).exists()):
        raise integrations.IntegrationError("Render needs the narration audio (run the Voice step first).")

    workdir = out.parent
    D = manifest["duration_sec"]
    W, H = CANVAS
    font = next((f for f in FONT_CANDIDATES if Path(f).exists()), None)

    inputs = ["-f", "lavfi", "-t", str(D), "-i", f"color=c=0x0d0d12:s={W}x{H}:r=30"]
    filters, last, n = [], "[0:v]", 1
    skipped = []

    # Main visuals
    for item in manifest["visuals"]:
        img = _local_image(item, workdir)
        if not img:
            skipped.append(item["title"])
            continue
        dur = max(item["end"] - item["start"], 0.5)
        inputs += ["-loop", "1", "-t", f"{dur:.2f}", "-i", str(img)]
        x, y, w, h = LAYOUT["main"]
        filters.append(f"[{n}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                       f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
                       f"setpts=PTS-STARTPTS+{item['start']:.2f}/TB[v{n}]")
        filters.append(f"{last}[v{n}]overlay={x}:{y}:eof_action=pass:"
                       f"enable='between(t,{item['start']:.2f},{item['end']:.2f})'[o{n}]")
        last, n = f"[o{n}]", n + 1

    # Animated connective tissue: keyframe stills (e.g. from the local image model) cover the
    # main area during their window. Video pieces are left to the editor for now.
    for item in manifest.get("animation", []):
        img = _local_image(item, workdir) if item.get("path") else None
        if not img:
            continue
        dur = max(item["end"] - item["start"], 0.5)
        inputs += ["-loop", "1", "-t", f"{dur:.2f}", "-i", str(img)]
        x, y, w, h = LAYOUT["main"]
        filters.append(f"[{n}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                       f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x0d0d12,"
                       f"setpts=PTS-STARTPTS+{item['start']:.2f}/TB[v{n}]")
        filters.append(f"{last}[v{n}]overlay={x}:{y}:eof_action=pass:"
                       f"enable='between(t,{item['start']:.2f},{item['end']:.2f})'[o{n}]")
        last, n = f"[o{n}]", n + 1

    # Host animation / logo (upper right)
    brand = manifest.get("brand") or {}
    host = brand.get("host_animation_path") or brand.get("logo_path")
    if host and Path(host).exists() and Path(host).suffix.lower() != ".svg":
        x, y, w, h = LAYOUT["host"]
        is_video = (mimetypes.guess_type(host)[0] or "").startswith("video/")
        inputs += (["-stream_loop", "-1", "-i", host] if is_video else ["-loop", "1", "-i", host])
        filters.append(f"[{n}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                       f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x16161f[v{n}]")
        filters.append(f"{last}[v{n}]overlay={x}:{y}:shortest=1[o{n}]")
        last, n = f"[o{n}]", n + 1

    # Text panels (links bar, sources, brand fallback). textfile= avoids escaping issues.
    if font:
        def text(label, body, x, y, size, color="white"):
            nonlocal last
            tf = workdir / f"text_{label}.txt"
            tf.write_text(body, encoding="utf-8")
            filters.append(f"{last}drawtext=fontfile='{font}':textfile='{tf.as_posix()}':"
                           f"x={x}:y={y}:fontsize={size}:fontcolor={color}:line_spacing=10[t_{label}]")
            last = f"[t_{label}]"

        links = "   |   ".join(f"{l['label']}: {l['url']}" for l in manifest.get("links_bar", []))
        text("links", "FOLLOW  " + (links or brand.get("name", "")), 40, 810 + 110, 28)
        text("srclabel", "SOURCES", 1460, 580, 34, "0xe63946")
        text("srcsub", "every document we used:", 1460, 630, 22, "0xaaaaaa")
        text("srcurl", _wrap(manifest.get("sources_url", ""), 30), 1460, 675, 24)
        if not (host and Path(host).exists()):
            text("brand", brand.get("name", "DanDon Media"), 1480, 250, 44)

    filters.append(f"{last}format=yuv420p[vout]")

    if plate_only:
        cmd = [ff, "-y", *inputs, "-filter_complex", ";".join(filters), "-map", "[vout]", "-t", str(D),
               "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if proc.returncode != 0:
            raise integrations.IntegrationError("ffmpeg failed: " + proc.stderr[-1500:])
        return {"output": str(out)}

    # Audio: narration + music bed(s) ducked underneath
    inputs += ["-i", voice["path"]]
    voice_idx = n
    n += 1
    audio_chain = f"[{voice_idx}:a]anull[a0]"
    mix = ["[a0]"]
    for i, m in enumerate(manifest.get("music", [])):
        if m.get("path") and Path(m["path"]).exists():
            inputs += ["-stream_loop", "-1", "-i", m["path"]]
            filters.append(f"[{n}:a]volume={float(m.get('level_db', -22))}dB[m{i}]")
            mix.append(f"[m{i}]")
            n += 1
    filters.append(audio_chain)
    filters.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[aout]")

    cmd = [ff, "-y", *inputs, "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
           "-t", str(D), "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise integrations.IntegrationError("ffmpeg failed: " + proc.stderr[-1500:])
    return {"output": str(out), "visuals_used": len(manifest["visuals"]) - len(skipped),
            "visuals_skipped": skipped}


def _wrap(s: str, width: int) -> str:
    return "\n".join(s[i:i + width] for i in range(0, len(s), width))
