"""Reading one PDF: the text of every page, its headings, the PDF's own outline, and OCR for pages without a text layer.

Two libraries, both free for this use: **pypdfium2** (Apache-2.0 / BSD-3, Google's PDFium) gives the text in reading order (designed
reports put two or three columns side by side, and PDFium follows the order they were written in), renders pages for OCR and reads the
outline. **pdfplumber** (MIT, on pdfminer.six, MIT) gives each word with its position and font size, which the headings, tables
(tables.py) and people (people.py) need. OCR is **Tesseract** (Apache-2.0), run as a program if it's installed. Without it, empty pages
are marked `needs_ocr`, never filled with a guess."""

import io
import logging
import re
import shutil
import statistics
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("annualreports")

READER_VERSION = 1          # raise when what's extracted changes: documents read by an older version are read again on the next run

BOLD = re.compile(r"bold|heavy|black|semibold|demi|-hv|-bd|extrabold", re.I)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


@dataclass
class Line:
    """One line of words at the same height, left to right. Two columns side by side end up in one line: tables.py splits them by gaps."""
    words: list[dict]
    top: float
    bottom: float

    @property
    def text(self) -> str:
        return " ".join(w["text"] for w in self.words)

    @property
    def x0(self) -> float:
        return self.words[0]["x0"]

    @property
    def x1(self) -> float:
        return self.words[-1]["x1"]


@dataclass
class Page:
    page_no: int                    # 1 = the PDF's first page (not the number printed on it)
    width: float
    height: float
    text: str                       # the PDF's own text layer, in reading order, as it is
    text_source: str                # pdf, ocr, empty (nothing on it to read), needs_ocr (no OCR tool), ocr_failed
    ocr_text: str | None = None
    ocr_confidence: float | None = None
    note: str | None = None
    image_count: int = 0
    headings: list[dict] = field(default_factory=list)     # {"text", "size", "top"}
    lines: list[Line] = field(default_factory=list)        # not saved: what tables.py and people.py work from
    body_size: float | None = None
    tables: list[dict] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text.strip())

    @property
    def word_count(self) -> int:
        return len((self.text or "").split()) + len((self.ocr_text or "").split())

    @property
    def full_text(self) -> str:
        return "\n".join(t for t in (self.text.strip(), (self.ocr_text or "").strip()) if t)


def clean(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return CONTROL.sub("", text)


def build_lines(words: list[dict], tolerance: float = 3.0) -> list[Line]:
    """Words whose tops are within `tolerance` points share a line. Sorted top to bottom, then left to right."""
    lines: list[Line] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(w["top"] - lines[-1].top) <= tolerance:
            lines[-1].words.append(w)
            lines[-1].bottom = max(lines[-1].bottom, w["bottom"])
        else:
            lines.append(Line([w], w["top"], w["bottom"]))
    for line in lines:
        line.words.sort(key=lambda w: w["x0"])
    return lines


def body_size(words: list[dict]) -> float | None:
    """The font size most of the page's letters are in: the size headings are compared with."""
    sizes = Counter()
    for w in words:
        sizes[round(w.get("size", 0) * 2) / 2] += len(w["text"])
    sizes.pop(0, None)
    return sizes.most_common(1)[0][0] if sizes else None


def segments(line: Line, gap: float = 18.0) -> list[list[dict]]:
    """A line cut where the space between two words is wider than `gap`: columns, or a label and its numbers."""
    out: list[list[dict]] = [[line.words[0]]]
    for prev, w in zip(line.words, line.words[1:]):
        if w["x0"] - prev["x1"] > gap:
            out.append([])
        out[-1].append(w)
    return out


def _looks_like_heading(text: str) -> bool:
    letters = sum(c.isalpha() for c in text)
    visible = sum(not c.isspace() for c in text)
    return letters >= 3 and visible and letters / visible > 0.6 and len(text.split()) <= 15 and len(text) <= 140


def find_headings(lines: list[Line], body: float | None) -> list[dict]:
    """Text in a clearly bigger font than the page's body text. Lines of the same heading (a title on two lines) are joined."""
    if not body:
        return []
    found: list[dict] = []
    for line in lines:
        for seg in segments(line):
            size = statistics.median(w.get("size", 0) for w in seg)
            big = size >= body * 1.3 and size >= body + 2
            bold = size >= body + 0.9 and all(BOLD.search(w.get("fontname", "")) for w in seg)
            if not (big or bold):
                continue
            text = " ".join(w["text"] for w in seg)
            x0, x1 = seg[0]["x0"], seg[-1]["x1"]
            last = found[-1] if found else None
            if (last and abs(last["size"] - size) < 0.6 and 0 <= line.top - last["bottom"] < size * 0.9
                    and x0 < last["x1"] and x1 > last["x0"]):
                last.update(text=last["text"] + " " + text, bottom=line.bottom, x0=min(x0, last["x0"]), x1=max(x1, last["x1"]))
            else:
                found.append({"text": text, "size": round(size, 1), "top": round(line.top, 1), "bottom": line.bottom, "x0": x0, "x1": x1})
    return [{"text": h["text"], "size": h["size"], "top": h["top"]} for h in found if _looks_like_heading(h["text"])]


# --- OCR -----------------------------------------------------------------------------------------------------

def tesseract_path() -> str | None:
    return shutil.which("tesseract") or next((p for p in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "/usr/bin/tesseract")
                                              if Path(p).exists()), None)


def ocr_image(png: bytes, tesseract: str, lang: str, timeout: int) -> tuple[str, float | None]:
    """Tesseract's words, back in lines, with its average confidence (0-100). Raises on failure."""
    res = subprocess.run([tesseract, "stdin", "stdout", "-l", lang, "--psm", "3", "tsv"], input=png, capture_output=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode(errors="replace").strip()[:300] or f"tesseract exit {res.returncode}")
    lines: dict[tuple, list[str]] = {}
    confs: list[float] = []
    for row in res.stdout.decode(errors="replace").splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or not cols[11].strip():
            continue
        lines.setdefault((int(cols[2]), int(cols[3]), int(cols[4])), []).append(cols[11])
        try:
            if float(cols[10]) >= 0:
                confs.append(float(cols[10]))
        except ValueError:
            pass
    text = "\n".join(" ".join(words) for _, words in sorted(lines.items()))
    return clean(text), (round(sum(confs) / len(confs), 1) if confs else None)


def _ocr_page(pdfium_page, page: Page, ocr: dict) -> None:
    tess = tesseract_path() if ocr.get("enabled", True) else None
    if not tess:
        page.text_source = "needs_ocr"
        page.note = "no text layer, and OCR is off or Tesseract isn't installed (brew install tesseract)"
        return
    try:
        image = pdfium_page.render(scale=ocr.get("dpi", 300) / 72).to_pil()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        text, conf = ocr_image(buf.getvalue(), tess, ocr.get("lang", "eng"), ocr.get("timeout_seconds", 120))
    except Exception as e:                  # one bad page shouldn't stop the document
        page.text_source, page.note = "ocr_failed", f"OCR failed: {str(e)[:200]}"
        return
    if len(text.split()) < ocr.get("min_words", 3):
        page.text_source = "empty"
        page.note = "no text layer; OCR found no words (a picture, a chart or a blank page)"
        page.ocr_text, page.ocr_confidence = (text or None), conf
        return
    page.text_source, page.ocr_text, page.ocr_confidence = "ocr", text, conf
    page.note = f"no text layer: read by OCR ({ocr.get('lang', 'eng')}), confidence {conf}" if conf is not None else "read by OCR"


# --- the document --------------------------------------------------------------------------------------------

def outline(pdf) -> list[dict]:
    """The PDF's own bookmarks, if it has any: the publisher's sections, better than guessing from font sizes."""
    out = []
    try:
        for item in pdf.get_toc(max_depth=6):
            title = clean(item.get_title() if hasattr(item, "get_title") else getattr(item, "title", "")).strip()
            page_index = item.page_index if hasattr(item, "page_index") else None
            if title and page_index is not None:
                out.append({"title": title, "level": item.level + 1, "page_no": page_index + 1})
    except Exception as e:                  # a broken outline: fall back to headings
        logger.debug("outline: %s", e)
    return out


def metadata(pdf) -> dict:
    try:
        return {k: clean(v) for k, v in pdf.get_metadata_dict().items() if v}
    except Exception:
        return {}


def ocr_pages(path: Path, pages: list[Page], settings: dict, own_text) -> None:
    """OCR for the pages whose own text (`own_text(page)`: without the running header, which a scanned page often still has) is
    shorter than `ocr.min_chars`. Done after reading, once the running headers are known."""
    import pypdfium2 as pdfium
    ocr = settings.get("ocr", {})
    todo = [p for p in pages if len(own_text(p).strip()) < ocr.get("min_chars", 30)]
    if not todo:
        return
    pdf = pdfium.PdfDocument(str(path))
    try:
        for page in todo:
            fpage = pdf[page.page_no - 1]
            try:
                _ocr_page(fpage, page, ocr)
            finally:
                fpage.close()
    finally:
        pdf.close()


def read_pages(path: Path, settings: dict, on_page=None):
    """Yields each Page (with its lines and tables) of the PDF at `path`. OCR comes after: ocr_pages."""
    import pdfplumber
    import pypdfium2 as pdfium

    from .tables import find_tables

    pdf = pdfium.PdfDocument(str(path))
    try:
        with pdfplumber.open(str(path)) as plumber:
            for i, ppage in enumerate(plumber.pages):
                fpage = pdf[i]
                try:
                    text = clean(fpage.get_textpage().get_text_range())
                    page = Page(page_no=i + 1, width=round(float(ppage.width), 1), height=round(float(ppage.height), 1),
                                text=text, text_source="pdf")
                    try:
                        words = ppage.extract_words(extra_attrs=["size", "fontname", "upright"], x_tolerance=1.5, keep_blank_chars=False)
                        page.image_count = len(ppage.images)
                    except Exception as e:      # pdfminer can't parse every page; the text layer above is still there
                        words = []
                        page.note = f"word positions unreadable ({str(e)[:120]}): no headings or tables for this page"
                    for w in words:
                        w["text"] = clean(w["text"])
                    page.lines = build_lines([w for w in words if w["text"].strip()])
                    page.body_size = body_size(words)
                    page.headings = find_headings(page.lines, page.body_size)
                    sideways = sum(len(w["text"]) for w in words if not w.get("upright", True))
                    if words and sideways > 0.5 * sum(len(w["text"]) for w in words):
                        page.note = "printed sideways: its tables aren't split into rows and cells (the page text above is complete)"
                    elif page.lines:
                        page.tables = find_tables(page.lines, ppage, settings.get("tables", {}))
                    if on_page:
                        on_page(page)
                    yield page
                finally:
                    ppage.close()
                    fpage.close()
    finally:
        pdf.close()
