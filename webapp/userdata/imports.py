"""CSV and Excel files into own tables, and a grid's rows into a new table. A file is read once, kept for the few minutes it
takes to confirm its columns (in .local/, the project drive), and then deleted."""

import csv
import io
import json
import re
import secrets
import time
from datetime import date, datetime

import project_paths
from webapp.catalog.models import Dataset, Field

from . import fields as field_tools
from . import store

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 100
KEEP_SECONDS = 24 * 3600
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}([ T].*)?")


class ImportProblem(ValueError):
    """What to tell the person."""


def _folder():
    folder = project_paths.LOCAL_DIR / "webapp-imports"
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in folder.glob("*.json"):
        if now - old.stat().st_mtime > KEEP_SECONDS:
            old.unlink(missing_ok=True)
    return folder


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _read_csv(raw: bytes) -> list[list]:
    text = _decode(raw)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    try:
        return [row for row in csv.reader(io.StringIO(text, newline=""), dialect)]
    except csv.Error as e:
        raise ImportProblem(f"That CSV file can't be read: {e}.") from None


def _plain_cell(value):
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _read_excel(raw: bytes) -> list[list]:
    from openpyxl import load_workbook
    try:
        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 - openpyxl raises many kinds for a damaged or disguised file
        raise ImportProblem("That Excel file can't be read. Save it as .xlsx or CSV and try again.") from None
    try:
        return [[_plain_cell(v) for v in row] for row in book.worksheets[0].iter_rows(values_only=True)]
    finally:
        book.close()


def read_file(name: str, raw: bytes) -> tuple[list[str], list[list]]:
    """The file's headings and rows."""
    if len(raw) > MAX_BYTES:
        raise ImportProblem(f"That file is over {MAX_BYTES // (1024 * 1024)} MB. Split it, or filter it first.")
    lower = name.lower()
    if lower.endswith(".xlsx"):
        rows = _read_excel(raw)
    elif lower.endswith((".csv", ".txt", ".tsv")):
        rows = _read_csv(raw)
    else:
        raise ImportProblem("Choose a .csv or .xlsx file.")
    rows = [r for r in rows if any(c not in (None, "") for c in r)]
    if len(rows) < 2:
        raise ImportProblem("That file has no rows under its headings. The first line must be the headings.")
    if len(rows) - 1 > MAX_ROWS:
        raise ImportProblem(f"That file has {len(rows) - 1:,} rows; a table takes at most {MAX_ROWS:,} at once.")
    width = max(len(r) for r in rows)
    if width > MAX_COLUMNS:
        raise ImportProblem(f"That file has {width} columns; at most {MAX_COLUMNS}.")
    headings = _headings(rows[0], width)
    return headings, [[*r, *[None] * (width - len(r))] for r in rows[1:]]


def _headings(row: list, width: int) -> list[str]:
    out = []
    for i in range(width):
        name = " ".join(str(row[i] if i < len(row) and row[i] is not None else "").split())[:120] or f"Column {i + 1}"
        base, n = name, 1
        while name.lower() in {x.lower() for x in out}:
            n += 1
            name = f"{base} {n}"
        out.append(name)
    return out


def guess_type(values: list) -> str:
    """The type every value of a column fits. Text if in doubt: nothing is lost, and it can be changed before importing."""
    seen = [v for v in values if v not in (None, "")]
    if not seen:
        return Field.TEXT

    def fits(check) -> bool:
        return all(check(v) for v in seen[:5000])

    def number(v):
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            return True
        text = str(v).strip()
        if re.fullmatch(r"0\d+", text):       # 0112345678 is a phone number or a code: as a number it would lose its 0
            return False
        return bool(re.fullmatch(r"-?\d[\d,]*(\.\d+)?", text))
    if fits(number):
        return Field.NUMBER
    if fits(lambda v: str(v).strip().lower() in ("yes", "no", "true", "false")):
        return Field.BOOL
    if fits(lambda v: bool(DATE_RE.fullmatch(str(v).strip()))):
        return Field.DATE
    if fits(lambda v: str(v).lower().startswith(("http://", "https://"))):
        return Field.LINK
    if fits(lambda v: bool(store.EMAIL_RE.fullmatch(str(v).strip()))):
        return Field.EMAIL
    texts = {str(v).strip().lower() for v in seen}
    if len(seen) >= 10 and len(texts) <= 20 and len(texts) * 2 <= len(seen) and all(len(t) <= store.MAX_CHOICE for t in texts):
        return Field.CHOICE
    return Field.TEXT


def keep(user, filename: str, headings: list[str], rows: list[list], into: Dataset | None = None) -> str:
    token = secrets.token_urlsafe(16)
    (_folder() / f"{token}.json").write_text(json.dumps({"user": user.pk, "filename": filename, "headings": headings, "rows": rows, "into": into.pk if into else None},
                                                        ensure_ascii=False), encoding="utf-8")
    return token


def fetch(token: str, user) -> dict | None:
    if not re.fullmatch(r"[\w-]+", token or ""):
        return None
    path = _folder() / f"{token}.json"
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return kept if kept["user"] == user.pk else None


def discard(token: str) -> None:
    (_folder() / f"{token}.json").unlink(missing_ok=True)


def columns(kept: dict) -> list[dict]:
    """Each column of the file: its heading, a guessed type, examples."""
    rows, out = kept["rows"], []
    for i, heading in enumerate(kept["headings"]):
        values = [r[i] for r in rows]
        examples = [str(v) for v in values if v not in (None, "")][:3]
        out.append({"index": i, "heading": heading, "type": guess_type(values), "examples": examples})
    return out


def load_rows(cur, ds: Dataset, mapping: list[tuple[int, Field]], rows: list[list], who: str) -> dict:
    """Adds rows. A value that doesn't fit its field is left empty and counted, rather than losing the whole file."""
    added_choices: set = set()
    records, bad = [], 0
    for row in rows:
        data = {}
        for index, f in mapping:
            try:
                value = store.clean_value(f, row[index], added_choices)
            except store.StoreProblem:
                bad += 1
                continue
            if value is not None:
                data[f.json_key] = value
        if data:
            records.append(data)
    for _, f in mapping:
        if f.pk in added_choices:
            f.save(update_fields=["values"])
    store.add_rows(cur, ds, who, records)
    return {"rows": len(records), "bad": bad, "empty": len(rows) - len(records)}


def type_from(source: Field) -> str:
    """The field a grid's column becomes in a table made from it. What can't be typed by hand becomes text."""
    if source.type in field_tools.TYPE_NAMES:
        return source.type
    if source.base == Field.NUMBER:
        return Field.NUMBER
    if source.base == Field.DATETIME:
        return Field.TEXT
    if source.base == Field.LIST:
        return Field.MULTI
    return Field.TEXT
