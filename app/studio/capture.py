"""Frame capture for fact visuals: turn a real source document into the image
we put on screen.

PDF sources (bills, court filings, IG/GAO reports) are downloaded, the page
holding the quoted passage is found, the passage is highlighted in yellow and
a 16:9 frame around it is saved. That frame is the real document — nothing is
generated. Pages that aren't PDFs are left for a human screenshot.
"""
import io
import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

FRAME_W, FRAME_H = 1440, 810  # the 3/4 main area of the layout
MAX_PDF_BYTES = 60 * 1024 * 1024


class CaptureError(Exception):
    pass


def fetch_pdf(url: str, cache: Path) -> bytes:
    """Download a PDF (cached next to the project's media)."""
    if cache.exists():
        return cache.read_bytes()
    try:
        resp = httpx.get(url, timeout=90, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (DanDon Media Studio; source capture)"})
    except httpx.HTTPError as e:
        raise CaptureError(f"Could not download the source: {e}") from e
    data = resp.content
    if resp.status_code >= 400:
        raise CaptureError(f"Source returned HTTP {resp.status_code}.")
    if not data.startswith(b"%PDF"):
        raise CaptureError("Source isn't a PDF — capture this one as a screenshot.")
    if len(data) > MAX_PDF_BYTES:
        raise CaptureError("PDF is too large to capture automatically.")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(data)
    return data


def _phrases(highlight: str) -> list[str]:
    """Search candidates, longest first: PDFs break lines mid-quote, so fall back to shorter runs."""
    text = re.sub(r"\s+", " ", (highlight or "").strip().strip('"“”'))
    if not text:
        return []
    words = text.split(" ")
    out = [text[:160]]
    for n in (10, 7, 5):
        if len(words) > n:
            out.append(" ".join(words[:n]))
    return list(dict.fromkeys(out))


def _find(pdf, phrases: list[str], page_hint: int | None):
    """Return (page_index, [rects in PDF points]) for the first phrase found."""
    order = list(range(len(pdf)))
    if page_hint is not None and 0 <= page_hint < len(pdf):
        order.remove(page_hint)
        order.insert(0, page_hint)
    for phrase in phrases:
        for i in order:
            textpage = pdf[i].get_textpage()
            searcher = textpage.search(phrase, match_case=False, match_whole_word=False)
            hit = searcher.get_next()
            if hit:
                start, count = hit
                rects = [textpage.get_rect(r) for r in range(textpage.count_rects(start, count))]
                return i, rects
    return None, []


def capture_pdf(data: bytes, highlight: str, out: Path, page_hint: int | None = None) -> dict:
    """Render the page containing `highlight`, mark it, crop a 16:9 frame. Returns a report."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw

    pdf = pdfium.PdfDocument(data)
    try:
        page_index, rects = _find(pdf, _phrases(highlight), page_hint)
        found = page_index is not None
        if not found:
            page_index = page_hint if page_hint is not None and 0 <= page_hint < len(pdf) else 0
        page = pdf[page_index]
        pw, ph = page.get_size()
        scale = max(2.0, FRAME_W / pw)
        img = page.render(scale=scale).to_pil().convert("RGBA")
    finally:
        pdf.close()

    boxes = []
    for left, bottom, right, top in rects:
        boxes.append((left * scale, (ph - top) * scale, right * scale, (ph - bottom) * scale))
    if boxes:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for x0, y0, x1, y1 in boxes:
            draw.rectangle((x0 - 3, y0 - 2, x1 + 3, y1 + 2), fill=(255, 224, 0, 110))
        img = Image.alpha_composite(img, overlay)

    # 16:9 window, full page width, centred on the passage (or the top of the page)
    w, h = img.size
    win_h = min(h, int(w * FRAME_H / FRAME_W))
    if boxes:
        cy = (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2
        top = int(min(max(cy - win_h / 2, 0), h - win_h))
    else:
        top = 0
    frame = img.crop((0, top, w, top + win_h)).convert("RGB")
    canvas = Image.new("RGB", (FRAME_W, FRAME_H), (20, 20, 26))
    frame.thumbnail((FRAME_W, FRAME_H), Image.LANCZOS)
    canvas.paste(frame, ((FRAME_W - frame.width) // 2, (FRAME_H - frame.height) // 2))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, "PNG")
    return {"page": page_index + 1, "highlighted": bool(boxes), "passage_found": found}


def looks_like_pdf(url: str, kind: str = "") -> bool:
    u = (url or "").lower().split("?")[0]
    return u.endswith(".pdf") or kind in ("bill", "court_filing", "gov_report")


def page_hint_from(value) -> int | None:
    m = re.search(r"\d+", str(value or ""))
    return int(m.group()) - 1 if m else None
