-- audit: people's corrections to collected data, kept next to the data so collectors can respect them (rule A:
-- docs/user-platform-plan.md). Not one of the data schemas: the catalog doesn't list it and the reader login can't read it.
-- Owned by the collectors' login; warehouse/postgres.py gives the web app what it needs. Safe to run again.

create schema if not exists audit;

create table if not exists audit.field_history (      -- every change to an editable field: never changed, never removed
    id          bigserial primary key,
    dataset     text not null,                        -- the table, e.g. companies.companies
    record      text not null,                        -- the row's key
    field       text not null,
    old_value   jsonb,
    new_value   jsonb,
    changed_by  text not null,                        -- a user name, or collector:<tool>
    changed_at  timestamptz not null default now(),
    origin      text not null,                        -- user, bulk, linked, restore, review, collector
    note        text
);
create index if not exists field_history_record_idx on audit.field_history (dataset, record, id desc);
create index if not exists field_history_dataset_idx on audit.field_history (dataset, id desc);

create table if not exists audit.field_source (       -- one row per corrected field: what protects it from the collector
    dataset       text not null,
    record        text not null,
    field         text not null,
    source_value  jsonb,                              -- the last value the source gave
    user_value    jsonb,                              -- the correction
    rule          text not null default 'A',          -- A newest change wins, B the person wins, C flag for review
    pending_value jsonb,                              -- rule C: what the source says now, waiting for a person
    edited_by     text,
    edited_at     timestamptz,
    primary key (dataset, record, field)
);

create table if not exists audit.hidden_records (     -- hidden, not deleted: stays hidden when a collector sees it again
    dataset    text not null,
    record     text not null,
    label      text,
    hidden_by  text,
    hidden_at  timestamptz not null default now(),
    primary key (dataset, record)
);

create or replace function audit.refuse_change() returns trigger language plpgsql as $$
begin
    raise exception 'audit.field_history is append-only';
end $$;
drop trigger if exists field_history_append_only on audit.field_history;
create trigger field_history_append_only before update or delete or truncate on audit.field_history
    for each statement execute function audit.refuse_change();
