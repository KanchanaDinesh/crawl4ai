"""The Foresight page: a thin view of what `python -m foresight` saved. It never makes or changes a forecast.

Who sees it: the menu right Foresight, plus the data behind each tab. A forecast shows what its section's data says, so taking the
Markets data away from a role also closes the Markets tab here, or this page would be a way around the rights.
"""

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from foresight import queries as q
from foresight.scoring import VERDICTS
from webapp.accounts.rights import menu_required
from webapp.insights.common import Reader, kpis, list_response, may_open, shell, text_param, url_with


@dataclass(frozen=True)
class Section:
    code: str
    label: str
    url_name: str
    why_not: str = ""                            # a section without an analyst yet says why


SECTIONS = (Section("markets", "Markets", "foresight_markets"),
            Section("news", "World news", "foresight_news"),
            Section("tech", "Tech intel", "foresight_tech", "Not started. It needs more weeks of saved sites before growth by category can be told from noise."),
            Section("vehicles", "Vehicles", "foresight_vehicles", "Not started. It needs more weeks of ads, and more ads per model."),
            Section("companies", "Companies", "foresight_companies", "No predictions yet: this data is a directory, and it doesn't change over time, so there's nothing to learn from yet."))
BY_CODE = {s.code: s for s in SECTIONS}
SHOWN_ZONES = ("oversold", "middle", "overbought")


def _allowed(user) -> list[Section]:
    return [s for s in SECTIONS if may_open(user, s.code)]


def _need_section(request, code: str) -> Section:
    section = BY_CODE.get(code)
    if section is None:
        raise Http404
    if not may_open(request.user, code):
        raise PermissionDenied(f"Your roles don't include the data behind this tab ({code}). An administrator can grant it on the Catalog page.")
    return section


@menu_required("foresight")
def landing(request):
    tabs = _allowed(request.user)
    if tabs:
        return redirect(reverse(tabs[0].url_name))
    return render(request, "insights/no_tabs.html")


# ── reading what's saved ────────────────────────────────────────────────────────────────────────────────────
def _verdict(test: dict) -> dict:
    return {**test, "label": q.QUESTION_LABELS.get(test["question"], test["question"]), "group": q.GROUP_LABELS.get(test["grp"], test["grp"]), "text": VERDICTS[test["verdict"]],
            "tone": {"better": "ok", "worse": "warn", "no_difference": "", "too_little": ""}[test["verdict"]],
            "skill_pct": None if test["skill"] is None else test["skill"] * 100, "low_pct": None if test["skill_low"] is None else test["skill_low"] * 100,
            "high_pct": None if test["skill_high"] is None else test["skill_high"] * 100,
            "zones": _zone_rows(test)}


def _zone_rows(test: dict) -> list[dict]:
    """JSONB keeps keys sorted, not in the order they were saved, so the order comes from the question."""
    details = test["details"]
    outcomes = q.QUESTION_OUTCOMES.get(test["question"], tuple(details["labels"]))
    known = [z for z in (*q.QUESTION_ZONES.get(test["question"], ()), "all") if z in details["zones"]]
    return [{"name": z, "cases": details["zones"][z]["cases"], "odds": [{"label": details["labels"][o], "pct": None if details["zones"][z][o] is None else details["zones"][z][o] * 100} for o in outcomes]}
            for z in known or details["zones"]]


def _record(row: dict) -> dict:
    skill = row["skill"]
    word = None if skill is None else "better than" if skill > 0 else "worse than" if skill < 0 else "the same as"
    return {**row, "label": q.QUESTION_LABELS.get(row["question"], row["question"]), "group": q.GROUP_LABELS.get(row["grp"], row["grp"]),
            "word": word, "skill_pct": None if skill is None else abs(skill) * 100}


def _what_is_going_on(row: dict) -> str:
    """The numbers behind a forecast, in a few words."""
    s = row["signals"]
    if row["question"] == "price_7d":
        return f"%K {s.get('pct_k', 0):.0f}"
    if row["question"] == "topic_7d":
        return f"{row['start_value']:.1f}% of headlines, {s.get('move_pct', 0):+.0f}%"
    return f"{s.get('sources')} sources, {s.get('countries')} countries"


def _table_row(row: dict) -> dict:
    cells = {c["key"]: c for c in row["scenarios"]}
    gap = max((abs(c["probability"] - c["normal"]) for c in row["scenarios"]), default=0)
    band = row["signals"].get("band_pct")
    return {**row, "cells": [{"pct": cells[k]["probability"] * 100, "normal": cells[k]["normal"] * 100, "label": cells[k]["label"]} for k in q.QUESTION_OUTCOMES[row["question"]]],
            "name": row["subject_name"] or row["subject"], "going_on": _what_is_going_on(row), "zone": row["signals"].get("zone"), "gap": gap * 100, "band": band,
            **{f"p_{c['key']}": c["probability"] for c in row["scenarios"]}}


def _filters(request, section: str, questions: list[dict]) -> dict:
    choices = [r["question"] for r in questions]
    question = request.GET.get("question")
    return {"question": question if question in choices else (choices[0] if choices else ""), "grp": text_param(request, "grp"), "zone": text_param(request, "zone"), "q": text_param(request, "q")}


def _rows_context(db, code: str, f: dict, sort: str, direction: int) -> dict:
    found = db.get(q.open_forecasts, code, f["question"] or None, f["grp"] or None, f["zone"] or None, f["q"] or None, default=[])
    rows = [_table_row(r) for r in found]
    outcomes = q.QUESTION_OUTCOMES.get(f["question"], ())
    columns = [("name", "What", False), ("zone", "Now", False), ("going_on", "Numbers", False)]
    if code == "markets":
        columns.append(("band", "Usual move", True))
    columns += [(f"p_{o}", q.OUTCOME_LABELS[o].capitalize(), True) for o in outcomes]
    columns += [("gap", "From normal", True), ("check_on", "Check on", False)]
    allowed = {c[0] for c in columns}
    sort = sort if sort in allowed else "gap"
    present = [r for r in rows if r.get(sort) is not None]
    present.sort(key=lambda r: r[sort].lower() if isinstance(r[sort], str) else r[sort], reverse=direction < 0)
    rows = present + [r for r in rows if r.get(sort) is None]
    heads = []
    for key, label, numeric in columns:
        next_dir = -direction if sort == key else (1 if not numeric else -1)
        heads.append({"key": key, "label": label, "numeric": numeric, "sorted": sort == key, "arrow": ("▲" if direction > 0 else "▼") if sort == key else "",
                      "url": url_with("foresight_rows", f, code, sort=key, dir=next_dir)})
    return {"rows": rows, "heads": heads, "show_band": code == "markets", "f": f}


def _sort_args(request) -> tuple[str, int]:
    return request.GET.get("sort") or "gap", (1 if request.GET.get("dir") == "1" else -1)


def _count_text(n: int) -> str:
    return f"{n:,} · click a row for its reasons · click a column to sort"


@menu_required("foresight")
def rows_view(request, code: str):
    _need_section(request, code)
    with Reader() as db:
        summary = db.get(q.counts, code, default=None) or {"questions": []}
        f = _filters(request, code, summary["questions"])
        sort, direction = _sort_args(request)
        context = _rows_context(db, code, f, sort, direction)
    return list_response(request, "foresight/_rows.html", context, _count_text(len(context["rows"])), url_with(BY_CODE[code].url_name, f))


@menu_required("foresight")
def section_view(request, code: str):
    section = _need_section(request, code)
    context = {"section": section, "tabs_foresight": _allowed(request.user)}
    with Reader() as db:
        if not section.why_not:
            summary = db.get(q.counts, code, default=None)
            run = db.get(q.latest_run, code, default=None)
            tests = [_verdict(t) for t in db.get(q.backtests, code, default=[])]
            record = [_record(r) for r in db.get(q.track_record, code, default=[])]
            s = summary or {"open": 0, "checked": 0, "not_checkable": 0, "next_check": None, "questions": [], "groups": []}
            f = _filters(request, code, s["questions"])
            sort, direction = _sort_args(request)
            rows = _rows_context(db, code, f, sort, direction)
            context.update(rows, s=s, run=run, tests=tests, record=record, no_edge=bool(tests) and all(t["verdict"] != "better" for t in tests), not_ready=[line for line in (run or {}).get("progress") or [] if "no odds yet" in line], watching_line=next((line for line in (run or {}).get("progress") or [] if line.startswith("Watching")), ""), count=_count_text(len(rows["rows"])),
                           kpis=kpis((s["open"], "Open forecasts"), (s["next_check"] or "–", "Next check"), (s["checked"], "Checked"), (s["not_checkable"], "Couldn't be checked")),
                           question_chips=[{"code": r["question"], "name": q.QUESTION_LABELS.get(r["question"], r["question"]), "n": r["n"]} for r in s["questions"]],
                           group_chips=[{"code": "", "name": "All", "n": None}, *[{"code": r["grp"], "name": q.GROUP_LABELS.get(r["grp"], r["grp"]), "n": r["n"]} for r in s["groups"]]] if len(s["groups"]) > 1 else [],
                           zone_chips=[{"code": "", "name": "Any", "n": None}, *[{"code": z, "name": z.capitalize(), "n": None} for z in SHOWN_ZONES]] if code == "markets" else [],
                           recent=[_table_row(r) | {"actual_label": q.OUTCOME_LABELS.get(r["actual"], "couldn't be checked")} for r in db.get(q.recent_outcomes, code, default=[])])
            if code == "news":
                context.update(watching=db.get(q.watching, default={"day": None, "stories": []}), topics=db.get(q.topics_now, default=[]))
    ctx = shell(request, code, db, **context)
    ctx["tabs"] = context["tabs_foresight"]
    ctx["tab"] = code
    return render(request, "foresight/section.html", ctx)


@menu_required("foresight")
def forecast_view(request, forecast_id: int):
    with Reader() as db:
        row = db.get(q.forecast, forecast_id, default=None)
        if row is None:
            raise Http404
        _need_section(request, row["section"])
        history = db.get(q.earlier, row["section"], row["subject"], row["question"], row["id"], default=[])
    return render(request, "foresight/_forecast.html", {
        "r": _table_row(row), "question": q.QUESTION_LABELS.get(row["question"], row["question"]), "banner": db.error,
        "actual": None if not row.get("actual") else q.OUTCOME_LABELS.get(row["actual"], "couldn't be checked"), "not_checkable": row.get("actual") == "no_data",
        "history": [{"as_of": h["as_of"], "check_on": h["check_on"], "cells": [f"{s['label']}: {s['probability'] * 100:.0f}%" for s in h["scenarios"]],
                     "actual": q.OUTCOME_LABELS.get(h["actual"], "couldn't be checked") if h.get("actual") else "not checked yet"} for h in history]})
