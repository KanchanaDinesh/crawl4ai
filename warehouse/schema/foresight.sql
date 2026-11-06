-- foresight: scenarios with odds, saved before the outcome is known, and scored afterwards (foresight/, docs/foresight-plan.md).

create table if not exists foresight.forecasts (
    id              bigserial primary key,
    made_at         timestamptz not null default now(),
    section         text not null,          -- markets, news, ...
    grp             text not null,          -- what it's scored with: stock_lk, crypto, ...
    subject         text not null,          -- crypto:BTC
    subject_name    text,
    question        text not null,          -- price_7d
    as_of           date not null,          -- the newest data it used
    check_on        date not null,
    start_value     double precision,
    scenarios       jsonb not null,         -- [{"key", "label", "probability", "normal"}], "normal" is the simple guess
    reasons         jsonb not null,         -- sentences, in plain words
    signals         jsonb not null,         -- the numbers behind them
    method          text not null,
    method_version  integer not null,
    cases           integer not null,       -- past cases the odds came from
    separate_weeks  integer not null,       -- how many separate weeks those cases came from
    unique (section, subject, question, as_of)
);
create index if not exists forecasts_due_idx on foresight.forecasts (check_on);

create table if not exists foresight.outcomes (
    forecast_id   bigint primary key references foresight.forecasts (id),
    checked_at    timestamptz not null default now(),
    end_value     double precision,
    end_day       date,
    move_pct      double precision,
    actual        text not null,            -- the scenario that happened
    score         double precision not null,    -- Brier score: 0 is perfect, lower is better
    normal_score  double precision not null    -- the same for the simple guess
);

-- a test on past data: useful to learn if a method has any value, and kept apart from the live record
create table if not exists foresight.backtests (
    id              bigserial primary key,
    run_at          timestamptz not null default now(),
    section         text not null,
    grp             text not null,
    question        text not null,
    method          text not null,
    method_version  integer not null,
    first_day       date,
    last_day        date,
    cases           integer not null,
    weeks           integer not null,
    score           double precision,
    normal_score    double precision,
    skill           double precision,       -- 1 - score / normal_score: above 0 beats the simple guess
    skill_low       double precision,       -- 90% range, resampling whole weeks
    skill_high      double precision,
    verdict         text not null,          -- better, worse, no_difference, too_little
    details         jsonb
);

create table if not exists foresight.runs (
    run_at       timestamptz not null default now(),
    section      text not null,
    made         integer not null,
    checked      integer not null,
    not_made     jsonb,
    note         text
);

-- Rule 1 of the plan: a forecast can't be changed or removed after it's saved, including the embarrassing ones.
create or replace function foresight.refuse_change() returns trigger language plpgsql as $$
begin
    raise exception 'foresight.% is append-only: a saved forecast or outcome is never changed or removed', tg_table_name;
end $$;
create or replace trigger forecasts_append_only before update or delete on foresight.forecasts
    for each row execute function foresight.refuse_change();
create or replace trigger forecasts_no_truncate before truncate on foresight.forecasts
    for each statement execute function foresight.refuse_change();
create or replace trigger outcomes_append_only before update or delete on foresight.outcomes
    for each row execute function foresight.refuse_change();
create or replace trigger outcomes_no_truncate before truncate on foresight.outcomes
    for each statement execute function foresight.refuse_change();

-- the live track record: only forecasts saved before their outcome, never tests on the past
create or replace view foresight.v_track_record as
    select f.section, f.grp, f.question, f.method, f.method_version, count(*) as checked,
           avg(o.score) as score, avg(o.normal_score) as normal_score,
           case when avg(o.normal_score) > 0 then 1 - avg(o.score) / avg(o.normal_score) end as skill
    from foresight.forecasts f join foresight.outcomes o on o.forecast_id = f.id
    group by f.section, f.grp, f.question, f.method, f.method_version;
