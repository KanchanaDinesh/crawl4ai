-- annualreports: everything in the PDFs of the project's "Anual reports/" folder (annualreports/). One document per distinct file
-- (by SHA-256): the same PDF under two names is read once. Re-reading a document replaces all its rows in one transaction.

create table if not exists annualreports.documents (
    id              bigserial primary key,
    sha256          text not null unique,
    file_name       text not null,              -- the name it was first read under; every name is in files
    file_size       bigint,
    page_count      integer not null,
    title           text,                       -- the PDF's own title, or "<company> Annual Report <year>"
    company         text,                       -- as printed ("BPPL HOLDINGS PLC")
    company_from    text,                       -- running header, most repeated company name, settings
    fiscal_year     text,                       -- as printed: 2024/25, 2024
    year            integer,                    -- the year the period ends in: 2024/25 -> 2025
    period_end      date,                       -- the most common "year ended 31 March 2025" / "as at ..." date
    language        text,                       -- en or zh (by the letters in it)
    pdf_meta        jsonb,                      -- Title, Author, Creator, Producer, dates
    outline         jsonb,                      -- the PDF's own bookmarks, if any
    running_headers text[],                     -- lines repeated at the top or bottom of most pages (digits as #)
    company_id      text,                       -- companies.companies.id, only when the match is certain (see link_how)
    cse_symbol      text,                       -- the CSE voting share, e.g. BPPL.N0000
    cse_symbols     text[],                     -- every CSE share line of the company
    link_how        text,                       -- how the link was made, or why there's none
    pages_text      integer,                    -- pages with a text layer
    pages_ocr       integer,                    -- pages with no text layer, read by OCR
    pages_empty     integer,                    -- pages with nothing to read (blank, or only a picture)
    pages_needs_ocr integer,                    -- pages with no text layer that couldn't be read: no OCR tool, or it failed
    reader_version  integer not null,           -- a newer reader reads the document again
    read_seconds    double precision,
    read_at         timestamptz not null default now()
);
create index if not exists documents_company_idx on annualreports.documents (company, year);

create table if not exists annualreports.files (
    path          text primary key,             -- relative to the project folder when inside it
    sha256        text not null,
    file_name     text not null,
    file_size     bigint,
    modified_at   timestamptz,
    first_seen    timestamptz not null default now(),
    last_seen     timestamptz not null default now(),
    missing_since timestamptz,                  -- no longer in the folder; its document stays
    error         text                          -- the last reason it couldn't be read (tried again every run)
);
create index if not exists files_sha_idx on annualreports.files (sha256);

create table if not exists annualreports.pages (
    document_id    bigint not null references annualreports.documents (id) on delete cascade,
    page_no        integer not null,            -- 1 = the PDF's first page, not the number printed on it
    width          double precision,
    height         double precision,
    text           text not null,               -- the PDF's text layer, as it is, in reading order
    text_source    text not null,               -- pdf, ocr, empty, needs_ocr, ocr_failed
    ocr_text       text,                        -- for pages without a text layer: what OCR read (never mixed into text)
    ocr_confidence double precision,            -- Tesseract's average word confidence, 0-100
    note           text,
    char_count     integer not null,
    word_count     integer not null,
    image_count    integer not null default 0,
    headings       text[],                      -- text in a bigger or bold font, top to bottom
    primary key (document_id, page_no)
);
create index if not exists pages_text_search_idx on annualreports.pages
    using gin (to_tsvector('simple', coalesce(text, '') || ' ' || coalesce(ocr_text, '')));

create table if not exists annualreports.sections (
    document_id  bigint not null references annualreports.documents (id) on delete cascade,
    section_no   integer not null,
    title        text not null,
    level        integer not null,              -- 1 = top
    page_no      integer not null,              -- where it starts
    page_to      integer not null,              -- where it ends
    source       text not null,                 -- outline (the PDF's bookmarks) or headings (found by font size)
    text         text not null,                 -- from its heading to the next one's
    primary key (document_id, section_no)
);

create table if not exists annualreports.tables (
    document_id  bigint not null references annualreports.documents (id) on delete cascade,
    table_no     integer not null,              -- 1, 2, ... in page order through the document
    page_no      integer not null,
    method       text not null,                 -- aligned (columns of numbers, no lines) or ruled (drawn with lines)
    title        text,                          -- the nearest text above it
    statement    text,                          -- income, balance, cash_flow, equity, summary, highlights, or null
    columns      text[],                        -- each value column's heading ("Group 2025 Rs. '000"); the first cell of a row is its label
    columns_from integer,                       -- headings taken from this table: it continues it (below it, or on the next page)
    unit_text    text,                          -- "Rs. '000", "$m", as printed
    currency     text,                          -- LKR, USD, GBP
    multiplier   double precision,              -- 1000 for Rs. '000
    bbox         double precision[],            -- x0, top, x1, bottom in PDF points
    n_rows       integer not null,
    n_cols       integer not null,
    primary key (document_id, table_no)
);
alter table annualreports.tables add column if not exists columns_from integer;
create index if not exists tables_page_idx on annualreports.tables (document_id, page_no);

create table if not exists annualreports.table_rows (
    document_id  bigint not null,
    table_no     integer not null,
    row_no       integer not null,              -- 0 = top
    kind         text not null,                 -- header, data (has a number), text (a label only: a subheading)
    cells        text[] not null,               -- exactly as printed: "(366,495)", "-"
    primary key (document_id, table_no, row_no),
    foreign key (document_id, table_no) references annualreports.tables (document_id, table_no) on delete cascade
);

create table if not exists annualreports.figures (
    document_id   bigint not null references annualreports.documents (id) on delete cascade,
    metric        text not null,                -- revenue, profit_before_tax, total_assets, eps_basic, ... (annualreports/figures.py)
    statement     text not null,                -- the statement or page it's from
    label         text not null,                -- the row's label as printed
    column_no     integer not null,
    column_header text,                         -- "Group 2025 Rs. '000"
    year          integer,                      -- from the column heading
    entity        text,                         -- group, company, bank, from the column heading
    value_text    text not null,                -- as printed
    value         double precision not null,    -- the number printed: (12,407) -> -12407
    amount        double precision,             -- in currency units: value x the table's unit (per-share figures as printed)
    currency      text,
    unit_text     text,
    page_no       integer not null,
    table_no      integer not null,
    row_no        integer not null
);
create index if not exists figures_doc_idx on annualreports.figures (document_id, metric);

create table if not exists annualreports.people (
    document_id  bigint not null references annualreports.documents (id) on delete cascade,
    name_key     text not null,                 -- capitals only, to count one person once per document
    name         text not null,                 -- as printed
    honorific    text,                          -- Mr., Dr., Sir ...
    roles        text[] not null,               -- every role printed with the name
    pages        integer[] not null,
    found_by     text not null,                 -- title (Mr./Dr. ... line) or heading (a name heading with a role under it)
    primary key (document_id, name_key)
);

create table if not exists annualreports.runs (
    run_at   timestamptz not null default now(),
    seconds  double precision,
    counts   jsonb,
    note     text
);

-- a cell's number: '(1,234)' -> -1234, '4.5%' -> 4.5, '-' and words -> null
create or replace function annualreports.num(t text) returns double precision language sql immutable as $$
    select case when btrim(t) ~ '^[(\[]?[-–−]?[$£€]?[0-9][0-9,]*(\.[0-9]+)?[)\]]?%?\*?$'
                then (case when btrim(t) ~ '^[(\[]|^[-–−]' then -1 else 1 end) * regexp_replace(t, '[^0-9.]', '', 'g')::double precision end
$$;

create or replace view annualreports.v_documents as
    select d.id, d.company, d.fiscal_year, d.year, d.period_end, d.title, d.file_name, d.page_count, d.language,
           d.company_id, d.cse_symbol, d.link_how,
           (select count(*) from annualreports.tables t where t.document_id = d.id) as tables,
           (select count(*) from annualreports.figures f where f.document_id = d.id) as figures,
           (select count(*) from annualreports.people p where p.document_id = d.id) as people,
           (select count(*) from annualreports.sections s where s.document_id = d.id) as sections,
           d.pages_text, d.pages_ocr, d.pages_empty, d.pages_needs_ocr,
           (select array_agg(f.path order by f.path) from annualreports.files f where f.sha256 = d.sha256) as paths,
           d.file_size, d.sha256, d.read_at, d.read_seconds, d.reader_version
    from annualreports.documents d;

create or replace view annualreports.v_pages as
    select d.company, d.fiscal_year, p.document_id, p.page_no, p.text_source,
           concat_ws(E'\n', nullif(p.text, ''), p.ocr_text) as full_text, p.headings, p.word_count, p.image_count, p.ocr_confidence, p.note
    from annualreports.pages p join annualreports.documents d on d.id = p.document_id;

create or replace view annualreports.v_cells as
    select r.document_id, t.page_no, r.table_no, t.title as table_title, r.row_no, r.kind, r.cells[1] as row_label,
           c.col_no - 1 as col_no, case when c.col_no > 1 then t.columns[c.col_no - 1] end as column_header,
           c.cell as value_text, annualreports.num(c.cell) as value, d.company, d.fiscal_year
    from annualreports.table_rows r
    join annualreports.tables t on t.document_id = r.document_id and t.table_no = r.table_no
    join annualreports.documents d on d.id = r.document_id
    cross join lateral unnest(r.cells) with ordinality as c(cell, col_no)
    where c.col_no > 1;

create or replace view annualreports.v_sections as
    select d.company, d.fiscal_year, s.section_no, s.title, s.level, s.page_no, s.page_to, s.source, s.text, s.document_id
    from annualreports.sections s join annualreports.documents d on d.id = s.document_id;

create or replace view annualreports.v_tables as
    select d.company, d.fiscal_year, t.page_no, t.table_no, t.title, t.statement, t.method, t.columns, t.unit_text, t.currency,
           t.n_rows, t.n_cols, t.columns_from, t.document_id
    from annualreports.tables t join annualreports.documents d on d.id = t.document_id;

create or replace view annualreports.v_figures as
    select d.company, d.fiscal_year, d.company_id, d.cse_symbol, f.*
    from annualreports.figures f join annualreports.documents d on d.id = f.document_id;

-- one value per document and figure: from the statements before the highlights, the report's own year, the group before the company,
-- the first page it's on
create or replace view annualreports.v_key_figures as
    select distinct on (f.document_id, f.metric)
           d.company, d.fiscal_year, d.year as report_year, f.metric, f.value, f.amount, f.currency, f.unit_text, f.year, f.entity,
           f.label, f.column_header, f.value_text, f.statement, f.page_no, f.document_id, d.company_id, d.cse_symbol
    from annualreports.figures f join annualreports.documents d on d.id = f.document_id
    order by f.document_id, f.metric,
             case f.statement when 'income' then 0 when 'balance' then 0 when 'cash_flow' then 0 when 'summary' then 1 else 2 end,
             (f.year = d.year) desc nulls last, f.year desc nulls last,
             case f.entity when 'group' then 0 when 'bank' then 1 when 'company' then 3 else 2 end,
             f.page_no, f.table_no, f.row_no, f.column_no;

create or replace view annualreports.v_people as
    select d.company, d.fiscal_year, p.name, p.honorific, p.roles, p.pages, p.found_by, p.document_id, d.company_id
    from annualreports.people p join annualreports.documents d on d.id = p.document_id;
