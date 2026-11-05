-- news: world news headlines (worldnews/). Same tables and columns as data/worldnews/worldnews.duckdb.

create table if not exists news.news_items (
    url            text primary key,
    id             text,
    title          text,
    summary        text,
    published_at   timestamptz,
    source         text,
    group_code     text,                   -- US, IN, LK, ...
    topics         text[],
    tech_terms     text[],
    image          text,
    first_seen_at  timestamptz,
    last_seen_at   timestamptz,
    times_seen     integer default 0
);
create index if not exists news_items_published_at_idx on news.news_items (published_at);
create index if not exists news_items_first_seen_at_idx on news.news_items (first_seen_at);
create index if not exists news_items_source_idx on news.news_items (source);
create index if not exists news_items_group_code_idx on news.news_items (group_code);
create index if not exists news_items_title_trgm_idx on news.news_items using gin (lower(title) gin_trgm_ops);

create table if not exists news.news_runs (
    run_at         timestamptz primary key,
    finished_at    timestamptz,
    item_limit     integer,
    sources        integer,
    sources_ok     integer,
    headlines      integer,
    new_headlines  integer
);

create table if not exists news.news_run_items (   -- which headlines each run saw, in which order
    run_at  timestamptz,
    url     text,
    source  text,
    rank    integer
);
create index if not exists news_run_items_run_at_idx on news.news_run_items (run_at);
create index if not exists news_run_items_url_idx on news.news_run_items (url);

create table if not exists news.news_source_runs (
    run_at      timestamptz,
    source      text,
    group_code  text,
    status      text,
    via         text,
    feed_url    text,
    error       text,
    headlines   integer
);
create index if not exists news_source_runs_run_at_idx on news.news_source_runs (run_at);

create table if not exists news.feed_cache (
    source      text primary key,
    feed_url    text,
    updated_at  timestamptz
);
