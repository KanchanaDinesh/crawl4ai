"""Making a report: its numbers (worked out with the rights of whoever it's for), the page, the PDF file."""

import os
import re
import time
from datetime import datetime
from pathlib import Path

from django.template.loader import render_to_string
from django.utils import timezone

import project_paths
from webapp.charts.data import ChartProblem, chart_data, show_number, table_rows
from webapp.charts.models import Chart
from webapp.charts.svg import chart_svg

from . import newsletter
from .models import Report, ReportRun

TABLE_ROWS = 25


class ReportProblem(RuntimeError):
    """What to tell the person."""


def reports_dir() -> Path:
    folder = Path(os.environ.get("SCRAPERS_OUTPUT_DIR", project_paths.REPO_ROOT / "output")) / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _section(chart: Chart, user, with_table: bool) -> dict:
    section = {"name": chart.name, "dataset": chart.dataset.label}
    try:
        data = chart_data(chart, user)
    except ChartProblem as e:
        section["problem"] = str(e)
        return section
    section["note"] = data.note
    if data.kind == Chart.NUMBER:
        section.update(number=show_number(data.number), measure=data.measure_label)
        return section
    if data.kind != Chart.TABLE:
        section["svg"] = chart_svg(data)
    if data.kind == Chart.TABLE or with_table:
        heads, rows = table_rows(data, TABLE_ROWS)
        section.update(heads=heads, rows=[[row[0], *[show_number(v) for v in row[1:]]] for row in rows],
                       more=max(0, len(data.categories) - TABLE_ROWS))
    if not data.categories:
        section["empty"] = True
    return section


def report_context(report: Report, user, now: datetime | None = None) -> dict:
    now = timezone.localtime(now or timezone.now())
    context = {"report": report, "made_at": now, "paragraphs": [p.strip() for p in report.intro.split("\n\n") if p.strip()],
               "sections": newsletter.sections_for(report, user)}
    if report.is_newsletter:
        context["issue"] = report.runs.filter(problem="").count() + 1
    else:
        context["charts"] = [_section(i.chart, user, report.include_tables) for i in report.dashboard.items.select_related("chart", "chart__dataset")]
    return context


def render_html(report: Report, user, now=None) -> str:
    return render_to_string("reports/document.html", report_context(report, user, now))


def _weasyprint():
    """WeasyPrint needs the Pango library. Homebrew's folder isn't where macOS looks for libraries by itself, and setting that through
    the environment doesn't reach every way a program gets started here (launchd, for one), so it's added at the point of loading."""
    import cffi
    original = cffi.FFI.dlopen

    def dlopen(self, name, flags=0):
        try:
            return original(self, name, flags)
        except OSError:
            for folder in ("/opt/homebrew/lib", "/usr/local/lib"):
                path = os.path.join(folder, str(name))
                if os.path.exists(path):
                    return original(self, path, flags)
            raise
    cffi.FFI.dlopen = dlopen
    try:
        import weasyprint
    except OSError as e:
        raise ReportProblem("The PDF maker's libraries aren't installed. On the Mac: brew install pango. On a Linux server: apt install libpango-1.0-0 libpangoft2-1.0-0") from e
    finally:
        cffi.FFI.dlopen = original
    return weasyprint


def no_fetching():
    """A report is made only from what's in its page. Text from collected pages must never make the server fetch anything.
    A refused address is passed over: the PDF is still made."""
    import weasyprint.urls

    class NoFetching(weasyprint.urls.URLFetcher):
        def fetch(self, url, headers=None):
            raise ValueError(f"Reports don't fetch anything ({str(url)[:80]})")
    return NoFetching()


def write_pdf(html: str, path: Path) -> None:
    weasyprint = _weasyprint()       # first: it brings in the libraries the rest needs
    partial = path.with_name(f".{path.name}.partial")
    try:
        weasyprint.HTML(string=html, url_fetcher=no_fetching()).write_pdf(str(partial))
        partial.rename(path)
    finally:
        partial.unlink(missing_ok=True)


def _file_name(report: Report, now: datetime) -> str:
    slug = re.sub(r"[^\w-]+", "-", report.name, flags=re.ASCII).strip("-").lower() or "report"
    return f"{slug[:60]}-{report.pk}-{now:%Y-%m-%d-%H%M%S}.pdf"


def make_report(report: Report, user, scheduled: bool = False) -> ReportRun:
    """Writes the PDF and keeps a row for it. A problem is kept too, so it can be read on the report's page."""
    started = time.monotonic()
    now = timezone.localtime()
    run = ReportRun(report=report, made_by=user, scheduled=scheduled)
    try:
        name = _file_name(report, now)
        path = reports_dir() / name
        write_pdf(render_html(report, user, now), path)
        run.filename, run.size = name, path.stat().st_size
    except (ReportProblem, OSError, ValueError) as e:
        run.problem = str(e)[:1000]
    run.seconds = round(time.monotonic() - started, 2)
    run.save()
    return run


def is_due(report: Report, now: datetime) -> bool:
    """A run that's missed (the Mac was off, nobody started run_all) happens at the next chance. The first one happens straight away."""
    if not report.schedule:
        return False
    last = report.last_scheduled
    if last is None:
        return True
    last, now = timezone.localtime(last), timezone.localtime(now)
    if report.schedule == Report.DAILY:
        return last.date() < now.date()
    if report.schedule == Report.MONTHLY:
        return (last.year, last.month) < (now.year, now.month)
    days = (now.date() - last.date()).days
    return days >= 7 or (days >= 1 and now.weekday() == report.weekday)


def run_due(now: datetime | None = None) -> list[ReportRun]:
    from webapp.audit.log import record
    now = now or timezone.now()
    runs = []
    for report in Report.objects.exclude(schedule="").select_related("owner", "dashboard"):
        if not is_due(report, now):
            continue
        if not report.owner.is_active:
            continue                 # someone who has left: nobody gets reports in their name
        run = make_report(report, report.owner, scheduled=True)
        if not run.problem:
            report.last_scheduled = now
            report.save(update_fields=["last_scheduled"])
        record(None, "report_failed" if run.problem else "report_made", f"Scheduled report “{report.name}”" + (f" failed: {run.problem[:150]}" if run.problem else f": {run.filename}"),
               target_type="report", target_id=report.pk, user=report.owner)
        runs.append(run)
    return runs
