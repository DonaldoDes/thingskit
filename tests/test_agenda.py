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


def _run_agenda(thingskit, monkeypatch, tmp_path, rows, capsys, horizon="today"):
    db_file = _make_db(tmp_path, rows)
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    ns = argparse.Namespace(horizon=horizon, json=True)
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


def test_horizon_week_includes_upcoming_within_7_days(thingskit, monkeypatch,
                                                       tmp_path, capsys):
    # BUG-001-02.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "w1", "title": "Dans 3 jours", "start": 2,
         "startDate": _encode(IN_3_DAYS)},
    ], capsys, horizon="week")
    assert [o["uuid"] for o in out] == ["w1"]


def test_horizon_week_excludes_upcoming_beyond_7_days(thingskit, monkeypatch,
                                                       tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "w2", "title": "Dans 10 jours", "start": 2,
         "startDate": _encode(IN_10_DAYS)},
    ], capsys, horizon="week")
    assert out == []


def test_horizon_week_includes_deadline_within_7_days(thingskit, monkeypatch,
                                                       tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "w3", "title": "Échéance dans 3 jours", "start": 1,
         "startDate": None, "deadline": _encode(IN_3_DAYS)},
    ], capsys, horizon="week")
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
    # "today" est strictement inclus dans "week".
    rows = [
        {"uuid": "t1", "title": "Aujourd'hui", "start": 1,
         "startDate": _encode(TODAY)},
        {"uuid": "w1", "title": "Dans 3 jours", "start": 2,
         "startDate": _encode(IN_3_DAYS)},
    ]
    today_out = _run_agenda(thingskit, monkeypatch, tmp_path, rows, capsys,
                            horizon="today")
    week_out = _run_agenda(thingskit, monkeypatch, tmp_path, rows, capsys,
                           horizon="week")
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
