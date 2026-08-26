"""`thingskit set-notes` / `append-notes --project-id UUID`.

Pourquoi cette voie d'adressage
-------------------------------
`--project` résout par **titre exact**. Le vault, lui, ne connaît pas les titres
Things : il porte un `things_id` en frontmatter. Poser le lien retour Things →
note sur 38 projets à partir du vault imposait donc soit de retraduire chaque
identifiant en titre (avec le risque d'homonymie que le projet refuse par
principe), soit de contourner le CLI par un script ad hoc. `--project-id` est la
troisième voie, et la seule qui respecte la règle du dépôt : tout ce que le
harness sait faire avec Things passe par ici.

Ce qui est constaté, et ce qui est refusé
-----------------------------------------
Mesuré le 2026-08-12 sur la vraie base, pas déduit :

  - `to do id "<uuid de projet>"` **résout** — la classe `project` hérite de
    `to do` dans le dictionnaire AppleScript, donc l'écriture des notes d'un
    projet emprunte exactement la même surface que celle d'une tâche, et
    `_write_task_notes` est réutilisée telle quelle plutôt que dupliquée.
  - `to do id "<uuid de heading>"` **résout aussi**, et `set notes` y retourne
    `0` — mais la valeur n'est **jamais** stockée : ni relue par AppleScript, ni
    présente en base après plusieurs secondes. C'est le mode d'échec que la
    constitution nomme (« code retour 0 = commande envoyée » ≠ « effet
    constaté »). Un heading est donc **refusé avant toute sollicitation de
    l'application** : lui envoyer l'ordre laisserait croire à un lien posé.

Ces tests ne touchent jamais l'application ni la vraie base.
"""
from __future__ import annotations

import argparse
import sqlite3

import pytest

from test_delete_task import _make_db  # noqa: F401  (utilisé par les fixtures)
from test_task_notes import live, rigged  # noqa: F401  (fixtures réexportées)

PROJECT = "PPPPPPPPPPPPPPPPPPPPPP"
HEADING = "HHHHHHHHHHHHHHHHHHHHHH"

TYPE_TASK, TYPE_PROJECT, TYPE_HEADING = 0, 1, 2

# Fixture volontairement percent-encodée ET accentuée : c'est la classe
# de caractères que les notes doivent traverser sans altération.
URI = ("obsidian://open?vault=basic-memory&file=projects%2F"
       "Projet%20de%20d%C3%A9monstration")


def _ns_set(project=None, project_id=None, task=None, task_id=None, notes=""):
    return argparse.Namespace(project=project, project_id=project_id,
                              task=task, task_id=task_id, notes=notes)


def _ns_app(project=None, project_id=None, task=None, task_id=None, line=""):
    return argparse.Namespace(project=project, project_id=project_id,
                              task=task, task_id=task_id, line=line)


def _notes(db_file, uuid=PROJECT):
    con = sqlite3.connect(db_file)
    try:
        return con.execute("select notes from TMTask where uuid=?",
                           (uuid,)).fetchone()[0]
    finally:
        con.close()


# --- Le chemin nominal -----------------------------------------------------

def test_set_notes_by_project_id_writes_via_targeted_applescript(thingskit, live):
    calls, setup = live
    db = setup([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT}])
    assert thingskit.cmd_set_notes(_ns_set(project_id=PROJECT, notes=URI)) == 0
    assert len(calls) == 1
    assert "set notes of to do id" in calls[0]
    assert "System Events" not in calls[0] and "keystroke" not in calls[0]
    assert _notes(db) == URI


def test_append_notes_by_project_id_appends(thingskit, live):
    calls, setup = live
    db = setup([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
                 "notes": "Contexte existant"}])
    assert thingskit.cmd_append_notes(_ns_app(project_id=PROJECT, line=URI)) == 0
    assert _notes(db) == "Contexte existant\n" + URI


def test_append_preserves_existing_project_notes_byte_for_byte(thingskit, live):
    """Le défaut le plus grave possible ici : silencieux et irrécupérable. On
    compare l'INTÉGRALITÉ de l'avant/après, pas la seule présence de la ligne."""
    calls, setup = live
    before = ("Première ligne — accents, tiret cadratin\n"
              "\tune tabulation, des \"guillemets\" et un \\ antislash\n"
              "things:///show?id=ABC\n\n")
    db = setup([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
                 "notes": before}])
    assert thingskit.cmd_append_notes(_ns_app(project_id=PROJECT, line=URI)) == 0
    after = _notes(db)
    assert after[:len(before)] == before
    assert after == before + "\n" + URI


def test_idempotence_does_not_solicit_the_application(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
               "notes": "Contexte\n" + URI}])
    assert thingskit.cmd_append_notes(_ns_app(project_id=PROJECT, line=URI)) == 0
    assert calls["osa"] == []


# --- Les refus, TOUS avant sollicitation de l'application ------------------

def test_heading_is_refused_without_any_osa_call(thingskit, rigged, capsys):
    """Un heading accepte l'ordre et retourne 0 sans rien stocker (mesuré) :
    l'envoyer laisserait croire à un lien posé. Refus en amont."""
    calls, set_rows = rigged
    set_rows([{"uuid": HEADING, "title": "Une initiative", "type": TYPE_HEADING}])
    assert thingskit.cmd_append_notes(_ns_app(project_id=HEADING, line=URI)) == 1
    assert calls["osa"] == []
    err = capsys.readouterr().err
    assert "heading" in err.lower()


def test_task_uuid_is_refused_on_project_id_without_any_osa_call(thingskit, rigged):
    """`--project-id` désigne un projet. Une tâche a sa propre voie (`--task-id`)
    et ses propres gardes : accepter l'un pour l'autre brouillerait les deux."""
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Une tâche", "type": TYPE_TASK}])
    assert thingskit.cmd_append_notes(_ns_app(project_id=PROJECT, line=URI)) == 1
    assert calls["osa"] == []


def test_trashed_project_is_refused_without_any_osa_call(thingskit, rigged, capsys):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Jeté", "type": TYPE_PROJECT, "trashed": 1}])
    assert thingskit.cmd_append_notes(_ns_app(project_id=PROJECT, line=URI)) == 1
    assert calls["osa"] == []
    assert "Corbeille" in capsys.readouterr().err


def test_unknown_uuid_is_refused_without_any_osa_call(thingskit, rigged, capsys):
    """Une area vit dans TMArea, pas dans TMTask : son identifiant est ici
    introuvable — ce qui est le comportement voulu, une area n'ayant pas de
    champ notes du tout."""
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT}])
    assert thingskit.cmd_set_notes(
        _ns_set(project_id="ZZZZZZZZZZZZZZZZZZZZZZ", notes=URI)) == 1
    assert calls["osa"] == []
    assert "introuvable" in capsys.readouterr().err


def test_malformed_uuid_is_refused_without_any_osa_call(thingskit, rigged, capsys):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT}])
    assert thingskit.cmd_set_notes(_ns_set(project_id="../../etc", notes=URI)) == 1
    assert calls["osa"] == []
    assert "malformé" in capsys.readouterr().err


def test_failure_when_stored_value_differs(thingskit, rigged):
    """`0` signifie « constaté en base », jamais « commande envoyée » : l'osa
    inerte ne change rien, la relecture doit donc faire échouer la commande."""
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT}])
    assert thingskit.cmd_set_notes(_ns_set(project_id=PROJECT, notes=URI)) == 1
    assert len(calls["osa"]) == 1


# --- La CLI expose bien la troisième voie, exclusive des deux autres -------

def _parse(thingskit, argv, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "argv", ["thingskit"] + argv)
    captured = {}
    monkeypatch.setattr(thingskit, "cmd_append_notes",
                        lambda a: (captured.update(a=a), 0)[1])
    monkeypatch.setattr(thingskit, "cmd_set_notes",
                        lambda a: (captured.update(a=a), 0)[1])
    return captured, thingskit.main()


def test_cli_accepts_project_id(thingskit, monkeypatch):
    captured, rc = _parse(thingskit,
                          ["append-notes", "--project-id", PROJECT, "--line", URI],
                          monkeypatch)
    assert rc == 0
    assert captured["a"].project_id == PROJECT


def test_cli_refuses_two_targets_at_once(thingskit, monkeypatch):
    with pytest.raises(SystemExit):
        _parse(thingskit,
               ["set-notes", "--project-id", PROJECT, "--task-id", PROJECT,
                "--notes", URI],
               monkeypatch)
