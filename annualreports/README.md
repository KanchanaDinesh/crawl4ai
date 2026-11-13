# Annual reports

Everything in the PDFs of the project's **`Anual reports/`** folder (the folder's real spelling), saved in PostgreSQL schema `annualreports`: every page's full text, the sections, every table as rows and cells, the key financial figures, the directors, and a link to the company in `companies` and its CSE share in `markets` where the name is the same. Nothing is summarised or left out: the page text is kept as the PDF has it, and the tables, figures and people are extra, with the page they came from. Offline: nothing is sent anywhere.

## Commands

```bash
uv run python -m annualreports run                    # read new or changed PDFs (safe to repeat). Also: ./run_all.sh annualreports
uv run python -m annualreports run --file "Colombo Dockyard PLC 2024.pdf"     # just this one (a name in the folder, or a path)
uv run python -m annualreports run --dry-run          # read and report what it finds, save nothing
uv run python -m annualreports run --again            # read every PDF again (each one's rows are replaced)
uv run python -m annualreports show                   # the documents
uv run python -m annualreports show --id 3            # one: key figures, people, sections
uv run python -m annualreports stats
uv run python -m annualreports search dividend policy # pages with all these words
```

To add reports: put the PDFs in `Anual reports/` and run `run`. A file that's already saved is skipped in a second (its SHA-256 is compared, not its name), so it costs nothing to run every time.

## What's stored

| Table | One row per | Key |
|---|---|---|
| `documents` | distinct PDF (by SHA-256): company, fiscal year, period end, title, PDF metadata, outline, link | `id` |
| `files` | file name in the folder; two names with the same content share a document | `path` |
| `pages` | page: the PDF's own `text`, `ocr_text` if it had none, `text_source`, headings, counts | `document_id, page_no` |
| `sections` | section: title, level, first and last page, its full text | `document_id, section_no` |
| `tables` | table: page, method, title, statement, column headings, unit | `document_id, table_no` |
| `table_rows` | row: `cells` exactly as printed ("(366,495)", "-"), kind header/data/text | `document_id, table_no, row_no` |
| `figures` | key figure in one column: metric, value as printed, number, amount in currency units, column, year, page | |
| `people` | person per document: name, title (Mr., Dr.), roles, pages | `document_id, name_key` |
| `runs` | run: how long, counts | |

Views: `v_documents` (each document with its counts and file names), `v_key_figures` (one value per document and figure: the report's own year, the group before the company, the statements before the highlights), `v_figures`, `v_pages` (`full_text` = text + OCR), `v_cells` (one row per table cell, with `annualreports.num(cell)` as a number), `v_people`.

```sql
select company, fiscal_year, metric, value_text, unit_text, amount, page_no from annualreports.v_key_figures order by company, metric;
select * from annualreports.v_cells where document_id = 2 and page_no = 80;
```

## How it reads a PDF

- **Text:** pypdfium2 (Google's PDFium; Apache-2.0 / BSD-3), in reading order, so two or three columns side by side come out one after the other.
- **Words, fonts, tables:** pdfplumber (MIT, on pdfminer.six, MIT).
- **OCR:** Tesseract (Apache-2.0, `brew install tesseract`, English only on this Mac) for pages whose text layer is empty apart from the running header. The OCR text goes in `ocr_text` with Tesseract's confidence, never into `text`. If Tesseract isn't there, or OCR finds nothing, the page says so (`needs_ocr`, `empty`): nothing is made up.
- **Company and year:** from the line printed on (nearly) every page, "BPPL HOLDINGS PLC | ANNUAL REPORT 2024/25". A name ending in PLC/plc/Limited wins; otherwise the most repeated company name. `fiscal_year` is as printed; `year` is the year it ends in (2024/25 → 2025). Settings can set them for a file: `"documents": {"file name.pdf": {"company": "...", "fiscal_year": "2024"}}`.
- **Sections:** the PDF's own bookmarks if it has them, otherwise the headings (text in a clearly bigger or bold font).
- **Tables, two methods.** `aligned`: rows that end in two or more numbers whose columns line up, the way financial statements are printed, with the lines above them as column headings ("Group 2025 Rs. '000"). `ruled`: pdfplumber's finder, for tables drawn with lines. pdfplumber alone split and merged cells on the statements, so ours comes first and a ruled table on the same spot is dropped.
- **Key figures:** only from pages that say which statement they are (income statement, statement of financial position, cash flows, highlights, five-year summary), and only rows whose label is exactly one of the usual names ("Revenue", "Profit before tax", "Total assets", "Basic earnings per share", ...; `annualreports/figures.py`). Note columns are never values. On highlights pages, only columns headed by a year. A report that words it differently simply has no figure: its table is still saved whole.
- **People:** only on board and management pages: a line that is a title and a name ("Dr. Nimal Silva"), or a name heading with a role under it ("Sir Mark E Tucker" / "Group Chairman"). Roles as printed. The same person written two ways ("Mr. S. D. Amarasinghe", "Dr. Anush Amarasinghe") stays two rows: guessing would be wrong as often as right.
- **Links:** to `companies.companies` and the CSE share in `markets` only when the name is the same letter for letter (spaces, dots and capitals aside: "B P P L Holdings PLC" = "BPPL HOLDINGS PLC"), and only one company has it. `link_how` says how. By hand in settings: `"links": {"HSBC Holdings plc": {"company_id": "wikidata:Q190464"}}`.

## Re-runs

- Unchanged files are skipped. The same PDF under another name is one document with two `files` rows.
- A changed file (same name, new content) is read again; its new document replaces the old one in one transaction, so nothing is ever half saved or doubled.
- A file removed from the folder is marked `missing_since`; what was read from it stays.
- When the reader improves (`READER_VERSION` in `reading.py`), every document is read again on the next run.
- A PDF that can't be opened (broken, password-protected) is noted in `files.error`, and the others are still read.

## Not done (yet)

- No Claude API: better extraction (directors in free text, figures worded differently) could come from the AI step, which is last by Kanchana's decision.
- Pictures and charts aren't saved, only their count per page; a chart's numbers are only there if the PDF prints them as text.
- OCR is English only (the Chinese HSBC report has a text layer, so it didn't need OCR).
