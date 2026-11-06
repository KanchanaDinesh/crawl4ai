-- articles: articles and blogs from the most valuable companies' own sites, tagged by service (articles/).

create table if not exists articles.companies (
    id            text primary key,         -- Wikidata's id, or x:<name> for one added by hand
    name          text not null,
    countries     text[] not null,          -- where it's listed: Rio Tinto is in both GB and AU
    indexes       text[] not null,          -- S&P 100, FTSE 100, ... or "added by hand"
    in_list       boolean not null default true,    -- false once it has left its index; its articles stay
    website       text,
    ticker        text,
    sector        text,
    market_cap    double precision,         -- Wikidata's newest value: for information, patchy and sometimes old
    cap_currency  text,
    cap_date      date,
    source        text not null,            -- wikipedia or settings
    first_seen    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    feeds_checked_at timestamptz,           -- when its feeds were last looked for
    feeds_note    text
);

create table if not exists articles.feeds (
    url             text primary key,
    company_id      text not null references articles.companies (id),
    found_how       text,                   -- known, homepage, guess
    title           text,
    last_checked    timestamptz,
    last_ok         timestamptz,
    items_last      integer,
    error           text,
    refused_until   timestamptz             -- it said 401/403/429: left alone until then
);
create index if not exists feeds_company_idx on articles.feeds (company_id);

create table if not exists articles.articles (
    url           text primary key,
    company_id    text not null references articles.companies (id),
    feed_url      text,
    title         text not null,
    summary       text,
    text          text,                     -- kept for our own analysis, never republished
    text_status   text not null,            -- feed, page, short, not_allowed, refused, failed
    word_count    integer not null default 0,
    author        text,
    published_at  timestamptz,              -- the source's own date; empty if it gives none, never a guess
    date_from     text,                     -- feed, page or none
    tags          text[] not null,
    first_seen_at timestamptz not null default now()
);
create index if not exists articles_published_idx on articles.articles (published_at desc nulls last);
create index if not exists articles_company_idx on articles.articles (company_id);
create index if not exists articles_tags_idx on articles.articles using gin (tags);

create table if not exists articles.runs (
    run_at     timestamptz not null default now(),
    step       text not null,
    seconds    double precision,
    counts     jsonb,
    note       text
);

-- one flat row per article, for the grid, exports, reports and later analysis
create or replace view articles.v_articles as
    select a.url, a.title, c.name as company, c.countries, a.tags, a.published_at, a.date_from, a.summary, a.text, a.word_count, a.text_status,
           a.author, c.sector, c.indexes, a.feed_url, a.first_seen_at, a.company_id
    from articles.articles a join articles.companies c on c.id = a.company_id;

create or replace view articles.v_companies as
    select c.id, c.name, c.countries, c.indexes, c.sector, c.ticker, c.website, c.market_cap, c.cap_currency, c.cap_date, c.in_list, c.source,
           (select count(*) from articles.feeds f where f.company_id = c.id and f.last_ok is not null) as working_feeds,
           (select count(*) from articles.articles a where a.company_id = c.id) as articles,
           (select max(a.published_at) from articles.articles a where a.company_id = c.id) as latest_article,
           c.feeds_note, c.feeds_checked_at
    from articles.companies c;
