"""What the web page (webapp/annualreports/) reads from schema annualreports. Plain queries with no Django, on a read-only connection.
Lists leave the page text out; only a report's own page reads it, so a list stays quick."""

TS = "to_char({} at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
MARK_START, MARK_END = "[[[", "]]]"           # ts_headline's marks around the words found; the page turns them into <mark> after escaping

METRICS = {          # the order the side panel shows them in
    "revenue": "Revenue", "gross_income": "Gross income", "total_operating_income": "Total operating income",
    "net_interest_income": "Net interest income", "gross_profit": "Gross profit", "operating_profit": "Operating profit",
    "profit_before_tax": "Profit before tax", "income_tax": "Income tax", "profit_for_year": "Profit for the year",
    "total_assets": "Total assets", "total_liabilities": "Total liabilities", "total_equity": "Total equity",
    "shareholders_equity": "Shareholders' equity", "eps_basic": "Earnings per share (basic)", "eps_diluted": "Earnings per share (diluted)",
    "dividend_per_share": "Dividend per share", "cash_at_end": "Cash at the end of the year",
}
LIST_METRICS = ("revenue", "profit_before_tax", "profit_for_year", "total_assets", "eps_basic")     # the columns of the list
TEXT_SOURCE = {"pdf": "The PDF's own text", "ocr": "No text layer: read by OCR", "empty": "Nothing to read on this page (a picture or a blank page)",
               "needs_ocr": "No text layer, and OCR wasn't available", "ocr_failed": "No text layer, and OCR failed"}


def _rows(con, sql: str, params=()) -> list[dict]:
    cur = con.execute(sql, params)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def summary(con) -> dict:
    return _rows(con, f"""select (select count(*) from annualreports.documents) as reports,
            (select count(distinct company) from annualreports.documents) as companies,
            (select count(*) from annualreports.pages) as pages, (select coalesce(sum(word_count), 0) from annualreports.pages) as words,
            (select count(*) from annualreports.tables) as tables, (select count(*) from annualreports.figures) as figures,
            (select count(*) from annualreports.people) as people,
            (select count(*) from annualreports.documents where company_id is not null or cse_symbol is not null) as linked,
            (select {TS.format('max(read_at)')} from annualreports.documents) as last_read""")[0]


def company_choices(con) -> list[dict]:
    return _rows(con, "select company as name, count(*) as n from annualreports.documents where company is not null group by 1 order by 1")


def reports(con, company=None, q=None) -> list[dict]:
    """Every report (a handful to a few hundred), with its headline figures as printed."""
    where, params = ["true"], []
    if company:
        where.append("d.company = %s"); params.append(company)
    if q:
        where.append("(d.company ilike %s or d.title ilike %s or d.file_name ilike %s)"); params += [_like(q)] * 3
    picks = ", ".join(f"max(k.value_text) filter (where k.metric = '{m}') as {m}, max(k.unit_text) filter (where k.metric = '{m}') as {m}_unit"
                      for m in LIST_METRICS)
    rows = _rows(con, f"""select d.id, d.company, d.fiscal_year, d.year, d.title, d.file_name, d.page_count, d.tables, d.figures, d.people,
            d.cse_symbol, d.company_id, {picks},
            max(k.value_text) filter (where k.metric in ('gross_income', 'total_operating_income')) as other_income,
            max(k.metric) filter (where k.metric in ('gross_income', 'total_operating_income')) as other_income_metric,
            max(k.unit_text) filter (where k.metric in ('gross_income', 'total_operating_income')) as other_income_unit
        from annualreports.v_documents d left join annualreports.v_key_figures k on k.document_id = d.id
        where {" and ".join(where)}
        group by d.id, d.company, d.fiscal_year, d.year, d.title, d.file_name, d.page_count, d.tables, d.figures, d.people, d.cse_symbol, d.company_id
        order by d.company nulls last, d.year desc nulls last, d.figures desc, d.file_name""", params)
    return rows


def report(con, doc_id: int) -> dict | None:
    found = _rows(con, f"""select d.*, {TS.format('d.read_at')} as read_text from annualreports.v_documents d where d.id = %s""", [doc_id])
    if not found:
        return None
    d = found[0]
    d["key_figures"] = _rows(con, """select metric, value_text, unit_text, amount, currency, column_header, statement, page_no, label
        from annualreports.v_key_figures where document_id = %s""", [doc_id])
    d["people_list"] = _rows(con, """select name, honorific, roles, pages from annualreports.people where document_id = %s
        order by pages[1], name""", [doc_id])
    d["section_list"] = nav_sections(con, doc_id)
    d["running_headers"] = con.execute("select running_headers from annualreports.documents where id = %s", [doc_id]).fetchone()[0]
    d["statements"] = _rows(con, """select statement, array_agg(distinct page_no order by page_no) as pages from annualreports.tables
        where document_id = %s and statement is not null group by 1 order by min(page_no)""", [doc_id])
    return d


def nav_sections(con, doc_id: int) -> list[dict]:
    """The sections for a report's contents: the top level only (a report can have hundreds of headings)."""
    return _rows(con, """select section_no, title, page_no, page_to from annualreports.sections where document_id = %s
        and level = (select min(level) from annualreports.sections where document_id = %s) order by section_no limit 400""", [doc_id, doc_id])


def document(con, doc_id: int) -> dict | None:
    """What every detail page needs about its report: names, page count, running headers (left out of the formatted text)."""
    found = _rows(con, """select id, company, fiscal_year, title, page_count, running_headers, cse_symbol from annualreports.documents
        where id = %s""", [doc_id])
    return found[0] if found else None


def _tables(con, doc_id: int, first: int, last: int) -> list[dict]:
    tables = _rows(con, """select table_no, page_no, title, statement, method, columns, unit_text from annualreports.tables
        where document_id = %s and page_no between %s and %s order by table_no""", [doc_id, first, last])
    cells = _rows(con, """select table_no, row_no, kind, cells from annualreports.table_rows where document_id = %s
        and table_no = any(%s) order by table_no, row_no""", [doc_id, [t["table_no"] for t in tables]])
    by_table: dict[int, list[dict]] = {}
    for c in cells:
        by_table.setdefault(c["table_no"], []).append(c)
    for t in tables:
        t["rows"] = by_table.get(t["table_no"], [])
    return tables


def pages(con, doc_id: int) -> dict:
    """Everything to show a whole report on one page: every page's text, every table with its rows, and the sections (all levels,
    so each one's title can go above the page it starts on)."""
    found = _rows(con, """select page_no, text, ocr_text, text_source, ocr_confidence, note, headings, word_count from annualreports.pages
        where document_id = %s order by page_no""", [doc_id])
    tables = _tables(con, doc_id, 1, 1_000_000)
    sections = _rows(con, """select section_no, title, level, page_no from annualreports.sections where document_id = %s
        order by section_no""", [doc_id])
    return {"pages": found, "tables": tables, "sections": sections}


def search(con, words: str, company=None, limit=100, offset=0) -> dict:
    """Pages with all these words (a phrase in quotes, -word to leave one out), with the words marked in a short extract."""
    text = "coalesce(p.text, '') || ' ' || coalesce(p.ocr_text, '')"
    where, params = [f"to_tsvector('simple', {text}) @@ websearch_to_tsquery('simple', %s)"], [words]
    if company:
        where.append("d.company = %s"); params.append(company)
    cond = " and ".join(where)
    total = con.execute(f"select count(*) from annualreports.pages p join annualreports.documents d on d.id = p.document_id where {cond}",
                        params).fetchone()[0]
    rows = _rows(con, f"""select p.document_id, p.page_no, d.company, d.fiscal_year, d.title, d.page_count, p.headings,
            ts_headline('simple', {text}, websearch_to_tsquery('simple', %s),
                        'MaxWords=40, MinWords=15, MaxFragments=2, FragmentDelimiter=" … ", StartSel="{MARK_START}", StopSel="{MARK_END}"') as snippet
        from annualreports.pages p join annualreports.documents d on d.id = p.document_id where {cond}
        order by d.company, d.year desc nulls last, d.id, p.page_no limit {int(limit)} offset {int(offset)}""", [words] + params)
    return {"total": total, "items": rows}


def people(con, company=None, q=None, limit=100, offset=0) -> dict:
    where, params = ["true"], []
    if company:
        where.append("company = %s"); params.append(company)
    if q:
        where.append("(name ilike %s or array_to_string(roles, ' ') ilike %s)"); params += [_like(q)] * 2
    cond = " and ".join(where)
    total = con.execute(f"select count(*) from annualreports.v_people where {cond}", params).fetchone()[0]
    rows = _rows(con, f"""select document_id, company, fiscal_year, name, honorific, roles, pages from annualreports.v_people where {cond}
        order by company, name limit {int(limit)} offset {int(offset)}""", params)
    return {"total": total, "items": rows}
