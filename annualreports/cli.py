"""python -m annualreports <command>

    run     [--file X.pdf] [--folder DIR] [--dry-run] [--again]   read new or changed PDFs into the database (safe to repeat)
    show    [--id N] [--company X] [--limit 50]                   the documents, or one document's key figures, people and sections
    stats                                                         how much is saved
    search  WORDS [--limit 20]                                    pages that contain all these words
    link                                                          check every document's link to companies / CSE shares again

The PDFs are in the project's "Anual reports/" folder (settings: annualreports/config/settings.json). Nothing goes to the internet.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from project_paths import REPO_ROOT

from .document import build, sha256_of
from .reading import READER_VERSION, tesseract_path

CONFIG = Path(__file__).resolve().parent / "config" / "settings.json"
logger = logging.getLogger("annualreports")


def load_settings(path: Path = CONFIG) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def folder_of(settings: dict, override: str | None = None) -> Path:
    folder = Path(override or os.environ.get("ANNUALREPORTS_FOLDER") or settings["folder"])
    return folder if folder.is_absolute() else REPO_ROOT / folder


def stored_path(path: Path) -> str:
    """Inside the project folder, the path is kept relative to it, so the database doesn't depend on where the drive is mounted."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def pdfs_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf" and not p.name.startswith("._"))


def _summary(result: dict) -> str:
    d = result["document"]
    pages = f"{d['page_count']} pages"
    extra = [f"{d['pages_ocr']} by OCR" if d["pages_ocr"] else "", f"{d['pages_needs_ocr']} unreadable" if d["pages_needs_ocr"] else ""]
    extra = ", ".join(x for x in extra if x)
    return (f"{d['company'] or '(company not found)'} {d['fiscal_year'] or ''}: {pages}{' (' + extra + ')' if extra else ''}, "
            f"{len(result['sections'])} sections, {len(result['tables'])} tables "
            f"({sum(len(t['rows']) for t in result['tables'])} rows), {len(result['figures'])} figures, {len(result['people'])} people, "
            f"in {d['read_seconds']}s")


def _key_figures(result: dict) -> list[str]:
    """The same pick as the view v_key_figures, for --dry-run."""
    d, best = result["document"], {}
    order = {"income": 0, "balance": 0, "cash_flow": 0, "summary": 1}
    entity = {"group": 0, "bank": 1, None: 2, "company": 3}
    for f in result["figures"]:
        rank = (order.get(f["statement"], 2), f["year"] != d["year"], -(f["year"] or 0), entity.get(f["entity"], 2), f["page_no"])
        if f["metric"] not in best or rank < best[f["metric"]][0]:
            best[f["metric"]] = (rank, f)
    return [f"    {m:<22} {f['value_text']:>16}  {f['unit_text'] or ''}  ({f['column_header'] or 'no heading'}, page {f['page_no']})"
            for m, (_, f) in sorted(best.items())]


def cmd_run(args, settings: dict) -> int:
    started = time.monotonic()
    folder = folder_of(settings, args.folder)
    if args.file:
        f = Path(args.file).expanduser()
        if not f.is_absolute() and not f.exists():
            f = folder / f
        if not f.is_file():
            print(f"No such file: {f}")
            return 2
        files = [f]
    else:
        if not folder.is_dir():
            print(f"The folder isn't there: {folder}\nPut the PDFs there, or set \"folder\" in {CONFIG.relative_to(REPO_ROOT)}.")
            return 2
        files = pdfs_in(folder)
    if settings.get("ocr", {}).get("enabled", True) and not tesseract_path():
        logger.warning("Tesseract isn't installed, so pages without a text layer are marked needs_ocr. To read them: brew install tesseract")

    store = None
    known: dict[str, int] = {}
    if args.dry_run:
        try:
            from .store import ReportStore
            reader = ReportStore("reader")
            known = reader.known()
            reader.close()
        except Exception as e:              # no schema yet, or no database: then every file is new
            logger.info("(dry run: can't see what's saved yet, so every PDF counts as new: %s)", str(e).splitlines()[0][:150])
    else:
        from .store import ReportStore
        store = ReportStore()
        known = store.known()

    counts = {"files": len(files), "read": 0, "unchanged": 0, "same_as_another_file": 0, "failed": 0, "pages": 0, "tables": 0,
              "figures": 0, "people": 0}
    seen_hashes: set[str] = set()
    present = []
    for i, path in enumerate(files, 1):
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        rel = stored_path(path)
        present.append(rel)
        sha = sha256_of(path)
        label = f"[{i}/{len(files)}] {path.name}"
        if (known.get(sha, 0) >= READER_VERSION and not args.again) or sha in seen_hashes:
            what = "same content as another file, read once" if sha in seen_hashes else "already read, unchanged"
            counts["same_as_another_file" if sha in seen_hashes else "unchanged"] += 1
            logger.info("%s: %s", label, what)
            if store:
                store.see_file(rel, sha, path.name, stat.st_size, modified)
            continue
        seen_hashes.add(sha)
        logger.info("%s: reading (%.1f MB)", label, stat.st_size / 1e6)
        try:
            result = build(path, settings, sha, log=lambda m: logger.info(m))
        except Exception as e:              # a broken or locked PDF: say so, keep going with the others
            counts["failed"] += 1
            msg = f"{type(e).__name__}: {str(e)[:300]}"
            logger.error("%s: couldn't read it (%s). It's tried again next run; if it's password-protected, save an unlocked copy.", label, msg)
            if store:
                store.file_failed(rel, sha, path.name, stat.st_size, modified, msg)
            continue
        counts["read"] += 1
        counts["pages"] += result["document"]["page_count"]
        for k in ("tables", "figures", "people"):
            counts[k] += len(result[k])
        if store:
            link = store.link(result["document"]["company"], result["document"].get("link_by_hand") or
                              settings.get("links", {}).get(result["document"]["company"] or "", {}))
            store.save(result, rel, modified, link)
            logger.info("  %s. Link: %s", _summary(result), link["how"])
        else:
            logger.info("  %s (dry run: nothing saved)", _summary(result))
            for line in _key_figures(result):
                logger.info(line)
    if store:
        if not args.file:
            missing = store.mark_missing(present, stored_path(folder) + os.sep)
            if missing:
                counts["missing_now"] = missing
                logger.info("%d file(s) are no longer in the folder; what was read from them stays.", missing)
        store.save_run(time.monotonic() - started, counts, args.file)
        store.close()
    logger.info("Done in %.0fs: %d read, %d unchanged, %d duplicates, %d failed; %d pages, %d tables, %d figures, %d people.",
                time.monotonic() - started, counts["read"], counts["unchanged"], counts["same_as_another_file"], counts["failed"],
                counts["pages"], counts["tables"], counts["figures"], counts["people"])
    return 1 if counts["failed"] and not counts["read"] and not counts["unchanged"] else 0


def _print_rows(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = {c: min(60, max(len(c), *(len(str(r.get(c) if r.get(c) is not None else "")) for r in rows))) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    for r in rows:
        print("  ".join(str(r.get(c) if r.get(c) is not None else "")[:widths[c]].ljust(widths[c]) for c in columns))


def cmd_show(args, store) -> int:
    if args.id:
        doc = store.rows("select * from annualreports.v_documents where id = %s", [args.id])
        if not doc:
            print(f"No document {args.id}. See them all: uv run python -m annualreports show")
            return 2
        d = doc[0]
        print(f"#{d['id']} {d['company']} {d['fiscal_year'] or ''} - {d['title']}")
        print(f"  file: {', '.join(d['paths'] or [d['file_name']])}  ({d['page_count']} pages, {d['pages_ocr']} by OCR, "
              f"{d['pages_needs_ocr']} unreadable, {d['pages_empty']} empty)")
        print(f"  period end: {d['period_end']}   link: {d['company_id'] or '-'} / {d['cse_symbol'] or '-'}  ({d['link_how']})")
        print(f"  {d['sections']} sections, {d['tables']} tables, {d['figures']} figures, {d['people']} people\n")
        print("Key figures (as printed; amount = in currency units):")
        _print_rows(store.rows("""select metric, value_text, amount, currency, column_header, statement, page_no from annualreports.v_key_figures
                                   where document_id = %s order by metric""", [args.id]),
                    ["metric", "value_text", "amount", "currency", "column_header", "statement", "page_no"])
        print("\nPeople:")
        _print_rows(store.rows("""select name, honorific, array_to_string(roles, ' / ') as roles, array_to_string(pages, ',') as pages
                                   from annualreports.people where document_id = %s order by pages[1], name""", [args.id]),
                    ["honorific", "name", "roles", "pages"])
        print("\nSections (first 40):")
        _print_rows(store.rows("""select section_no, level, title, page_no, page_to, length(text) as chars from annualreports.sections
                                   where document_id = %s and level = (select min(level) from annualreports.sections where document_id = %s)
                                   order by section_no limit 40""", [args.id, args.id]),
                    ["section_no", "title", "page_no", "page_to", "chars"])
        return 0
    where, params = "", []
    if args.company:
        where, params = "where company ilike %s", [f"%{args.company}%"]
    _print_rows(store.rows(f"""select id, company, fiscal_year, page_count as pages, tables, figures, people, coalesce(cse_symbol, company_id) as link,
                                      file_name from annualreports.v_documents {where} order by company nulls last, year, file_name limit %s""",
                           params + [args.limit]),
                ["id", "company", "fiscal_year", "pages", "tables", "figures", "people", "link", "file_name"])
    print("\nOne document in full:  uv run python -m annualreports show --id N")
    return 0


def cmd_stats(store) -> int:
    one = store.rows("""select (select count(*) from annualreports.documents) as documents, (select count(*) from annualreports.files) as files,
        (select count(*) from annualreports.files where missing_since is not null) as files_missing,
        (select count(*) from annualreports.files where error is not null) as files_failed,
        (select count(*) from annualreports.pages) as pages, (select count(*) from annualreports.pages where text_source = 'ocr') as pages_ocr,
        (select count(*) from annualreports.pages where text_source in ('needs_ocr', 'ocr_failed')) as pages_unreadable,
        (select count(*) from annualreports.pages where text_source = 'empty') as pages_empty,
        (select coalesce(sum(word_count), 0) from annualreports.pages) as words,
        (select count(*) from annualreports.sections) as sections, (select count(*) from annualreports.tables) as tables,
        (select count(*) from annualreports.table_rows) as table_rows, (select count(*) from annualreports.figures) as figures,
        (select count(*) from annualreports.people) as people,
        (select count(*) from annualreports.documents where company_id is not null or cse_symbol is not null) as linked""")[0]
    for k, v in one.items():
        print(f"  {k:<17} {v:>10,}")
    print("\nPer company:")
    _print_rows(store.rows("""select coalesce(company, '(not found)') as company, count(*) as documents, string_agg(distinct fiscal_year, ', ') as years,
                                      sum(page_count) as pages, sum(tables) as tables, sum(figures) as figures, sum(people) as people
                               from annualreports.v_documents group by 1 order by 1"""),
                ["company", "documents", "years", "pages", "tables", "figures", "people"])
    return 0


def cmd_search(args, store) -> int:
    words = " ".join(args.words)
    rows = store.rows("""select d.company, d.fiscal_year, p.document_id, p.page_no,
            ts_headline('simple', concat_ws(' ', p.text, p.ocr_text), plainto_tsquery('simple', %s), 'MaxWords=25, MinWords=10') as snippet
        from annualreports.pages p join annualreports.documents d on d.id = p.document_id
        where to_tsvector('simple', coalesce(p.text, '') || ' ' || coalesce(p.ocr_text, '')) @@ plainto_tsquery('simple', %s)
        order by d.company, d.year, p.page_no limit %s""", [words, words, args.limit])
    for r in rows:
        print(f"#{r['document_id']} p{r['page_no']:<4} {r['company']} {r['fiscal_year'] or ''}: {' '.join(r['snippet'].split())}")
    if not rows:
        print("No page has all those words.")
    return 0


def cmd_link(settings: dict) -> int:
    from .store import ReportStore
    store = ReportStore()
    try:
        changed = store.relink(settings.get("links", {}))
        n = store.con.execute("select count(*), count(*) filter (where company_id is not null or cse_symbol is not null) from annualreports.documents").fetchone()
    finally:
        store.close()
    for c in changed:
        print(f"#{c['id']} {c['company']}: {c['company_id'] or '-'} / {c['cse_symbol'] or '-'}  ({c['how']})")
    print(f"{n[1]} of {n[0]} documents linked; {len(changed)} changed.")
    return 0


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m annualreports", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="read new or changed PDFs")
    run.add_argument("--file", help="only this PDF (a name in the folder, or a path)")
    run.add_argument("--folder", help="another folder of PDFs")
    run.add_argument("--dry-run", action="store_true", help="read and report, save nothing")
    run.add_argument("--again", action="store_true", help="read even the PDFs that are already saved (their rows are replaced)")
    show = sub.add_parser("show", help="the documents, or one in detail")
    show.add_argument("--id", type=int)
    show.add_argument("--company")
    show.add_argument("--limit", type=int, default=50)
    sub.add_parser("stats", help="how much is saved")
    sub.add_parser("link", help="check the links to companies and CSE shares again")
    search = sub.add_parser("search", help="pages with these words")
    search.add_argument("words", nargs="+")
    search.add_argument("--limit", type=int, default=20)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    settings = load_settings()
    if args.command == "run":
        sys.exit(cmd_run(args, settings))
    if args.command == "link":
        sys.exit(cmd_link(settings))
    from warehouse.postgres import DatabaseError
    from .store import ReportStore
    try:
        store = ReportStore("reader")
    except DatabaseError as e:
        print(e)
        sys.exit(2)
    try:
        sys.exit({"show": lambda: cmd_show(args, store), "stats": lambda: cmd_stats(store), "search": lambda: cmd_search(args, store)}[args.command]())
    finally:
        store.close()
