-- markets: prices over time (markets/). Same tables and columns as data/markets/markets.duckdb.

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
