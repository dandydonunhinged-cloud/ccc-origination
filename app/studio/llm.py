"""LLM layer for the studio specialists.

Provider order (STUDIO_LLM_PROVIDER overrides):
  anthropic — Claude via the official SDK. Research roles get Anthropic's
              server-side web_search / web_fetch tools so their sources are
              real, retrievable URLs.
  offline   — no model configured. Returns clearly-labelled placeholder
              output so the pipeline can be clicked through end-to-end. It
              never invents facts: every placeholder says "[needs research]".
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Models that support server-side refusal fallbacks ("default" routing).
_FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}
# Models that take the _20260209 web tools (dynamic filtering).
_NEW_WEB_TOOL_MODELS = ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                        "claude-sonnet-5", "claude-sonnet-4-6", "claude-fable-5")


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str
    citations: list = field(default_factory=list)  # [{"url", "title"}] from web search/fetch


def provider() -> str:
    forced = os.environ.get("STUDIO_LLM_PROVIDER", "").strip().lower()
    if forced:
        return forced
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    return "offline"


def _web_tools(model: str, tools: list[str]) -> list[dict]:
    new = model.startswith(_NEW_WEB_TOOL_MODELS)
    out = []
    if "web_search" in tools:
        out.append({"type": "web_search_20260209" if new else "web_search_20250305",
                    "name": "web_search", "max_uses": 12})
    if "web_fetch" in tools:
        out.append({"type": "web_fetch_20260209" if new else "web_fetch_20250910",
                    "name": "web_fetch", "max_uses": 12})
    return out


def _collect_citations(content) -> list[dict]:
    found = {}
    for block in content:
        btype = getattr(block, "type", "")
        if btype == "web_search_tool_result":
            results = getattr(block, "content", None)
            if isinstance(results, list):  # a list on success, an error object otherwise
                for r in results:
                    url = getattr(r, "url", None)
                    if url:
                        found.setdefault(url, {"url": url, "title": getattr(r, "title", "") or ""})
        elif btype == "web_fetch_tool_result":
            res = getattr(block, "content", None)
            url = getattr(res, "url", None)
            if url:
                doc = getattr(res, "content", None)
                found.setdefault(url, {"url": url, "title": getattr(doc, "title", "") or ""})
        elif btype == "text":
            for c in getattr(block, "citations", None) or []:
                url = getattr(c, "url", None)
                if url:
                    found.setdefault(url, {"url": url, "title": getattr(c, "title", "") or ""})
    return list(found.values())


def complete(system: str, messages: list[dict], model: str = "", tools: list[str] | None = None,
             effort: str = "high", max_tokens: int = 32000) -> LLMResult:
    """One specialist turn. `messages` are plain {"role", "content": str} dicts."""
    prov = provider()
    model = model or "claude-opus-5"
    if prov == "anthropic":
        return _anthropic(system, messages, model, tools or [], effort, max_tokens)
    if prov == "offline":
        return LLMResult(text=_offline(system, messages), model="offline", provider="offline")
    raise LLMError(f"Unknown STUDIO_LLM_PROVIDER '{prov}'")


def _anthropic(system, messages, model, tools, effort, max_tokens) -> LLMResult:
    import anthropic

    client = anthropic.Anthropic()
    kwargs = dict(model=model, max_tokens=max_tokens, system=system)
    if not model.startswith("claude-haiku"):  # Haiku 4.5 takes neither adaptive thinking nor effort
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": effort or "high"}
    server_tools = _web_tools(model, tools)
    if server_tools:
        kwargs["tools"] = server_tools
    betas = []
    if model in _FALLBACK_MODELS:
        betas.append("server-side-fallback-2026-07-01")
        kwargs["fallbacks"] = "default"
    if betas:
        kwargs["betas"] = betas

    convo = [dict(m) for m in messages]
    citations: list[dict] = []
    text_parts: list[str] = []
    served_by = model
    # Server tools can pause a long turn; resume by sending the partial turn back.
    for _ in range(6):
        try:
            with client.beta.messages.stream(messages=convo, **kwargs) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMError("Anthropic credentials were rejected — check ANTHROPIC_API_KEY.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Rate limited by the Anthropic API — try again shortly.") from e
        except anthropic.BadRequestError as e:
            raise LLMError(f"Anthropic API rejected the request: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Anthropic API.") from e

        served_by = getattr(msg, "model", model) or model
        citations += _collect_citations(msg.content)
        text_parts += [b.text for b in msg.content if getattr(b, "type", "") == "text"]

        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            why = getattr(details, "explanation", "") if details else ""
            raise LLMError(f"The model declined this request. {why}".strip())
        if msg.stop_reason == "pause_turn":
            convo.append({"role": "assistant", "content": msg.content})
            continue
        break

    uniq = {c["url"]: c for c in citations}
    return LLMResult(text="".join(text_parts).strip(), model=served_by, provider="anthropic",
                     citations=list(uniq.values()))


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str):
    """Pull the last JSON object/array out of a model reply. Returns None if none parses."""
    candidates = _FENCE.findall(text or "")[::-1]
    candidates.append(text or "")
    for cand in candidates:
        cand = cand.strip()
        try:
            return json.loads(cand)
        except Exception:
            pass
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = cand.find(opener), cand.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(cand[start:end + 1])
                except Exception:
                    continue
    return None


# ---------------------------------------------------------------------------
# Offline placeholders
# ---------------------------------------------------------------------------

OFFLINE_NOTE = ("[OFFLINE DRAFT — no AI model is configured (set ANTHROPIC_API_KEY). "
                "This is structure only; nothing here is researched.]")


def _offline(system: str, messages: list[dict]) -> str:
    """Return schema-shaped placeholders keyed off the output contract in the prompt."""
    prompt = messages[-1]["content"] if messages else ""
    if isinstance(prompt, list):
        prompt = " ".join(str(p) for p in prompt)
    m = re.search(r"OUTPUT_CONTRACT:(\w+)", prompt)
    contract = m.group(1) if m else "text"
    title = re.search(r"TITLE:(.*)", prompt)
    title = title.group(1).strip() if title else "this project"

    if contract == "investigate":
        return json.dumps({
            "verdict": "needs_research", "confidence": 0,
            "summary": f"{OFFLINE_NOTE} Viability of '{title}' has not been investigated.",
            "evidence": [], "counter_evidence": [],
            "topics": [{"title": f"Topic {i} [needs research]", "angle": "", "why_it_matters": "",
                        "key_documents": [], "strength": "unknown"} for i in (1, 2, 3)],
            "gaps": ["Configure a model to run the investigation."],
        })
    if contract == "research":
        return json.dumps({"summary": OFFLINE_NOTE, "sources": [], "timeline": [],
                           "key_facts": [], "open_questions": ["Run with a model configured."]})
    if contract == "story":
        segs = [("Cold open", "cold_open", 0.75), ("Headlines", "headlines", 2.5),
                ("Deep dive", "deep_dive", 5), ("Senior correspondent", "correspondent", 2.5),
                ("Convergence", "convergence", 1.5), ("Button", "button", 0.25)]
        return json.dumps({
            "logline": f"{OFFLINE_NOTE}", "thesis": "[needs research]", "bomb": "[needs research]",
            "segments": [{"title": t, "format": f, "minutes": mnt, "angle": "[needs research]",
                          "beats": ["[needs research]"], "source_urls": []} for t, f, mnt in segs],
        })
    if contract == "standards":
        return json.dumps({"verdict": "not_checked", "issues": [], "summary": OFFLINE_NOTE})
    if contract == "visuals":
        return json.dumps({"shots": []})
    if contract == "animation":
        return json.dumps({"pieces": [{"placement": "open", "duration_sec": 4,
                                       "concept": "DanDon Media ident [placeholder]", "prompt": "",
                                       "on_screen_text": ""}]})
    if contract == "music":
        return json.dumps({"cues": [{"placement": "full episode", "mood": "[choose]", "bpm": 0,
                                     "source": "", "level_db": -22}]})
    if contract == "social":
        return json.dumps({})
    if contract == "question":
        return ""
    if contract == "segment":
        return (f"{OFFLINE_NOTE}\n\n[HOST, to camera]\n[needs research — this segment has not been written]\n")
    if contract == "edit":
        return f"{OFFLINE_NOTE}\n\n[needs research — episode not assembled]"
    return f"{OFFLINE_NOTE}\n\nSpock is offline. Configure ANTHROPIC_API_KEY to talk it through."
