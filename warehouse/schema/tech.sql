-- tech: tech intel pipeline (techintel/). Same tables as data/techintel/techintel.duckdb: each row keeps the
-- whole record as JSON in `data`; typed views (v_candidates, v_profiles, ...) come with step 3.
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
