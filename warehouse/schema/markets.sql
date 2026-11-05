-- markets: prices over time (markets/). Moved from data/markets/markets.duckdb on 2026-11-06.

create table if not exists markets.quotes (
    run_at       timestamptz,
    asset_class  text,                     -- stock_us, stock_lk, index, metal, fx, crypto
    symbol       text,
    name         text,
    price        double precision,
    prev_close   double precision,
    change       double precision,
    change_pct   double precision,
    currency     text,
    volume       double precision,
    market_cap   double precision,
    rank         integer,
    high         double precision,
    low          double precision,
    as_of        timestamptz,
    source       text,
    extra        jsonb
);
create index if not exists quotes_symbol_run_at_idx on markets.quotes (symbol, run_at);
create index if not exists quotes_run_at_idx on markets.quotes (run_at);

create table if not exists markets.runs (
    run_at       timestamptz primary key,
    finished_at  timestamptz,
    quotes       integer,
    errors       jsonb
);

create table if not exists markets.source_runs (
    run_at   timestamptz,
    source   text,
    grp      text,
    ok       boolean,
    count    integer,
    error    text,
    seconds  double precision
);
create index if not exists source_runs_run_at_idx on markets.source_runs (run_at);

-- the latest quote of each instrument: one index walk per instrument
create index if not exists quotes_latest_idx on markets.quotes (asset_class, symbol, run_at desc);

create or replace view markets.v_latest as
    select distinct on (asset_class, symbol) *
    from markets.quotes
    order by asset_class, symbol, run_at desc;

-- the last quote of each day (UTC) per instrument, for charts over weeks and months
create or replace view markets.v_daily as
    select distinct on (asset_class, symbol, (run_at at time zone 'UTC')::date)
           (run_at at time zone 'UTC')::date as day, *
    from markets.quotes
    order by asset_class, symbol, (run_at at time zone 'UTC')::date, run_at desc;
