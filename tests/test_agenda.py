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
import re
import sqlite3

import pytest


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
                 today=None, lead_days=None, text=False):
    db_file = _make_db(tmp_path, rows)
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    kwargs = {}
    if lead_days is not None:
        kwargs["lead_days"] = lead_days
    ns = argparse.Namespace(horizon=horizon, json=not text, today=today,
                            **kwargs)
    rc = thingskit.cmd_agenda(ns)
    assert rc == 0
    if text:
        return capsys.readouterr().out
    return json.loads(capsys.readouterr().out)


TODAY = dt.date.today()
YESTERDAY = TODAY - dt.timedelta(days=1)
TOMORROW = TODAY + dt.timedelta(days=1)
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


def test_horizon_today_excludes_deadline_beyond_the_start_window(
        thingskit, monkeypatch, tmp_path, capsys):
    # TOOL-433 excluait J+3 ; TOOL-436 l'inclut (fenêtre de démarrage, 7 j).
    # Ce qui reste exclu de `today` : une échéance au-delà de la fenêtre.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "t5", "title": "Échéance dans 10 jours", "start": 1,
         "startDate": None, "deadline": _encode(IN_10_DAYS)},
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
    # (J+3 toujours dans la fenêtre) ne tient plus une fois la semaine
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


def test_horizon_year_includes_overdue_deadline_from_previous_year(
        thingskit, monkeypatch, tmp_path, capsys):
    # TOOL-433 l'excluait (« pas un retard dans la période »). TOOL-436 :
    # une échéance DÉPASSÉE encore ouverte est en retard quel que soit
    # l'horizon — sinon `today` (qui la rend, § 1 du ticket) ne serait plus
    # un sous-ensemble de `year`.
    today = dt.date(2026, 12, 15)
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "y1", "title": "Échéance de l'an dernier", "start": 1,
         "startDate": None, "deadline": _encode(dt.date(2025, 12, 20))},
    ], capsys, horizon="year", today=today.isoformat())
    assert [o["uuid"] for o in out] == ["y1"]
    assert out[0]["overdue"] is True
    assert out[0]["start_window"]["reason"] == "overdue"


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
                              "has_deadline", "overdue", "start_window"}
    assert out[0]["overdue"] is False
    assert out[0]["start_window"] is None


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


# ---------------------------------------------------------------------------
# TOOL-436 — fenêtre de démarrage : « à faire aujourd'hui » ≠ « échéance
# aujourd'hui ». Sur `today`, en plus du planifié du jour : toute échéance
# DÉPASSÉE encore ouverte, toute échéance dans la fenêtre par défaut
# (`LEAD_DAYS_DEFAULT` = 7, `--lead-days N`), et les tâches portant un
# marqueur `lead: Nj` en note (fenêtre propre à la tâche).
# ---------------------------------------------------------------------------
FIXED = dt.date(2026, 9, 11)  # un vendredi


def _deadline_task(uuid, days_from_today, notes=None, today=FIXED):
    return {"uuid": uuid, "title": f"Échéance J{days_from_today:+d}",
            "start": 1, "startDate": None, "notes": notes,
            "deadline": _encode(today + dt.timedelta(days=days_from_today))}


def test_today_includes_overdue_deadline_with_start_window(
        thingskit, monkeypatch, tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d1", -30),
    ], capsys, horizon="today", today=FIXED.isoformat())
    assert [o["uuid"] for o in out] == ["d1"]
    assert out[0]["overdue"] is True
    assert out[0]["start_window"] == {
        "reason": "overdue", "deadline": "2026-08-12", "lead_days": 7}


def test_today_includes_deadline_at_j_plus_3_as_near(
        thingskit, monkeypatch, tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d1", 3),
    ], capsys, horizon="today", today=FIXED.isoformat())
    assert [o["uuid"] for o in out] == ["d1"]
    assert out[0]["overdue"] is False
    assert out[0]["start_window"] == {
        "reason": "near", "deadline": "2026-09-14", "lead_days": 7}


def test_today_excludes_deadline_at_j_plus_10_without_lead(
        thingskit, monkeypatch, tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d1", 10),
    ], capsys, horizon="today", today=FIXED.isoformat())
    assert out == []


def test_today_includes_deadline_at_j_plus_10_with_lead_14(
        thingskit, monkeypatch, tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d1", 10, notes="Prérequis lourds.\nlead: 14j\n"),
    ], capsys, horizon="today", today=FIXED.isoformat())
    assert [o["uuid"] for o in out] == ["d1"]
    assert out[0]["start_window"] == {
        "reason": "lead", "deadline": "2026-09-21", "lead_days": 14}


def test_today_default_window_is_seven_days_inclusive(
        thingskit, monkeypatch, tmp_path, capsys):
    assert thingskit.LEAD_DAYS_DEFAULT == 7
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d7", 7), _deadline_task("d8", 8),
    ], capsys, horizon="today", today=FIXED.isoformat())
    assert [o["uuid"] for o in out] == ["d7"]


def test_lead_days_zero_keeps_only_the_deadline_of_the_day(
        thingskit, monkeypatch, tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d0", 0), _deadline_task("d1", 1),
        _deadline_task("d14", 10, notes="lead: 14j"),
    ], capsys, horizon="today", today=FIXED.isoformat(), lead_days=0)
    assert sorted(o["uuid"] for o in out) == ["d0", "d14"]
    by = {o["uuid"]: o for o in out}
    assert by["d0"]["start_window"] == {
        "reason": "near", "deadline": "2026-09-11", "lead_days": 0}
    assert by["d14"]["start_window"]["reason"] == "lead"


def test_lead_days_option_widens_the_default_window(
        thingskit, monkeypatch, tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d10", 10), _deadline_task("d31", 31),
    ], capsys, horizon="today", today=FIXED.isoformat(), lead_days=30)
    assert [o["uuid"] for o in out] == ["d10"]
    assert out[0]["start_window"]["lead_days"] == 30


def test_task_lead_marker_replaces_the_default_window(
        thingskit, monkeypatch, tmp_path, capsys):
    # La fenêtre est PROPRE à la tâche : `lead: 2j` la rend invisible à J+5
    # même si le défaut est 7 — le marqueur remplace, il ne s'ajoute pas.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d5", 5, notes="lead: 2j"),
        _deadline_task("d2", 2, notes="lead: 2j"),
    ], capsys, horizon="today", today=FIXED.isoformat())
    assert [o["uuid"] for o in out] == ["d2"]
    assert out[0]["start_window"] == {
        "reason": "lead", "deadline": "2026-09-13", "lead_days": 2}


def test_week_includes_deadline_outside_civil_week_but_inside_window(
        thingskit, monkeypatch, tmp_path, capsys):
    # Vendredi 11 : la semaine civile finit dimanche 13, la fenêtre de 7
    # jours va jusqu'au vendredi 18. Lundi 14 est hors période, dans la
    # fenêtre -> inclus ; samedi 19 est hors des deux -> exclu.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d3", 3), _deadline_task("d8", 8),
    ], capsys, horizon="week", today=FIXED.isoformat())
    assert [o["uuid"] for o in out] == ["d3"]
    assert out[0]["start_window"]["reason"] == "near"


def test_month_includes_deadline_outside_civil_month_but_inside_lead(
        thingskit, monkeypatch, tmp_path, capsys):
    today = dt.date(2026, 9, 28)
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("m1", 12, notes="lead: 20j", today=today),
        _deadline_task("m2", 12, today=today),
    ], capsys, horizon="month", today=today.isoformat())
    assert [o["uuid"] for o in out] == ["m1"]
    assert out[0]["start_window"]["reason"] == "lead"


def test_year_includes_deadline_of_next_january_inside_window(
        thingskit, monkeypatch, tmp_path, capsys):
    today = dt.date(2026, 12, 29)
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("y1", 4, today=today),   # 2 janvier 2027
        _deadline_task("y2", 20, today=today),  # 18 janvier 2027
    ], capsys, horizon="year", today=today.isoformat())
    assert [o["uuid"] for o in out] == ["y1"]
    assert out[0]["start_window"]["deadline"] == "2027-01-02"


def test_deadline_inside_period_but_beyond_window_has_no_start_window(
        thingskit, monkeypatch, tmp_path, capsys):
    today = dt.date(2026, 9, 1)
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("m1", 20, today=today),
    ], capsys, horizon="month", today=today.isoformat())
    assert [o["uuid"] for o in out] == ["m1"]
    assert out[0]["start_window"] is None


def test_horizon_month_leap_year_ends_february_29th(
        thingskit, monkeypatch, tmp_path, capsys):
    # Mineur relevé en review de TOOL-433 : 2028 est bissextile.
    today = dt.date(2028, 2, 10)
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "b1", "title": "29 février", "start": 2,
         "startDate": _encode(dt.date(2028, 2, 29))},
        {"uuid": "b2", "title": "1er mars", "start": 2,
         "startDate": _encode(dt.date(2028, 3, 1))},
    ], capsys, horizon="month", today=today.isoformat())
    assert [o["uuid"] for o in out] == ["b1"]
    assert thingskit._month_last_day(dt.date(2028, 2, 1)) == dt.date(2028, 2, 29)
    assert thingskit._month_last_day(dt.date(2027, 2, 1)) == dt.date(2027, 2, 28)


def test_text_rendering_marks_overdue_and_start(
        thingskit, monkeypatch, tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d1", -2), _deadline_task("d2", 3),
    ], capsys, horizon="today", today=FIXED.isoformat(), text=True)
    lines = out.splitlines()
    assert any("⚠ en retard" in ln for ln in lines), lines
    assert any("→ à commencer, échéance le 2026-09-14" in ln for ln in lines), lines


# --- Marqueur `lead:` : entrée NON FIABLE (zone sensible 1) -----------------
@pytest.mark.parametrize("notes", [
    "lead: -5j",                    # négatif
    "lead: abc",                    # non numérique
    "lead: 1e3",                    # notation scientifique
    "lead:",                        # vide
    "lead: \x1b[2K\r14j",           # séquence de contrôle
    "lead: ١٤j",                    # chiffres non ASCII (arabe-indien)
    "lead: 14​j",              # espace de largeur nulle dans la valeur
    "lead: 14j 15j",                # deux valeurs
    "xlead: 14j",                   # préfixe collé
    "lead: 14 jours de plus que prévu",  # texte après la valeur
    "lead: 14j\x00",                # NUL final
])
def test_lead_marker_garbage_never_crashes_and_falls_back_to_default(
        thingskit, monkeypatch, tmp_path, capsys, notes):
    assert thingskit._task_lead_days(notes) is None
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d10", 10, notes=notes),
        _deadline_task("d3", 3, notes=notes),
    ], capsys, horizon="today", today=FIXED.isoformat())
    assert [o["uuid"] for o in out] == ["d3"]
    assert out[0]["start_window"] == {
        "reason": "near", "deadline": "2026-09-14", "lead_days": 7}


@pytest.mark.parametrize("notes, expected", [
    ("lead: 14j", 14),
    ("LEAD : 14 J", 14),
    ("lead:14", 14),
    ("lead: 14 jours", 14),
    ("lead: 14d", 14),
    ("lead: 14 days", 14),
    ("Contexte.\r\nlead: 14j\r\nSuite.", 14),
    (" lead: 14j", 14),        # espace insécable devant
    ("lead: 0j", 0),
    ("lead: 007j", 7),
])
def test_lead_marker_accepted_forms(thingskit, notes, expected):
    assert thingskit._task_lead_days(notes) == expected


@pytest.mark.parametrize("notes", [
    "lead: 99999j",
    "lead: " + "9" * 5000 + "j",    # au-delà de la limite int(str) de Python
    "lead: 400j",
], ids=["99999", "5000-chiffres", "400"])
def test_lead_marker_huge_value_is_clamped_to_the_ceiling(thingskit, notes):
    assert thingskit._task_lead_days(notes) == thingskit.LEAD_DAYS_MAX
    assert thingskit.LEAD_DAYS_MAX == 366


def test_lead_marker_on_non_string_notes_is_ignored(thingskit):
    assert thingskit._task_lead_days(None) is None
    assert thingskit._task_lead_days(b"lead: 14j") is None
    assert thingskit._task_lead_days(14) is None


def test_lead_marker_value_never_reaches_the_output_raw(
        thingskit, monkeypatch, tmp_path, capsys):
    # Le rendu ne porte QUE l'entier converti — jamais la note.
    # Marqueur VALIDE sur sa ligne, puis du contenu hostile sur la suivante :
    # la fenêtre est lue (14), la note ne sort jamais.
    hostile = "lead: 14j\n\x1b[2K\rTÂCHE FAITE"
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d10", 10, notes=hostile),
    ], capsys, horizon="today", today=FIXED.isoformat(), text=True)
    assert "\x1b" not in out and "\r" not in out
    assert "TÂCHE FAITE" not in out
    js = _run_agenda(thingskit, monkeypatch, tmp_path, [
        _deadline_task("d10", 10, notes=hostile),
    ], capsys, horizon="today", today=FIXED.isoformat())
    assert js[0]["start_window"]["lead_days"] == 14


@pytest.mark.parametrize("value", [-1, 367, 10**9])
def test_lead_days_option_out_of_bounds_is_refused(
        thingskit, monkeypatch, tmp_path, capsys, value):
    db_file = _make_db(tmp_path, [_deadline_task("d1", 1)])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    ns = argparse.Namespace(horizon="today", json=True, today=FIXED.isoformat(),
                            lead_days=value)
    rc = thingskit.cmd_agenda(ns)
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "lead-days" in captured.err


def test_agenda_help_documents_lead_days_and_the_marker(run_cli):
    code, help_text, err = run_cli(["agenda", "--help"])
    assert code == 0, err
    assert "--lead-days" in help_text
    assert "défaut 7" in help_text
    assert "lead: 14j" in help_text


def test_lead_marker_parsing_is_linear_in_the_length_of_the_note(thingskit):
    # Review sécurité TOOL-436 : deux classes de blancs adjacentes autour de
    # l'unité optionnelle donnaient un backtracking quadratique — mesuré
    # 4 000 blancs -> 75 ms, 16 000 -> 1 194 ms sur le motif fautif. Une note
    # Things est une entrée non fiable ; sa longueur ne doit pas commander
    # le temps de `agenda`. Le motif fautif tient ici plusieurs dizaines de
    # secondes ; le motif attendu, quelques millisecondes.
    import time
    hostile = "lead: 5" + " " * 100_000 + "x"
    started = time.perf_counter()
    result = thingskit._task_lead_days(hostile)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result is None
    assert elapsed_ms < 50, f"{elapsed_ms:.0f} ms"


def test_lead_marker_pattern_has_no_adjacent_overlapping_quantifiers(thingskit):
    # Garde STRUCTURELLE, indépendante de l'horloge : deux quantifieurs
    # `[...]*` portant sur la même classe ne doivent jamais être séparés par
    # un seul terme optionnel — c'est la forme exacte qui backtrack.
    pattern = thingskit._LEAD_MARKER.pattern
    assert not re.search(r"\]\*\(\?:[^)]*\)\?\[", pattern), pattern



def test_today_scheduled_task_with_past_deadline_is_overdue(
        thingskit, monkeypatch, tmp_path, capsys):
    # UAT TOOL-436 : « le retard n'est jamais omis ». Planifiée aujourd'hui
    # (`scheduling_list == "today"`) ET échéance dépassée : la planification
    # ne masque pas le retard — `overdue: true`, `start_window: overdue`.
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "d1", "title": "Planifiée aujourd'hui, échéance passée",
         "start": 1, "startDate": _encode(FIXED),
         "deadline": _encode(FIXED - dt.timedelta(days=4))},
    ], capsys, horizon="today", today=FIXED.isoformat())
    assert [o["uuid"] for o in out] == ["d1"]
    assert out[0]["today"] is True
    assert out[0]["overdue"] is True
    assert out[0]["start_window"] == {
        "reason": "overdue", "deadline": "2026-09-07", "lead_days": 7}


def test_today_scheduled_task_with_past_deadline_is_marked_late_in_text(
        thingskit, monkeypatch, tmp_path, capsys):
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "d1", "title": "Planifiée aujourd'hui, échéance passée",
         "start": 1, "startDate": _encode(FIXED),
         "deadline": _encode(FIXED - dt.timedelta(days=4))},
    ], capsys, horizon="today", today=FIXED.isoformat(), text=True)
    assert "⚠ en retard" in out, out


def test_today_scheduled_task_with_future_deadline_is_not_overdue(
        thingskit, monkeypatch, tmp_path, capsys):
    # Garde symétrique : planifiée aujourd'hui, échéance à venir -> jamais
    # `overdue`. (Si elle doit porter `start_window: near`, c'est une
    # décision à part — non prise ici.)
    out = _run_agenda(thingskit, monkeypatch, tmp_path, [
        {"uuid": "d1", "title": "Planifiée aujourd'hui, échéance J+3",
         "start": 1, "startDate": _encode(FIXED),
         "deadline": _encode(FIXED + dt.timedelta(days=3))},
    ], capsys, horizon="today", today=FIXED.isoformat())
    assert out[0]["today"] is True
    assert out[0]["overdue"] is False
