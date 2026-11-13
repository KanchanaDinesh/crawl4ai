"""Key figures (revenue, profit, assets, equity, EPS, dividends) from the tables of the financial statements and the highlights pages.

Only rows whose label is exactly one of the usual names are taken ("Profit before tax", "Total assets"), and only from pages that say
which statement they are. Each figure keeps the label and cell as printed, the column it's in ("Group 2025 Rs. '000"), the unit, and its
page, so it can be checked against the PDF. Nothing is computed or guessed: a report that words it differently has no figure for it,
and its table is still saved in full."""

import re

from .tables import parse_number

STATEMENTS = [            # checked in this order on the page's headings and table titles
    ("income", re.compile(r"income statement|statement of (?:profit or loss|comprehensive income|income)|profit and loss|statement of profit", re.I)),
    ("balance", re.compile(r"statement of financial position|balance sheet", re.I)),
    ("cash_flow", re.compile(r"statement of cash flows?|cash flow statement", re.I)),
    ("equity", re.compile(r"statement of changes in equity", re.I)),
    ("summary", re.compile(r"(?:five|ten|5|10)[- ]year (?:summary|review|financial summary)|decade at a glance|financial summary", re.I)),
    ("highlights", re.compile(r"financial highlights|performance highlights|financial performance|key (?:financial )?(?:figures|indicators|highlights)|"
                              r"highlights of the year|year at a glance", re.I)),
]
NOTES = re.compile(r"notes? to the (?:consolidated )?financial statements|accounting policies", re.I)

PL = r"(?:net\s+)?(?:profit|loss|\(loss\)\s*/\s*profit|profit\s*/\s*\(loss\)|\(loss\)|earnings)"
METRICS = [
    ("revenue", re.compile(r"^(?:total\s+)?(?:revenue|turnover|sales)(?:\s+from contracts with customers)?$")),
    ("gross_income", re.compile(r"^(?:total\s+)?gross income$")),
    ("total_operating_income", re.compile(r"^(?:total|net) operating income$")),
    ("net_interest_income", re.compile(r"^net interest income$")),
    ("gross_profit", re.compile(r"^gross profit(?:\s*/\s*\(loss\))?$")),
    ("operating_profit", re.compile(rf"^(?:operating {PL}|{PL} from operations|results from operating activities)$")),
    ("profit_before_tax", re.compile(rf"^{PL}\s+before\s+(?:income\s+)?tax(?:ation)?$")),
    ("income_tax", re.compile(r"^(?:income tax(?: expense)?|tax expense|taxation|income tax \(expense\)\s*/\s*reversal)$")),
    ("profit_for_year", re.compile(rf"^{PL}\s+(?:for the (?:year|period)|after tax(?:ation)?)$")),
    ("total_assets", re.compile(r"^total assets$")),
    ("total_liabilities", re.compile(r"^total liabilities$")),
    ("total_equity", re.compile(r"^total equity$")),
    ("shareholders_equity", re.compile(r"^total (?:shareholders|equity holders)['’]? ?(?:funds|equity)$")),
    ("eps_basic", re.compile(rf"^(?:basic\s+(?:and\s+diluted\s+)?{PL}\s+per\s+(?:ordinary\s+)?share|{PL} per (?:ordinary )?share\s*[-–(]?\s*basic\)?|"
                             rf"basic\s+eps|{PL} per share)(?:\s*\(rs\.?\)|\s*\(\$\)|\s*-\s*rs\.?)?$")),
    ("eps_diluted", re.compile(rf"^(?:diluted\s+{PL}\s+per\s+(?:ordinary\s+)?share|{PL} per (?:ordinary )?share\s*[-–(]?\s*diluted\)?|diluted eps)"
                               r"(?:\s*\(rs\.?\)|\s*\(\$\))?$")),
    ("dividend_per_share", re.compile(r"^(?:total\s+)?dividends?\s+per\s+(?:ordinary\s+)?share(?:\s*\(rs\.?\)|\s*\(\$\)|\s*-\s*rs\.?)?$")),
    ("cash_at_end", re.compile(r"^cash and cash equivalents at (?:the )?end(?: of (?:the )?(?:year|period))?$")),
]
FOR_STATEMENT = {          # which figures each statement is trusted for; "profit for the year" in the statement of changes in equity is
    "income": {"revenue", "gross_income", "total_operating_income", "net_interest_income", "gross_profit", "operating_profit",
               "profit_before_tax", "income_tax", "profit_for_year", "eps_basic", "eps_diluted", "dividend_per_share"},
    "balance": {"total_assets", "total_liabilities", "total_equity", "shareholders_equity"},
    "cash_flow": {"cash_at_end"},
    "equity": set(),
}
PER_SHARE = {"eps_basic", "eps_diluted", "dividend_per_share"}
YEAR = re.compile(r"\b((?:19|20)\d{2})(?:\s*/\s*(\d{2,4}))?\b")
ENTITY = re.compile(r"\b(group|company|bank|consolidated|parent)\b", re.I)


def norm_label(label: str) -> str:
    """'– Profit before tax 4' -> 'profit before tax'. Note numbers and leading dashes go; the words stay as printed."""
    s = label.lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"^[\s–—\-•·*]+|[:*]+$", "", s)
    s = re.sub(r"(?:\s+\d{1,2}(?:\.\d{1,2})?(?:\s*,\s*\d{1,2})*)+$", "", s)       # note references at the end
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"\(\s*", "(", re.sub(r"\s*\)", ")", s))
    return re.sub(r"\s+", " ", s).strip(" .:")


def statement_of(texts: list[str]) -> str | None:
    joined = " | ".join(t for t in texts if t)
    if NOTES.search(joined) and not any(rx.search(joined) for name, rx in STATEMENTS[:4]):
        return None
    for name, rx in STATEMENTS:
        if rx.search(joined):
            return name
    return None


def metric_of(label: str) -> str | None:
    n = norm_label(label)
    for name, rx in METRICS:
        if rx.match(n):
            return name
    return None


def column_year(header: str) -> int | None:
    """The year a column is for: the last year in its heading ('2024/25' -> 2025)."""
    found = YEAR.findall(header or "")
    if not found:
        return None
    start, end = found[-1]
    if end:
        return int(end) if len(end) == 4 else int(start[:2] + end[-2:])
    return int(start)


def column_entity(header: str) -> str | None:
    m = ENTITY.search(header or "")
    if not m:
        return None
    word = m.group(1).lower()
    return {"consolidated": "group", "parent": "company"}.get(word, word)


PLAIN_WORDS = re.compile(r"\b(?:19|20)\d{2}\b|/\d{2,4}|rs\.?|lkr|us\$|usd|\$m|\$bn|\$|£m|£|mn|bn|million|billion|'?000|[’']000|group|company|bank|"
                         r"consolidated|restated|audited|as at|at|year|ended|31|30|march|december|june|september|dec|mar|jun|sep|fy|[()'’.,:/*†-]", re.I)


def plain_column(header: str | None, need_year: bool = True) -> bool:
    """A column is trusted only when its heading is a year, the group or company, and a unit: 'Group 2025 Rs. '000', not
    'Commercial Banking 2024' (a segment), 'Change %' or 'Eliminations'. On the highlights and summary pages it must name a year."""
    if not header:
        return not need_year
    if "%" in header or (need_year and not YEAR.search(header)):
        return False
    return not PLAIN_WORDS.sub(" ", header).strip()


def figures_in_table(table: dict, statement: str, page_no: int, table_no: int) -> list[dict]:
    out = []
    columns = table.get("columns") or []
    for row_no, row in enumerate(table["rows"]):
        if row["kind"] != "data":
            continue
        cells = row["cells"]
        metric = metric_of(cells[0]) if cells and cells[0] else None
        if not metric or (statement in FOR_STATEMENT and metric not in FOR_STATEMENT[statement]):
            continue
        for col, value_text in enumerate(cells[1:]):
            header = columns[col] if col < len(columns) else ""
            if not plain_column(header, need_year=statement in ("highlights", "summary")):
                continue
            value = parse_number(value_text)
            if value is None:
                continue
            mult = table.get("multiplier")
            per_share = metric in PER_SHARE
            out.append({"metric": metric, "statement": statement, "label": cells[0], "column_no": col + 1, "column_header": header or None,
                        "year": column_year(header), "entity": column_entity(header), "value_text": value_text, "value": value,
                        "amount": value if per_share else (value * mult if mult else None),
                        "currency": table.get("currency"), "unit_text": None if per_share else table.get("unit_text"),
                        "page_no": page_no, "table_no": table_no, "row_no": row_no})
    return out
