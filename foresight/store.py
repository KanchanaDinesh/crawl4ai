"""Foresight's database: PostgreSQL schema `foresight` (warehouse/schema/foresight.sql).

Forecasts and outcomes can only be added: the database refuses changes (rule 1 of docs/foresight-plan.md). This is the only
place the module writes, and only to its own schema. It reads other tools' data through a separate read-only connection.
"""

import json


def _row(cols, values) -> dict:
    return dict(zip(cols, values))


class ForesightStore:
    def __init__(self):
        from warehouse import postgres
        self.con = postgres.connect("writer", autocommit=True)
        self.con.execute("set timezone = 'UTC'")
        postgres.migrate(self.con, ("foresight",))

    def save_forecasts(self, rows: list[dict]) -> int:
        """Adds them, at most one per question per day: a second run on the same data adds nothing and changes nothing."""
        made = 0
        with self.con.transaction():
            cur = self.con.cursor()
            for r in rows:
                cur.execute("""insert into foresight.forecasts (section, grp, subject, subject_name, question, as_of, check_on, start_value,
                        scenarios, reasons, signals, method, method_version, cases, separate_weeks)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (section, subject, question, as_of) do nothing""",
                            [r["section"], r["grp"], r["subject"], r["subject_name"], r["question"], r["as_of"], r["check_on"], r["start_value"],
                             json.dumps(r["scenarios"]), json.dumps(r["reasons"]), json.dumps(r["signals"]), r["method"], r["method_version"],
                             r["cases"], r["separate_weeks"]])
                made += cur.rowcount
        return made

    def due(self, section: str, today) -> list[dict]:
        cur = self.con.execute("""select f.id, f.subject, f.question, f.as_of, f.check_on, f.start_value, f.scenarios, f.signals from foresight.forecasts f
            left join foresight.outcomes o on o.forecast_id = f.id
            where f.section = %s and f.check_on <= %s and o.forecast_id is null order by f.check_on, f.id""", [section, today])
        cols = [c.name for c in cur.description]
        return [_row(cols, v) for v in cur.fetchall()]

    def save_outcomes(self, rows: list[dict]) -> int:
        with self.con.transaction():
            self.con.cursor().executemany("""insert into foresight.outcomes (forecast_id, end_value, end_day, move_pct, actual, score, normal_score)
                values (%s, %s, %s, %s, %s, %s, %s) on conflict (forecast_id) do nothing""",
                                          [(r["forecast_id"], r["end_value"], r["end_day"], r["move_pct"], r["actual"], r["score"], r["normal_score"]) for r in rows])
        return len(rows)

    def save_signals(self, section: str, rows: list[tuple]) -> int:
        """rows: (kind, day, subject, payload). What's saved stays as it was first seen."""
        if rows:
            with self.con.transaction():
                self.con.cursor().executemany("""insert into foresight.signals (section, kind, day, subject, payload) values (%s, %s, %s, %s, %s)
                    on conflict do nothing""", [(section, k, d, s, json.dumps(p)) for k, d, s, p in rows])
        return len(rows)

    def save_backtest(self, r: dict) -> None:
        self.con.execute("""insert into foresight.backtests (section, grp, question, method, method_version, first_day, last_day, cases, weeks,
                score, normal_score, skill, skill_low, skill_high, verdict, details)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                         [r["section"], r["grp"], r["question"], r["method"], r["method_version"], r["first_day"], r["last_day"], r["cases"], r["weeks"],
                          r["score"], r["normal_score"], r["skill"], r["skill_low"], r["skill_high"], r["verdict"], json.dumps(r["details"])])

    def save_run(self, section: str, made: int, checked: int, not_made: dict, note: str | None = None, progress: list[str] | None = None) -> None:
        self.con.execute("insert into foresight.runs (section, made, checked, not_made, note, progress) values (%s, %s, %s, %s, %s, %s)",
                         [section, made, checked, json.dumps(not_made), note, json.dumps(progress or [])])

    def close(self) -> None:
        self.con.close()
