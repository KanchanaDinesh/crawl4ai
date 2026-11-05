"""The audit log can only grow: a trigger refuses UPDATE and DELETE on it, for every login, the app's included."""

from django.db import migrations

SQL = """
create or replace function audit_append_only() returns trigger language plpgsql as $$
begin
    raise exception 'The audit log can''t be changed or deleted (it only grows).';
end $$;
create trigger audit_event_append_only before update or delete on audit_auditevent
    for each row execute function audit_append_only();
create trigger audit_event_no_truncate before truncate on audit_auditevent
    for each statement execute function audit_append_only();
"""
REVERSE = """
drop trigger if exists audit_event_no_truncate on audit_auditevent;
drop trigger if exists audit_event_append_only on audit_auditevent;
drop function if exists audit_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
