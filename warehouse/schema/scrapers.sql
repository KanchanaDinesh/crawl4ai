-- scrapers: the older single-file scrapers' output (scrapers/, warehouse/scrapers_db.py).
-- Same table as data/scrapers/scrapers.duckdb: one row per saved record, the record as JSON.

create sequence if not exists scrapers.scraper_row_seq;

create table if not exists scrapers.scraper_rows (
    row_id    bigint primary key default nextval('scrapers.scraper_row_seq'),
    saved_at  timestamptz,
    scraper   text,                        -- file name without extension, e.g. news, products_x, cse
    row_no    integer,
    data      jsonb
);
create index if not exists scraper_rows_scraper_saved_at_idx on scrapers.scraper_rows (scraper, saved_at);
