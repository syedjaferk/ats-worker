"""Read the text a person would actually see in the PDF.

Hidden text is the usual way to game an ATS or steer an AI reviewer: keywords or instructions in white on
white, in 1pt type, placed off the page, transparent, or in invisible render mode. Those spans are left out
of what the model reads, and reported so the review gets checked by a person.
"""
import re
from dataclasses import dataclass, field

import pymupdf

from .guards import INVISIBLE_RE

MIN_FONT_SIZE = 3.0       # points; smaller than this nobody can read it
MIN_CONTRAST = 1.25       # WCAG contrast ratio between text and what's behind it (1 = identical colours)
OCR_BELOW_CHARS = 200     # try OCR when a PDF has less text than this (scanned resumes)


@dataclass
class Extraction:
    text: str = ""
    pages: int = 0
    encrypted: bool = False
    used_ocr: bool = False
    hidden: list = field(default_factory=list)     # [(reason, text)]


def _luminance(rgb):
    def channel(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _int_to_rgb(value):
    return ((value >> 16) & 255) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255


def _overlap(a, b):
    """Share of rect a covered by rect b."""
    inter = pymupdf.Rect(a) & pymupdf.Rect(b)
    area = pymupdf.Rect(a).get_area()
    return inter.get_area() / area if area else 0.0


class _Page:
    def __init__(self, page):
        self.rect = page.rect
        # Filled shapes in paint order, so the last one under a point is what you see behind the text.
        self.fills = [(d["rect"], d["fill"]) for d in page.get_drawings() if d.get("fill") and d.get("rect")]
        self.images = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
        # Invisible (render mode 3) or fully transparent text, from the low-level trace.
        self.invisible = [t["bbox"] for t in page.get_texttrace()
                          if t.get("type") == 3 or t.get("opacity", 1) < 0.1]

    def background(self, point):
        colour = (1.0, 1.0, 1.0)
        for rect, fill in self.fills:
            if point in pymupdf.Rect(rect):
                colour = tuple(fill) * 3 if len(fill) == 1 else tuple(fill)[:3]   # grey or RGB
        return colour

    def hidden_reason(self, span):
        bbox = pymupdf.Rect(span["bbox"])
        if not bbox.intersects(self.rect):
            return "outside the page"
        if span["size"] < MIN_FONT_SIZE:
            return f"tiny text ({span['size']:.1f}pt)"
        if any(_overlap(bbox, r) > 0.5 for r in self.invisible):
            return "invisible text"
        if span.get("alpha", 255) < 26:
            return "transparent text"
        centre = pymupdf.Point((bbox.x0 + bbox.x1) / 2, (bbox.y0 + bbox.y1) / 2)
        if any(centre in r for r in self.images):
            return None      # text over a picture: we can't tell what's behind it, so trust it
        if _contrast(_int_to_rgb(span["color"]), self.background(centre)) < MIN_CONTRAST:
            return "same colour as the background"
        return None


def extract(data, max_pages=6, ocr=False):
    out = Extraction()
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError):
        return out
    with doc:
        out.pages = doc.page_count
        if doc.needs_pass:
            out.encrypted = True
            return out
        lines = []
        for page in list(doc)[:max_pages]:
            info = _Page(page)
            for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]:
                for line in block.get("lines", []):
                    visible = []
                    for span in line["spans"]:
                        if not INVISIBLE_RE.sub("", span["text"]).strip():
                            visible.append(span["text"])
                            continue
                        reason = info.hidden_reason(span)
                        if reason:
                            # Dropped either way; only worth reporting if it hides actual words
                            # (Google Docs leaves stray 2pt zero-width spaces and dots around).
                            if len(re.findall(r"\w", span["text"])) >= 3:
                                out.hidden.append((reason, span["text"].strip()))
                        else:
                            visible.append(span["text"])
                    text = "".join(visible).strip()
                    if text:
                        lines.append(text)
                lines.append("")
        out.text = "\n".join(lines).strip()

        if ocr and len(out.text) < OCR_BELOW_CHARS:
            texts = []
            for page in list(doc)[:max_pages]:
                try:
                    tp = page.get_textpage_ocr(language="eng", dpi=300, full=True)
                except RuntimeError:     # tesseract not installed / no language data
                    return out
                texts.append(page.get_text("text", textpage=tp))
            out.text, out.used_ocr = "\n".join(texts).strip(), True
    return out
