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
    notes TEXT,
    creationDate REAL
);
CREATE TABLE TMSettings (uuid TEXT PRIMARY KEY,
                         uriSchemeAuthenticationToken TEXT);
"""

OPEN, CANCELED, COMPLETED = 0, 2, 3


def _make_db(tmp_path, task_rows, area_rows=(), token="jeton-de-test"):
    db_file = tmp_path / "main.sqlite"
    con = sqlite3.connect(db_file)
    con.executescript(SCHEMA)
    defaults = dict(
        uuid=None, title=None, type=0, trashed=0, project=None, heading=None,
        area=None, startDate=None, startBucket=None, deadline=None,
        reminderTime=None, status=0, notes=None, creationDate=None,
    )
    for r in task_rows:
        row = {**defaults, **r}
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,project,heading,area,"
            "startDate,startBucket,deadline,reminderTime,status,notes,"
            "creationDate) values "
            "(:uuid,:title,:type,:trashed,:project,:heading,:area,"
            ":startDate,:startBucket,:deadline,:reminderTime,:status,:notes,"
            ":creationDate)",
            row,
        )
    for uuid, title in area_rows:
        con.execute("insert into TMArea (uuid, title) values (?, ?)", (uuid, title))
    if token is not None:
        con.execute("insert into TMSettings (uuid, uriSchemeAuthenticationToken) "
                    "values (?, ?)", ("settings", token))
    con.commit()
    con.close()
    return db_file


def _ns(id=None, title=None, to_project=None, to_area=None, to_heading=None):
    return argparse.Namespace(id=id, title=title, to_project=to_project,
                              to_area=to_area, to_heading=to_heading)


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    """`osa` inerte qui enregistre ses appels — aucun effet en base."""
    calls = {"osa": [], "url": [], "db": None}

    def _set_rows(task_rows, area_rows=(), token="jeton-de-test"):
        db_file = _make_db(tmp_path, task_rows, area_rows, token=token)
        monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
        calls["db"] = db_file
        return db_file

    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "osa",
                        lambda script: (calls["osa"].append(script), (0, ""))[1])
    monkeypatch.setattr(
        thingskit, "url_open",
        lambda payload, background=True, auth_token=None:
            calls["url"].append({"payload": payload, "background": background,
                                 "auth_token": auth_token}))
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


# =======================================================================
# US-010 — déplacement sous un EN-TÊTE (`--to-heading`)
# =======================================================================
#
# Surface mesurée le 2026-08-27 sur un projet/heading/tâches JETABLES réels,
# après épuisement de l'AppleScript (mesure, pas déduction) :
#
#   - AppleScript : la classe `heading` n'existe pas dans le `sdef` (0
#     occurrence du mot), et les quatre formes essayées échouent toutes —
#     `move to do id T to list id H` (-1728), `move … to to do id H` (301),
#     `set project of to do id T to project id H` (-10006), `set area … to
#     area id H` (-10006), `move … to list "<titre du heading>"` (-1728), et
#     la commande cachée `_private_experimental_ reorder to dos in` ne
#     comprend pas un heading comme destinataire (-1708). Le constat d'US-006
#     est donc CONFIRMÉ et élargi.
#   - Schéma d'URL, opération `update` : fonctionne, et c'est la seule
#     surface qui fonctionne. `heading-id=<uuid>` déplace une tâche EXISTANTE
#     sous l'en-tête en ~150 ms ; l'uuid et la `creationDate` sont inchangés,
#     la colonne `project` passe à NULL (cohérent avec le constat du docstring
#     module : une tâche sous heading a son `project` vide).
#   - Le jeton exigé par `update` n'impose AUCUNE configuration à
#     l'utilisateur : il est lisible en base, colonne
#     `TMSettings.uriSchemeAuthenticationToken` (lecture `mode=ro`, comme tout
#     le reste). SANS jeton, l'ordre est un NO-OP SILENCIEUX — mesuré : un
#     `update` de simple `title` sans jeton ne change rien et `open` rend
#     malgré tout 0. Même mode d'échec « commande envoyée ≠ effet constaté »
#     que l'area inexistante ; d'où le refus AVANT tout envoi quand le jeton
#     manque, et la vérification post-action dans tous les cas.
#   - Un `heading` qui ne nomme aucun en-tête est lui aussi ignoré en
#     silence (mesuré) : la résolution PRÉCÈDE l'envoi, comme pour `add-task`.

HEADING = "HHHHHHHHHHHHHHHHHHHHHH"
HEADING2 = "IIIIIIIIIIIIIIIIIIIIII"
TYPE_PROJECT, TYPE_HEADING = 1, 2
CREATED = 1787784510.8758821


def _heading_world(extra=()):
    """Un projet, un en-tête dedans, une tâche libre — le cas réel du besoin."""
    return [
        {"uuid": TARGET, "title": "Solo", "type": 0, "creationDate": CREATED},
        {"uuid": PROJECT, "title": "Projet", "type": TYPE_PROJECT},
        {"uuid": HEADING, "title": "Section", "type": TYPE_HEADING,
         "project": PROJECT},
        *extra,
    ]


def _rig_effective_heading_move(thingskit, monkeypatch, calls,
                                heading=HEADING, uuid=TARGET,
                                creation=..., project=None):
    """`url_open` qui simule l'effet MESURÉ : `heading` posé, `project` vidé."""
    db_file = calls["db"]

    def _fake(payload, background=True, auth_token=None):
        calls["url"].append({"payload": payload, "background": background,
                             "auth_token": auth_token})
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set heading=?, project=?, uuid=? "
                    "where uuid=?", (heading, project, uuid, TARGET))
        if creation is not ...:
            con.execute("update TMTask set creationDate=? where uuid=?",
                        (creation, uuid))
        con.commit()
        con.close()

    monkeypatch.setattr(thingskit, "url_open", _fake)


# --- refus AVANT toute sollicitation ------------------------------------

def test_to_heading_without_a_project_scope_refuses(thingskit, rigged,
                                                    capsys):
    """Un titre d'en-tête n'est unique QUE dans son projet : le résoudre sans
    projet reviendrait à « prendre le premier », ce que ce projet refuse."""
    calls, set_rows = rigged
    set_rows(_heading_world())
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_heading="Section"))
    assert rc == 1
    assert calls["osa"] == [] and calls["url"] == []
    # Le code retour seul ne discrimine RIEN : sans cette garde, la résolution
    # échoue de toute façon sur un projet nommé `None` et rend 1 sans rien
    # envoyer. Le test ne vaut donc que par le MESSAGE — c'est lui qui dit à
    # l'utilisateur ce qui manque, et lui seul qui rougit si la garde saute.
    assert "--to-heading exige --to-project" in capsys.readouterr().err


def test_to_heading_with_an_area_target_refuses(thingskit, rigged, capsys):
    """Une area n'a pas d'en-tête. Ici encore le code retour ne discrimine
    pas — sans la garde, la résolution échoue sur un projet nommé `None` et
    rend 1 elle aussi. Seul le MESSAGE distingue les deux."""
    calls, set_rows = rigged
    set_rows(_heading_world(), [(AREA, "Une area")])
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_area="Une area",
                                     to_heading="Section"))
    assert rc == 1
    assert calls["osa"] == [] and calls["url"] == []
    assert "--to-heading exige --to-project" in capsys.readouterr().err


def test_unknown_heading_refuses_without_any_solicitation(thingskit, rigged,
                                                          capsys):
    calls, set_rows = rigged
    set_rows(_heading_world())
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                     to_heading="Inexistante"))
    assert rc == 1
    assert calls["osa"] == [] and calls["url"] == []
    assert "Inexistante" in capsys.readouterr().err


def test_ambiguous_heading_refuses_without_any_solicitation(thingskit, rigged,
                                                            capsys):
    calls, set_rows = rigged
    set_rows(_heading_world([
        {"uuid": HEADING2, "title": "Section", "type": TYPE_HEADING,
         "project": PROJECT}]))
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                     to_heading="Section"))
    assert rc == 1
    assert calls["osa"] == [] and calls["url"] == []
    assert "AMBIGU" in capsys.readouterr().err


def test_a_homonym_heading_of_another_project_is_never_picked(thingskit,
                                                              monkeypatch,
                                                              rigged):
    """Deux projets peuvent porter un en-tête du même nom : c'est le cas
    NORMAL, pas une ambiguïté — la résolution est bornée au projet visé."""
    calls, set_rows = rigged
    set_rows(_heading_world([
        {"uuid": PROJECT2, "title": "Autre projet", "type": TYPE_PROJECT},
        {"uuid": HEADING2, "title": "Section", "type": TYPE_HEADING,
         "project": PROJECT2}]))
    _rig_effective_heading_move(thingskit, monkeypatch, calls,
                                heading=HEADING2)
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Autre projet",
                                     to_heading="Section"))
    assert rc == 0
    assert calls["url"][0]["payload"][0]["attributes"]["heading-id"] == HEADING2


def test_a_trashed_heading_is_not_a_target(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": TARGET, "title": "Solo", "type": 0, "creationDate": CREATED},
        {"uuid": PROJECT, "title": "Projet", "type": TYPE_PROJECT},
        {"uuid": HEADING, "title": "Section", "type": TYPE_HEADING,
         "project": PROJECT, "trashed": 1}])
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                     to_heading="Section"))
    assert rc == 1
    assert calls["osa"] == [] and calls["url"] == []


def test_a_trashed_task_is_refused_before_the_heading_route(thingskit, rigged):
    calls, set_rows = rigged
    set_rows(_heading_world([]))
    import sqlite3 as _s
    con = _s.connect(calls["db"])
    con.execute("update TMTask set trashed=1 where uuid=?", (TARGET,))
    con.commit(); con.close()
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                     to_heading="Section"))
    assert rc == 1
    assert calls["osa"] == [] and calls["url"] == []


# --- le jeton du schéma d'URL -------------------------------------------

def test_a_missing_uri_token_refuses_before_any_solicitation(thingskit, rigged,
                                                             capsys):
    """SANS jeton, `update` est un NO-OP SILENCIEUX et `open` rend 0 — le seul
    endroit où ce défaut est arrêtable est AVANT l'envoi."""
    calls, set_rows = rigged
    set_rows(_heading_world(), token=None)
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                     to_heading="Section"))
    assert rc == 1
    assert calls["osa"] == [] and calls["url"] == []
    assert "jeton" in capsys.readouterr().err.lower()


def test_an_empty_uri_token_is_treated_as_missing(thingskit, rigged):
    calls, set_rows = rigged
    set_rows(_heading_world(), token="")
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading="Section")) == 1
    assert calls["url"] == []


def test_the_token_never_reaches_stdout_or_stderr_on_success(thingskit,
                                                             monkeypatch,
                                                             rigged, capsys):
    calls, set_rows = rigged
    set_rows(_heading_world(), token="SECRET-jeton-42")
    _rig_effective_heading_move(thingskit, monkeypatch, calls)
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading="Section")) == 0
    out = capsys.readouterr()
    assert "SECRET-jeton-42" not in out.out + out.err


def test_the_token_never_reaches_stderr_on_failure(thingskit, rigged, capsys):
    calls, set_rows = rigged
    set_rows(_heading_world(), token="SECRET-jeton-42")
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading="Section")) == 1
    out = capsys.readouterr()
    assert "SECRET-jeton-42" not in out.out + out.err


# --- le déplacement lui-même --------------------------------------------

def test_move_to_heading_by_id(thingskit, monkeypatch, rigged, capsys):
    calls, set_rows = rigged
    set_rows(_heading_world())
    _rig_effective_heading_move(thingskit, monkeypatch, calls)
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                     to_heading="Section"))
    assert rc == 0
    assert calls["osa"] == [], "la voie heading n'emprunte JAMAIS AppleScript"
    assert len(calls["url"]) == 1
    item = calls["url"][0]["payload"][0]
    assert item["type"] == "to-do" and item["operation"] == "update"
    assert item["id"] == TARGET
    assert item["attributes"]["heading-id"] == HEADING
    assert item["attributes"]["list-id"] == PROJECT
    assert calls["url"][0]["auth_token"] == "jeton-de-test"
    assert "Section" in capsys.readouterr().out


def test_move_to_heading_by_title(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows(_heading_world())
    _rig_effective_heading_move(thingskit, monkeypatch, calls)
    assert thingskit.cmd_move_task(_ns(title="Solo", to_project="Projet",
                                       to_heading="Section")) == 0


def test_uuid_and_creation_date_unchanged_after_a_heading_move(
        thingskit, monkeypatch, rigged):
    """L'invariant central d'US-010 : le contournement rejeté (recréer +
    supprimer) casse exactement ces deux propriétés."""
    calls, set_rows = rigged
    set_rows(_heading_world())
    _rig_effective_heading_move(thingskit, monkeypatch, calls)
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading="Section")) == 0
    con = sqlite3.connect(calls["db"])
    row = con.execute("select uuid, heading, project, creationDate from "
                      "TMTask where uuid=?", (TARGET,)).fetchone()
    con.close()
    assert row == (TARGET, HEADING, None, CREATED)


def test_a_changed_creation_date_is_a_failure(thingskit, monkeypatch, rigged,
                                              capsys):
    calls, set_rows = rigged
    set_rows(_heading_world())
    _rig_effective_heading_move(thingskit, monkeypatch, calls, creation=1.0)
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading="Section")) == 1
    assert "création" in capsys.readouterr().err


def test_a_changed_uuid_is_a_failure(thingskit, monkeypatch, rigged, capsys):
    """Cause défensive, voie en-tête : la relecture rend une AUTRE ligne que
    celle déplacée. C'est l'invariant central d'US-010 — le contournement
    rejeté (recréer + supprimer) produit exactement cette situation."""
    calls, set_rows = rigged
    set_rows(_heading_world())
    real_q = thingskit.q
    state = {"moved": False}

    def _q(sql, args=()):
        rows = real_q(sql, args)
        if state["moved"] and sql.startswith("select uuid, project, area, heading"):
            return [(OTHER,) + tuple(rows[0][1:])] if rows else rows
        return rows

    def _fake(payload, background=True, auth_token=None):
        calls["url"].append(payload)
        state["moved"] = True

    monkeypatch.setattr(thingskit, "url_open", _fake)
    monkeypatch.setattr(thingskit, "q", _q)
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading="Section")) == 1
    assert "UUID" in capsys.readouterr().err


def test_failure_when_the_heading_effect_is_not_observed(thingskit, rigged,
                                                         capsys):
    """`url_open` inerte : l'ordre est parti, rien n'a bougé — code retour
    = échec, jamais « commande envoyée »."""
    calls, set_rows = rigged
    set_rows(_heading_world())
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading="Section")) == 1
    err = capsys.readouterr().err
    assert "ÉCHEC" in err and "heading=None" in err


def test_landing_under_the_wrong_heading_is_a_failure(thingskit, monkeypatch,
                                                      rigged, capsys):
    calls, set_rows = rigged
    set_rows(_heading_world([
        {"uuid": HEADING2, "title": "Ailleurs", "type": TYPE_HEADING,
         "project": PROJECT}]))
    _rig_effective_heading_move(thingskit, monkeypatch, calls,
                                heading=HEADING2)
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading="Section")) == 1
    assert HEADING2 in capsys.readouterr().err


def test_the_heading_failure_message_uses_the_observed_problem(
        thingskit, monkeypatch, rigged, capsys):
    """L'effet atterrit APRÈS le dernier sondage : le message doit citer ce
    que la sonde a vu, pas une seconde lecture qui rendrait « aucun écart »."""
    calls, set_rows = rigged
    set_rows(_heading_world())
    monkeypatch.setattr(thingskit, "url_open",
                        lambda payload, background=True, auth_token=None:
                            calls["url"].append(payload))
    real_wait = thingskit.wait_for_effect

    def _wait(probe, *args, **kw):
        verdict = real_wait(probe, timeout=0, interval=0)
        con = sqlite3.connect(calls["db"])
        con.execute("update TMTask set heading=?, project=null where uuid=?",
                    (HEADING, TARGET))
        con.commit(); con.close()
        return verdict

    monkeypatch.setattr(thingskit, "wait_for_effect", _wait)
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading="Section")) == 1
    err = capsys.readouterr().err
    assert "heading=None" in err
    assert thingskit._NO_OBSERVATION not in err


def test_a_database_unreadable_throughout_the_heading_wait_is_said(
        thingskit, monkeypatch, rigged, capsys):
    calls, set_rows = rigged
    set_rows(_heading_world())
    resolved = {"done": False}
    real_q = thingskit.q

    def _q(sql, args=()):
        if resolved["done"]:
            raise sqlite3.OperationalError("database is locked")
        return real_q(sql, args)

    def _fake(payload, background=True, auth_token=None):
        calls["url"].append(payload)
        resolved["done"] = True

    monkeypatch.setattr(thingskit, "url_open", _fake)
    monkeypatch.setattr(thingskit, "q", _q)
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading="Section")) == 1
    assert thingskit._NO_OBSERVATION in capsys.readouterr().err


# --- idempotence : un no-op ne se déguise pas en déplacement -------------

def test_a_task_already_under_the_target_heading_is_a_no_op_without_any_solicitation(
        thingskit, rigged, capsys):
    calls, set_rows = rigged
    set_rows(_heading_world())
    con = sqlite3.connect(calls["db"])
    con.execute("update TMTask set heading=?, project=null where uuid=?",
                (HEADING, TARGET))
    con.commit(); con.close()
    rc = thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                     to_heading="Section"))
    assert rc == 0
    assert calls["osa"] == [] and calls["url"] == []
    assert "déjà" in capsys.readouterr().out


def test_the_no_op_message_is_not_reused_for_a_real_move(thingskit, monkeypatch,
                                                         rigged, capsys):
    """Contre-épreuve du sur-court-circuit : un vrai déplacement ne doit
    JAMAIS emprunter le message « déjà sous l'en-tête »."""
    calls, set_rows = rigged
    set_rows(_heading_world())
    _rig_effective_heading_move(thingskit, monkeypatch, calls)
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading="Section")) == 0
    out = capsys.readouterr().out
    assert "déjà" not in out
    assert len(calls["url"]) == 1


# --- rendu borné des valeurs d'origine non contrôlée ---------------------

@pytest.mark.parametrize("hostile", ["\x1b[2K\r", "\n", "‮"])
def test_the_heading_success_message_never_emits_a_control_sequence(
        thingskit, monkeypatch, rigged, capsys, hostile):
    calls, set_rows = rigged
    piege = f"Section{hostile}rangée"
    set_rows([
        {"uuid": TARGET, "title": "Solo", "type": 0, "creationDate": CREATED},
        {"uuid": PROJECT, "title": "Projet", "type": TYPE_PROJECT},
        {"uuid": HEADING, "title": piege, "type": TYPE_HEADING,
         "project": PROJECT}])
    _rig_effective_heading_move(thingskit, monkeypatch, calls)
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading=piege)) == 0
    # Le saut de ligne FINAL est celui de `print`, pas une valeur qui fuit :
    # la borne porte sur le corps du message, pas sur son terminateur.
    out = capsys.readouterr().out.rstrip("\n")
    assert hostile not in out


@pytest.mark.parametrize("hostile", ["\x1b[2K\r", "\n", "‮"])
def test_the_heading_failure_message_never_emits_a_control_sequence(
        thingskit, rigged, capsys, hostile):
    calls, set_rows = rigged
    piege = f"Section{hostile}rangée"
    set_rows([
        {"uuid": TARGET, "title": f"Solo{hostile}!", "type": 0,
         "creationDate": CREATED},
        {"uuid": PROJECT, "title": "Projet", "type": TYPE_PROJECT},
        {"uuid": HEADING, "title": piege, "type": TYPE_HEADING,
         "project": PROJECT}])
    assert thingskit.cmd_move_task(_ns(id=TARGET, to_project="Projet",
                                       to_heading=piege)) == 1
    err = capsys.readouterr().err.rstrip("\n")
    assert hostile not in err


def test_the_heading_route_is_registered_in_cli_help(thingskit, run_cli):
    code, out, _ = run_cli(["move-task", "--help"])
    assert code == 0
    assert "--to-heading" in out
    assert "move-task" in (thingskit.__doc__ or "")
