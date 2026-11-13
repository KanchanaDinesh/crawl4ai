"""Tables on a page, as rows and cells.

Annual reports print most tables without lines: a label on the left and numbers in columns on the right (the financial statements,
their notes, five-year summaries, shareholder lists). pdfplumber's own table finder needs ruling lines, and on those pages it splits and
merges cells (tested on BPPL's statement of financial position). So there are two methods, and a table found by both is kept once:

- `aligned` (ours): rows that end in at least two numbers, close together, with their numbers in the same columns. The lines just above
  that sit over the columns are the column headings ("Group 2025 Rs. '000"). Cells are kept exactly as printed ("(366,495)", "-").
- `ruled`: pdfplumber's finder, for tables drawn with lines (directors' attendance, shareholder lists), when the aligned method didn't
  already take that part of the page.

The page's full text is saved anyway, so a table missed here is still in the database, just not in rows and cells."""

import re
import statistics

from .reading import Line

NUMBER = re.compile(r"""^[\(\[]?[-–−]?(?:Rs\.?|LKR|US\$|\$|£|€)?\d{1,3}(?:,\d{3})+(?:\.\d+)?[\)\]]?%?\*?$     # 1,234,567 (1,234.5) $1,234
                      |^[\(\[]?[-–−]?(?:Rs\.?|US\$|\$|£|€)?\d+(?:\.\d+)?[\)\]]?%?\*?$                      # 2025 12.5 (3) 4.2%
                      |^[-–—]$                                                                              # a dash: nothing / zero
                      |^\(?[-–−]?\.\d+\)?%?$                                                                # .45
                      """, re.X)
UNITS = [
    (re.compile(r"(?:Rs|LKR)\s*\.?\s*['’‘`]\s*0{3}|(?:Rs|LKR)\.?\s*(?:in\s+)?thousands?|['’‘]000\s*(?:Rs|LKR)", re.I), "LKR", 1e3),
    (re.compile(r"rupees\s+(?:in\s+)?thousands|thousands\s+of\s+(?:sri\s+lanka(?:n)?\s+)?rupees|(?:LKR|Rs\.?)\s+thousands", re.I), "LKR", 1e3),
    (re.compile(r"rupees\s+(?:in\s+)?millions?|millions?\s+of\s+(?:sri\s+lanka(?:n)?\s+)?rupees", re.I), "LKR", 1e6),
    (re.compile(r"(?:Rs|LKR)\.?\s*(?:Mn|Million|Mil)\b", re.I), "LKR", 1e6),
    (re.compile(r"(?:Rs|LKR)\.?\s*(?:Bn|Billion)\b", re.I), "LKR", 1e9),
    (re.compile(r"(?:US\$|USD|\$)\s*(?:m|mn|million)\b", re.I), "USD", 1e6),
    (re.compile(r"(?:US\$|USD|\$)\s*(?:bn|billion)\b", re.I), "USD", 1e9),
    (re.compile(r"(?:US\$|USD|\$)\s*['’]?000\b", re.I), "USD", 1e3),
    (re.compile(r"£\s*(?:m|mn|million)\b", re.I), "GBP", 1e6),
    (re.compile(r"£\s*['’]?000\b", re.I), "GBP", 1e3),
    (re.compile(r"(?<![A-Za-z])(?:Rs\.?|LKR)(?![A-Za-z])"), "LKR", 1.0),
]
DASHES = {"-", "–", "—"}


def is_number(text: str) -> bool:
    return bool(NUMBER.match(text.strip()))


def parse_number(text: str | None) -> float | None:
    """'(1,234.5)' -> -1234.5, '4.2%' -> 4.2, '-' -> None (a dash means nothing here, not a known zero)."""
    if not text:
        return None
    s = text.strip().rstrip("*")
    if s in DASHES:
        return None
    neg = (s.startswith("(") and s.endswith(")")) or s[:1] in "-–−" and len(s) > 1
    s = re.sub(r"[()\[\]%,\s]|Rs\.?|LKR|US\$|\$|£|€", "", s).lstrip("-–−")
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if neg else value


def find_unit(texts: list[str]) -> tuple[str | None, str | None, float | None]:
    """The first amount unit in these texts: ("Rs. '000", "LKR", 1000)."""
    for text in texts:
        for rx, currency, mult in UNITS:
            m = rx.search(text or "")
            if m:
                return m.group(0).strip(), currency, mult
    return None, None, None


# --- aligned tables -------------------------------------------------------------------------------------------

def _trailing_numbers(words: list[dict]) -> int:
    """How many words at the end of the line are numbers. A number in a clearly smaller font, right against the word before it, is a footnote mark ("basis⁴"), not a cell."""
    size = statistics.median(w.get("size", 0) or 0 for w in words)
    n = 0
    for i in range(len(words) - 1, -1, -1):
        w = words[i]
        small = size and (w.get("size") or size) < size * 0.8
        attached = i > 0 and w["x0"] - words[i - 1]["x1"] < 3            # right against the word before it
        if not is_number(w["text"]) or (small and attached):
            break
        n += 1
    return n


def _numeric_line(line: Line, label_gap: float = 12.0) -> dict | None:
    """A line ending in at least two numbers: where its label starts, and its number cells. Words far to the left of the label
    (another column of the page) don't belong to it."""
    words = line.words
    n = _trailing_numbers(words)
    if n < 2:
        return None
    cells = words[len(words) - n:]
    rest = words[:len(words) - n]
    label: list[dict] = []
    for w in reversed(rest):
        if label and label[0]["x0"] - w["x1"] > label_gap:
            break
        if not label and cells[0]["x0"] - w["x1"] > 400:         # nothing to the left close enough to be this row's label
            break
        label.insert(0, w)
    left = label[0]["x0"] if label else cells[0]["x0"]
    return {"line": line, "left": left, "cells": cells, "right": cells[-1]["x1"]}


def _line_height(lines: list[Line]) -> float:
    heights = [ln.bottom - ln.top for ln in lines if ln.bottom > ln.top]
    return statistics.median(heights) if heights else 10.0


def _blocks(lines: list[Line]) -> list[list[dict]]:
    """Numeric lines close below each other, ending at about the same place on the right."""
    lh = _line_height(lines)
    blocks: list[list[dict]] = []
    for line in lines:
        nl = _numeric_line(line)
        if not nl:
            continue
        if blocks:
            last = blocks[-1][-1]
            right = statistics.median(r["right"] for r in blocks[-1])
            if nl["line"].top - last["line"].bottom <= lh * 6 and abs(nl["right"] - right) <= 30:
                blocks[-1].append(nl)
                continue
        blocks.append([nl])
    return [b for b in blocks if len(b) >= 3]


def _columns(cells: list[dict]) -> list[list[float]]:
    """Columns from the cells' spans: cells in one column overlap, whether right-aligned, centred or left-aligned."""
    spans = sorted([c["x0"], c["x1"]] for c in cells)
    cols: list[list[float]] = []
    for x0, x1 in spans:
        if cols and x0 < cols[-1][1] - 0.5:
            cols[-1][1] = max(cols[-1][1], x1)
        else:
            cols.append([x0, x1])
    return cols


def _column_of(word: dict, cols: list[list[float]]) -> list[int]:
    """Which columns a word sits over: those it overlaps; if none, the two it sits between (a heading centred over both,
    "Group" over 2025 and 2024), or the nearest."""
    hit = [i for i, (a, b) in enumerate(cols) if word["x0"] < b and word["x1"] > a]
    if hit:
        return hit
    centre = (word["x0"] + word["x1"]) / 2
    for i in range(len(cols) - 1):
        if cols[i][1] <= word["x0"] and word["x1"] <= cols[i + 1][0]:
            gap_mid = (cols[i][1] + cols[i + 1][0]) / 2
            if abs(centre - gap_mid) < (cols[i + 1][0] - cols[i][1]) * 0.3:
                return [i, i + 1]
            return [i] if centre < gap_mid else [i + 1]
    dist = [min(abs(centre - a), abs(centre - b)) for a, b in cols]
    return [dist.index(min(dist))]


def _row_cells(words: list[dict], cols: list[list[float]]) -> list[str]:
    label = [w for w in words if w["x1"] <= cols[0][0] + 0.5]
    cells = [[] for _ in cols]
    for w in words:
        if w in label:
            continue
        cells[_column_of(w, cols)[0]].append(w["text"])
    return [" ".join(w["text"] for w in label)] + [" ".join(c) for c in cells]


def _lines_above(lines: list[Line], first_top: float, left: float, right: float, boundary: float, reach: float):
    """Walking up from the first row: subheadings in the label column ("ASSETS", "Non-Current Assets"), then up to 5 lines whose
    words all sit over the number columns ("Group  Company", "Note 2025 2024", "Rs. '000"). Returns (headers, subheadings)."""
    mid = boundary
    headers: list[list[dict]] = []
    subheads: list[list[dict]] = []
    edge = first_top
    for ln in reversed([ln for ln in lines if ln.bottom <= first_top + 0.5]):
        words = [w for w in ln.words if w["x1"] > left - 2 and w["x0"] < right + 2]
        if not words:
            continue
        if edge - ln.bottom > reach:
            break
        if any(is_number(w["text"]) and re.search(r"[,().]", w["text"]) for w in words if w["x0"] >= mid):
            break                               # amounts above the table: another table, or a chart's axis, not headings
        over = [w for w in words if w["x0"] >= mid]
        stub = [w for w in words if w["x1"] <= mid]
        clear_gap = bool(over) and len(stub) + len(over) == len(words) and (not stub or over[0]["x0"] - stub[-1]["x1"] > 12)
        if clear_gap and len(headers) < 5 and (not stub or len(over) >= 2):
            headers.insert(0, words)            # "For the year ended 31 March   Note  2025  2024": the left part labels the label column
        elif not headers and all(w["x1"] <= mid for w in words) and len(words) <= 8 and len(subheads) < 3:
            subheads.insert(0, words)
        else:
            break
        edge = ln.top
    return headers, subheads


YEAR = re.compile(r"^(?:19|20)\d{2}$")
NOTE_REF = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,2}[a-z]?$|^\d{1,2}(?:\s*,\s*\d{1,2})+$")


def _note_column(rows: list[dict], names: list[str]) -> int | None:
    """The column of note numbers ("5.1", "16.2"): its cells are all short references, some rows have none, and the next column has
    a number on (nearly) every row. Its heading becomes "Note", so it's never read as a value."""
    data = [r["cells"] for r in rows if r["kind"] == "data"]
    if len(names) < 2 or not data:
        return None
    for i in range(len(names) - 1):
        cells = [c[i + 1] for c in data if c[i + 1]]
        nxt = [c[i + 2] for c in data if c[i + 2]]
        if cells and all(NOTE_REF.match(x) for x in cells) and len(cells) < len(data) and len(nxt) >= 0.8 * len(data) \
                and any("," in x or len(x.strip("()")) > 4 for x in nxt):
            return i
        if cells:
            return None                          # the note column is always the first one with values
    return None


def aligned_tables(lines: list[Line]) -> list[dict]:
    lh = _line_height(lines)
    tables = []
    for block in _blocks(lines):
        while block and all(YEAR.match(c["text"]) for c in block[0]["cells"]):     # "Note 2025 2024": the headings, not a row
            block = block[1:]
        if len(block) < 3:
            continue
        left = min(r["left"] for r in block)
        right = max(r["right"] for r in block)
        cols = _columns([c for r in block for c in r["cells"]])
        used = [sum(1 for r in block if any(c["x0"] < b and c["x1"] > a for c in r["cells"])) for a, b in cols]
        cols = [c for c, n in zip(cols, used) if n >= max(2, 0.15 * len(block))] or cols     # a stray number isn't a column
        col_left = cols[0][0]
        top, bottom = block[0]["line"].top, block[-1]["line"].bottom
        tops = [r["line"].top for r in block]
        pitch = statistics.median(b - a for a, b in zip(tops, tops[1:])) if len(tops) > 1 else lh
        boundary = max((left + col_left) / 2, col_left - 40)        # left of it: the label column; right of it: the number columns
        headers, subheads = _lines_above(lines, top, left, right, boundary, max(lh * 3.5, min(pitch, lh * 3) * 1.8))
        rows = []
        column_names = [[] for _ in cols]
        for words in headers:
            cells = [""] + ["" for _ in cols]
            for w in words:
                lonely_note = re.match(r"(?i)^notes?\*?$", w["text"]) and not any(w["x0"] < b and w["x1"] > a for a, b in cols)
                if w["x1"] <= boundary or lonely_note:          # the label column's own heading ("For the year ended 31 March"), or
                    cells[0] = (cells[0] + " " + w["text"]).strip()    # "Note" over a note column with no numbers in it
                    continue
                for i in _column_of(w, cols):
                    cells[i + 1] = (cells[i + 1] + " " + w["text"]).strip()
                    column_names[i].append(w["text"])
            rows.append({"kind": "header", "cells": cells})
        for words in subheads:
            rows.append({"kind": "text", "cells": _row_cells(words, cols)})
        for ln in lines:
            if ln.top < top - 0.5 or ln.top > bottom:
                continue
            words = [w for w in ln.words if (w["x0"] + w["x1"]) / 2 >= left - 2 and (w["x0"] + w["x1"]) / 2 <= right + 2]
            if not words:
                continue
            cells = _row_cells(words, cols)
            kind = "data" if any(is_number(c) for c in cells[1:] if c) else "text"
            rows.append({"kind": kind, "cells": cells})
        names = [" ".join(c) for c in column_names]
        note = _note_column(rows, names)
        if note is not None and not re.search(r"\bnotes?\b", names[note], re.I):
            names[note] = ("Note " + names[note]).strip()
        unit = find_unit([" ".join(w["text"] for w in h) for h in headers])
        tables.append({"method": "aligned", "bbox": [round(left, 1), round(min([w["top"] for w in (headers + subheads)[0]] if headers or subheads else [top]), 1),
                                                     round(right, 1), round(bottom, 1)],
                       "columns": names, "rows": rows, "spans": [[round(a, 1), round(b, 1)] for a, b in cols],
                       "unit_text": unit[0], "currency": unit[1], "multiplier": unit[2]})
    return tables


# --- ruled tables -----------------------------------------------------------------------------------------------

def _overlap(a: list[float], b: list[float]) -> float:
    """Share of the smaller box covered by the other."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1
    return w * h / smaller


def _cell(text) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def ruled_tables(plumber_page, taken: list[list[float]]) -> list[dict]:
    out = []
    try:
        found = plumber_page.find_tables()
    except Exception:                   # pdfminer can trip on odd drawings; the aligned tables and the text are still there
        return out
    for t in found:
        bbox = [round(float(v), 1) for v in t.bbox]
        if any(_overlap(bbox, b) > 0.3 for b in taken):
            continue
        rows = [[_cell(c) for c in row] for row in t.extract()]
        rows = [r for r in rows if any(r)]
        cells = [c for r in rows for c in r]
        if len(rows) < 2 or max((len(r) for r in rows), default=0) < 2 or sum(1 for c in cells if c) < 0.4 * len(cells):
            continue
        unit = find_unit(rows[0] + rows[1] if len(rows) > 1 else rows[0])
        out.append({"method": "ruled", "bbox": bbox, "columns": rows[0][1:], "rows":
                    [{"kind": "header" if i == 0 else ("data" if any(is_number(c) for c in r[1:] if c) else "text"), "cells": r}
                     for i, r in enumerate(rows)],
                    "unit_text": unit[0], "currency": unit[1], "multiplier": unit[2]})
    return out


def _title(lines: list[Line], top: float, headings: list[dict] | None = None) -> str | None:
    """The nearest text above the table on the left: usually its name ("Statement of Financial Position", "Note 12 ...")."""
    above = [ln for ln in lines if ln.bottom <= top + 0.5 and not any(is_number(w["text"]) for w in ln.words[-1:])]
    for ln in reversed(above[-6:]):
        text = ln.text.strip()
        if len(text) >= 4 and sum(c.isalpha() for c in text) >= 4:
            return text[:200]
    return None


def find_tables(lines: list[Line], plumber_page, settings: dict | None = None) -> list[dict]:
    settings = settings or {}
    tables = aligned_tables(lines)
    if settings.get("ruled", True):
        tables += ruled_tables(plumber_page, [t["bbox"] for t in tables])
    tables.sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))
    for t in tables:
        t["title"] = _title(lines, t["bbox"][1])
        if not t["unit_text"]:                 # "Rs. '000" is sometimes a line above the headings, or in the title
            above = [ln.text for ln in lines if t["bbox"][1] - 60 <= ln.top <= t["bbox"][1]]
            t["unit_text"], t["currency"], t["multiplier"] = find_unit(above)
    return tables
