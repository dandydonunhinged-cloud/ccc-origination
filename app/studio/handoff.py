"""Editor handoff: package an episode for DaVinci Resolve Studio + Blender.

The Studio plans, researches, writes and sources; the finish happens on your
workstation. `build_package` writes one zip containing:

  resolve_build.py      builds the timeline in DaVinci Resolve Studio
  plan.json             the edit list the script reads (times in seconds)
  render_bumpers.bat    renders every bumper in Blender (GPU/EEVEE)
  blender/bumper.py     the Blender scene; blender/anim_NN.json one per piece
                        (drop a Meshy export in as blender/host.glb to use it)
  media/                plate, fact-visual clips, keyframe clips, narration, music
  sources/              the downloaded source PDFs (open in Acrobat Pro to mark up)
  scripts/              stage-direction and clean TTS scripts
  sources.csv, NEEDS_FRAME_GRAB.txt, README.txt
"""
import csv
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from . import integrations, render

TEMPLATES = Path(__file__).resolve().parent / "handoff_templates"
FPS = 30


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:50] or "episode"


def _still_clip(image: Path, seconds: float, out: Path, bg: str = "0x000000"):
    """Turn a still (document frame, keyframe) into a 1440x810 clip of exact length."""
    ff = integrations.ffmpeg()
    subprocess.run([ff, "-y", "-loop", "1", "-t", f"{max(seconds, 0.5):.3f}", "-i", str(image),
                    "-vf", f"scale=1440:810:force_original_aspect_ratio=decrease,"
                           f"pad=1440:810:(ow-iw)/2:(oh-ih)/2:color={bg},format=yuv420p",
                    "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)],
                   check=True, capture_output=True, timeout=600)


def _color_clip(seconds: float, out: Path):
    ff = integrations.ffmpeg()
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", f"color=c=0x0d0d12:s=1440x810:r={FPS}",
                    "-t", f"{max(seconds, 0.5):.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   check=True, capture_output=True, timeout=600)


def build_package(project, manifest: dict, brand: dict) -> Path:
    if not integrations.ffmpeg():
        raise integrations.IntegrationError("ffmpeg is needed to build the editor package.")
    base = integrations.media_path(project.public_id, "handoff")
    if base.exists():
        shutil.rmtree(base)
    media, blender, sources_dir, scripts = (base / d for d in ("media", "blender", "sources", "scripts"))
    for d in (media, blender, sources_dir, scripts):
        d.mkdir(parents=True)

    D = manifest["duration_sec"]
    clips, markers, needs_frame = [], [], []

    # V1 — layout plate for the whole episode
    render.render_mp4(manifest, media / "plate.mp4", plate_only=True)
    clips.append({"file": "media/plate.mp4", "track": 1, "type": "video", "start": 0, "end": D})

    for seg in manifest["segments"]:
        markers.append({"at": seg["start"], "color": "Blue", "name": seg["title"], "note": seg["format"]})

    # V2 — fact visuals (real documents only)
    for i, v in enumerate(manifest["visuals"], start=1):
        img = render._local_image(v, media)
        if img:
            rel = f"media/visual_{i:02d}.mp4"
            _still_clip(img, v["end"] - v["start"], base / rel)
            clips.append({"file": rel, "track": 2, "type": "video", "start": v["start"], "end": v["end"],
                          "main_area": True, "source": v.get("source_url", "")})
        else:
            note = f"{v.get('source_title', '')} {v.get('source_url', '')}".strip()
            if v.get("clip_in"):
                note += f" | clip {v['clip_in']}-{v.get('clip_out', '')}"
            if v.get("highlight"):
                note += f" | highlight: {v['highlight'][:120]}"
            markers.append({"at": v["start"], "color": "Red", "name": f"FRAME GRAB: {v['title']}", "note": note})
            needs_frame.append((v, note))

    # V3 — connective tissue: keyframe clip now, Blender bumper when rendered
    bat = ["@echo off", "rem Renders every bumper with Blender. Set BLENDER if it's installed elsewhere.",
           'if "%BLENDER%"=="" set BLENDER=C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe',
           'cd /d "%~dp0"']
    sh = ["#!/bin/sh", 'cd "$(dirname "$0")"', 'BLENDER="${BLENDER:-blender}"']
    for i, a in enumerate(manifest.get("animation", []), start=1):
        rel = f"media/anim_{i:02d}.mp4"
        seconds = a["end"] - a["start"]
        bg_rel = None
        if a.get("path") and Path(a["path"]).exists():
            bg_rel = f"media/key_{i:02d}{Path(a['path']).suffix}"
            shutil.copyfile(a["path"], base / bg_rel)
            _still_clip(base / bg_rel, seconds, base / rel, bg="0x0d0d12")
        else:
            _color_clip(seconds, base / rel)
        (blender / f"anim_{i:02d}.json").write_text(json.dumps({
            "on_screen_text": a.get("on_screen_text") or a.get("title", ""), "concept": a.get("concept", ""),
            "duration_sec": round(seconds, 2), "accent": brand.get("accent", "#e63946"),
            "brand": brand.get("name", "DanDon Media"), "background_image": bg_rel,
            "output": f"media/anim_{i:02d}_blender.mp4"}, indent=2), encoding="utf-8")
        bat.append(f'"%BLENDER%" -b -P blender\\bumper.py -- blender\\anim_{i:02d}.json')
        sh.append(f'"$BLENDER" -b -P blender/bumper.py -- blender/anim_{i:02d}.json')
        clips.append({"file": rel, "track": 3, "type": "video", "start": a["start"], "end": a["end"],
                      "main_area": True})
    shutil.copyfile(TEMPLATES / "bumper.py", blender / "bumper.py")
    (base / "render_bumpers.bat").write_text("\r\n".join(bat + ["pause"]) + "\r\n", encoding="utf-8")
    (base / "render_bumpers.sh").write_text("\n".join(sh) + "\n", encoding="utf-8")

    # A1 narration, A2 music
    voice = manifest.get("voice") or {}
    if voice.get("path") and Path(voice["path"]).exists():
        rel = "media/narration" + Path(voice["path"]).suffix
        shutil.copyfile(voice["path"], base / rel)
        clips.append({"file": rel, "track": 1, "type": "audio", "start": 0, "end": D})
    for i, m in enumerate(manifest.get("music", []), start=1):
        if m.get("path") and Path(m["path"]).exists():
            rel = f"media/music_{i:02d}" + Path(m["path"]).suffix
            shutil.copyfile(m["path"], base / rel)
            clips.append({"file": rel, "track": 2, "type": "audio", "start": 0, "end": D,
                          "volume_db": m.get("level_db", -22)})

    # Scripts, sources, cached PDFs
    for a in project.assets:
        if a.kind in ("script_stage", "script_clean") and a.body:
            (scripts / f"{a.kind}.txt").write_text(a.body, encoding="utf-8")
    with (base / "sources.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "verified", "kind", "title", "publisher", "date", "url", "quote"])
        for s in project.sources:
            w.writerow([f"S{s.id}", "yes" if s.verified else "no", s.kind, s.title, s.publisher,
                        s.published, s.url, s.quote])
    pdf_cache = integrations.MEDIA_DIR / project.public_id / "pdf"
    if pdf_cache.exists():
        for pdf in pdf_cache.glob("*.pdf"):
            shutil.copyfile(pdf, sources_dir / pdf.name)
    if needs_frame:
        (base / "NEEDS_FRAME_GRAB.txt").write_text(
            "These shots are pages or clips that need a real frame grab (screenshot, clip export,\n"
            "or a marked-up page from Acrobat Pro). Red markers on the timeline show where.\n\n" +
            "\n".join(f"{v['start']:7.1f}s  {v['title']}\n         {note}" for v, note in needs_frame),
            encoding="utf-8")

    name = _slug(project.title)
    plan = {"fps": FPS, "project_name": f"DanDon - {name}", "timeline_name": f"{name} v1",
            "episode_title": project.title, "duration_sec": D, "sources_url": manifest.get("sources_url"),
            "clips": clips, "markers": markers}
    (base / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    shutil.copyfile(TEMPLATES / "resolve_build.py", base / "resolve_build.py")
    (base / "README.txt").write_text(README.format(title=project.title, n_clips=len(clips),
                                                   n_frames=len(needs_frame)), encoding="utf-8")

    out = integrations.media_path(project.public_id, f"{name}_resolve_package.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(base.rglob("*")):
            if p.is_file():
                z.write(p, Path(name) / p.relative_to(base))
    return out


README = """{title} — DanDon Media editor package
==================================================

1. Unzip anywhere on the workstation.
2. (Optional) Blender bumpers: drop your Meshy host model in as blender\\host.glb,
   then double-click render_bumpers.bat (uses the GPU; set BLENDER= if Blender
   isn't in the default folder). Each bumper also saves a .blend you can art-direct.
3. Open DaVinci Resolve Studio, set Preferences > System > General >
   External scripting using = Local, then from this folder run:
       python resolve_build.py
   It builds the project and timeline: {n_clips} clips on the DanDon layout tracks,
   blue markers at each segment, red markers where a frame grab is still needed ({n_frames}).
4. Source PDFs are in sources\\ — open them in Acrobat Pro for any extra markup.
   Every source is listed in sources.csv and on the public sources page.

Fact visuals are real documents only. AI images appear only on the connective-tissue track.
"""
