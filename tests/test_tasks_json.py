"""`thingskit tasks --json` doit exposer when/alarm/deadline/status — sans
quoi une tâche créée avec --when/--deadline est invérifiable (constat de
la mission). Ces tests ne touchent jamais Things : ils pointent
`thingskit.db_path` vers une base SQLite jetable dont le schéma imite les
seules colonnes lues par le CLI (cf. .schema TMTask réel).
"""
from __future__ import annotations

import argparse
import json
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


def _make_db(tmp_path, rows):
    """rows: list of dict with the TMTask columns above (defaults filled)."""
    db_file = tmp_path / "main.sqlite"
    con = sqlite3.connect(db_file)
    con.executescript(SCHEMA)
    defaults = dict(
        uuid=None, title=None, type=0, trashed=0, notes=None, project=None,
        heading=None, area=None, start=1, startDate=None, startBucket=None,
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


def _run_tasks(thingskit, monkeypatch, tmp_path, rows, capsys):
    db_file = _make_db(tmp_path, rows)
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    ns = argparse.Namespace(project=None, area=None, inbox=False, json=True)
    rc = thingskit.cmd_tasks(ns)
    assert rc == 0
    out = capsys.readouterr().out
    return json.loads(out)


def test_task_without_any_date(thingskit, monkeypatch, tmp_path, capsys):
    out = _run_tasks(thingskit, monkeypatch, tmp_path, [
        {"uuid": "u1", "title": "Sans date"},
    ], capsys)
    assert out[0]["when"] is None
    assert out[0]["alarm"] is None
    assert out[0]["deadline"] is None
    assert out[0]["status"] == "open"


def test_task_with_when_only(thingskit, monkeypatch, tmp_path, capsys):
    out = _run_tasks(thingskit, monkeypatch, tmp_path, [
        {"uuid": "u2", "title": "When seul", "startDate": 132810496},
    ], capsys)
    assert out[0]["when"] == "2026-08-14"
    assert out[0]["alarm"] is None
    assert out[0]["deadline"] is None


def test_task_with_when_and_alarm(thingskit, monkeypatch, tmp_path, capsys):
    out = _run_tasks(thingskit, monkeypatch, tmp_path, [
        {"uuid": "u3", "title": "When + alarme", "startDate": 132811008,
         "reminderTime": 603979776},
    ], capsys)
    assert out[0]["when"] == "2026-08-18"
    assert out[0]["alarm"] == "09:00"


def test_task_with_deadline_only(thingskit, monkeypatch, tmp_path, capsys):
    out = _run_tasks(thingskit, monkeypatch, tmp_path, [
        {"uuid": "u4", "title": "Deadline seule", "deadline": 132811392},
    ], capsys)
    assert out[0]["when"] is None
    assert out[0]["deadline"] == "2026-08-21"


def test_task_with_when_and_deadline(thingskit, monkeypatch, tmp_path, capsys):
    out = _run_tasks(thingskit, monkeypatch, tmp_path, [
        {"uuid": "u5", "title": "Les deux", "startDate": 132811136,
         "deadline": 132811776, "reminderTime": 603979776},
    ], capsys)
    assert out[0]["when"] == "2026-08-19"
    assert out[0]["alarm"] == "09:00"
    assert out[0]["deadline"] == "2026-08-24"


@pytest.mark.parametrize("status_int,label", [(0, "open"), (2, "canceled"), (3, "completed")])
def test_task_status_label(thingskit, monkeypatch, tmp_path, capsys, status_int, label):
    out = _run_tasks(thingskit, monkeypatch, tmp_path, [
        {"uuid": "u6", "title": "Statut", "status": status_int},
    ], capsys)
    assert out[0]["status"] == label


# ---------------------------------------------------------------------------
# notes — la lecture doit restituer ce que l'écriture a posé.
#
# Sans ce champ, la doctrine « une écriture vérifie son effet avant de rendre
# la main » est invérifiable depuis la façade : `set-notes` relit la base en
# interne, mais rien de ce qu'il a écrit n'est observable par l'utilisateur.
# Constat du 2026-08-12 : les 32 tâches d'une area rendues avec des notes
# absentes du JSON alors que l'une portait 940 caractères en base.
# ---------------------------------------------------------------------------
def test_task_notes_are_exposed(thingskit, monkeypatch, tmp_path, capsys):
    out = _run_tasks(thingskit, monkeypatch, tmp_path, [
        {"uuid": "n1", "title": "Avec notes",
         "notes": "Note vault : obsidian://open?vault=basic-memory&file=X"},
    ], capsys)
    assert out[0]["notes"] == ("Note vault : "
                               "obsidian://open?vault=basic-memory&file=X")


def test_task_notes_multiline_are_exposed_verbatim(thingskit, monkeypatch,
                                                   tmp_path, capsys):
    body = "Première ligne\nDeuxième — avec tiret cadratin\n\nQuatrième"
    out = _run_tasks(thingskit, monkeypatch, tmp_path, [
        {"uuid": "n2", "title": "Notes multilignes", "notes": body},
    ], capsys)
    assert out[0]["notes"] == body


def test_task_without_notes_reads_empty_string(thingskit, monkeypatch,
                                               tmp_path, capsys):
    # NULL et chaîne vide sont indistinguables côté usage : les deux disent
    # « pas de notes ». On normalise, pour qu'un consommateur JSON n'ait pas
    # à traiter deux formes de la même absence.
    out = _run_tasks(thingskit, monkeypatch, tmp_path, [
        {"uuid": "n3", "title": "Sans notes", "notes": None},
    ], capsys)
    assert out[0]["notes"] == ""


# ---------------------------------------------------------------------------
# list — où la tâche apparaît réellement dans Things.
#
# Table de vérité MESURÉE le 2026-08-12 en croisant l'AppleScript
# `to dos of list "<libellé>"` avec les colonnes `start` / `startDate` de la
# base, re-mesurée le 2026-08-12 (BUG-001) sur la totalité des tâches
# ouvertes (610) :
#
#   start=0, sans date  -> À classer      (126/126)
#   start=1, sans date  -> À tout moment  (458)
#   start=1, avec date  -> Aujourd'hui    (11)
#   start=2, sans date  -> Un jour        (1)
#   start=2, avec date  -> À venir        (14)
#
# Le point contre-intuitif est la dernière ligne : `start=2` n'est PAS
# « Un jour » à lui seul. C'est la présence d'une `startDate` qui décide, et
# une tâche planifiée à une date future porte bien `start=2`. Lire `start=2`
# comme « Un jour » sans regarder la date est le contresens qui a fait croire
# à un défaut d'écriture le 2026-08-12.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("start,start_date,expected", [
    (0, None, "inbox"),
    (1, None, "anytime"),
    (1, 132810240, "today"),
    (2, None, "someday"),
    (2, 132810496, "upcoming"),
])
def test_scheduling_list_truth_table(thingskit, start, start_date, expected):
    assert thingskit.scheduling_list(start, start_date) == expected


def test_scheduling_list_unknown_start_is_none(thingskit):
    # Ne jamais inventer un libellé pour une valeur non mesurée.
    assert thingskit.scheduling_list(7, None) is None


def test_dated_task_is_reported_upcoming_not_someday(thingskit, monkeypatch,
                                                     tmp_path, capsys):
    out = _run_tasks(thingskit, monkeypatch, tmp_path, [
        {"uuid": "s1", "title": "Planifiée au 14", "start": 2,
         "startDate": 132810496},
    ], capsys)
    assert out[0]["when"] == "2026-08-14"
    assert out[0]["list"] == "upcoming"


def test_undated_someday_task_is_reported_someday(thingskit, monkeypatch,
                                                  tmp_path, capsys):
    out = _run_tasks(thingskit, monkeypatch, tmp_path, [
        {"uuid": "s2", "title": "Un jour", "start": 2, "startDate": None},
    ], capsys)
    assert out[0]["when"] is None
    assert out[0]["list"] == "someday"
