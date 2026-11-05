"""Writing SQL from catalog names. Names and JSON keys come from what PostgreSQL itself reported in a scan, never from
a request, and are still quoted here, so an odd name can't change the meaning of a query. Values always go as
parameters. The doubled % is because the database driver treats % as the start of a parameter."""


def q(name: str) -> str:
    """A quoted identifier."""
    return '"' + name.replace('"', '""').replace("%", "%%") + '"'


def lit(text: str) -> str:
    """A quoted text literal (for JSON keys)."""
    return "'" + text.replace("'", "''").replace("%", "%%") + "'"


def relation(schema: str, name: str) -> str:
    return f"{q(schema)}.{q(name)}"
