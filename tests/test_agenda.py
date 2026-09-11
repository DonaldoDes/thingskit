"""`thingskit agenda --horizon {today,week}` doit filtrer sur le signal
`scheduling_list`/`decode_things_date` déjà exposé par `cmd_tasks` — pas sur
`startBucket`, qui ne discrimine rien (BUG-001, mesuré 2026-08-12 : 610
tâches ouvertes réelles, `startBucket=0` pour 126 d'entre elles sans rapport
avec « aujourd'hui », et aucune deadline sur la totalité de la base).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3


SCHEMA = """
CREATE TABLE TMArea (uuid TEXT PRIMARY KEY, title TEXT);
CREATE TABLE TMTask (
    uuid TEXT PRIMARY KEY,
    title TEXT,
    type INTEGER,
    trashed INTEGER,
    notes TEXT,
    project TEXT,
    heading TEXT,
    area TEXT,
    start INTEGER,
    startDate INTEGER,
    startBucket INTEGER,
    deadline INTEGER,
    reminderTime INTEGER,
    status INTEGER
);
"""


def _encode(d: dt.date) -> int:
    """Même format bit-packé que `decode_things_date` : année<<16 | mois<<12
    | jour<<7 — pas de composante heure sur ce champ.
    """
    return (d.year << 16) | (d.month << 12) | (d.day << 7)


_DB_COUNTER = [0]


def _make_db(tmp_path, rows):
    _DB_COUNTER[0] += 1
    db_file = tmp_path / f"main-{_DB_COUNTER[0]}.sqlite"
    con = sqlite3.connect(db_file)
    con.executescript(SCHEMA)
    defaults = dict(
        uuid=None, title=None, type=0, trashed=0, notes=None, project=None,
        heading=None, area=None, start=1, startDate=None, startBucket=1,
        deadline=None, reminderTime=None, status=0,
    )
    for r in rows:
        row = {**defaults, **r}
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,notes,project,heading,"
            "area,start,startDate,startBucket,deadline,reminderTime,status) "
            "values (:uuid,:title,:type,:trashed,:notes,:project,:heading,"
            ":area,:start,:startDate,:startBucket,:deadline,:reminderTime,"
            ":status)",
            row,
        )
    con.commit()
    con.close()
    return db_file


def _run_agenda(thingskit, monkeypatch, tmp_path, rows, capsys, horizon="today",
                 today=None):
    db_file = _make_db(tmp_path, rows)
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    ns = argparse.Namespace(horizon=horizon, json=True, today=today)
    rc = thingskit.cmd_agenda(ns)
    assert rc == 0
    return json.loads(capsys.readouterr().out)


TODAY = dt.date.today()
YESTERDAY = TODAY - dt.timedelta(days=1)
TOMORROW = TODAY + dt.timedelta(days=1)
IN_3_DAYS = TODAY + dt.timedelta(days=3)
IN_10_DAYS = TODAY + dt.timedelta(days=10)


def test_horizon_today_includes_today_scheduled_task(thingskit, monkeypatch,
                                                      tmp_path, capsys):
    # BUG-001-01 : start=1 + startDate -> `scheduling_list` == "today".
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "t1", "title": "Prévue aujourd'hui", "start": 1,
         "startDate": _encode(TODAY)},
    ], capsys, horizon="today")
    assert [o["uuid"] for o in out] == ["t1"]


def test_horizon_today_includes_overdue_task(thingskit, monkeypatch,
                                             tmp_path, capsys):
    # Comportement natif Things assumé, pas masqué : ce qui était planifié
    # hier et non fait reste dans "Aujourd'hui" (cf. ticket, § Comportement
    # attendu).
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "t2", "title": "En retard", "start": 1,
         "startDate": _encode(YESTERDAY)},
    ], capsys, horizon="today")
    assert [o["uuid"] for o in out] == ["t2"]


def test_horizon_today_includes_deadline_of_the_day(thingskit, monkeypatch,
                                                     tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "t3", "title": "Échéance du jour", "start": 1,
         "startDate": None, "deadline": _encode(TODAY)},
    ], capsys, horizon="today")
    assert [o["uuid"] for o in out] == ["t3"]


def test_horizon_today_excludes_future_upcoming_task(thingskit, monkeypatch,
                                                      tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "t4", "title": "Planifiée demain", "start": 2,
         "startDate": _encode(TOMORROW)},
    ], capsys, horizon="today")
    assert out == []


def test_horizon_today_excludes_future_deadline(thingskit, monkeypatch,
                                                tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "t5", "title": "Échéance dans 3 jours", "start": 1,
         "startDate": None, "deadline": _encode(IN_3_DAYS)},
    ], capsys, horizon="today")
    assert out == []


def test_horizon_today_excludes_anytime_someday_inbox(thingskit, monkeypatch,
                                                       tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "a1", "title": "Anytime", "start": 1, "startDate": None},
        {"uuid": "a2", "title": "Someday", "start": 2, "startDate": None},
        {"uuid": "a3", "title": "Inbox", "start": 0, "startDate": None},
    ], capsys, horizon="today")
    assert out == []


def test_horizon_week_includes_upcoming_within_civil_week(
        thingskit, monkeypatch, tmp_path, capsys):
    # TOOL-433 : semaine CIVILE (lundi->dimanche), plus une fenêtre de 7
    # jours glissants — remplace l'ancien test BUG-001-02 dont l'hypothèse
    # (IN_3_DAYS toujours dans la fenêtre) ne tient plus une fois la semaine
    # civile en place. Vu depuis lundi, mercredi de la même semaine est
    # inclus.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "w1", "title": "Mercredi même semaine", "start": 2,
         "startDate": _encode(dt.date(2026, 9, 9))},
    ], capsys, horizon="week", today=dt.date(2026, 9, 7).isoformat())
    assert [o["uuid"] for o in out] == ["w1"]


def test_horizon_week_excludes_upcoming_beyond_civil_week(
        thingskit, monkeypatch, tmp_path, capsys):
    # Semaine suivante (lundi d'après) : hors de la période civile en cours.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "w2", "title": "Lundi suivant", "start": 2,
         "startDate": _encode(dt.date(2026, 9, 14))},
    ], capsys, horizon="week", today=dt.date(2026, 9, 7).isoformat())
    assert out == []


def test_horizon_week_includes_deadline_within_civil_week(
        thingskit, monkeypatch, tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "w3", "title": "Échéance mercredi même semaine", "start": 1,
         "startDate": None, "deadline": _encode(dt.date(2026, 9, 9))},
    ], capsys, horizon="week", today=dt.date(2026, 9, 7).isoformat())
    assert [o["uuid"] for o in out] == ["w3"]


def test_horizon_week_excludes_anytime_someday_inbox(thingskit, monkeypatch,
                                                      tmp_path, capsys):
    # BUG-001-02 : anytime/someday/inbox ne rentrent jamais, même en semaine.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "a1", "title": "Anytime", "start": 1, "startDate": None},
        {"uuid": "a2", "title": "Someday", "start": 2, "startDate": None},
        {"uuid": "a3", "title": "Inbox", "start": 0, "startDate": None},
    ], capsys, horizon="week")
    assert out == []


def test_horizon_today_and_week_differ(thingskit, monkeypatch, tmp_path,
                                       capsys):
    # BUG-001-03 : les deux ensembles diffèrent sur une base non vide, et
    # "today" est strictement inclus dans "week". Vu depuis lundi
    # 2026-09-07, mercredi 2026-09-09 est dans la semaine civile.
    monday = dt.date(2026, 9, 7)
    rows = [
        {"uuid": "t1", "title": "Aujourd'hui", "start": 1,
         "startDate": _encode(monday)},
        {"uuid": "w1", "title": "Mercredi même semaine", "start": 2,
         "startDate": _encode(dt.date(2026, 9, 9))},
    ]
    today_out = _run_agenda(thingskit, monkeypatch, tmp_path, rows, capsys,
                            horizon="today", today=monday.isoformat())
    week_out = _run_agenda(thingskit, monkeypatch, tmp_path, rows, capsys,
                           horizon="week", today=monday.isoformat())
    assert {o["uuid"] for o in today_out} == {"t1"}
    assert {o["uuid"] for o in week_out} == {"t1", "w1"}
    assert {o["uuid"] for o in today_out} < {o["uuid"] for o in week_out}


def test_horizon_excludes_completed_and_canceled(thingskit, monkeypatch,
                                                  tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "c1", "title": "Faite", "start": 1,
         "startDate": _encode(TODAY), "status": 3},
        {"uuid": "c2", "title": "Annulée", "start": 1,
         "startDate": _encode(TODAY), "status": 2},
    ], capsys, horizon="today")
    assert out == []


# --- TOOL-433 : horizons civils (semaine lundi->dimanche, mois, année) ---
# Dates fixes injectées via `--today` (argument `today` de `_run_agenda`),
# jamais `dt.date.today()` — déterminisme indépendant du jour d'exécution.
# 2026-09-07 = lundi, 2026-09-11 = vendredi, 2026-09-13 = dimanche (vérifié
# par `date.strftime('%A')`).
MONDAY = dt.date(2026, 9, 7)
FRIDAY = dt.date(2026, 9, 11)
SUNDAY = dt.date(2026, 9, 13)


def test_horizon_week_from_monday_spans_to_sunday(thingskit, monkeypatch,
                                                    tmp_path, capsys):
    # Lundi : la semaine civile va du jour même (lundi) au dimanche suivant
    # (6 jours plus tard) — pas une fenêtre de 7 jours glissants.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "w1", "title": "Dimanche même semaine", "start": 2,
         "startDate": _encode(SUNDAY)},
        {"uuid": "w2", "title": "Lundi suivant", "start": 2,
         "startDate": _encode(MONDAY + dt.timedelta(days=7))},
    ], capsys, horizon="week", today=MONDAY.isoformat())
    assert [o["uuid"] for o in out] == ["w1"]


def test_horizon_week_from_friday_ends_sunday_not_next_friday(
        thingskit, monkeypatch, tmp_path, capsys):
    # Vendredi : la semaine se termine le dimanche (3 jours), pas le vendredi
    # suivant (7 jours).
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "w1", "title": "Dimanche", "start": 2,
         "startDate": _encode(SUNDAY)},
        {"uuid": "w2", "title": "Lundi suivant", "start": 2,
         "startDate": _encode(SUNDAY + dt.timedelta(days=1))},
        {"uuid": "w3", "title": "Vendredi suivant", "start": 2,
         "startDate": _encode(FRIDAY + dt.timedelta(days=7))},
    ], capsys, horizon="week", today=FRIDAY.isoformat())
    assert [o["uuid"] for o in out] == ["w1"]


def test_horizon_week_from_sunday_is_the_day_itself(thingskit, monkeypatch,
                                                      tmp_path, capsys):
    # Dimanche : dernier jour de la semaine civile — la plage J->fin de
    # période se réduit au jour même, rien au-delà.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "w1", "title": "Lundi suivant", "start": 2,
         "startDate": _encode(SUNDAY + dt.timedelta(days=1))},
    ], capsys, horizon="week", today=SUNDAY.isoformat())
    assert out == []


def test_horizon_week_overdue_task_is_reported_not_omitted(
        thingskit, monkeypatch, tmp_path, capsys):
    # Datée avant J (mardi) mais dans la semaine civile en cours (débutée
    # lundi), toujours ouverte : rendue en retard, pas omise. Signal
    # "upcoming" (start=2), qui n'a pas de filet natif comme start=1.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "w2", "title": "Retard upcoming semaine", "start": 2,
         "startDate": _encode(MONDAY + dt.timedelta(days=1))},
    ], capsys, horizon="week", today=FRIDAY.isoformat())
    assert [o["uuid"] for o in out] == ["w2"]
    assert out[0]["overdue"] is True


def test_horizon_month_ends_last_day_of_month(thingskit, monkeypatch,
                                               tmp_path, capsys):
    today = dt.date(2026, 2, 27)  # février 2026, 28 jours
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "m1", "title": "Dernier jour du mois", "start": 2,
         "startDate": _encode(dt.date(2026, 2, 28))},
        {"uuid": "m2", "title": "Premier jour du mois suivant", "start": 2,
         "startDate": _encode(dt.date(2026, 3, 1))},
    ], capsys, horizon="month", today=today.isoformat())
    assert [o["uuid"] for o in out] == ["m1"]


def test_horizon_month_overdue_task_is_reported_not_omitted(
        thingskit, monkeypatch, tmp_path, capsys):
    today = dt.date(2026, 2, 20)
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "m1", "title": "Échéance en retard ce mois-ci", "start": 1,
         "startDate": None, "deadline": _encode(dt.date(2026, 2, 5))},
    ], capsys, horizon="month", today=today.isoformat())
    assert [o["uuid"] for o in out] == ["m1"]
    assert out[0]["overdue"] is True


def test_horizon_year_ends_december_31st(thingskit, monkeypatch, tmp_path,
                                          capsys):
    today = dt.date(2026, 12, 15)
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "y1", "title": "31 décembre", "start": 2,
         "startDate": _encode(dt.date(2026, 12, 31))},
        {"uuid": "y2", "title": "1er janvier suivant", "start": 2,
         "startDate": _encode(dt.date(2027, 1, 1))},
    ], capsys, horizon="year", today=today.isoformat())
    assert [o["uuid"] for o in out] == ["y1"]


def test_horizon_year_overdue_task_is_reported_not_omitted(
        thingskit, monkeypatch, tmp_path, capsys):
    today = dt.date(2026, 12, 15)
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "y1", "title": "Échéance en retard cette année", "start": 1,
         "startDate": None, "deadline": _encode(dt.date(2026, 3, 1))},
    ], capsys, horizon="year", today=today.isoformat())
    assert [o["uuid"] for o in out] == ["y1"]
    assert out[0]["overdue"] is True


def test_horizon_year_excludes_overdue_from_previous_year(
        thingskit, monkeypatch, tmp_path, capsys):
    # Datée avant J mais HORS de la période civile (année précédente) :
    # n'est pas un retard "dans la période" — reste exclue.
    today = dt.date(2026, 12, 15)
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "y1", "title": "Échéance de l'an dernier", "start": 1,
         "startDate": None, "deadline": _encode(dt.date(2025, 12, 20))},
    ], capsys, horizon="year", today=today.isoformat())
    assert out == []


def test_horizon_json_shape_preserved_plus_overdue_field(
        thingskit, monkeypatch, tmp_path, capsys):
    # `--json` reste une LISTE d'objets ; les champs existants sont
    # préservés, `overdue` est un champ AJOUTÉ, jamais une restructuration.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "t1", "title": "Aujourd'hui", "start": 1,
         "startDate": _encode(TODAY)},
    ], capsys, horizon="today")
    assert isinstance(out, list)
    assert out[0].keys() == {"uuid", "title", "where", "today",
                              "has_deadline", "overdue"}
    assert out[0]["overdue"] is False


def test_agenda_today_injection_makes_today_horizon_deterministic(
        thingskit, monkeypatch, tmp_path, capsys):
    # La date "aujourd'hui" est injectable, indépendante de dt.date.today().
    # Signal sensible à la comparaison réelle (deadline), pas `start=1` qui
    # vaut toujours "today" quelle que soit la date (insensible par
    # construction, donc impropre à prouver l'injection).
    fixed = dt.date(2030, 1, 1)
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "t1", "title": "Échéance du jour injecté", "start": 1,
         "startDate": None, "deadline": _encode(fixed)},
    ], capsys, horizon="today", today=fixed.isoformat())
    assert [o["uuid"] for o in out] == ["t1"]


def test_agenda_today_injection_via_env(thingskit, monkeypatch, tmp_path,
                                         capsys):
    fixed = dt.date(2031, 6, 15)
    monkeypatch.setenv("THINGSKIT_TODAY", fixed.isoformat())
    db_file = _make_db(tmp_path, [
        {"uuid": "t1", "title": "Échéance du jour via env", "start": 1,
         "startDate": None, "deadline": _encode(fixed)},
    ])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    ns = argparse.Namespace(horizon="today", json=True, today=None)
    rc = thingskit.cmd_agenda(ns)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert [o["uuid"] for o in out] == ["t1"]
