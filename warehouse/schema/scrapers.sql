-- scrapers: the older single-file scrapers' output (scrapers/, warehouse/scrapers_db.py).
-- Moved from data/scrapers/scrapers.duckdb on 2026-11-06: one row per saved record, the record as JSON.

create sequence if not exists scrapers.scraper_row_seq;

create table if not exists scrapers.scraper_rows (
    row_id    bigint primary key default nextval('scrapers.scraper_row_seq'),
    saved_at  timestamptz,
    scraper   text,                        -- file name without extension, e.g. news, products_x, cse
    row_no    integer,
    data      jsonb
);
create index if not exists scraper_rows_scraper_saved_at_idx on scrapers.scraper_rows (scraper, saved_at);

create or replace view scrapers.v_news_headlines as
    select saved_at, data->>'name' as site, data->>'title' as title, data->>'url' as url,
           data->>'country' as country, data->>'category' as category
    from scrapers.scraper_rows where scraper = 'news';

create or replace view scrapers.v_products as
    select saved_at, scraper, data->>'title' as title, data->>'url' as url, data->>'price' as price,
           data->>'image' as image
    from scrapers.scraper_rows where scraper like 'products\_%';
