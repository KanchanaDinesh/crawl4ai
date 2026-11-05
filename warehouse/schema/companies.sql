-- companies: company directory (companies/). Same tables and columns as data/companies/companies.duckdb.
-- Safe to run again (python -m warehouse migrate). Views come with step 3 (docs/postgres-migration.md).

create table if not exists companies.companies (
    id          text primary key,          -- "<source>:<source id>", e.g. wikidata:Q95, gleif:<LEI>, cse:JKH.N0000
    source      text,
    source_id   text,
    name        text,
    country     text,                      -- ISO code (GB, not UK)
    website     text,
    domain      text,
    industry    text,
    extra       jsonb,                     -- the source's own fields
    first_seen  timestamptz,
    last_seen   timestamptz
);
create index if not exists companies_country_idx on companies.companies (country);
create index if not exists companies_source_idx on companies.companies (source);
create index if not exists companies_domain_idx on companies.companies (domain);
create index if not exists companies_name_trgm_idx on companies.companies using gin (lower(name) gin_trgm_ops);

create table if not exists companies.sites (     -- the latest website check, one row per domain
    domain          text primary key,
    url             text,
    final_url       text,
    checked_at      timestamptz,
    status          text,                  -- ok, unreachable, not_allowed
    error           text,
    name            text,
    legal_name      text,
    founded         text,
    employees       text,
    employees_hint  text,
    address         text,
    people          jsonb,
    emails          jsonb,
    phones          jsonb,
    social          jsonb,
    linkedin_people jsonb,
    pages           jsonb
);
create index if not exists sites_status_idx on companies.sites (status);
create index if not exists sites_checked_at_idx on companies.sites (checked_at);

create table if not exists companies.refusals (  -- websites that answered 401/403/429: not results
    domain      text primary key,
    refused_at  timestamptz,
    error       text
);

create table if not exists companies.cursors (   -- where each source stopped, per country
    source      text,
    key         text,
    value       jsonb,
    updated_at  timestamptz,
    primary key (source, key)
);

create table if not exists companies.runs (
    started_at  timestamptz,
    command     text,
    seconds     double precision,
    summary     jsonb
);
create index if not exists runs_started_at_idx on companies.runs (started_at);
