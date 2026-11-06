"""python -m webapp run_reports: makes the reports that are due (run_all step `reports`). Files go in output/reports/."""

from django.core.management.base import BaseCommand, CommandError

from webapp.reports.making import reports_dir, run_due


class Command(BaseCommand):
    help = "Make every scheduled report that's due, as a PDF in output/reports/."

    def handle(self, *args, **options):
        runs = run_due()
        for run in runs:
            self.stdout.write(f"{run.report.name}: " + (f"FAILED: {run.problem}" if run.problem else f"{reports_dir() / run.filename} ({run.size / 1024:.0f} KB, {run.seconds:.1f}s)"))
        if not runs:
            self.stdout.write("No report is due.")
        failed = [r for r in runs if r.problem]
        if failed:
            raise CommandError(f"{len(failed)} report(s) failed: {failed[0].problem[:200]}")
