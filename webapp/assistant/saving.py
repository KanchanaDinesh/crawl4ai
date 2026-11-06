"""An answer as a live dataset: a view, so its rows follow the data. From then on it's a dataset like any other: the grid, filters, exports and charts all work on it.
Checked again when saved, against what the person may see now."""

import secrets

from django.db import connection, transaction

from webapp.catalog.models import QUERY_PREFIX, USERDATA, Dataset
from webapp.catalog.scan import scan_one
from webapp.catalog.sqlnames import relation

from . import prompt, sqlcheck
from .models import Question


class SaveProblem(ValueError):
    """What to tell the person."""


def save(question: Question, label: str, user) -> Dataset:
    label = " ".join(str(label or "").split())[:120]
    if not label:
        raise SaveProblem("Give it a name.")
    if Dataset.objects.filter(owner=user, missing=False, label__iexact=label).exists():
        raise SaveProblem(f"You already have something called “{label}”.")
    datasets = prompt.usable_datasets(user)
    try:
        checked = sqlcheck.check(question.sql, prompt.allowed_map(datasets))
    except sqlcheck.SqlRefused as e:
        raise SaveProblem(f"This query can't be saved any more: {e}") from None
    by_ref = {ds.ref: ds for ds in datasets}
    name = f"{QUERY_PREFIX}{secrets.token_hex(5)}"
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(f"create view {relation(USERDATA, name)} as {checked.sql}")
            scan_one(cur, USERDATA, name, "view")
            ds = Dataset.objects.get(schema=USERDATA, name=name)
            ds.label, ds.owner = label, user
            ds.description = f"From the AI assistant: {question.text}"[:300]
            ds.save(update_fields=["label", "owner", "description"])
            ds.query_sources.set([by_ref[ref] for ref in checked.sources])
            question.saved_as = ds
            question.save(update_fields=["saved_as"])
    except Exception as e:  # noqa: BLE001 - a view can fail in several ways; none of them should lose the answer on screen
        raise SaveProblem(f"It couldn't be saved: {str(e).strip().splitlines()[0][:200]}") from None
    return ds
