"""python -m foresight <command>

    run    [--section markets]   check the forecasts that are due, then make today's (at most one per question per day)
    test   [--section markets]   how the method would have done on the past, against a normal-week guess
    show   [--section markets] [--subject BTC] [--zone oversold] [--limit 30]   the latest forecasts and how far to trust them
    score                        the live track record: only forecasts saved before their outcome

Scenarios with odds, not promises, and numbers only: never advice to buy or sell. Settings: foresight/config/settings.json.
Plan and rules: docs/foresight-plan.md
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

from .analysts import ANALYSTS
from .scoring import VERDICTS
from .store import ForesightStore

CONFIG = os.path.join(os.path.dirname(__file__), "config", "settings.json")
LK = timezone(timedelta(hours=5, minutes=30))
STALE_DAYS = 3


def today_here() -> date:
    return datetime.now(LK).date()


def _analysts(args) -> list:
    settings = json.load(open(args.config or CONFIG))
    chosen = [args.section] if getattr(args, "section", None) else list(ANALYSTS)
    if set(chosen) - set(ANALYSTS):
        sys.exit(f"--section takes: {', '.join(ANALYSTS)}")
    return [ANALYSTS[s](settings[s]) for s in chosen if settings.get(s, {}).get("enabled", True)]


def _reader():
    from warehouse.postgres import DatabaseError, connect
    try:
        return connect("reader", autocommit=True)
    except DatabaseError as e:
        sys.exit(str(e))


def _load(analyst, reader):
    import psycopg
    try:
        return analyst.load(reader)
    except psycopg.errors.UndefinedTable:
        sys.exit(f"{analyst.section} has no data yet. For markets, run:  uv run python -m markets closes")


def run_section(analyst, store: ForesightStore, reader, today: date) -> dict:
    """Checks first, so a forecast made today can't be mistaken for a checked one, and the record is up to date."""
    data = _load(analyst, reader)
    outcomes = [o for row in store.due(analyst.section, today) if (o := analyst.check(row, data))]
    store.save_outcomes(outcomes)
    rows, not_made = analyst.forecast(data, today)
    made = store.save_forecasts(rows)
    stale = [g for g, d in data.last_day.items() if d < today - timedelta(days=STALE_DAYS)]
    note = f"prices are old for: {', '.join(stale)}" if stale else None
    store.save_run(analyst.section, made, len(outcomes), dict(not_made), note)
    return {"section": analyst.section, "made": made, "checked": len(outcomes), "not_made": dict(not_made), "stale": stale}


def cmd_run(args) -> None:
    store, reader = ForesightStore(), _reader()
    for analyst in _analysts(args):
        r = run_section(analyst, store, reader, today_here())
        print(f"{r['section']}: {r['made']} new forecasts, {r['checked']} checked.")
        for why, n in sorted(r["not_made"].items(), key=lambda x: -x[1]):
            print(f"  no forecast for {n}: {why}")
        if r["stale"]:
            print(f"  The prices are more than {STALE_DAYS} days old for {', '.join(r['stale'])}. Run first:  uv run python -m markets closes")
    store.close()
    reader.close()


def _pct(v) -> str:
    return "–" if v is None else f"{v * 100:.0f}%"


def cmd_test(args) -> None:
    store, reader = ForesightStore(), _reader()
    for analyst in _analysts(args):
        for r in analyst.test(_load(analyst, reader)):
            store.save_backtest(r)
            print(f"\n{r['label']} ({r['section']}, {r['question']}): {VERDICTS[r['verdict']]}.")
            if r["skill"] is not None:
                range_text = f", 90% range {r['skill_low'] * 100:+.1f}% to {r['skill_high'] * 100:+.1f}%" if r["skill_low"] is not None else ""
                print(f"  {r['cases']:,} scored days across {r['weeks']} weeks, {r['first_day']} to {r['last_day']}. "
                      f"Skill {r['skill'] * 100:+.1f}%{range_text}.")
            print(f"  What happened next, by zone (of {r['details']['symbols']} instruments):")
            for name, z in r["details"]["zones"].items():
                print(f"    {name:<11} {z['cases']:>7,} days   up {_pct(z['up']):>4}   within {_pct(z['flat']):>4}   down {_pct(z['down']):>4}")
    print("\nA test on the past can flatter; only the live record counts in the end (python -m foresight score). Not advice.")
    store.close()
    reader.close()


def cmd_score(args) -> None:
    reader = _reader()
    rows = reader.execute("""select section, grp, question, checked, score, normal_score, skill from foresight.v_track_record order by 1, 2, 3""").fetchall()
    reader.close()
    if not rows:
        print("No forecast has reached its date yet. The first ones are checked 7 days after the first run.")
    for section, grp, question, checked, score, normal, skill in rows:
        word = "better than" if skill > 0 else "worse than" if skill < 0 else "the same as"
        print(f"{section} {grp} {question}: {checked:,} checked, {abs(skill) * 100:.1f}% {word} the normal-week guess "
              f"(score {score:.3f}, guess {normal:.3f}). Early numbers can be luck.")


def cmd_show(args) -> None:
    reader = _reader()
    tests = reader.execute("""select distinct on (section, grp) section, grp, verdict, skill from foresight.backtests order by section, grp, run_at desc""").fetchall()
    for section, grp, verdict, skill in tests:
        print(f"{section} {grp}: on the past, {VERDICTS[verdict]}" + (f" (skill {skill * 100:+.1f}%)" if skill is not None else "") + ".")
    if tests and all(t[2] != "better" for t in tests):
        print("So read the odds below as about the same as a normal week. The useful part is each one's usual move.")
    where, params = ["true"], []
    if args.section:
        where.append("section = %s"); params.append(args.section)
    if args.subject:
        where.append("(subject ilike %s or subject_name ilike %s)"); params += [f"%{args.subject}%"] * 2
    if args.zone:
        where.append("signals ->> 'zone' = %s"); params.append(args.zone)
    rows = reader.execute(f"""select distinct on (subject, question) subject, subject_name, as_of, check_on, scenarios, reasons, signals ->> 'zone'
        from foresight.forecasts where {' and '.join(where)} order by subject, question, as_of desc limit {int(args.limit)}""", params).fetchall()
    reader.close()
    if not rows:
        print("No forecasts yet. Run:  uv run python -m foresight run")
    for subject, name, as_of, check_on, scenarios, reasons, _ in rows:
        print(f"\n{name} ({subject}), {as_of} to {check_on}")
        print("  " + "  ·  ".join(f"{s['label']}: {s['probability'] * 100:.0f}% (normal {s['normal'] * 100:.0f}%)" for s in scenarios))
        if args.subject:
            for line in reasons:
                print(f"  {line}")
    print("\nScenarios with odds, not promises. Numbers only, not advice.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m foresight", description="Scenarios with odds, saved first and scored afterwards.")
    p.add_argument("--config", help="A different settings.json")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("run", "test"):
        sub.add_parser(name).add_argument("--section")
    s = sub.add_parser("show")
    s.add_argument("--section")
    s.add_argument("--subject")
    s.add_argument("--zone", choices=("overbought", "middle", "oversold"))
    s.add_argument("--limit", type=int, default=30)
    sub.add_parser("score")
    args = p.parse_args(argv)
    {"run": cmd_run, "test": cmd_test, "score": cmd_score, "show": cmd_show}[args.command](args)
