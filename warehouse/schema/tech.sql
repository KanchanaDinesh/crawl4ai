-- tech: tech intel pipeline (techintel/). Moved from data/techintel/techintel.duckdb on 2026-11-06: each row keeps
-- the whole record as JSON in `data`, and typed views (v_candidates, v_profiles, ...) make it easy to query.
-- created_at was a plain TIMESTAMP in DuckDB (the Mac's local time); here it is timestamptz.

create sequence if not exists tech.row_seq;      -- one sequence for all tables, as in DuckDB

create table if not exists tech.candidates (
    row_id bigint primary key default nextval('tech.row_seq'), key text,
    created_at timestamptz default now(), data jsonb);
create table if not exists tech.qualifications (
    row_id bigint primary key default nextval('tech.row_seq'), key text,
    created_at timestamptz default now(), data jsonb);
create table if not exists tech.registrations (
    row_id bigint primary key default nextval('tech.row_seq'), key text,
    created_at timestamptz default now(), data jsonb);
create table if not exists tech.profiles (
    row_id bigint primary key default nextval('tech.row_seq'), key text,
    created_at timestamptz default now(), data jsonb);
create table if not exists tech.changes (
    row_id bigint primary key default nextval('tech.row_seq'), key text,
    created_at timestamptz default now(), data jsonb);
create table if not exists tech.companies (
    row_id bigint primary key default nextval('tech.row_seq'), key text,
    created_at timestamptz default now(), data jsonb);
create table if not exists tech.runs (
    row_id bigint primary key default nextval('tech.row_seq'), key text,
    created_at timestamptz default now(), data jsonb);

create index if not exists candidates_key_idx on tech.candidates (key);
create index if not exists qualifications_key_idx on tech.qualifications (key);
create index if not exists registrations_key_idx on tech.registrations (key);
create index if not exists profiles_key_idx on tech.profiles (key);
create index if not exists changes_key_idx on tech.changes (key);
create index if not exists companies_key_idx on tech.companies (key);
create index if not exists runs_created_at_idx on tech.runs (created_at);

-- Safe casts for values inside the JSON records (DuckDB's try_cast): NULL instead of an error for a bad value.
create or replace function tech.try_ts(t text) returns timestamptz language sql stable as
    $$ select case when pg_input_is_valid(t, 'timestamptz') then t::timestamptz end $$;
create or replace function tech.try_num(t text) returns float8 language sql immutable as
    $$ select case when pg_input_is_valid(t, 'float8') then t::float8 end $$;
create or replace function tech.try_int(t text) returns integer language sql immutable as
    $$ select case when pg_input_is_valid(t, 'integer') then t::integer end $$;

-- typed views over the JSON records; "latest" = the newest row per domain (highest row_id)
create index if not exists qualifications_key_row_idx on tech.qualifications (key, row_id desc);
create index if not exists registrations_key_row_idx on tech.registrations (key, row_id desc);
create index if not exists profiles_key_row_idx on tech.profiles (key, row_id desc);
create index if not exists companies_key_row_idx on tech.companies (key, row_id desc);

create or replace view tech.v_candidates as
    select row_id, key as domain, data->>'source' as source, data->>'url' as url,
           tech.try_ts(data->>'discovered_at') as discovered_at,
           tech.try_ts(data->>'signal_at') as signal_at, data->'detail' as detail
    from tech.candidates;

create or replace view tech.v_qualifications as
    select distinct on (key) key as domain, data->>'status' as status, data->>'reason' as reason,
           data->>'category' as category, tech.try_num(data->>'score') as score, data->>'title' as title,
           data->>'final_url' as final_url, tech.try_ts(data->>'checked_at') as checked_at
    from tech.qualifications order by key, row_id desc;

create or replace view tech.v_registrations as
    select distinct on (key) key as domain, tech.try_ts(data->>'registered_at') as registered_at,
           data->>'registrar' as registrar, data->>'error' as error
    from tech.registrations order by key, row_id desc;

create or replace view tech.v_profiles as
    select distinct on (key) key as domain, data->>'status' as status, data->>'name' as name,
           data->>'description' as description, data->>'category' as category, data->'tech_stack' as tech_stack,
           data->'signals' as signals, data->'keywords' as keywords, tech.try_ts(data->>'profiled_at') as profiled_at
    from tech.profiles where data->>'status' = 'ok' order by key, row_id desc;

create or replace view tech.v_changes as
    select key as domain, data->>'change_type' as change_type, data->>'field' as field,
           data->'before' as before, data->'after' as after, tech.try_ts(data->>'detected_at') as detected_at
    from tech.changes;

create or replace view tech.v_companies as
    select distinct on (key) key as domain, data->>'name' as name, data->'social'->>'linkedin' as linkedin,
           data->'social'->>'facebook' as facebook, data->'social'->>'x' as x,
           data->'social'->>'instagram' as instagram, data->'social'->>'github' as github,
           data->'social' as social, data->'people' as people,
           coalesce(data->>'employees', data->>'employees_hint') as employees, data->>'founded' as founded,
           data->>'legal_name' as legal_name, data->>'address' as address, data->'emails' as emails,
           data->'phones' as phones, data->'linkedin_people' as linkedin_people,
           tech.try_int(data->'github'->>'public_repos') as github_repos,
           tech.try_int(data->'github'->>'followers') as github_followers,
           tech.try_ts(data->'github'->>'created_at') as github_since,
           tech.try_ts(data->>'checked_at') as checked_at
    from tech.companies where data->>'status' = 'ok' order by key, row_id desc;
