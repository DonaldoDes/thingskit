"""`thingskit find-task` — recherche d'une tâche OUVERTE bornée à une liste de
rattachement (projet, area ou heading), servant de garde d'idempotence à un
cron qui doit décider « faut-il créer cette action ? ».

Contrat de sortie (US-001, cf. précédent BUG-003 côté omnikit — ne jamais
partager un code entre « non trouvé » et « échec réel ») :
  0 = trouvée (ouverte, dans la cible demandée)
  1 = non trouvée (cible résolue, mais aucune tâche ouverte de ce titre)
  2 = erreur (cible de rattachement introuvable ou ambiguë) — jamais un
      « non trouvé » silencieux

Lecture pure : ces tests ne mockent aucune surface d'écriture (`osa`,
`url_open`, `ensure_running`) car `find-task` n'en emprunte aucune — seul
`db_path` est redirigé vers une base SQLite jetable.
"""
from __future__ import annotations

import argparse
import sqlite3

import pytest


SCHEMA = """
CREATE TABLE TMArea (uuid TEXT PRIMARY KEY, title TEXT);
CREATE TABLE TMTask (
    uuid TEXT PRIMARY KEY,
    title TEXT,
    type INTEGER,
    trashed INTEGER,
    project TEXT,
    heading TEXT,
    area TEXT,
    status INTEGER
);
"""

TYPE_TASK, TYPE_PROJECT, TYPE_HEADING = 0, 1, 2
OPEN, CANCELED, COMPLETED = 0, 2, 3


def _make_db(tmp_path, areas=(), tasks=()):
    db_file = tmp_path / "main.sqlite"
    con = sqlite3.connect(db_file)
    con.executescript(SCHEMA)
    for a in areas:
        con.execute("insert into TMArea (uuid, title) values (?, ?)",
                    (a["uuid"], a["title"]))
    defaults = dict(uuid=None, title=None, type=TYPE_TASK, trashed=0,
                    project=None, heading=None, area=None, status=OPEN)
    for r in tasks:
        row = {**defaults, **r}
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,project,heading,area,status) "
            "values (:uuid,:title,:type,:trashed,:project,:heading,:area,:status)",
            row)
    con.commit()
    con.close()
    return db_file


def _ns(title=None, list=None, heading=None, json=False):
    return argparse.Namespace(title=title, list=list, heading=heading, json=json)


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    def _set(areas=(), tasks=()):
        db_file = _make_db(tmp_path, areas, tasks)
        monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
        return db_file
    return _set


# --- trouvé / non-trouvé / erreur -------------------------------------------

def test_open_task_found_in_targeted_project(thingskit, rigged):
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
        {"uuid": "T1", "title": "Ma tâche", "type": TYPE_TASK, "project": "P1",
         "status": OPEN},
    ])
    rc = thingskit.cmd_find_task(_ns(title="Ma tâche", list="Projet X"))
    assert rc == 0


def test_task_absent_from_targeted_list_is_not_found(thingskit, rigged):
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
    ])
    rc = thingskit.cmd_find_task(_ns(title="Ma tâche", list="Projet X"))
    assert rc == 1


def test_task_found_elsewhere_does_not_leak_across_lists(thingskit, rigged):
    """Une tâche du même titre existant dans une AUTRE liste ne doit ni
    empêcher ni faussement autoriser la création dans la cible visée."""
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
        {"uuid": "P2", "title": "Projet Y", "type": TYPE_PROJECT},
        {"uuid": "T1", "title": "Ma tâche", "type": TYPE_TASK, "project": "P2",
         "status": OPEN},
    ])
    rc = thingskit.cmd_find_task(_ns(title="Ma tâche", list="Projet X"))
    assert rc == 1


def test_completed_task_does_not_count_as_found(thingskit, rigged):
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
        {"uuid": "T1", "title": "Ma tâche", "type": TYPE_TASK, "project": "P1",
         "status": COMPLETED},
    ])
    rc = thingskit.cmd_find_task(_ns(title="Ma tâche", list="Projet X"))
    assert rc == 1


def test_canceled_task_does_not_count_as_found(thingskit, rigged):
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
        {"uuid": "T1", "title": "Ma tâche", "type": TYPE_TASK, "project": "P1",
         "status": CANCELED},
    ])
    rc = thingskit.cmd_find_task(_ns(title="Ma tâche", list="Projet X"))
    assert rc == 1


def test_missing_target_list_is_an_explicit_error_not_silent_not_found(thingskit, rigged):
    rigged(tasks=[])
    rc = thingskit.cmd_find_task(_ns(title="Ma tâche", list="Liste fantôme"))
    assert rc == 2
    assert rc not in (0, 1)  # code distinct des deux autres cas


def test_error_code_is_distinct_from_found_and_not_found(thingskit, rigged):
    """Les 3 codes doivent être mutuellement exclusifs — pas de recouvrement
    entre 'non trouvé' et 'erreur' (précédent BUG-003)."""
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
    ])
    rc_not_found = thingskit.cmd_find_task(_ns(title="Absente", list="Projet X"))
    rc_error = thingskit.cmd_find_task(_ns(title="Absente", list="Fantôme"))
    assert {0, 1, 2} == {0, 1, 2}
    assert rc_not_found == 1
    assert rc_error == 2
    assert rc_not_found != rc_error


# --- bornage à une area ------------------------------------------------------

def test_open_task_found_in_targeted_area(thingskit, rigged):
    rigged(
        areas=[{"uuid": "A1", "title": "Area Z"}],
        tasks=[{"uuid": "T1", "title": "Ma tâche", "type": TYPE_TASK,
                "area": "A1", "status": OPEN}],
    )
    rc = thingskit.cmd_find_task(_ns(title="Ma tâche", list="Area Z"))
    assert rc == 0


# --- bornage à un heading ----------------------------------------------------

def test_open_task_found_in_targeted_heading(thingskit, rigged):
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
        {"uuid": "H1", "title": "Section A", "type": TYPE_HEADING, "project": "P1"},
        {"uuid": "T1", "title": "Ma tâche", "type": TYPE_TASK, "heading": "H1",
         "status": OPEN},
    ])
    rc = thingskit.cmd_find_task(
        _ns(title="Ma tâche", list="Projet X", heading="Section A"))
    assert rc == 0


def test_task_in_other_heading_of_same_project_not_found(thingskit, rigged):
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
        {"uuid": "H1", "title": "Section A", "type": TYPE_HEADING, "project": "P1"},
        {"uuid": "H2", "title": "Section B", "type": TYPE_HEADING, "project": "P1"},
        {"uuid": "T1", "title": "Ma tâche", "type": TYPE_TASK, "heading": "H2",
         "status": OPEN},
    ])
    rc = thingskit.cmd_find_task(
        _ns(title="Ma tâche", list="Projet X", heading="Section A"))
    assert rc == 1


def test_missing_heading_in_existing_project_is_an_error(thingskit, rigged):
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
    ])
    rc = thingskit.cmd_find_task(
        _ns(title="Ma tâche", list="Projet X", heading="Section fantôme"))
    assert rc == 2


# --- ambiguïté : refus, jamais 'le premier' ---------------------------------

def test_ambiguous_list_name_across_project_and_area_is_an_error(thingskit, rigged):
    rigged(
        areas=[{"uuid": "A1", "title": "Doublon"}],
        tasks=[{"uuid": "P1", "title": "Doublon", "type": TYPE_PROJECT}],
    )
    rc = thingskit.cmd_find_task(_ns(title="Ma tâche", list="Doublon"))
    assert rc == 2


def test_ambiguous_project_title_is_an_error(thingskit, rigged):
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
        {"uuid": "P2", "title": "Projet X", "type": TYPE_PROJECT},
    ])
    rc = thingskit.cmd_find_task(_ns(title="Ma tâche", list="Projet X"))
    assert rc == 2


# --- exploitable en shell sans parser la sortie -----------------------------

def test_exit_code_alone_is_sufficient_in_a_shell_conditional(thingskit, rigged, capsys):
    """Démonstration : aucune inspection de stdout n'est nécessaire pour
    décider — seul le code de sortie compte, comme dans un `if cmd; then`."""
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
        {"uuid": "T1", "title": "Ma tâche", "type": TYPE_TASK, "project": "P1",
         "status": OPEN},
    ])
    rc = thingskit.cmd_find_task(_ns(title="Ma tâche", list="Projet X"))
    capsys.readouterr()  # la sortie n'est pas inspectée : seul rc décide
    decision = "already exists, skip create" if rc == 0 else (
        "create it" if rc == 1 else "abort: broken target")
    assert decision == "already exists, skip create"


# --- aucune écriture : AC-6 --------------------------------------------------

def test_no_write_surface_touched(thingskit, rigged, monkeypatch):
    """`find-task` n'emprunte aucun chemin d'écriture. Toute invocation
    accidentelle de `osa`/`url_open`/`ensure_running` fait échouer le test."""
    def _boom(*a, **k):
        raise AssertionError("find-task ne doit jamais solliciter d'écriture")
    monkeypatch.setattr(thingskit, "osa", _boom)
    monkeypatch.setattr(thingskit, "url_open", _boom)
    monkeypatch.setattr(thingskit, "ensure_running", _boom)
    rigged(tasks=[
        {"uuid": "P1", "title": "Projet X", "type": TYPE_PROJECT},
        {"uuid": "T1", "title": "Ma tâche", "type": TYPE_TASK, "project": "P1",
         "status": OPEN},
    ])
    assert thingskit.cmd_find_task(_ns(title="Ma tâche", list="Projet X")) == 0
