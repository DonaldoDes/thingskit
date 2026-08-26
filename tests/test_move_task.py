"""`thingskit move-task` — déplacement d'une tâche déjà créée vers un autre
projet ou une autre area (US-006).

Surface d'écriture choisie sur ce qui est RÉELLEMENT exposé, mesuré le
2026-08-19 sur une tâche/projet/area jetables réels, pas déduit de la
documentation :

  - `move to do id … to project "Titre"` / `to project id "…"` échouent tous
    les deux (« Impossible de déplacer la tâche ») : `project` hérite de
    `to do`, pas de `list`, et la commande `move` (cocoa `moveToList:`) ne
    l'accepte pas comme cible.
  - `move to do id … to list "Titre"` fonctionne pour une AREA (qui hérite
    bien de `list`), mais échoue pour un projet (« Il est impossible
    d'obtenir list "Titre" ») — cohérent avec le point précédent.
  - En revanche les PROPRIÉTÉS `project` et `area` du dictionnaire (classe
    `to do`) sont déclarées rw sans restriction : `set project of to do id …
    to project id "<uuid>"` et `set area of to do id … to area id "<uuid>"`
    fonctionnent toutes les deux, chacune efface silencieusement l'autre
    colonne (constaté par relecture SQL : poser `project` vide `area`, poser
    `area` vide `project` et `heading`) — Things applique lui-même
    l'exclusivité d'appartenance à une seule liste. C'est donc la propriété,
    jamais `move`, qui est utilisée ici, adressée par IDENTIFIANT (jamais par
    titre littéral) pour ne dépendre d'aucune localisation de libellé.
  - Aucune surface (ni `move`, ni propriété) n'expose de `heading` dans le
    dictionnaire AppleScript de la classe `to do` : la classe `heading`
    n'existe même pas dans le `sdef`. Le déplacement vers un heading n'est
    donc PAS couvert par cette commande (US-006 § Spécifications
    techniques : à ne pas exiger sans preuve de faisabilité).

Ces tests ne touchent jamais l'application ni la vraie base : `db_path` est
redirigée vers une base SQLite jetable, `osa`/`ensure_running`/`time.sleep`
mockés.
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
    startDate INTEGER,
    startBucket INTEGER,
    deadline INTEGER,
    reminderTime INTEGER,
    status INTEGER,
    notes TEXT
);
"""

OPEN, CANCELED, COMPLETED = 0, 2, 3


def _make_db(tmp_path, task_rows, area_rows=()):
    db_file = tmp_path / "main.sqlite"
    con = sqlite3.connect(db_file)
    con.executescript(SCHEMA)
    defaults = dict(
        uuid=None, title=None, type=0, trashed=0, project=None, heading=None,
        area=None, startDate=None, startBucket=None, deadline=None,
        reminderTime=None, status=0, notes=None,
    )
    for r in task_rows:
        row = {**defaults, **r}
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,project,heading,area,"
            "startDate,startBucket,deadline,reminderTime,status,notes) values "
            "(:uuid,:title,:type,:trashed,:project,:heading,:area,"
            ":startDate,:startBucket,:deadline,:reminderTime,:status,:notes)",
            row,
        )
    for uuid, title in area_rows:
        con.execute("insert into TMArea (uuid, title) values (?, ?)", (uuid, title))
    con.commit()
    con.close()
    return db_file


def _ns(id=None, title=None, to_project=None, to_area=None):
    return argparse.Namespace(id=id, title=title, to_project=to_project,
                              to_area=to_area)


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    """`osa` inerte qui enregistre ses appels — aucun effet en base."""
    calls = {"osa": [], "db": None}

    def _set_rows(task_rows, area_rows=()):
        db_file = _make_db(tmp_path, task_rows, area_rows)
        monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
        calls["db"] = db_file
        return db_file

    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "osa",
                        lambda script: (calls["osa"].append(script), (0, ""))[1])
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    return calls, _set_rows


def _rig_effective_move(thingskit, monkeypatch, calls, project=..., area=...,
                        heading=...):
    """`osa` qui simule l'effet réel constaté : poser `project` efface `area`
    (et réciproquement), comme mesuré sur la vraie application."""
    db_file = calls["db"]

    def _fake(script):
        calls["osa"].append(script)
        con = sqlite3.connect(db_file)
        cols = {}
        if project is not ...:
            cols["project"] = project
        if area is not ...:
            cols["area"] = area
        if heading is not ...:
            cols["heading"] = heading
        for col, val in cols.items():
            con.execute(f"update TMTask set {col}=? where uuid=?", (val, TARGET))
        con.commit()
        con.close()
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _fake)


TARGET = "AAAAAAAAAAAAAAAAAAAAAA"
OTHER = "BBBBBBBBBBBBBBBBBBBBBB"
PROJECT = "PPPPPPPPPPPPPPPPPPPPPP"
PROJECT2 = "QQQQQQQQQQQQQQQQQQQQQQ"
AREA = "RRRRRRRRRRRRRRRRRRRRRR"
AREA2 = "SSSSSSSSSSSSSSSSSSSSSS"


# --- adressage de la tâche ---------------------------------------------

def test_missing_id_and_title_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Solo", "type": 0}],
             [(AREA, "Une area")])
    rc = thingskit.cmd_move_task(_ns(to_area="Une area"))
    assert rc != 0
    assert calls["osa"] == []


def test_malformed_task_uuid_refuses_before_any_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Solo", "type": 0}],
             [(AREA, "Une area")])
    rc = thingskit.cmd_move_task(_ns(id="not a uuid!!", to_area="Une area"))
    assert rc != 0
    assert calls["osa"] == []


def test_title_ambiguous_refuses_no_move(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": TARGET, "title": "Doublon", "type": 0},
        {"uuid": OTHER, "title": "Doublon", "type": 0},
    ], [(AREA, "Une area")])
    rc = thingskit.cmd_move_task(_ns(title="Doublon", to_area="Une area"))
    assert rc != 0
    assert calls["osa"] == []


def test_title_no_match_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Autre chose", "type": 0}],
             [(AREA, "Une area")])
    rc = thingskit.cmd_move_task(_ns(title="Introuvable", to_area="Une area"))
    assert rc != 0
    assert calls["osa"] == []


def test_task_id_not_found_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": OTHER, "title": "Autre", "type": 0}],
             [(AREA, "Une area")])
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_area="Une area"))
    assert rc != 0
    assert calls["osa"] == []


def test_trashed_task_refused_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "type": 0, "trashed": 1}],
             [(AREA, "Une area")])
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_area="Une area"))
    assert rc != 0
    assert calls["osa"] == []


# --- adressage de la cible ----------------------------------------------

def test_missing_target_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "type": 0}], [])
    rc = thingskit.cmd_move_task(_ns(id=TARGET))
    assert rc != 0
    assert calls["osa"] == []


def test_to_project_unknown_title_refuses_no_silent_inbox(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "type": 0}], [])
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="N'existe pas"))
    assert rc != 0
    assert calls["osa"] == []


def test_to_area_unknown_title_refuses_no_silent_inbox(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "type": 0}], [])
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_area="N'existe pas"))
    assert rc != 0
    assert calls["osa"] == []


def test_to_project_ambiguous_title_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": TARGET, "title": "Cible", "type": 0},
        {"uuid": PROJECT, "title": "Doublon Projet", "type": 1},
        {"uuid": PROJECT2, "title": "Doublon Projet", "type": 1},
    ], [])
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Doublon Projet"))
    assert rc != 0
    assert calls["osa"] == []


def test_to_area_ambiguous_title_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "type": 0}],
             [(AREA, "Doublon Area"), (AREA2, "Doublon Area")])
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_area="Doublon Area"))
    assert rc != 0
    assert calls["osa"] == []


# --- chemin nominal -------------------------------------------------------

def test_move_to_project_by_id(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": TARGET, "title": "Cible", "type": 0, "area": AREA},
        {"uuid": PROJECT, "title": "Projet cible", "type": 1},
    ], [(AREA, "Ancienne area")])
    _rig_effective_move(thingskit, monkeypatch, calls, project=PROJECT, area=None,
                        heading=None)

    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet cible"))
    assert rc == 0
    assert len(calls["osa"]) == 1
    assert PROJECT in calls["osa"][0]
    assert TARGET in calls["osa"][0]

    con = sqlite3.connect(calls["db"])
    project, area = con.execute(
        "select project, area from TMTask where uuid=?", (TARGET,)).fetchone()
    con.close()
    assert project == PROJECT
    assert area is None


def test_move_to_area_by_title(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": TARGET, "title": "Unique", "type": 0, "project": PROJECT},
    ], [(AREA, "Area cible")])
    _rig_effective_move(thingskit, monkeypatch, calls, project=None, area=AREA,
                        heading=None)

    rc = thingskit.cmd_move_task(_ns(title="Unique", to_area="Area cible"))
    assert rc == 0

    con = sqlite3.connect(calls["db"])
    project, area = con.execute(
        "select project, area from TMTask where uuid=?", (TARGET,)).fetchone()
    con.close()
    assert project is None
    assert area == AREA


def test_uuid_unchanged_after_move(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "type": 0}],
             [(AREA, "Area cible")])
    _rig_effective_move(thingskit, monkeypatch, calls, project=None, area=AREA,
                        heading=None)

    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_area="Area cible"))
    assert rc == 0

    con = sqlite3.connect(calls["db"])
    row = con.execute("select uuid from TMTask where uuid=?", (TARGET,)).fetchone()
    con.close()
    assert row is not None
    assert row[0] == TARGET


# --- vérification post-action --------------------------------------------

def test_failure_when_effect_not_observed(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "type": 0}],
             [(AREA, "Area cible")])
    # osa "réussit" (rc=0) mais ne modifie rien en base.
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_area="Area cible"))
    assert rc != 0
    assert len(calls["osa"]) == 1


def test_failure_when_stored_value_differs_from_target(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": TARGET, "title": "Cible", "type": 0},
        {"uuid": PROJECT, "title": "Projet cible", "type": 1},
    ], [])
    # L'effet observé pointe vers un AUTRE projet que celui demandé.
    _rig_effective_move(thingskit, monkeypatch, calls, project=PROJECT2, area=None,
                        heading=None)

    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet cible"))
    assert rc != 0


# --- la sonde EST la source du message d'échec ----------------------------
#
# `cmd_move_task` était le seul des treize sites à appeler `wait_for_effect`
# sans lire son retour, puis à recomposer son verdict à côté en dupliquant le
# prédicat que la sonde venait d'évaluer. Fonctionnellement correct, mais deux
# copies divergent : celle qui attend cesse de décrire celle qui juge. Le
# motif aligné est celui de `cmd_reschedule_task` — une seule fonction nommée,
# sonde ET source du message. Ces trois tests gardent le seul acquis qu'il
# fallait préserver dans l'alignement : la cascade distingue trois causes, et
# les nomme séparément.

def test_the_failure_message_names_a_task_that_vanished(thingskit, monkeypatch,
                                                        rigged, capsys):
    calls, set_rows = rigged
    db_file = set_rows([{"uuid": TARGET, "title": "Cible", "type": 0}],
                       [(AREA, "Area cible")])

    def _vanishing(script):
        calls["osa"].append(script)
        con = sqlite3.connect(db_file)
        con.execute("delete from TMTask where uuid=?", (TARGET,))
        con.commit()
        con.close()
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _vanishing)

    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_area="Area cible"))

    assert rc != 0
    assert "introuvable après l'opération" in capsys.readouterr().err


def test_the_failure_message_names_a_uuid_that_changed(thingskit, monkeypatch,
                                                       rigged, capsys):
    """Cause défensive : la relecture rend une AUTRE ligne que celle déplacée."""
    calls, set_rows = rigged
    set_rows([
        {"uuid": TARGET, "title": "Cible", "type": 0},
        {"uuid": PROJECT, "title": "Projet cible", "type": 1},
    ], [])
    real_q = thingskit.q
    state = {"moved": False}

    def _q(sql, args=()):
        rows = real_q(sql, args)
        if state["moved"] and sql.startswith("select uuid, project, area, heading"):
            return [(OTHER,) + tuple(rows[0][1:])] if rows else rows
        return rows

    def _osa(script):
        calls["osa"].append(script)
        state["moved"] = True
        return 0, ""

    monkeypatch.setattr(thingskit, "q", _q)
    monkeypatch.setattr(thingskit, "osa", _osa)

    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet cible"))

    assert rc != 0
    err = capsys.readouterr().err
    assert "UUID relu" in err and OTHER in err and "invariant violé" in err


def test_the_failure_message_names_the_observed_membership(
        thingskit, monkeypatch, rigged, capsys):
    calls, set_rows = rigged
    set_rows([
        {"uuid": TARGET, "title": "Cible", "type": 0},
        {"uuid": PROJECT, "title": "Projet cible", "type": 1},
    ], [])
    _rig_effective_move(thingskit, monkeypatch, calls, project=PROJECT2,
                        area=None, heading=None)

    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet cible"))

    assert rc != 0
    err = capsys.readouterr().err
    assert "appartenance relue en base" in err
    assert PROJECT2 in err and PROJECT in err


def test_the_membership_predicate_exists_only_once(thingskit):
    """L'alignement se constate sur le code, pas sur la seule sortie.

    La relecture d'appartenance ne doit apparaître qu'une fois dans la
    commande : c'est ce qui garantit que la sonde et le message d'échec ne
    peuvent pas diverger.
    """
    import inspect

    src = inspect.getsource(thingskit.cmd_move_task)
    assert src.count("select uuid, project, area, heading") == 1


# --- la course entre le dernier sondage et la composition du message -------

def test_the_failure_message_uses_the_observed_problem_not_a_fresh_query(
        thingskit, monkeypatch, rigged, capsys):
    """L'effet atterrit ENTRE le dernier sondage et la composition du message.

    Rappeler la vérification à ce moment-là rend `None` — le message imprimait
    alors littéralement « None » et cessait de dire pourquoi il échouait, au
    moment précis où il en a le plus besoin. La valeur qui juge doit être
    celle que la sonde a OBSERVÉE, pas une seconde lecture.
    """
    calls, set_rows = rigged
    db_file = set_rows([
        {"uuid": TARGET, "title": "Cible", "type": 0},
        {"uuid": PROJECT, "title": "Projet cible", "type": 1},
    ], [])

    def _wait_then_land(probe, *args, **kwargs):
        observed = probe()          # dernier sondage : rien n'a encore atterri
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set project=?, area=NULL, heading=NULL "
                    "where uuid=?", (PROJECT, TARGET))
        con.commit()
        con.close()
        return observed

    monkeypatch.setattr(thingskit, "wait_for_effect", _wait_then_land)

    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet cible"))

    assert rc != 0
    err = capsys.readouterr().err
    assert ") : None " not in err, err
    assert "appartenance relue en base" in err, err


def test_a_database_unreadable_throughout_is_said_not_invented(
        thingskit, monkeypatch, rigged, capsys):
    """Branche de repli du motif de capture, jamais exercée jusqu'ici.

    Quand la sonde n'a rien pu observer de toute l'attente, `seen["problem"]`
    vaut encore `None` — et sans le repli, le message imprimerait « None » ou
    rien du tout, au lieu de dire que la base est restée illisible.
    """
    calls, set_rows = rigged
    set_rows([
        {"uuid": TARGET, "title": "Cible", "type": 0},
        {"uuid": PROJECT, "title": "Projet cible", "type": 1},
    ], [])

    real_q = thingskit.q
    state = {"sent": False}

    def _q(sql, args=()):
        if state["sent"]:
            raise sqlite3.OperationalError("database is locked")
        return real_q(sql, args)

    def _osa(script):
        state["sent"] = True
        return 0, ""

    monkeypatch.setattr(thingskit, "q", _q)
    monkeypatch.setattr(thingskit, "osa", _osa)

    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet cible"))

    assert rc != 0
    err = capsys.readouterr().err
    assert "lecture de la base refusée pendant toute l'attente" in err, err
