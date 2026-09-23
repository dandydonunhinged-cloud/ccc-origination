"""The production engine: runs a task through its specialist and applies the
side effects each stage is responsible for (sources, segments, writer tasks,
scripts, audio, shot lists, render, posts).

Tasks run in a background thread with their own DB session so the UI stays
responsive; the project page polls until nothing is running.
"""
import json
import logging
import re
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..db import SessionLocal
from . import llm, integrations, render
from .models import (
    StudioProject, StudioTask, StudioSegment, StudioSpecialist, StudioSource, StudioAsset,
    StudioMessage, StudioPost,
)
from .pipeline import (
    STEWART_DOCTRINE, HOUSE_RULES, WORDS_PER_MINUTE, WRITER_FOR_FORMAT, INTAKE_QUESTIONS,
    populate_tasks, runtime_target, get_setting, stages_for,
)

logger = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------

def _brief_text(project: StudioProject) -> str:
    brief = project.brief or {}
    return "\n".join(f"- {q}\n  → {a}" for q, a in brief.items() if a) or "(no intake answers)"


def _sources_text(project: StudioProject, limit_quote: int = 400) -> str:
    rows = []
    for s in project.sources:
        line = f"[S{s.id}] {s.title or s.url} — {s.publisher or '?'} ({s.published or 'n.d.'}) {s.url}"
        if s.kind:
            line += f" [{s.kind}]"
        if s.quote:
            line += f'\n      "{s.quote[:limit_quote]}"'
        rows.append(line)
    return "\n".join(rows) or "(no sources collected yet)"


def project_context(project: StudioProject) -> str:
    parts = [f"TITLE:{project.title}", f"KIND: {project.kind}", f"PREMISE: {project.premise or '(none)'}",
             "INTAKE BRIEF:\n" + _brief_text(project)]
    if project.parent:
        p = project.parent
        inv = (p.data or {}).get("investigation", {})
        parts.append(f"PART OF SERIES: {p.title}\nSERIES PREMISE: {p.premise}\n"
                     f"SERIES BRIEF:\n{_brief_text(p)}\n"
                     f"SERIES INVESTIGATION VERDICT: {inv.get('verdict', 'n/a')} — {inv.get('summary', '')}")
        order = [c.title for c in sorted(p.children, key=lambda c: c.rank)]
        parts.append("SERIES EPISODE ORDER:\n" + "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(order)))
    if project.notes:
        parts.append("DIRECTOR NOTES:\n" + project.notes)
    research = (project.data or {}).get("research")
    if research:
        parts.append("RESEARCH SUMMARY:\n" + research.get("summary", "") + "\nKEY FACTS:\n" + "\n".join(
            f"- [{f.get('status', '?')}] {f.get('fact')} ({f.get('url', '')})" for f in research.get("key_facts", [])))
    story = (project.data or {}).get("story")
    if story:
        parts.append(f"LOGLINE: {story.get('logline', '')}\nTHESIS: {story.get('thesis', '')}\n"
                     f"EDUCATIONAL BOMB: {story.get('bomb', '')}")
    parts.append("SOURCES ON FILE:\n" + _sources_text(project))
    return "\n\n".join(parts)


def _rundown_text(project: StudioProject) -> str:
    return "\n".join(
        f"{i + 1}. {s.title} [{s.format}, ~{s.minutes} min] — {s.angle}\n   beats: " + "; ".join(s.beats or [])
        for i, s in enumerate(project.segments)) or "(no rundown)"


SCRIPT_CONVENTIONS = """Script conventions (the formatter depends on these):
- Spoken lines start with a speaker label: "HOST:" or "CORRESPONDENT (Name):".
- Every production direction goes in square brackets on its own line:
  [VISUAL: S12 — highlight "exact passage"], [CLIP: S7 00:41–00:55], [SFX: ...],
  [MUSIC: ...], [BEAT], [TRANSITION: ...], [GRAPHIC: ...].
- Tag each factual claim with its source id in braces right after it, e.g. {S12}.
- Only use source ids that appear in SOURCES ON FILE. If a claim has no source,
  write it as {NEEDS SOURCE} so the standards editor catches it.
"""


def _json_contract(schema: str) -> str:
    return f"Return ONLY a JSON object in a ```json fenced block with this shape:\n{schema}"


# ---------------------------------------------------------------------------
# Stage handlers. Each returns (output_text, output_data, next_status).
# ---------------------------------------------------------------------------

def _call(db: Session, task: StudioTask, prompt: str, system_extra: str = "", effort: str | None = None):
    spec = db.get(StudioSpecialist, task.specialist) if task.specialist else None
    system = "\n\n".join(x for x in [
        spec.system_prompt if spec else "", HOUSE_RULES, system_extra] if x)
    result = llm.complete(system=system, messages=[{"role": "user", "content": prompt}],
                          model=(spec.model if spec else ""), tools=(spec.tools if spec else []),
                          effort=effort or (spec.effort if spec else "high"))
    task.model_used = result.model
    return result


def _norm_url(u: str) -> str:
    return (u or "").strip().rstrip("/").replace("http://", "https://").lower()


def _add_sources(db: Session, project: StudioProject, items: list[dict], added_by: str, seen_urls: set) -> int:
    existing = {_norm_url(s.url) for s in project.sources}
    rank = max([s.rank for s in project.sources] or [0]) + 1
    added = 0
    for it in items or []:
        url = (it.get("url") or "").strip()
        if not url or _norm_url(url) in existing:
            continue
        supports = it.get("supports") or it.get("claim") or ""
        if it.get("status"):
            supports = f"[{it['status'].upper()}] {supports}"
        if seen_urls and _norm_url(url) not in seen_urls:
            supports = "[URL not seen in search results — verify] " + supports
        db.add(StudioSource(project_id=project.id, url=url, title=it.get("title", "")[:500],
                            publisher=it.get("publisher", "")[:300], kind=it.get("kind", "article")[:40],
                            published=str(it.get("date", ""))[:40], quote=it.get("quote", ""),
                            supports=supports, added_by=added_by, rank=rank))
        existing.add(_norm_url(url))
        rank += 1
        added += 1
    db.flush()
    return added


def h_investigate(db, task, project):
    prompt = f"""{project_context(project)}

TASK: Viability investigation. Determine whether the public record contains enough real,
documented evidence to substantiate the claim above. Search for primary documents
(court filings, IG/GAO reports, congressional records, bills, disclosures, FOIA
releases) and credible reporting. Report evidence that cuts against the claim too.
Then break the claim into 4–10 candidate episode topics, strongest first.

{_json_contract('''{"verdict": "substantiated|partially_substantiated|insufficient",
 "confidence": 0-100, "summary": "3-6 sentences",
 "evidence": [{"claim": "", "status": "proven|alleged", "title": "", "publisher": "", "date": "", "kind": "court_filing|bill|gov_report|dataset|transcript|video|article", "url": "", "quote": "exact passage"}],
 "counter_evidence": [ same shape ],
 "topics": [{"title": "", "angle": "", "why_it_matters": "", "key_documents": ["url"], "strength": "strong|moderate|thin"}],
 "gaps": ["what we could not substantiate"]}''')}
OUTPUT_CONTRACT:investigate"""
    res = _call(db, task, prompt)
    data = llm.extract_json(res.text)
    if not isinstance(data, dict):
        return res.text, {"citations": res.citations}, "review"
    seen = {_norm_url(c["url"]) for c in res.citations}
    n = _add_sources(db, project, data.get("evidence", []), "investigator", seen)
    n += _add_sources(db, project, [dict(e, status="counter") for e in data.get("counter_evidence", [])],
                      "investigator", seen)
    d = dict(project.data or {})
    d["investigation"] = {k: data.get(k) for k in ("verdict", "confidence", "summary", "gaps")}
    d["topics"] = [dict(t, include=True) for t in data.get("topics", [])]
    project.data = d
    if project.status in ("intake", "investigating"):
        project.status = "planning"
    summary = (f"Verdict: {data.get('verdict')} (confidence {data.get('confidence')})\n\n{data.get('summary', '')}"
               f"\n\n{len(d['topics'])} candidate topics. {n} sources added.")
    if data.get("gaps"):
        summary += "\n\nGaps:\n" + "\n".join(f"- {g}" for g in data["gaps"])
    return summary, dict(data, citations=res.citations), "review"


def h_research(db, task, project):
    prompt = f"""{project_context(project)}

TASK: Deep research for THIS episode. Build the evidence file: collect every document that
substantiates (or undercuts) this episode's claim. Primary documents first. For each, pull
the exact passage we would put on screen. Build a dated timeline.

{_json_contract('''{"summary": "", "sources": [{"title": "", "publisher": "", "date": "", "kind": "court_filing|bill|gov_report|dataset|transcript|video|article", "url": "", "quote": "exact passage to show", "supports": "which claim"}],
 "timeline": [{"date": "", "event": "", "url": ""}],
 "key_facts": [{"fact": "", "status": "proven|alleged", "url": ""}],
 "open_questions": [""]}''')}
OUTPUT_CONTRACT:research"""
    res = _call(db, task, prompt)
    data = llm.extract_json(res.text)
    if not isinstance(data, dict):
        return res.text, {"citations": res.citations}, "review"
    n = _add_sources(db, project, data.get("sources", []), "research_analyst",
                     {_norm_url(c["url"]) for c in res.citations})
    d = dict(project.data or {})
    d["research"] = {k: data.get(k) for k in ("summary", "timeline", "key_facts", "open_questions")}
    project.data = d
    return (f"{data.get('summary', '')}\n\n{n} sources added. {len(data.get('key_facts', []))} key facts, "
            f"{len(data.get('timeline', []))} timeline entries."), dict(data, citations=res.citations), "review"


def h_story(db, task, project):
    lo, hi = runtime_target(project)
    structure = STEWART_DOCTRINE if project.kind != "documentary" else (
        "DOCUMENTARY FORMAT: acts instead of segments (format \"act\"), each act a self-contained chapter that "
        "escalates toward the educational bomb. Same sourcing non-negotiables as below.\n\n" + STEWART_DOCTRINE)
    prompt = f"""{project_context(project)}

{structure}

TASK: Synthesize the evidence into a story and a running order of segments for a
{lo}–{hi} minute {project.kind}. Each segment will be written by its own writer, so give
each one a clear angle, its beats, and the source URLs it will lean on. Minutes must add
up to {lo}–{hi}.

{_json_contract('''{"logline": "", "thesis": "", "bomb": "the educational bomb, one sentence",
 "segments": [{"title": "", "format": "cold_open|headlines|deep_dive|correspondent|interview|convergence|button|act", "minutes": 2.5, "angle": "", "beats": [""], "source_urls": [""]}]}''')}
OUTPUT_CONTRACT:story"""
    res = _call(db, task, prompt)
    data = llm.extract_json(res.text)
    if not isinstance(data, dict) or not data.get("segments"):
        return res.text, {}, "review"

    # Re-running replaces the rundown and its writer tasks.
    for t in [t for t in project.tasks if t.stage == "write"]:
        db.delete(t)
    for s in list(project.segments):
        db.delete(s)
    db.flush()

    d = dict(project.data or {})
    d["story"] = {k: data.get(k) for k in ("logline", "thesis", "bomb")}
    project.data = d
    segs = data["segments"]
    for i, sd in enumerate(segs):
        fmt = (sd.get("format") or "deep_dive").lower()
        writer = WRITER_FOR_FORMAT.get(fmt, "segment_writer")
        seg = StudioSegment(project_id=project.id, rank=i, title=sd.get("title", f"Segment {i + 1}")[:300],
                            format=fmt, minutes=float(sd.get("minutes") or 2), angle=sd.get("angle", ""),
                            beats=sd.get("beats", []), source_urls=sd.get("source_urls", []), writer=writer)
        db.add(seg)
        db.flush()
        db.add(StudioTask(project_id=project.id, segment_id=seg.id, stage="write",
                          title=f"Write segment {i + 1}: {seg.title}", specialist=writer, auto=True,
                          instructions=f"Write this {fmt.replace('_', ' ')} segment (~{seg.minutes} min).",
                          rank=task.rank + (i + 1) / (len(segs) + 1)))
    total = sum(float(s.get("minutes") or 0) for s in segs)
    out = (f"LOGLINE: {data.get('logline')}\nTHESIS: {data.get('thesis')}\nBOMB: {data.get('bomb')}\n\n"
           f"RUNDOWN ({total:g} min):\n" + "\n".join(
               f"{i + 1}. {s.get('title')} [{s.get('format')}, {s.get('minutes')} min] — {s.get('angle')}"
               for i, s in enumerate(segs)) + f"\n\n{len(segs)} writer tasks created.")
    return out, data, "review"


def h_write(db, task, project):
    seg = task.segment
    if seg is None:
        raise ValueError("This writer task is not attached to a segment.")
    words = int((seg.minutes or 2) * WORDS_PER_MINUTE)
    prompt = f"""{project_context(project)}

{STEWART_DOCTRINE}

FULL RUNDOWN (you are writing ONE of these; set up / pay off the others):
{_rundown_text(project)}

YOUR SEGMENT: "{seg.title}" — format: {seg.format}, target ~{seg.minutes} min (~{words} spoken words)
ANGLE: {seg.angle}
BEATS: {"; ".join(seg.beats or [])}
LEAN ON: {", ".join(seg.source_urls or []) or "(pick from sources on file)"}

{SCRIPT_CONVENTIONS}

TASK: Write the full script for this segment only. Output the script, nothing else.
OUTPUT_CONTRACT:segment"""
    res = _call(db, task, prompt)
    seg.script = res.text
    return res.text, {"words": render.word_count(_clean_for_tts(res.text))}, "review"


def h_edit(db, task, project):
    lo, hi = runtime_target(project)
    missing = [s.title for s in project.segments if not s.script]
    if missing:
        raise ValueError("Segments without a script yet: " + ", ".join(missing))
    scripts = "\n\n".join(f"===== SEGMENT {i + 1}: {s.title} [{s.format}] =====\n{s.script}"
                          for i, s in enumerate(project.segments))
    prompt = f"""{project_context(project)}

{STEWART_DOCTRINE}

SEGMENT SCRIPTS:
{scripts}

{SCRIPT_CONVENTIONS}

TASK: You are the head writer/editor. Combine these into ONE {project.kind} that runs
{lo}–{hi} minutes ({lo * WORDS_PER_MINUTE}–{hi * WORDS_PER_MINUTE} spoken words). Keep one host voice, add
callbacks between segments, tighten, and make sure the educational bomb lands. Keep a
"===== SEGMENT n: title =====" header before each segment. Output the full script only.
OUTPUT_CONTRACT:edit"""
    res = _call(db, task, prompt)
    words = render.word_count(_clean_for_tts(res.text))
    d = dict(project.data or {})
    d["master_script"] = res.text
    d["master_words"] = words
    project.data = d
    minutes = words / WORDS_PER_MINUTE
    note = f"\n\n[{words} spoken words ≈ {minutes:.1f} min; target {lo}–{hi} min]"
    if not (lo <= minutes <= hi):
        note += " ⚠ outside target runtime"
    return res.text + note, {"words": words, "minutes": round(minutes, 1)}, "review"


def h_standards(db, task, project):
    master = (project.data or {}).get("master_script")
    if not master:
        raise ValueError("No assembled script yet — run the Edit step first.")
    prompt = f"""{project_context(project)}

SCRIPT TO CHECK:
{master}

TASK: Standards & fact-check. For every factual statement, confirm the cited source
actually supports it (open the URL if needed). Flag: unsupported claims, {{NEEDS SOURCE}}
tags, allegations stated as proven fact, misquotes, stale numbers, and anything that
reads as a factual assertion about a real person that the record doesn't support.

{_json_contract('''{"verdict": "clear|fix_required", "summary": "",
 "issues": [{"quote": "line from script", "problem": "", "severity": "high|medium|low", "fix": "suggested rewrite", "source_id": "S12"}]}''')}
OUTPUT_CONTRACT:standards"""
    res = _call(db, task, prompt)
    data = llm.extract_json(res.text)
    if not isinstance(data, dict):
        return res.text, {}, "review"
    issues = data.get("issues", [])
    out = f"Verdict: {data.get('verdict')}\n{data.get('summary', '')}\n\n" + "\n\n".join(
        f"[{i.get('severity', '?').upper()}] \"{i.get('quote', '')}\"\n  Problem: {i.get('problem')}\n  Fix: {i.get('fix')}"
        for i in issues)
    status = "blocked" if any(i.get("severity") == "high" for i in issues) else "review"
    return out, data, status


_DIRECTION = re.compile(r"^\s*\[[^\]]*\]\s*$", re.M)
_INLINE_DIRECTION = re.compile(r"\[[^\]]*\]")
_SOURCE_TAG = re.compile(r"\s*\{(?:S\d+(?:\s*,\s*S\d+)*|NEEDS SOURCE)\}")
_SPEAKER = re.compile(r"^\s*[A-Z][A-Z .'’-]{1,40}(?:\s*\([^)]*\))?:\s*", re.M)
_HEADER = re.compile(r"^\s*=+.*=+\s*$", re.M)


def _clean_for_tts(script: str) -> str:
    """Strip stage directions, source tags, speaker labels and headers → narration text."""
    text = _HEADER.sub("", script or "")
    text = _DIRECTION.sub("", text)
    text = _INLINE_DIRECTION.sub("", text)
    text = _SOURCE_TAG.sub("", text)
    text = _SPEAKER.sub("", text)
    text = re.sub(r"[*_#>`]+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _replace_assets(db, project, kind, only_auto=True):
    for a in [a for a in project.assets if a.kind == kind and (not only_auto or (a.meta or {}).get("auto"))]:
        db.delete(a)
    db.flush()


def h_scripts(db, task, project):
    master = (project.data or {}).get("master_script")
    if not master:
        raise ValueError("No assembled script yet — run the Edit step first.")
    clean = _clean_for_tts(master)
    _replace_assets(db, project, "script_stage")
    _replace_assets(db, project, "script_clean")
    db.add(StudioAsset(project_id=project.id, kind="script_stage", title="Script — with stage directions",
                       body=master, meta={"auto": True}, status="ready", rank=1))
    db.add(StudioAsset(project_id=project.id, kind="script_clean", title="Script — clean for text-to-speech",
                       body=clean, meta={"auto": True}, status="ready", rank=2))
    words = render.word_count(clean)
    return (f"Two scripts produced.\nStage version: {render.word_count(master)} words incl. directions.\n"
            f"Clean TTS version: {words} words ≈ {words / WORDS_PER_MINUTE:.1f} min."), {"words": words}, "done"


def h_voice(db, task, project):
    clean = next((a for a in project.assets if a.kind == "script_clean"), None)
    if clean is None:
        raise ValueError("No clean script yet — run the Two scripts step first.")
    if not clean.body.strip():
        raise ValueError("The clean script has no spoken lines to voice — check the assembled script.")
    voice = get_setting(db, "voice") or {}
    out = integrations.media_path(project.public_id, f"narration.{voice.get('format', 'mp3')}")
    integrations.synthesize(clean.body, out, voice)
    _replace_assets(db, project, "audio")
    dur = integrations.probe_duration(out)
    db.add(StudioAsset(project_id=project.id, kind="audio", title="Narration (OmniVoice)",
                       path=str(out), url=integrations.media_url(out), status="ready",
                       meta={"auto": True, "duration_sec": dur, "voice": voice}))
    return f"Narration rendered{f' ({dur:.0f}s)' if dur else ''}.", {"duration_sec": dur}, "review"


def h_visuals(db, task, project):
    if not project.sources:
        raise ValueError("No sources on file — the visuals desk only uses real, sourced material.")
    body = "\n\n".join(f"===== SEGMENT index {i}: {s.title} =====\n{s.script or s.angle}"
                       for i, s in enumerate(project.segments))
    prompt = f"""{project_context(project)}

SCRIPT BY SEGMENT:
{body}

TASK: Build the shot list for the main (3/4-screen) visual area. For each [VISUAL]/[CLIP]
direction and each factual beat, specify the REAL artifact to show: the document page and
the exact passage to highlight, an official page to screenshot, a real video with
timestamps, or a chart built from a cited dataset. Use only source ids from SOURCES ON FILE.
If a beat needs a visual we don't have a source for, include it with "source_id": null so
we know to go get it. Never propose imagined or illustrative imagery for a factual claim.
If a source links directly to an image file, put that URL in "image_url".

{_json_contract('''{"shots": [{"segment_index": 0, "cue": "script line it covers", "visual_type": "document_highlight|screenshot|video_clip|chart|photo",
 "source_id": "S12", "image_url": "", "page": "", "highlight_text": "", "timestamp_in": "", "timestamp_out": "", "caption": ""}]}''')}
OUTPUT_CONTRACT:visuals"""
    res = _call(db, task, prompt)
    data = llm.extract_json(res.text)
    if not isinstance(data, dict):
        return res.text, {}, "review"
    by_id = {f"S{s.id}": s for s in project.sources}
    segs = list(project.segments)
    _replace_assets(db, project, "visual")
    ok = gaps = 0
    for i, shot in enumerate(data.get("shots", [])):
        src = by_id.get(str(shot.get("source_id") or "").strip())
        idx = shot.get("segment_index")
        seg = segs[idx] if isinstance(idx, int) and 0 <= idx < len(segs) else None
        status = "ready" if src else "needs_sourcing"
        ok += bool(src)
        gaps += not src
        db.add(StudioAsset(project_id=project.id, kind="visual", segment_id=seg.id if seg else None,
                           source_id=src.id if src else None, rank=i, status=status,
                           title=(shot.get("caption") or shot.get("cue") or f"Shot {i + 1}")[:300],
                           url=shot.get("image_url") or (src.url if src else ""),
                           meta={**shot, "auto": True}))
    return f"{ok} sourced shots, {gaps} beats still need a real source.", data, "review"


def h_animation(db, task, project):
    prompt = f"""{project_context(project)}

RUNDOWN:
{_rundown_text(project)}

TASK: Design the animated connective tissue: an open ident, a transition after each
segment, and any explainer graphics that carry the story (e.g. a money-flow diagram built
from cited figures). Short, punchy, on-brand for DanDon Media. For each piece give a
production-ready prompt/brief an animator or generator can execute.

{_json_contract('''{"pieces": [{"after_segment_index": -1, "duration_sec": 3, "concept": "", "style": "", "prompt": "", "on_screen_text": ""}]}''')}
OUTPUT_CONTRACT:animation"""
    res = _call(db, task, prompt)
    data = llm.extract_json(res.text)
    if not isinstance(data, dict):
        return res.text, {}, "review"
    _replace_assets(db, project, "animation")
    pieces = []
    for i, p in enumerate(data.get("pieces", [])):
        a = StudioAsset(project_id=project.id, kind="animation", rank=i, title=p.get("concept", "")[:300],
                        body=p.get("prompt", ""), meta={**p, "auto": True}, status="draft")
        db.add(a)
        pieces.append(a)
    db.flush()
    out = f"{len(pieces)} animation pieces designed."
    out += _generate_keyframes(db, pieces) if integrations.imagegen_configured() else \
        " Attach rendered files to each piece (or set up image generation to render keyframes)."
    return out, data, "review"


GENERATED_KINDS = ("animation", "thumbnail")  # never "visual": fact visuals are real documents only


def generate_asset_image(db: Session, asset: StudioAsset) -> StudioAsset:
    """Render an image for a non-factual asset with the local image model (Qwen-Image-2.1)."""
    if asset.kind not in GENERATED_KINDS:
        raise ValueError("AI images are only for animation and thumbnails — fact visuals must be real documents.")
    prompt = (asset.body or (asset.meta or {}).get("prompt") or asset.title or "").strip()
    if not prompt:
        raise ValueError("This asset has no prompt to generate from.")
    out = integrations.media_path(asset.project.public_id, f"gen_{asset.kind}_{asset.id}.png")
    integrations.generate_image(prompt, out, get_setting(db, "imagegen") or {})
    asset.path = str(out)
    asset.url = integrations.media_url(out) + f"?v={int(datetime.now().timestamp())}"
    asset.meta = {**(asset.meta or {}), "generated": True, "generator": "image model (AI-generated)"}
    asset.status = "ready"
    return asset


def _generate_keyframes(db, assets) -> str:
    made, failed = 0, []
    for a in assets:
        try:
            generate_asset_image(db, a)
            made += 1
        except (integrations.IntegrationError, ValueError) as e:
            failed.append(f"{a.title[:40]}: {e}")
    msg = f" {made} keyframes generated on the image model."
    if failed:
        msg += " Failed: " + "; ".join(failed[:3])
    return msg


def h_music(db, task, project):
    prompt = f"""{project_context(project)}

RUNDOWN:
{_rundown_text(project)}

TASK: Build the music & sound cue sheet: an underlying bed for the whole episode plus
stingers/transitions. Royalty-free or properly licensed sources only. Levels should sit
under narration (bed around -22 dB).

{_json_contract('''{"cues": [{"placement": "", "mood": "", "bpm": 90, "instrumentation": "", "suggested_source": "", "level_db": -22}]}''')}
OUTPUT_CONTRACT:music"""
    res = _call(db, task, prompt, effort="medium")
    data = llm.extract_json(res.text)
    if not isinstance(data, dict):
        return res.text, {}, "review"
    _replace_assets(db, project, "music")
    for i, c in enumerate(data.get("cues", [])):
        db.add(StudioAsset(project_id=project.id, kind="music", rank=i,
                           title=f"{c.get('placement', '')} — {c.get('mood', '')}"[:300],
                           body=c.get("suggested_source", ""), meta={**c, "auto": True}, status="draft"))
    return f"{len(data.get('cues', []))} cues. Upload the chosen audio file onto each cue.", data, "review"


def sources_url(db, project) -> str:
    base = (get_setting(db, "public_base_url") or "").rstrip("/")
    return f"{base}/studio/p/{project.public_id}/sources/"


def h_transparency(db, task, project):
    url = sources_url(db, project)
    _replace_assets(db, project, "transparency")
    db.add(StudioAsset(project_id=project.id, kind="transparency", title="Public sources page", url=url,
                       meta={"auto": True}, status="ready"))
    unverified = sum(1 for s in project.sources if not s.verified)
    msg = f"Sources page live at {url} ({len(project.sources)} sources)."
    if unverified:
        msg += f"\n⚠ {unverified} sources not yet marked verified by a human."
    return msg, {"url": url}, "review" if unverified else "done"


def h_render(db, task, project):
    brand = dict(get_setting(db, "brand") or {})
    manifest = render.build_manifest(project, brand, get_setting(db, "links_bar") or [], sources_url(db, project))
    _replace_assets(db, project, "manifest")
    db.add(StudioAsset(project_id=project.id, kind="manifest", title="Render manifest (edit list)",
                       body=json.dumps(manifest, indent=2), meta={"auto": True}, status="ready",
                       url=f"/studio/episode/{project.id}/player/"))
    db.flush()
    lines = [f"Manifest built: {manifest['duration_sec']:.0f}s ({manifest['duration_source']}), "
             f"{len(manifest['visuals'])} visuals, {len(manifest['animation'])} animation pieces.",
             f"Preview: /studio/episode/{project.id}/player/"]
    lines += [f"⚠ {w}" for w in manifest["warnings"]]
    status = "review"
    if integrations.ffmpeg() and manifest.get("voice") and manifest["voice"].get("path"):
        out = integrations.media_path(project.public_id, "episode.mp4")
        try:
            report = render.render_mp4(manifest, out)
            _replace_assets(db, project, "render")
            db.add(StudioAsset(project_id=project.id, kind="render", title="Rendered episode (MP4)",
                               path=str(out), url=integrations.media_url(out), status="ready",
                               meta={"auto": True, **report}))
            lines.append(f"Rendered MP4: {integrations.media_url(out)}")
            if report["visuals_skipped"]:
                lines.append(f"{len(report['visuals_skipped'])} visuals need a frame grab (PDF/page/clip) "
                             "before they burn in — attach an image to those assets and re-render.")
            project.status = "rendered"
        except integrations.IntegrationError as e:
            lines.append(f"MP4 render failed: {e}")
            status = "blocked"
    else:
        lines.append("MP4 not rendered (needs ffmpeg + narration audio). The browser player works now.")
    return "\n".join(lines), manifest, status


def h_publish(db, task, project):
    platforms = [p for p in (get_setting(db, "platforms") or []) if p.get("enabled")]
    story = (project.data or {}).get("story", {})
    prompt = f"""{project_context(project)}

LOGLINE: {story.get('logline', '')}
BOMB: {story.get('bomb', '')}
SOURCES PAGE: {sources_url(db, project)}

TASK: Write native post copy for each platform: {", ".join(p['key'] for p in platforms)}.
Lead with the hook, respect each platform's length norms (X ≤ 280 chars incl. link,
Bluesky ≤ 300, Threads ≤ 500), and always point to the sources page. No claims beyond
what the episode proves.

Also write "thumbnail_prompt": an image-generation prompt for a bold 16:9 thumbnail
(no real people's faces, no fake documents; stylized, text-free art — we add text later).

{_json_contract('''{"<platform_key>": {"title": "", "caption": "", "hashtags": "#a #b"}, "thumbnail_prompt": ""}''')}
OUTPUT_CONTRACT:social"""
    res = _call(db, task, prompt, effort="medium")
    data = llm.extract_json(res.text) or {}
    for p in [p for p in project.posts if p.status == "draft"]:
        db.delete(p)
    db.flush()
    for p in platforms:
        entry = data.get(p["key"], {}) if isinstance(data, dict) else {}
        db.add(StudioPost(project_id=project.id, platform=p["key"], title=entry.get("title", project.title)[:300],
                          caption=entry.get("caption", ""), hashtags=entry.get("hashtags", "")[:500]))
    msg = (f"Drafted posts for {len(platforms)} platforms. Review them in the Publish panel, approve, "
           "then press Send to OnlySocial.")
    thumb_prompt = data.get("thumbnail_prompt") if isinstance(data, dict) else None
    if thumb_prompt:
        _replace_assets(db, project, "thumbnail")
        thumb = StudioAsset(project_id=project.id, kind="thumbnail", title="Thumbnail", body=thumb_prompt,
                            meta={"auto": True}, status="draft")
        db.add(thumb)
        db.flush()
        if integrations.imagegen_configured():
            try:
                generate_asset_image(db, thumb)
                msg += " Thumbnail generated."
            except (integrations.IntegrationError, ValueError) as e:
                msg += f" Thumbnail generation failed: {e}"
    return msg, data, "review"


HANDLERS = {
    "investigate": h_investigate, "research": h_research, "story": h_story, "write": h_write,
    "edit": h_edit, "standards": h_standards, "scripts": h_scripts, "voice": h_voice,
    "visuals": h_visuals, "animation": h_animation, "music": h_music, "transparency": h_transparency,
    "render": h_render, "publish": h_publish,
}


def h_generic(db, task, project):
    prompt = f"""{project_context(project)}

TASK: {task.title}
{task.instructions}
OUTPUT_CONTRACT:text"""
    res = _call(db, task, prompt)
    return res.text, {"citations": res.citations}, "review"


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

_running: set[int] = set()
_lock = threading.Lock()


def can_run(task: StudioTask) -> bool:
    return task.auto and (task.stage in HANDLERS or bool(task.specialist))


def start_task(db: Session, task: StudioTask) -> bool:
    """Mark a task running and execute it in a background thread."""
    with _lock:
        if task.id in _running:
            return False
        _running.add(task.id)
    task.status = "running"
    task.error = ""
    task.started_at = now()
    db.commit()
    threading.Thread(target=_run_in_thread, args=(task.id,), daemon=True).start()
    return True


def _run_in_thread(task_id: int):
    db = SessionLocal()
    try:
        run_task(db, task_id)
    finally:
        with _lock:
            _running.discard(task_id)
        db.close()


def run_task(db: Session, task_id: int):
    task = db.get(StudioTask, task_id)
    if task is None:
        return
    project = task.project
    handler = HANDLERS.get(task.stage, h_generic)
    try:
        output, data, status = handler(db, task, project)
        task.output = output or ""
        task.output_data = data or {}
        task.status = status
    except (llm.LLMError, integrations.IntegrationError, ValueError) as e:
        db.rollback()
        task = db.get(StudioTask, task_id)
        task.status = "blocked"
        task.error = str(e)
    except Exception as e:  # keep the worker alive; surface the failure on the card
        logger.exception("studio task %s failed", task_id)
        db.rollback()
        task = db.get(StudioTask, task_id)
        task.status = "blocked"
        task.error = f"Unexpected error: {e}"
    task.finished_at = now()
    refresh_project(task.project)
    db.commit()
    if task.status in ("review", "done") and (task.project.data or {}).get("autopilot"):
        approve(db, task)


def approve(db: Session, task: StudioTask):
    """Human sign-off: mark done and, on autopilot, start the next auto step."""
    task.status = "done"
    refresh_project(task.project)
    db.commit()
    if (task.project.data or {}).get("autopilot"):
        nxt = next_task(task.project)
        if nxt is not None and can_run(nxt):
            start_task(db, nxt)


def next_task(project: StudioProject) -> StudioTask | None:
    for t in sorted(project.tasks, key=lambda t: t.rank):
        if t.status in ("todo", "blocked", "review", "running"):
            return t if t.status == "todo" else None
    return None


_STAGE_STATUS = {
    "intake": "intake", "investigate": "investigating", "showrunner": "planning", "research": "in_production",
    "story": "in_production", "write": "in_production", "edit": "in_production", "standards": "in_production",
    "scripts": "in_production", "voice": "in_production", "visuals": "in_production",
    "animation": "in_production", "music": "in_production", "transparency": "in_production",
    "render": "review", "publish": "rendered",
}


def refresh_project(project: StudioProject):
    """Recompute the current stage and (unless parked/killed/published) the status."""
    open_tasks = [t for t in sorted(project.tasks, key=lambda t: t.rank) if t.status != "done"]
    d = dict(project.data or {})
    d["stage"] = open_tasks[0].stage if open_tasks else "done"
    project.data = d
    if project.status in ("parked", "killed", "published"):
        return
    if not project.tasks:
        return
    if not open_tasks:
        project.status = "published" if any(t.stage == "publish" for t in project.tasks) else "review"
        if project.kind == "series":
            project.status = "in_production"
    else:
        project.status = _STAGE_STATUS.get(open_tasks[0].stage, project.status)


def progress(project: StudioProject) -> dict:
    total = len(project.tasks)
    done = sum(1 for t in project.tasks if t.status == "done")
    stages = stages_for(project.kind)
    return {"total": total, "done": done, "pct": int(100 * done / total) if total else 0,
            "stage": (project.data or {}).get("stage", stages[0]), "stages": stages,
            "running": any(t.status == "running" for t in project.tasks)}


# ---------------------------------------------------------------------------
# Spock: intake interview, fine-tuning chat, topic ranking, locking episodes
# ---------------------------------------------------------------------------

def _spock_system(db: Session) -> str:
    spec = db.get(StudioSpecialist, "spock")
    return (spec.system_prompt if spec else "") + "\n\n" + HOUSE_RULES


def intake_next(db: Session, kind: str, answers: list[dict]) -> dict:
    """Return the next leading question, or {"done": True}.

    Works through the question bank for `kind`. After each answer Spock may ask
    one follow-up if the answer is too vague to act on (max 3 follow-ups).
    """
    bank = INTAKE_QUESTIONS.get(kind, INTAKE_QUESTIONS["project"])
    asked_bank = [a for a in answers if not a.get("followup")]
    followups = sum(1 for a in answers if a.get("followup"))
    last = answers[-1] if answers else None
    if last and not last.get("followup") and followups < 3 and llm.provider() != "offline":
        transcript = "\n".join(f"Q: {a['q']}\nA: {a['a']}" for a in answers)
        try:
            res = llm.complete(
                system=_spock_system(db),
                messages=[{"role": "user", "content":
                           f"We're scoping a new {kind}.\n\n{transcript}\n\nIf the LAST answer is too vague to act "
                           "on (no specifics, nothing provable), reply with ONE short, pointed follow-up question. "
                           "Otherwise reply with exactly: NEXT"}],
                model=(db.get(StudioSpecialist, "spock").model if db.get(StudioSpecialist, "spock") else ""),
                effort="low", max_tokens=2000)
            q = res.text.strip()
            if q and q.upper().strip(". ") != "NEXT" and len(q) < 600:
                return {"question": q, "followup": True, "step": len(asked_bank), "of": len(bank)}
        except llm.LLMError as e:
            logger.info("intake follow-up skipped: %s", e)
    if len(asked_bank) < len(bank):
        return {"question": bank[len(asked_bank)], "followup": False, "step": len(asked_bank) + 1, "of": len(bank)}
    return {"done": True}


def create_from_intake(db: Session, kind: str, answers: list[dict], title: str = "",
                       parent_id: int | None = None) -> StudioProject:
    brief = {}
    for a in answers:
        if a.get("a"):
            brief[a["q"]] = a["a"]
    first = answers[0]["a"].strip() if answers and answers[0].get("a") else ""
    title = (title or first or f"Untitled {kind}").strip()
    if len(title) > 120:
        title = title[:117].rsplit(" ", 1)[0] + "…"
    premise = answers[1]["a"] if len(answers) > 1 else first
    siblings = db.query(StudioProject).filter(StudioProject.parent_id == parent_id).all()
    project = StudioProject(kind=kind, title=title, premise=premise, brief=brief, parent_id=parent_id,
                            status="intake", rank=min([p.rank for p in siblings] or [1]) - 1, data={})
    db.add(project)
    db.flush()
    populate_tasks(db, project, skip_intake=True)
    if kind == "project":
        tasks_answer = answers[-1]["a"] if answers else ""
        for i, line in enumerate(l.strip("-•* ").strip() for l in tasks_answer.splitlines()):
            if line:
                db.add(StudioTask(project_id=project.id, stage="custom", title=line[:300], auto=False, rank=i + 1))
    db.add(StudioMessage(project_id=project.id, channel="intake", role="assistant", specialist="spock",
                         content="Intake transcript:\n\n" + "\n\n".join(f"Q: {a['q']}\nA: {a['a']}" for a in answers)))
    db.flush()
    db.refresh(project)
    refresh_project(project)
    db.commit()
    return project


def chat(db: Session, project: StudioProject, text: str) -> StudioMessage:
    db.add(StudioMessage(project_id=project.id, channel="showrunner", role="user", content=text))
    db.flush()
    history = [m for m in project.messages if m.channel == "showrunner"][-30:]
    topics = (project.data or {}).get("topics", [])
    ctx = project_context(project)
    if topics:
        ctx += "\n\nCANDIDATE TOPICS (current order):\n" + "\n".join(
            f"{i + 1}. {'' if t.get('include', True) else '(excluded) '}{t.get('title')} "
            f"[{t.get('strength', '?')}] — {t.get('angle', '')}" for i, t in enumerate(topics))
    msgs = [{"role": m.role, "content": m.content} for m in history]
    if msgs and msgs[0]["role"] == "user":
        msgs[0]["content"] = f"CONTEXT:\n{ctx}\n\n---\n{msgs[0]['content']}"
    else:
        msgs.insert(0, {"role": "user", "content": f"CONTEXT:\n{ctx}"})
    spec = db.get(StudioSpecialist, "spock")
    try:
        res = llm.complete(system=_spock_system(db), messages=msgs, model=spec.model if spec else "",
                           tools=spec.tools if spec else [], effort="medium", max_tokens=8000)
        reply = res.text
    except llm.LLMError as e:
        reply = f"(Spock is unavailable: {e})"
    msg = StudioMessage(project_id=project.id, channel="showrunner", role="assistant", specialist="spock",
                        content=reply)
    db.add(msg)
    db.commit()
    return msg


def rank_topics(db: Session, project: StudioProject) -> str:
    topics = (project.data or {}).get("topics", [])
    if not topics:
        raise ValueError("No topics to rank — run the investigation first.")
    listing = "\n".join(f"{i}. {t.get('title')} [{t.get('strength')}] — {t.get('angle')} / {t.get('why_it_matters')}"
                        for i, t in enumerate(topics))
    convo = "\n".join(f"{m.role.upper()}: {m.content}" for m in project.messages if m.channel == "showrunner")[-6000:]
    spec = db.get(StudioSpecialist, "spock")
    res = llm.complete(system=_spock_system(db), model=spec.model if spec else "", effort="high", messages=[{
        "role": "user", "content": f"{project_context(project)}\n\nTOPICS:\n{listing}\n\nOUR DISCUSSION SO FAR:\n{convo}\n\n"
        "Rank these topics into an episode order. Weigh strength of evidence, stakes, timeliness, and how each "
        "sets up the next (the series should escalate). Respect anything the creator said in the discussion.\n"
        + _json_contract('{"order": [topic indexes], "reasoning": "short"}')}])
    data = llm.extract_json(res.text)
    if not isinstance(data, dict) or not isinstance(data.get("order"), list):
        return res.text or "Spock couldn't produce a ranking."
    order = [i for i in data["order"] if isinstance(i, int) and 0 <= i < len(topics)]
    order += [i for i in range(len(topics)) if i not in order]
    d = dict(project.data)
    d["topics"] = [topics[i] for i in order]
    project.data = d
    db.add(StudioMessage(project_id=project.id, channel="showrunner", role="assistant", specialist="spock",
                         content="Proposed order:\n" + "\n".join(f"{n + 1}. {topics[i].get('title')}"
                                                                  for n, i in enumerate(order))
                         + f"\n\n{data.get('reasoning', '')}"))
    db.commit()
    return data.get("reasoning", "")


def lock_episodes(db: Session, series: StudioProject) -> list[StudioProject]:
    topics = [t for t in (series.data or {}).get("topics", []) if t.get("include", True)]
    if not topics:
        raise ValueError("No included topics to turn into episodes.")
    existing = {c.title for c in series.children}
    base = max([c.rank for c in series.children] or [0])
    created = []
    for i, t in enumerate(topics):
        title = t.get("title", f"Episode {i + 1}")
        if title in existing:
            continue
        ep = StudioProject(kind="episode", parent_id=series.id, title=title, premise=t.get("angle", ""),
                           priority=series.priority, rank=base + i + 1, status="in_production", data={},
                           brief={"Series": series.title, "Episode topic": title, "Angle": t.get("angle", ""),
                                  "Why it matters": t.get("why_it_matters", ""),
                                  "Evidence strength": t.get("strength", "")})
        db.add(ep)
        db.flush()
        populate_tasks(db, ep, skip_intake=True)
        # Seed the episode's evidence file with the documents the investigator tied to this topic.
        docs = set(_norm_url(u) for u in t.get("key_documents", []))
        seeded = [s for s in series.sources if _norm_url(s.url) in docs]
        for j, s in enumerate(seeded):
            db.add(StudioSource(project_id=ep.id, url=s.url, title=s.title, publisher=s.publisher, kind=s.kind,
                                published=s.published, quote=s.quote, supports=s.supports, verified=s.verified,
                                added_by="series", rank=j))
        db.flush()
        db.refresh(ep)
        refresh_project(ep)
        created.append(ep)
    # Locking the order is the sign-off on the investigation and the Spock session.
    for t in series.tasks:
        if t.stage == "showrunner" or (t.stage == "investigate" and t.status == "review"):
            t.status = "done"
    refresh_project(series)
    db.commit()
    return created


def publish_posts(db: Session, project: StudioProject) -> dict:
    """Send approved posts to OnlySocial as one multi-account post."""
    platforms = {p["key"]: p for p in (get_setting(db, "platforms") or [])}
    approved = [p for p in project.posts if p.status == "approved"]
    if not approved:
        raise ValueError("Approve at least one platform post first.")
    entries, missing = [], []
    for post in approved:
        acct = platforms.get(post.platform, {}).get("onlysocial_account_id")
        if not acct:
            missing.append(post.platform)
            continue
        body = "\n\n".join(x for x in [post.caption, post.hashtags] if x)
        entries.append({"account_id": int(acct) if str(acct).isdigit() else acct, "body": body, "post": post})
    render_asset = next((a for a in project.assets if a.kind == "render" and a.path), None)
    media = []
    if render_asset:
        from pathlib import Path
        media = [integrations.onlysocial_upload(Path(render_asset.path))]
    schedule = next((p.scheduled_for for p in approved if p.scheduled_for), "")
    resp = integrations.onlysocial_post([{k: v for k, v in e.items() if k != "post"} for e in entries],
                                        media, schedule)
    ext = str((resp.get("data") or resp).get("uuid") or (resp.get("data") or resp).get("id") or "")
    for e in entries:
        e["post"].status = "sent"
        e["post"].external_id = ext
        e["post"].response = resp
    if entries:
        project.status = "published"
    db.commit()
    return {"sent": [e["post"].platform for e in entries], "missing_account": missing, "media": bool(media)}
