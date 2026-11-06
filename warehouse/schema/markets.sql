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

-- one close per day per instrument, from public sources (markets/closes.py). Never from our own runs: a run's price can
-- be from the middle of a trading day
create table if not exists markets.daily_closes (
    asset_class  text not null,
    symbol       text not null,
    day          date not null,
    close        double precision not null,
    source       text not null,
    fetched_at   timestamptz not null default now(),
    primary key (asset_class, symbol, day)
);

-- how far each instrument's closes are complete, and its id at the source; lets a stopped run continue
create table if not exists markets.history_sync (
    asset_class  text not null,
    symbol       text not null,
    source       text,
    source_id    text,
    synced_day   date,
    error        text,
    fetched_at   timestamptz,
    primary key (asset_class, symbol)
);

-- when a source last answered 401/402/403/429: it's left alone for a while
create table if not exists markets.history_refusals (
    source      text primary key,
    refused_at  timestamptz not null,
    reason      text
);

-- the latest Stochastic RSI per instrument. A table written by python, not a view: Wilder's RSI carries each day's average
-- into the next, which SQL can only do with a slow recursive query, and every page and the grid read this many times
-- for each time the closes change. All values are 0-100
create table if not exists markets.stoch_rsi (
    asset_class    text not null,
    symbol         text not null,
    name           text,
    stoch_rsi      double precision,
    pct_k          double precision,
    pct_d          double precision,
    zone           text,                 -- overbought (%K above 80), oversold (below 20), middle
    last_close     double precision,
    last_day       date,
    closes_used    integer not null,
    closes_needed  integer not null,
    status         text not null,        -- ok, short (not enough history), flat (RSI didn't move)
    message        text,                 -- why there's no value
    source         text,
    computed_at    timestamptz not null default now(),
    primary key (asset_class, symbol)
);
