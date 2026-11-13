"""Annual reports' database: PostgreSQL schema `annualreports` (warehouse/schema/annualreports.sql).

A document is saved in one transaction with everything in it, so a run stopped part way leaves each document either complete or as it
was. Documents are keyed by the file's SHA-256: the same PDF under two names is one document with two `files` rows, and a file whose
content changed gets a new document while its old one goes (unless another file still has that content)."""

import json

from warehouse.postgres import clean_text as t, json_text

from .identify import name_key

SAVE_LOCK = 4_242_017            # advisory lock: two runs at once can't both save the same new PDF


class ReportStore:
    def __init__(self, role: str = "writer"):
        from warehouse import postgres
        self.con = postgres.connect(role, autocommit=True)
        self.con.execute("set timezone = 'UTC'")
        if role == "writer":
            postgres.migrate(self.con, ("annualreports",))

    # --- what's there ---------------------------------------------------------------------------------------

    def known(self) -> dict[str, int]:
        """sha256 -> reader_version of every document saved."""
        return dict(self.con.execute("select sha256, reader_version from annualreports.documents").fetchall())

    def file_hash(self, path: str) -> str | None:
        row = self.con.execute("select sha256 from annualreports.files where path = %s", [path]).fetchone()
        return row[0] if row else None

    # --- saving ---------------------------------------------------------------------------------------------

    def see_file(self, path: str, sha256: str, name: str, size: int, modified_at) -> str | None:
        """A file whose content is already saved: note the file (and its name), nothing else. If this path used to hold other content,
        that content's document goes when no file has it any more. Returns the sha256 it held before, if different."""
        with self.con.transaction():
            old = self._upsert_file(path, sha256, name, size, modified_at, None)
            self._drop_orphan(old, sha256)
        return old if old and old != sha256 else None

    def file_failed(self, path: str, sha256: str, name: str, size: int, modified_at, error: str) -> None:
        with self.con.transaction():
            self._upsert_file(path, sha256, name, size, modified_at, error)

    def mark_missing(self, present: list[str], folder_prefix: str) -> int:
        """Files of this folder that aren't there any more. Their documents stay: a moved or deleted PDF doesn't delete what was read."""
        cur = self.con.execute("""update annualreports.files set missing_since = coalesce(missing_since, now())
            where path like %s and not (path = any(%s)) and missing_since is null""", [folder_prefix.replace("%", r"\%") + "%", present])
        return cur.rowcount

    def save(self, result: dict, path: str, modified_at, link: dict) -> int:
        """Replace the document with this content (if it was read before) by what was read now, and note the file. One transaction."""
        d = result["document"]
        with self.con.transaction():
            self.con.execute("select pg_advisory_xact_lock(%s)", [SAVE_LOCK])
            self.con.execute("delete from annualreports.documents where sha256 = %s", [d["sha256"]])
            doc_id = self.con.execute("""insert into annualreports.documents (sha256, file_name, file_size, page_count, title, company, company_from,
                    fiscal_year, year, period_end, language, pdf_meta, outline, running_headers, company_id, cse_symbol, cse_symbols, link_how,
                    pages_text, pages_ocr, pages_empty, pages_needs_ocr, reader_version, read_seconds)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                [d["sha256"], t(d["file_name"]), d["file_size"], d["page_count"], t(d["title"]), t(d["company"]), d["company_from"],
                 d["fiscal_year"], d["year"], d["period_end"], d["language"], json_text(d["pdf_meta"]), json_text(d["outline"]),
                 [t(h) for h in d["running_headers"]], link.get("company_id"), link.get("cse_symbol"), link.get("cse_symbols"),
                 link.get("how"), d["pages_text"], d["pages_ocr"], d["pages_empty"], d["pages_needs_ocr"], d["reader_version"],
                 d["read_seconds"]]).fetchone()[0]
            cur = self.con.cursor()
            with cur.copy("""copy annualreports.pages (document_id, page_no, width, height, text, text_source, ocr_text, ocr_confidence, note,
                                 char_count, word_count, image_count, headings) from stdin""") as cp:
                for p in result["pages"]:
                    cp.write_row([doc_id, p.page_no, p.width, p.height, t(p.text), p.text_source, t(p.ocr_text), p.ocr_confidence, t(p.note),
                                  p.char_count, p.word_count, p.image_count, [t(h["text"]) for h in p.headings]])
            with cur.copy("copy annualreports.sections (document_id, section_no, title, level, page_no, page_to, source, text) from stdin") as cp:
                for i, s in enumerate(result["sections"], 1):
                    cp.write_row([doc_id, i, t(s["title"]), s["level"], s["page_no"], s["page_to"], s["source"], t(s["text"])])
            with cur.copy("""copy annualreports.tables (document_id, table_no, page_no, method, title, statement, columns, columns_from, unit_text,
                                 currency, multiplier, bbox, n_rows, n_cols) from stdin""") as cp:
                for tb in result["tables"]:
                    cp.write_row([doc_id, tb["table_no"], tb["page_no"], tb["method"], t(tb.get("title")), tb.get("statement"),
                                  [t(c) for c in tb["columns"]], tb.get("columns_from"), t(tb["unit_text"]), tb["currency"], tb["multiplier"], tb["bbox"],
                                  len(tb["rows"]), max((len(r["cells"]) for r in tb["rows"]), default=0)])
            with cur.copy("copy annualreports.table_rows (document_id, table_no, row_no, kind, cells) from stdin") as cp:
                for tb in result["tables"]:
                    for i, r in enumerate(tb["rows"]):
                        cp.write_row([doc_id, tb["table_no"], i, r["kind"], [t(c) for c in r["cells"]]])
            with cur.copy("""copy annualreports.figures (document_id, metric, statement, label, column_no, column_header, year, entity, value_text,
                                 value, amount, currency, unit_text, page_no, table_no, row_no) from stdin""") as cp:
                for f in result["figures"]:
                    cp.write_row([doc_id, f["metric"], f["statement"], t(f["label"]), f["column_no"], t(f["column_header"]), f["year"], f["entity"],
                                  t(f["value_text"]), f["value"], f["amount"], f["currency"], t(f["unit_text"]), f["page_no"], f["table_no"],
                                  f["row_no"]])
            with cur.copy("copy annualreports.people (document_id, name_key, name, honorific, roles, pages, found_by) from stdin") as cp:
                for p in result["people"]:
                    cp.write_row([doc_id, p["name_key"], t(p["name"]), t(p["honorific"]), [t(r) for r in p["roles"]], p["pages"], p["found_by"]])
            old = self._upsert_file(path, d["sha256"], d["file_name"], d["file_size"], modified_at, None)
            self._drop_orphan(old, d["sha256"])
        return doc_id

    def _upsert_file(self, path, sha256, name, size, modified_at, error) -> str | None:
        old = self.file_hash(path)
        self.con.execute("""insert into annualreports.files (path, sha256, file_name, file_size, modified_at, error) values (%s, %s, %s, %s, %s, %s)
            on conflict (path) do update set sha256 = excluded.sha256, file_name = excluded.file_name, file_size = excluded.file_size,
                modified_at = excluded.modified_at, last_seen = now(), missing_since = null, error = excluded.error""",
                         [path, sha256, t(name), size, modified_at, t(error)])
        return old

    def _drop_orphan(self, old_sha: str | None, new_sha: str) -> None:
        """The file at this path changed: its old content's document goes, unless another file still has the same content."""
        if old_sha and old_sha != new_sha:
            self.con.execute("""delete from annualreports.documents d where d.sha256 = %s
                and not exists (select 1 from annualreports.files f where f.sha256 = d.sha256)""", [old_sha])

    def save_run(self, seconds: float, counts: dict, note: str | None = None) -> None:
        self.con.execute("insert into annualreports.runs (seconds, counts, note) values (%s, %s, %s)", [seconds, json.dumps(counts), note])

    # --- links to the other tools -----------------------------------------------------------------------------

    def _exists(self, relation: str) -> bool:
        return self.con.execute("select to_regclass(%s) is not null", [relation]).fetchone()[0]

    def link(self, company: str | None, by_hand: dict | None = None) -> dict:
        """The company in `companies` and its CSE shares in `markets`, only when certain: the same name letter for letter (spaces,
        dots and capitals aside) and only one company with it, or what settings say by hand."""
        by_hand = by_hand or {}
        out: dict = {"company_id": None, "cse_symbol": None, "cse_symbols": None, "how": None}
        key = name_key(company)
        notes = []
        if key and self._exists("markets.v_latest"):
            rows = self.con.execute("""select distinct symbol from markets.v_latest where asset_class = 'stock_lk'
                and regexp_replace(upper(replace(name, '&', ' AND ')), '^THE\\s+|[^A-Z0-9]', '', 'g') = %s order by symbol""", [key]).fetchall()
            symbols = [r[0] for r in rows]
            if symbols:
                out["cse_symbols"] = symbols
                out["cse_symbol"] = next((s for s in symbols if s.endswith(".N0000")), symbols[0])
                notes.append(f"same name as CSE share {out['cse_symbol']}")
        if key and self._exists("companies.companies"):
            rows = self.con.execute("""select id, source from companies.companies
                where regexp_replace(upper(replace(name, '&', ' AND ')), '^THE\\s+|[^A-Z0-9]', '', 'g') = %s order by id""", [key]).fetchall()
            cse = [i for i, s in rows if s == "cse" and out["cse_symbols"] and i.split(":", 1)[1] in out["cse_symbols"]]
            pick = None
            if cse:
                pick = next((i for i in cse if i.endswith(".N0000")), cse[0])
            else:
                for source in ("cse", "wikidata", "gleif"):
                    same = [i for i, s in rows if s == source]
                    if len(same) == 1:
                        pick = same[0]
                        break
                    if len(same) > 1:
                        notes.append(f"{len(same)} {source} companies share this name: not linked")
                        break
            if pick:
                out["company_id"] = pick
                notes.append(f"same name in companies ({pick.split(':')[0]})")
        if by_hand.get("company_id"):
            out["company_id"] = by_hand["company_id"]
            notes.append("company by hand (settings)")
        if by_hand.get("cse_symbol"):
            out["cse_symbol"] = by_hand["cse_symbol"]
            out["cse_symbols"] = sorted(set((out["cse_symbols"] or []) + [by_hand["cse_symbol"]]))
            notes.append("CSE share by hand (settings)")
        out["how"] = "; ".join(notes) or ("no company or CSE share with this exact name" if key else "company not found in the PDF")
        return out

    def relink(self, links_by_hand: dict) -> list[dict]:
        """Check every document's link again (the companies directory and markets grow). Returns the ones that changed."""
        changed = []
        for doc_id, company, old_id, old_sym in self.con.execute(
                "select id, company, company_id, cse_symbol from annualreports.documents order by id").fetchall():
            link = self.link(company, links_by_hand.get(company or "", {}))
            self.con.execute("update annualreports.documents set company_id = %s, cse_symbol = %s, cse_symbols = %s, link_how = %s where id = %s",
                             [link["company_id"], link["cse_symbol"], link["cse_symbols"], link["how"], doc_id])
            if (link["company_id"], link["cse_symbol"]) != (old_id, old_sym):
                changed.append({"id": doc_id, "company": company, **link})
        return changed

    # --- reading back -----------------------------------------------------------------------------------------

    def rows(self, sql: str, params=None) -> list[dict]:
        cur = self.con.execute(sql, params or [])
        return [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]

    def close(self) -> None:
        self.con.close()
