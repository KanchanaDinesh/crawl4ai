"""python -m webapp catalog_scan: refresh the catalog (run_all step `catalog`)."""

import time

from django.core.management.base import BaseCommand

from webapp.audit.log import record
from webapp.catalog.scan import describe, scan


class Command(BaseCommand):
    help = "Look at every table and view in the data schemas and update the catalog."

    def handle(self, *args, **opts):
        t0 = time.monotonic()
        summary = scan()
        text = describe(summary)
        if summary["new"] or summary["new_fields"] or summary["missing"]:
            record(None, "catalog_scanned", f"Catalog scan: {text}", target_type="catalog", username="scheduled scan",
                   detail={k: summary[k] for k in ("new", "new_fields", "missing")})
        self.stdout.write(f"{text} in {time.monotonic() - t0:.1f}s")
        for ref, err in summary["errors"].items():
            self.stderr.write(f"  {ref}: {err}")
