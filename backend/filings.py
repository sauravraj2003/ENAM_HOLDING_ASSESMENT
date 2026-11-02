"""Download a filing PDF (BSE attachment, transcript, deck, annual report) and
return its text page by page. Text is cached forever; the PDF is not kept."""

import io
import logging
import re

import net
import store

log = logging.getLogger(__name__)


@store.cached("pdftext")
def pages(url: str, max_pages: int | None = None):
    from pypdf import PdfReader

    raw = net.get(url, timeout=90).content
    if not raw.startswith(b"%PDF"):
        # Some BSE links render an HTML viewer; nothing to read.
        return []
    reader = PdfReader(io.BytesIO(raw))
    out = []
    for i, p in enumerate(reader.pages):
        if max_pages and i >= max_pages:
            break
        try:
            t = p.extract_text() or ""
        except Exception:
            t = ""
        out.append(re.sub(r"[ \t]+", " ", t))
    return out


def text(url: str, max_pages: int | None = None) -> str:
    try:
        return "\n\n".join(f"[p{i + 1}] {t}" for i, t in enumerate(pages(url, max_pages)))
    except Exception as e:
        log.warning("pdf fetch failed %s: %s", url, e)
        return ""


def pick_pages(url: str, must: list[str], limit: int = 3, max_pages: int | None = 40) -> str:
    """Return only the pages that mention the most of `must` keywords - the
    cheapest possible chunking for a structured filing."""
    try:
        pgs = pages(url, max_pages)
    except Exception as e:
        log.warning("pdf fetch failed %s: %s", url, e)
        return ""
    scored = []
    for i, t in enumerate(pgs):
        low = t.lower()
        s = sum(1 for k in must if k in low)
        if s:
            scored.append((s, i))
    scored.sort(key=lambda x: (-x[0], x[1]))
    keep = sorted(i for _, i in scored[:limit])
    return "\n\n".join(f"[page {i + 1}]\n{pgs[i]}" for i in keep)
