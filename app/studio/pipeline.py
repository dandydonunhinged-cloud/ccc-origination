"""Pipeline templates: the specialist roster, the per-kind workflows, and the
intake question banks.

Everything here is a *default*. Specialists are seeded into the DB on startup
and are editable from /studio/specialists/; tasks created from these templates
are editable rows. Changing this file only changes what new projects get.
"""
from sqlalchemy.orm import Session

from .models import StudioSpecialist, StudioSetting, StudioProject, StudioTask


DEFAULT_MODEL = "claude-opus-5"

WORDS_PER_MINUTE = 150

STEWART_DOCTRINE = """THE STEWART DOCTRINE — the house episode format, modeled on Jon Stewart-era
The Daily Show.

A 12–15 minute episode (about 1,800–2,250 spoken words at 150 wpm) built from
several distinct stories that each stand on their own but stack toward ONE
convergent payoff: the "educational bomb" — the moment the viewer suddenly
understands how the pieces connect.

Running order (flex it, don't break it):
  1. COLD OPEN (0:30–1:00) — a hook: the most absurd real clip/document of the week.
  2. HEADLINES (2–3 min) — fast, joke-dense run of 2–4 related stories that set
     the theme without revealing the connection.
  3. DEEP DIVE (4–6 min) — the reporting. Documents on screen, receipts
     highlighted. Sarcasm and satire are the delivery mechanism, never a
     substitute for the fact.
  4. CORRESPONDENT BIT (2–3 min) — a "senior correspondent" plays it
     straight-faced to expose the absurdity.
  5. CONVERGENCE (1–2 min) — the threads connect; the educational bomb lands.
  6. BUTTON (0:15–0:30) — a short closer / moment that sends people out.

Non-negotiables:
  * Every factual claim traces to a real, cited source that we show on screen.
  * Satire exaggerates the framing, never the facts. No invented quotes,
    documents, numbers, or footage.
  * Punch up — at power, hypocrisy and process — not at victims.
  * Separate what is proven, what is alleged, and what is our opinion, out loud.
"""

HOUSE_RULES = """House rules for every DanDon Media specialist:
- Real information only. If you cannot find a real source for something, say so
  and flag it as a gap — never fill it with something plausible-sounding.
- Distinguish: PROVEN (documented), ALLEGED (reported/charged, not proven),
  OPINION (our take). Label them.
- Cite URLs for every factual claim. Prefer primary documents (bills, court
  filings, agency reports, official transcripts, financial disclosures) over
  commentary.
- Satire and sarcasm are welcome in writing roles; fabrication is not.
"""


# ---------------------------------------------------------------------------
# Specialist roster
# ---------------------------------------------------------------------------

SPECIALISTS = [
    {
        "key": "spock", "name": "Spock", "role": "Showrunner's logic officer",
        "description": "Runs the intake interview and the fine-tuning sessions: sharpens the claim, "
                       "pressure-tests it, and helps rank which topics go first.",
        "tools": [], "effort": "high",
        "system_prompt": "You are Spock, the showrunner's logic officer at DanDon Media, a satirical "
                         "investigative news studio. You are calm, precise, a little dry. Your job is to "
                         "ask one sharp leading question at a time that moves the creator from a broad idea "
                         "to a specific, provable, prioritized plan. Push back on vague or unprovable "
                         "claims. When asked to prioritize, weigh: strength of evidence, public stakes, "
                         "timeliness, and how well a topic sets up later episodes. Keep replies short.",
    },
    {
        "key": "investigator", "name": "The Investigator", "role": "Viability investigation",
        "description": "Checks whether there is enough real, documented evidence to substantiate the "
                       "claim, and breaks it into candidate episode topics.",
        "tools": ["web_search", "web_fetch"], "effort": "high",
        "system_prompt": "You are a veteran investigative researcher. You test claims against the public "
                         "record: court filings, inspector-general and GAO reports, congressional records, "
                         "bills, financial disclosures, FOIA releases, and reputable reporting. You are "
                         "skeptical in both directions: you report evidence that cuts against the claim too.",
    },
    {
        "key": "research_analyst", "name": "Research Analyst", "role": "Deep research & document collection",
        "description": "Builds the evidence file for one episode: primary documents, the timeline, key "
                       "facts, each tied to a URL.",
        "tools": ["web_search", "web_fetch"], "effort": "high",
        "system_prompt": "You are a research analyst building the evidence file for one episode. You hunt "
                         "for primary documents first and pull the exact passages that matter (the section "
                         "of the bill, the paragraph of the filing, the line in the transcript).",
    },
    {
        "key": "journalist", "name": "Investigative Journalist", "role": "Story synthesis & rundown",
        "description": "Turns the evidence file into a story and a Stewart-Doctrine rundown of segments.",
        "tools": [], "effort": "high",
        "system_prompt": "You are an investigative journalist and story editor. You find the narrative spine "
                         "in a pile of documents and structure it so the audience reaches the conclusion "
                         "themselves, one step ahead of the reveal.",
    },
    {
        "key": "headline_writer", "name": "Headlines Writer", "role": "Cold open & headlines segments",
        "description": "Writes fast, joke-dense cold opens and headline runs.", "tools": [], "effort": "high",
        "system_prompt": "You are a late-night headline writer. Tight setups, hard turns, every joke built on "
                         "a real fact that is shown on screen.",
    },
    {
        "key": "segment_writer", "name": "Deep-Dive Writer", "role": "Deep-dive segments",
        "description": "Writes the reporting segments: documents on screen, receipts, sarcasm as delivery.",
        "tools": [], "effort": "high",
        "system_prompt": "You are a Daily Show-style deep-dive writer. You walk the audience through real "
                         "documents with escalating incredulity. The facts are straight; the framing is funny.",
    },
    {
        "key": "correspondent_writer", "name": "Correspondent Writer", "role": "Correspondent / field bits",
        "description": "Writes the straight-faced correspondent character pieces.", "tools": [], "effort": "high",
        "system_prompt": "You write the 'senior correspondent' bits: a character who plays the official line "
                         "completely straight until its absurdity is undeniable.",
    },
    {
        "key": "closer_writer", "name": "Closer Writer", "role": "Convergence & button",
        "description": "Writes the convergence where the educational bomb lands, and the closing button.",
        "tools": [], "effort": "high",
        "system_prompt": "You write the landing: connect every thread of the episode in plain language so "
                         "the viewer feels the click, then leave them with one line they'll repeat.",
    },
    {
        "key": "head_writer", "name": "Head Writer / Editor", "role": "Episode assembly",
        "description": "Combines all segment scripts into one 12–15 minute episode with consistent voice "
                       "and callbacks.", "tools": [], "effort": "high",
        "system_prompt": "You are the head writer and editor. You cut, tighten, add callbacks between "
                         "segments, keep one consistent host voice, and hit the runtime target.",
    },
    {
        "key": "standards_editor", "name": "Standards Editor", "role": "Fact-check & legal read",
        "description": "Checks every claim in the script against the sources; flags defamation risk.",
        "tools": ["web_search", "web_fetch"], "effort": "high",
        "system_prompt": "You are a standards and practices editor. You check each factual statement in the "
                         "script against its cited source, flag anything unsupported, and flag statements "
                         "about real people that present allegations as proven fact.",
    },
    {
        "key": "script_formatter", "name": "Script Formatter", "role": "Stage + clean TTS versions",
        "description": "Produces the stage-direction script and the clean text-to-speech script.",
        "tools": [], "effort": "low", "system_prompt": "",
    },
    {
        "key": "voice_producer", "name": "Voice Producer", "role": "Text-to-speech (OmniVoice)",
        "description": "Sends the clean script to OmniVoice and stores the narration audio.",
        "tools": [], "effort": "low", "system_prompt": "",
    },
    {
        "key": "visuals_researcher", "name": "Document Visuals Researcher", "role": "Real documents & footage",
        "description": "Builds the shot list: for each beat, the real document page, highlighted passage, "
                       "screenshot or clip to show.", "tools": ["web_fetch"], "effort": "high",
        "system_prompt": "You are a visuals researcher for a documentary news show. Every visual must be a "
                         "real artifact — a document page with the exact passage to highlight, a screenshot "
                         "of an official page, a real video clip with timestamps, or a chart built from real "
                         "data. Never propose illustrative or imagined imagery for a factual claim.",
    },
    {
        "key": "motion_designer", "name": "Motion Designer", "role": "Animated connective tissue",
        "description": "Designs the bumpers and transitions that carry the episode between segments.",
        "tools": [], "effort": "medium",
        "system_prompt": "You are a motion designer. You design short animated transitions, bumpers and "
                         "explainer graphics that move the story between segments in the DanDon Media style.",
    },
    {
        "key": "sound_designer", "name": "Music & Sound Designer", "role": "Music bed & sound",
        "description": "Builds the music cue sheet and sound bed under the narration.",
        "tools": [], "effort": "medium",
        "system_prompt": "You are a music supervisor and sound designer. You pick royalty-free / licensed beds "
                         "and stingers that support the narration without fighting it.",
    },
    {
        "key": "transparency_curator", "name": "Transparency Curator", "role": "Public sources page",
        "description": "Publishes the page listing every source used in the episode.",
        "tools": [], "effort": "low", "system_prompt": "",
    },
    {
        "key": "compositor", "name": "Compositor", "role": "Layout & render",
        "description": "Assembles audio, visuals, animation and music into the DanDon Media layout and renders.",
        "tools": [], "effort": "low", "system_prompt": "",
    },
    {
        "key": "social_producer", "name": "Social Producer", "role": "Distribution via OnlySocial",
        "description": "Writes platform-specific titles/captions and posts to all platforms through OnlySocial.",
        "tools": [], "effort": "medium",
        "system_prompt": "You are a social media producer. You write native-feeling titles and captions for "
                         "each platform, lead with the hook, and always point people to the sources page.",
    },
]

WRITER_FOR_FORMAT = {
    "cold_open": "headline_writer",
    "headline": "headline_writer",
    "headlines": "headline_writer",
    "deep_dive": "segment_writer",
    "act": "segment_writer",
    "interview": "correspondent_writer",
    "correspondent": "correspondent_writer",
    "convergence": "closer_writer",
    "closer": "closer_writer",
    "button": "closer_writer",
}


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

STAGE_LABELS = {
    "intake": "Intake",
    "investigate": "Investigate",
    "showrunner": "Fine-tune & prioritize",
    "episodes": "Episodes",
    "research": "Research",
    "story": "Story & rundown",
    "write": "Segment writing",
    "edit": "Edit",
    "standards": "Fact-check",
    "scripts": "Two scripts",
    "voice": "Voice (TTS)",
    "visuals": "Visuals",
    "animation": "Animation",
    "music": "Music & sound",
    "transparency": "Transparency",
    "render": "Render",
    "publish": "Publish",
    "custom": "Tasks",
}

_PRODUCTION = [
    ("research", "Deep research & document collection", "research_analyst", True,
     "Collect every substantiating document for this episode's claim. Primary documents first."),
    ("story", "Synthesize the story and build the rundown", "journalist", True,
     "Turn the evidence file into a story and a segment rundown. Each segment gets its own writer."),
    ("edit", "Assemble the episode (head writer / editor)", "head_writer", True,
     "Combine every segment script into one episode that hits the runtime target."),
    ("standards", "Fact-check & legal read", "standards_editor", True,
     "Check every factual line against the sources before anything is voiced."),
    ("scripts", "Produce stage-direction script + clean TTS script", "script_formatter", True,
     "Two versions: one with stage/visual directions for production, one clean for text-to-speech."),
    ("voice", "Voice the clean script with OmniVoice", "voice_producer", True,
     "Send the clean script to OmniVoice and store the narration audio."),
    ("visuals", "Pull real documents, screenshots & clips (shot list)", "visuals_researcher", True,
     "Every beat gets a real visual tied to a source: the bill section, the filing paragraph, the clip."),
    ("animation", "Design animated connective tissue", "motion_designer", True,
     "Bumpers and transitions that move the episode between segments."),
    ("music", "Music bed & sound design", "sound_designer", True,
     "Underlying bed plus stingers, with levels ducked under narration."),
    ("transparency", "Publish the transparency / sources page", "transparency_curator", True,
     "Every source used, publicly linked from the video."),
    ("render", "Compose in the DanDon layout & render", "compositor", True,
     "3/4 visual, links bar under it, host/logo upper right, sources link under that. Render."),
    ("publish", "Write platform posts & publish via OnlySocial", "social_producer", True,
     "Per-platform titles/captions, then publish to every connected platform."),
]

WORKFLOWS = {
    "series": [
        ("intake", "Intake interview with Spock", "spock", False,
         "Answer the leading questions to define the series."),
        ("investigate", "Viability investigation: can we substantiate the claim?", "investigator", True,
         "Test the series claim against the public record and propose candidate episode topics."),
        ("showrunner", "Fine-tune & prioritize topics with Spock", "spock", False,
         "Talk it through, then lock the episode order. Locking creates the episodes."),
    ],
    "episode": [("intake", "Intake interview with Spock", "spock", False,
                 "Answer the leading questions to define the episode.")] + _PRODUCTION,
    "documentary": [
        ("intake", "Intake interview with Spock", "spock", False,
         "Answer the leading questions to define the documentary."),
        ("investigate", "Viability investigation: can we substantiate the claim?", "investigator", True,
         "Test the documentary's central claim against the public record."),
    ] + _PRODUCTION,
    "project": [],
}


def stages_for(kind: str) -> list[str]:
    seen = []
    for stage, *_ in WORKFLOWS.get(kind, []):
        if stage not in seen:
            seen.append(stage)
        if stage == "story" and "write" not in seen:
            seen.append("write")
    if kind == "series":
        seen.append("episodes")
    return seen or ["custom"]


def runtime_target(project: StudioProject) -> tuple[int, int]:
    """(min, max) minutes."""
    d = project.data or {}
    if d.get("runtime_min") and d.get("runtime_max"):
        return int(d["runtime_min"]), int(d["runtime_max"])
    if project.kind == "documentary":
        return 30, 45
    return 12, 15


def populate_tasks(db: Session, project: StudioProject, skip_intake: bool = False):
    """Create the template tasks for a project (idempotent per stage)."""
    existing = {t.stage for t in project.tasks}
    rank = max([t.rank for t in project.tasks] or [0]) + 1
    for stage, title, specialist, auto, instructions in WORKFLOWS.get(project.kind, []):
        if stage in existing:
            continue
        status = "done" if (stage == "intake" and skip_intake) else "todo"
        db.add(StudioTask(project_id=project.id, stage=stage, title=title, specialist=specialist,
                          auto=auto, instructions=instructions, status=status, rank=rank))
        rank += 1
    db.flush()


# ---------------------------------------------------------------------------
# Intake question banks ("leading questions")
# ---------------------------------------------------------------------------

INTAKE_QUESTIONS = {
    "series": [
        "What's the series about? Give me the subject in one line — e.g. \"the corruption of the Trump administration\".",
        "State the core claim as specifically as you can. What exactly do you believe is true — and what evidence would prove you wrong?",
        "Scope it: which people, agencies, companies and which time window are in bounds?",
        "What specific incidents, bills, court cases, contracts or documents do you already know about?",
        "Who is the audience, and what should they understand or do after the series that they don't now?",
        "What's the educational bomb — the one connection that, once people see it, changes how they see everything else?",
        "Tone dial: how hard do we lean into satire vs. straight investigation? Any lines we won't cross?",
        "How many episodes are you picturing, and how often do they drop?",
    ],
    "episode": [
        "What's this episode about? One line.",
        "What's the specific claim this episode proves? Be concrete — names, numbers, dates.",
        "What documents, bills, filings or clips do you already have or know exist?",
        "What's the educational bomb — the reveal the whole episode builds toward?",
        "What are 2–4 smaller stories that could feed into that reveal (the headlines run)?",
        "Tone dial and any lines we won't cross?",
        "Deadline or news peg we're racing?",
    ],
    "documentary": [
        "What's the documentary about? One line.",
        "What's the central question the film answers — and what's your hypothesis?",
        "Who are the key figures, institutions and places?",
        "What's the timeline — where does the story start and end?",
        "What archival material, documents, footage or interviews exist or could be obtained?",
        "What's the educational bomb the film builds toward?",
        "Target runtime (30, 45, 60 min?) and tone?",
    ],
    "project": [
        "What's the project? One line.",
        "What does done look like?",
        "What are the first few tasks you already know about?",
    ],
}


# ---------------------------------------------------------------------------
# Settings defaults
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "brand": {
        "name": "DanDon Media",
        "logo_url": "/static/studio/dandon-logo.svg",
        "host_animation_url": "",
        "accent": "#e63946",
    },
    # The 11 destinations. `onlysocial_account_id` is filled in from
    # Settings → "Load accounts from OnlySocial".
    "platforms": [
        {"key": "youtube", "name": "YouTube", "enabled": True, "onlysocial_account_id": ""},
        {"key": "tiktok", "name": "TikTok", "enabled": True, "onlysocial_account_id": ""},
        {"key": "instagram", "name": "Instagram", "enabled": True, "onlysocial_account_id": ""},
        {"key": "facebook", "name": "Facebook", "enabled": True, "onlysocial_account_id": ""},
        {"key": "x", "name": "X", "enabled": True, "onlysocial_account_id": ""},
        {"key": "threads", "name": "Threads", "enabled": True, "onlysocial_account_id": ""},
        {"key": "bluesky", "name": "Bluesky", "enabled": True, "onlysocial_account_id": ""},
        {"key": "linkedin", "name": "LinkedIn", "enabled": True, "onlysocial_account_id": ""},
        {"key": "reddit", "name": "Reddit", "enabled": True, "onlysocial_account_id": ""},
        {"key": "pinterest", "name": "Pinterest", "enabled": True, "onlysocial_account_id": ""},
        {"key": "mastodon", "name": "Mastodon", "enabled": True, "onlysocial_account_id": ""},
    ],
    # Links bar shown under the main visual.
    "links_bar": [
        {"label": "YouTube", "url": ""},
        {"label": "TikTok", "url": ""},
        {"label": "Instagram", "url": ""},
        {"label": "X", "url": ""},
        {"label": "Bluesky", "url": ""},
    ],
    "public_base_url": "",
    "voice": {"voice": "default", "model": "omnivoice", "speed": 1.0, "format": "mp3"},
}


def get_setting(db: Session, key: str):
    row = db.get(StudioSetting, key)
    if row is None:
        return DEFAULT_SETTINGS.get(key)
    return row.value


def set_setting(db: Session, key: str, value):
    row = db.get(StudioSetting, key)
    if row is None:
        db.add(StudioSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def seed(db: Session) -> int:
    """Insert any missing specialists. Never overwrites edits."""
    added = 0
    for i, spec in enumerate(SPECIALISTS):
        if db.get(StudioSpecialist, spec["key"]) is None:
            db.add(StudioSpecialist(model=DEFAULT_MODEL, rank=i, **spec))
            added += 1
    db.commit()
    return added
