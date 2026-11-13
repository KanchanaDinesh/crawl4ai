-- companies: company directory (companies/). Same tables and columns as data/companies/companies.duckdb.
-- Safe to run again (python -m warehouse migrate runs it when this file changes).

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

-- Which letters a name is written in (the dashboard shows names in English letters only, unless asked).
--   latin  Latin letters, accents included (Nestlé, Société Générale, ÇELİK), or a stray look-alike letter from
--          another script in a long Latin name (one Cyrillic "А" in "...DE BOLSА": at most 1 in 10 letters)
--   mixed  both, e.g. "SAS Institute Japan株式会社", "ALPHA ΕΠΕΝΔΥΤΙΚΗΣ ..."
--   other  no Latin letters: Chinese, Japanese, Korean, Cyrillic, Arabic, Hebrew, Greek, Thai, ...
--   none   no letters at all ("1776", "84.51°")
-- Letters only: digits, punctuation and symbols don't count; fullwidth Ａ-Ｚ counts as Latin.
create or replace function companies.name_script(t text) returns text language sql immutable parallel safe as $$
    select case when t is null then null
                when l = 0 and o = 0 then 'none' when o = 0 then 'latin' when l = 0 then 'other'
                when o * 10 <= l then 'latin' else 'mixed' end
    from (select length(regexp_replace(t, '[^A-Za-zÀ-ÖØ-öø-ʯᴀ-ᶿḀ-ỿⱠ-Ɀ꜠-ꟿꬰ-꭯ﬀ-ﬆＡ-Ｚａ-ｚ]', '', 'g')) as l,
                 length(regexp_replace(t, '[^Ͱ-ϿЀ-ԯ԰-֏֐-׿؀-ࣿऀ-෿฀-࿿က-ჿᄀ-ᇿሀ-᎟Ꭰ-᢯ᤀ-᳿ἀ-῿Ⰰ-ⱟⲀ-ⷿ⺀-鿿ꀀ-꛿ꠀ-꬯ꭰ-꯿가-퟿豈-﫿ﬓ-﷿ﹰ-﻿ｦ-ￜ\U00020000-\U0003FFFF]', '', 'g')) as o) x
$$;

-- The name to show in English letters when the source's name isn't: the English name the source gives (GLEIF's
-- other names in English), else the company's own Latin spelling (GLEIF's preferred transliteration). Never made up.
create or replace function companies.english_name(name text, extra jsonb) returns text language sql immutable parallel safe as $$
    select case when companies.name_script(name) in ('latin', 'none') then null
                when companies.name_script(extra ->> 'english_name') = 'latin' then extra ->> 'english_name'
                when companies.name_script(extra ->> 'latin_name') = 'latin' then extra ->> 'latin_name' end
$$;

-- Kept on every row by PostgreSQL itself (the trigger below), for collectors and people's corrections alike:
--   name_script   null = Latin letters (most names), else 'mixed', 'other' or 'none' (as name_script() says)
--   english_name  the English / Latin name the source gives, only for names that aren't in Latin letters
-- Plain columns and a trigger rather than generated columns: adding a generated column rewrites the whole table
-- (~270 MB, and as much again in PostgreSQL's log), too much for the Mac's nearly full drive. This way only the
-- ~11,000 rows that aren't Latin are written once. Nothing is ever deleted: the dashboard only leaves them out.
alter table companies.companies add column if not exists name_script text;
alter table companies.companies add column if not exists english_name text;

create or replace function companies.set_name_script() returns trigger language plpgsql as $$
begin
    new.name_script := nullif(companies.name_script(new.name), 'latin');
    new.english_name := companies.english_name(new.name, new.extra);
    return new;
end $$;
create or replace trigger companies_name_script before insert or update of name, extra on companies.companies
    for each row execute function companies.set_name_script();

-- Rows saved before the trigger, or whose values a changed name_script() would now work out differently.
-- Reads every name (a few seconds) but writes only the rows that change.
update companies.companies set name_script = nullif(companies.name_script(name), 'latin'),
                               english_name = companies.english_name(name, extra)
where name_script is distinct from nullif(companies.name_script(name), 'latin')
   or english_name is distinct from companies.english_name(name, extra);
create index if not exists companies_name_script_idx on companies.companies (name_script) where name_script is not null;

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

-- companies with their website check (the dashboard's Companies tab and `companies show` read this); what people hid isn't here.
-- New columns go at the end: create or replace view can't move or rename the ones there.
create or replace view companies.v_companies as
    select c.id, c.source, c.source_id, c.name, c.country, c.website, c.domain, c.industry, c.first_seen, c.last_seen,
           coalesce(s.status, case when c.domain is null then 'no website' else 'not read' end) as site_status,
           s.checked_at, s.social,
           case when jsonb_typeof(s.people) = 'array' then jsonb_array_length(s.people) end as people,
           coalesce(s.employees, s.employees_hint) as employees,
           s.social ->> 'linkedin' as linkedin, s.legal_name, s.founded,
           coalesce(c.name_script, 'latin') as name_script,
           -- the name in English letters: the name itself, or the English name the source gives; null = none known,
           -- so the dashboard's Companies tab leaves the company out unless "names in other scripts" is switched on
           coalesce(c.english_name, case when c.name_script is null or c.name_script = 'none' then c.name end) as name_en
    from companies.companies c left join companies.sites s on s.domain = c.domain
    where not exists (select 1 from audit.hidden_records h where h.dataset = 'companies.companies' and h.record = c.id);
