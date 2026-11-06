"""The tables behind people's own data, in schema `userdata`. Plain SQL rather than Django models: the fields of a row
aren't known in advance, so they're kept in JSONB, and everything reads them through the catalog anyway."""

from django.db import migrations

TABLES = """
create table if not exists userdata.records (       -- the rows of people's own tables
    id          bigserial primary key,
    dataset_id  bigint not null,
    data        jsonb not null default '{}',
    created_at  timestamptz not null default now(),
    created_by  text,
    updated_at  timestamptz,
    updated_by  text
);
create index if not exists records_dataset_idx on userdata.records (dataset_id, id);

create table if not exists userdata.extras (        -- people's own fields on rows of collected datasets
    dataset_id  bigint not null,
    record      text not null,
    data        jsonb not null default '{}',
    updated_at  timestamptz,
    updated_by  text,
    primary key (dataset_id, record)
);

create table if not exists userdata.links (         -- a row linked to a row, through a link field
    id           bigserial primary key,
    field_id     bigint not null,
    from_record  text not null,
    to_record    text not null,
    from_label   text,                              -- the rows' names when linked: a list of links stays fast
    to_label     text,                              -- even over a view of thousands of rows; own tables keep them current
    created_by   text,
    created_at   timestamptz not null default now(),
    unique (field_id, from_record, to_record)
);
create index if not exists links_to_idx on userdata.links (field_id, to_record);
"""


def create(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        cur.execute("select to_regnamespace('userdata') is not null")
        if not cur.fetchone()[0]:
            raise RuntimeError("Schema userdata doesn't exist yet. Run this first, then migrate again:  uv run python -m warehouse setup")
        cur.execute(TABLES)


class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
