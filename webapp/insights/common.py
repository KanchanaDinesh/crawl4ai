"""What the five dashboard tabs share: who may open which tab, reading PostgreSQL, the query-string filters, and the
line in the header about the last collection run.

Everything reads with the read-only login, the same way the old dashboard did: collectors never block these pages
and these pages never block a collector.
"""

import json
import logging
from dataclasses import dataclass
from functools import wraps
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

import project_paths
from webapp.accounts.rights import menu_required
from webapp.catalog.models import Dataset
from webapp.catalog.rights import can_view, is_admin

logger = logging.getLogger("webapp.insights")

PAGE = 100                                      # rows a list loads at a time
PERIODS = ((1, "Last 24 hours"), (7, "Last 7 days"), (30, "Last 30 days"), (3650, "All time"))


@dataclass(frozen=True)
class Tab:
    code: str
    label: str
    url_name: str
    schema: str                                 # the dataset that decides who may open the tab
    table: str


TABS = (Tab("news", "World News", "insights_news", "news", "news_items"),
        Tab("tech", "Tech Intel", "insights_tech", "tech", "v_candidates"),
        Tab("markets", "Markets", "insights_markets", "markets", "quotes"),
        Tab("vehicles", "Vehicles", "insights_vehicles", "vehicles", "ads"),
        Tab("companies", "Companies", "insights_companies", "companies", "companies"))


def allowed_tabs(user) -> list[Tab]:
    """A tab shows a dataset's rows, so it follows that dataset's rights: taking a dataset away from a role in the
    catalog must also close the tab, or the dashboard would be a way around the rights."""
    cached = getattr(user, "_allowed_tabs", None)
    if cached is not None:
        return cached
    if is_admin(user):
        tabs = list(TABS)
    else:
        found = {(d.schema, d.name): d for d in Dataset.objects.filter(schema__in=[t.schema for t in TABS])}
        tabs = [t for t in TABS if (d := found.get((t.schema, t.table))) is not None and can_view(user, d)]
    user._allowed_tabs = tabs
    return tabs


def may_open(user, code: str) -> bool:
    return any(t.code == code for t in allowed_tabs(user))


def tab_required(code: str):
    """View decorator: the Dashboard menu right, plus the right to see this tab's dataset (else 403)."""
    def decorate(view):
        @wraps(view)
        @menu_required("dashboard")
        def wrapper(request, *args, **kwargs):
            if not may_open(request.user, code):
                raise PermissionDenied(f"Your roles don't include the data behind this tab ({code}). "
                                       "An administrator can grant it on the Catalog page.")
            return view(request, *args, **kwargs)
        return wrapper
    return decorate


class Reader:
    """One read-only connection for one page. A table that doesn't exist yet just means the tool hasn't run, so the
    page shows its "nothing yet" message; anything else becomes one line at the top of the page, not an error page."""

    def __init__(self):
        self.con = None
        self.error = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.con is not None:
            self.con.close()

    def _connect(self):
        from warehouse.postgres import DatabaseError, connect
        try:
            self.con = connect("reader", autocommit=True)
            self.con.execute("set timezone = 'UTC'")
        except DatabaseError as e:
            self.error = str(e)
        return self.con

    def get(self, fn, *args, default=None, **kwargs):
        import psycopg
        if self.error or (self.con is None and self._connect() is None):
            return default
        try:
            return fn(self.con, *args, **kwargs)
        except psycopg.errors.UndefinedTable:
            return default
        except psycopg.Error as e:
            logger.warning("dashboard query %s failed: %s", getattr(fn, "__name__", fn), e)
            self.error = (f"A query for this page failed ({type(e).__name__}). The rest of the page is still right; "
                          "press Refresh to try again.")
            return default


# ── filters: everything is in the query string, so a filtered page can be reloaded, bookmarked and shared ─────
def int_param(request, name: str, default: int, lo: int = 1, hi: int = 100000) -> int:
    try:
        return max(lo, min(hi, int(request.GET.get(name, default))))
    except ValueError:
        return default


def text_param(request, name: str) -> str:
    return (request.GET.get(name) or "").strip()


def period_param(request, default: int) -> int:
    days = int_param(request, "days", default)
    return days if days in dict(PERIODS) else default


def period_label(days: int) -> str:
    return dict(PERIODS)[days].lower()


def query_string(params: dict, **extra) -> str:
    """Only what's set: unset filters stay out of the address."""
    merged = {**params, **extra}
    clean = {k: (1 if v is True else v) for k, v in merged.items() if v not in ("", None, False)}
    return urlencode(clean)


def url_with(name: str, params: dict, *args, **extra) -> str:
    qs = query_string(params, **extra)
    return reverse(name, args=args) + (f"?{qs}" if qs else "")


def shown(total: int, count: int, rest: str) -> str:
    """"780 · showing 100": lists load PAGE rows at a time, so say how many of the total are on the page."""
    return f"{total:,}" + (f" · showing {count:,}" if count < total else "") + f" · {rest}"


def list_response(request, template: str, context: dict, count: str, page_url: str | None = None) -> HttpResponse:
    """Rows for a list that's being filtered or scrolled. The count above the list isn't part of the swapped rows, so
    it travels in a header, and on a new filter the address bar follows (reloading shows the same list)."""
    response = HttpResponse(render_to_string(template, context, request=request))
    response["HX-Trigger"] = json.dumps({"listCount": count})
    if page_url:
        response["HX-Push-Url"] = page_url
    return response


# ── the page around every tab ───────────────────────────────────────────────────────────────────────────────
def last_runs(limit: int = 10) -> list[dict]:
    """Last lines of logs/history.log written by run_all.sh (manual and scheduled runs), newest first."""
    path = project_paths.REPO_ROOT / "logs" / "history.log"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    runs = []
    for line in reversed(lines):
        when, rest = line[:19], line[21:]
        runs.append({"when": when, "ok": rest.startswith("ok"), "skipped": rest.startswith("skipped"),
                     "text": rest.strip()})
    return runs


def shell(request, code: str, db: Reader, **context) -> dict:
    runs = last_runs()
    context.update(tabs=allowed_tabs(request.user), tab=code, banner=db.error, updated=timezone.localtime(),
                   last_run=next((r for r in runs if not r["skipped"]), None),
                   runs_title="\n".join(f"{r['when']}  {r['text']}" for r in runs), periods=PERIODS)
    return context


def page(request, code: str, db: Reader, **context):
    return render(request, f"insights/{code}.html", shell(request, code, db, **context))


# ── small shapes the templates draw ─────────────────────────────────────────────────────────────────────────
def bars(rows, label, value="n", search=False, set_field=None, set_value=None, title=None) -> list[dict]:
    """A ranked list: label, a bar in proportion, the number. `label` is a column name or a function of the row.
    `search` makes a row search the list for its label; `set_field` + `set_value` make it set a filter."""
    rows = list(rows or [])
    top = max((r[value] or 0 for r in rows), default=0) or 1
    out = []
    for r in rows:
        text = label(r) if callable(label) else r.get(label)
        text = "—" if text in (None, "") else str(text)
        out.append({"label": text, "n": r[value] or 0, "width": max(2, round(100 * (r[value] or 0) / top, 1)),
                    "search": text if search else "", "title": title(r) if title else text,
                    "set_field": set_field or "", "set_value": set_value(r) if set_value else ""})
    return out


def kpis(*pairs) -> list[dict]:
    return [{"value": v, "label": label} for v, label in pairs]
