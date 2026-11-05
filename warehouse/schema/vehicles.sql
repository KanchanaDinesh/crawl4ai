-- vehicles: vehicle ads over time (vehicles/). Same tables and columns as data/vehicles/vehicles.duckdb.

create table if not exists vehicles.ads (
    saved_at   timestamptz,
    site       text,
    rank       integer,
    title      text,
    url        text,
    price      text,
    price_lkr  bigint,
    location   text,
    year       text,
    mileage    text,
    posted     text,
    details    text,
    image      text,
    extra      jsonb
);
create index if not exists ads_site_saved_at_idx on vehicles.ads (site, saved_at);
create index if not exists ads_url_idx on vehicles.ads (url);

create table if not exists vehicles.runs (
    saved_at    timestamptz,
    site        text,
    source_url  text,
    success     boolean,
    ads         integer,
    seconds     double precision,
    error       text
);
create index if not exists runs_saved_at_idx on vehicles.runs (saved_at);

create table if not exists vehicles.meta (
    key    text primary key,
    value  text
);
