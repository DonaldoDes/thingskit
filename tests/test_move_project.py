"""`thingskit move-project` — déplacement d'un projet déjà créé vers une autre
area, sans jamais changer son UUID (pas de suppression puis recréation).

Surface d'écriture mesurée le 2026-08-24 sur une area/projet jetables réels,
pas déduite de la documentation (constitution § « Toute affirmation … est
constatée par test ») : un projet vit dans `TMTask` (`type=1`, hérite de
`to do` dans le dictionnaire AppleScript, cf. commentaire de `move-task`) ;
`set area of to do id "<uuid du projet>" to area id "<uuid area>"` déplace
bien un PROJET d'une area à l'autre (constaté par relecture SQL après coup :
la colonne `area` change, l'`uuid` ne bouge pas). C'est donc la même
propriété `area` que celle utilisée par `move-task`, adressée par
IDENTIFIANT (jamais par titre littéral interpolé) pour ne dépendre d'aucun
libellé localisé — jamais `move`, qui échoue sur les listes selon le même
constat que `move-task`.

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

TYPE_TASK, TYPE_PROJECT, TYPE_HEADING = 0, 1, 2


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


def _ns(project=None, project_id=None, area=None):
    return argparse.Namespace(project=project, project_id=project_id, area=area)


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


def _rig_effective_move(thingskit, monkeypatch, calls, area):
    """`osa` qui simule l'effet réel mesuré : poser `area` sur le projet."""
    db_file = calls["db"]

    def _fake(script):
        calls["osa"].append(script)
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set area=? where uuid=?", (area, PROJECT))
        con.commit()
        con.close()
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _fake)


PROJECT = "PPPPPPPPPPPPPPPPPPPPPP"
PROJECT2 = "QQQQQQQQQQQQQQQQQQQQQQ"
TASK = "TTTTTTTTTTTTTTTTTTTTTT"
HEADING = "HHHHHHHHHHHHHHHHHHHHHH"
AREA_OLD = "RRRRRRRRRRRRRRRRRRRRRR"
AREA_NEW = "SSSSSSSSSSSSSSSSSSSSSS"
AREA_DUP1 = "UUUUUUUUUUUUUUUUUUUUUU"
AREA_DUP2 = "VVVVVVVVVVVVVVVVVVVVVV"


# --- adressage du projet ---------------------------------------------------

def test_missing_project_and_project_id_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Solo", "type": TYPE_PROJECT}],
             [(AREA_NEW, "Cible")])
    rc = thingskit.cmd_move_project(_ns(area="Cible"))
    assert rc != 0
    assert calls["osa"] == []


def test_project_and_project_id_both_given_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Solo", "type": TYPE_PROJECT}],
             [(AREA_NEW, "Cible")])
    rc = thingskit.cmd_move_project(
        _ns(project="Solo", project_id=PROJECT, area="Cible"))
    assert rc != 0
    assert calls["osa"] == []


def test_malformed_project_id_refuses_before_any_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Solo", "type": TYPE_PROJECT}],
             [(AREA_NEW, "Cible")])
    rc = thingskit.cmd_move_project(_ns(project_id="not a uuid!!", area="Cible"))
    assert rc != 0
    assert calls["osa"] == []


def test_project_id_not_found_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT2, "title": "Autre", "type": TYPE_PROJECT}],
             [(AREA_NEW, "Cible")])
    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Cible"))
    assert rc != 0
    assert calls["osa"] == []


def test_project_title_ambiguous_refuses_no_move(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": PROJECT, "title": "Doublon", "type": TYPE_PROJECT},
        {"uuid": PROJECT2, "title": "Doublon", "type": TYPE_PROJECT},
    ], [(AREA_NEW, "Cible")])
    rc = thingskit.cmd_move_project(_ns(project="Doublon", area="Cible"))
    assert rc != 0
    assert calls["osa"] == []


def test_project_title_no_match_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Autre chose", "type": TYPE_PROJECT}],
             [(AREA_NEW, "Cible")])
    rc = thingskit.cmd_move_project(_ns(project="Introuvable", area="Cible"))
    assert rc != 0
    assert calls["osa"] == []


def test_empty_string_title_refuses(thingskit, rigged):
    """Adversarial : un titre vide ne doit matcher aucune ligne — pas de
    comportement de type wildcard sur une chaîne vide."""
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Autre chose", "type": TYPE_PROJECT}],
             [(AREA_NEW, "Cible")])
    rc = thingskit.cmd_move_project(_ns(project="", area="Cible"))
    assert rc != 0
    assert calls["osa"] == []


def test_trashed_project_refused_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
              "trashed": 1}], [(AREA_NEW, "Nouvelle area")])
    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle area"))
    assert rc != 0
    assert calls["osa"] == []


def test_trashed_project_refused_via_title_too(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
              "trashed": 1}], [(AREA_NEW, "Nouvelle area")])
    rc = thingskit.cmd_move_project(_ns(project="Cible", area="Nouvelle area"))
    assert rc != 0
    assert calls["osa"] == []


# --- adversarial : confusion de type -----------------------------------

def test_project_id_targeting_a_task_refuses(thingskit, rigged):
    """Adversarial : passer l'identifiant d'une TÂCHE en --project-id ne doit
    ni la déplacer ni être silencieusement accepté."""
    calls, set_rows = rigged
    set_rows([{"uuid": TASK, "title": "Une tâche", "type": TYPE_TASK}],
             [(AREA_NEW, "Cible")])
    rc = thingskit.cmd_move_project(_ns(project_id=TASK, area="Cible"))
    assert rc != 0
    assert calls["osa"] == []


def test_project_id_targeting_a_heading_refuses(thingskit, rigged):
    """Adversarial : passer l'identifiant d'un HEADING en --project-id est
    refusé — un heading n'est pas un projet, aucune surface ne le déplace."""
    calls, set_rows = rigged
    set_rows([{"uuid": HEADING, "title": "Un heading", "type": TYPE_HEADING}],
             [(AREA_NEW, "Cible")])
    rc = thingskit.cmd_move_project(_ns(project_id=HEADING, area="Cible"))
    assert rc != 0
    assert calls["osa"] == []


def test_project_id_that_is_actually_an_area_id_refuses(thingskit, rigged):
    """Adversarial : l'identifiant d'une AREA n'existe pas dans `TMTask` — le
    passer en --project-id doit échouer comme un identifiant introuvable,
    pas confondre les deux tables."""
    calls, set_rows = rigged
    set_rows([], [(AREA_OLD, "Une area"), (AREA_NEW, "Cible")])
    rc = thingskit.cmd_move_project(_ns(project_id=AREA_OLD, area="Cible"))
    assert rc != 0
    assert calls["osa"] == []


def test_title_with_sql_special_characters_does_not_match_unrelated_rows(
        thingskit, rigged):
    """Adversarial : un titre truffé de métacaractères SQL/wildcard est
    traité littéralement (requêtes paramétrées) — il ne doit matcher que
    l'exact, jamais élargir la sélection."""
    calls, set_rows = rigged
    set_rows([
        {"uuid": PROJECT, "title": "Normal", "type": TYPE_PROJECT},
        {"uuid": PROJECT2, "title": "%' OR '1'='1", "type": TYPE_PROJECT},
    ], [(AREA_NEW, "Cible")])
    rc = thingskit.cmd_move_project(_ns(project="%", area="Cible"))
    assert rc != 0
    assert calls["osa"] == []


# --- adressage de l'area cible ----------------------------------------

def test_missing_area_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT}], [])
    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT))
    assert rc != 0
    assert calls["osa"] == []


def test_unknown_area_title_refuses_no_silent_landing(thingskit, rigged):
    """Le piège nommé par la consigne : une area qui ne correspond à AUCUNE
    area existante ne doit jamais rendre un succès mensonger."""
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT}], [])
    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="N'existe pas"))
    assert rc != 0
    assert calls["osa"] == []


def test_area_ambiguous_title_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT}],
             [(AREA_DUP1, "Doublon Area"), (AREA_DUP2, "Doublon Area")])
    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Doublon Area"))
    assert rc != 0
    assert calls["osa"] == []


# --- chemin nominal -------------------------------------------------------

def test_move_project_to_area_by_title(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
              "area": AREA_OLD}], [(AREA_OLD, "Ancienne"), (AREA_NEW, "Nouvelle")])
    _rig_effective_move(thingskit, monkeypatch, calls, AREA_NEW)

    rc = thingskit.cmd_move_project(_ns(project="Cible", area="Nouvelle"))
    assert rc == 0
    assert len(calls["osa"]) == 1
    assert PROJECT in calls["osa"][0]
    assert AREA_NEW in calls["osa"][0]

    con = sqlite3.connect(calls["db"])
    area = con.execute("select area from TMTask where uuid=?",
                       (PROJECT,)).fetchone()[0]
    con.close()
    assert area == AREA_NEW


def test_move_project_to_area_by_project_id(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT}],
             [(AREA_NEW, "Nouvelle")])
    _rig_effective_move(thingskit, monkeypatch, calls, AREA_NEW)

    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))
    assert rc == 0

    con = sqlite3.connect(calls["db"])
    area = con.execute("select area from TMTask where uuid=?",
                       (PROJECT,)).fetchone()[0]
    con.close()
    assert area == AREA_NEW


def test_uuid_unchanged_after_move(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT}],
             [(AREA_NEW, "Nouvelle")])
    _rig_effective_move(thingskit, monkeypatch, calls, AREA_NEW)

    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))
    assert rc == 0

    con = sqlite3.connect(calls["db"])
    row = con.execute("select uuid from TMTask where uuid=?", (PROJECT,)).fetchone()
    con.close()
    assert row is not None
    assert row[0] == PROJECT


# --- vérification post-action (constitution § Zones sensibles 1) ---------

def test_failure_when_effect_not_observed(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT}],
             [(AREA_NEW, "Nouvelle")])
    # osa "réussit" (rc=0) mais ne modifie rien en base.
    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))
    assert rc != 0
    assert len(calls["osa"]) == 1


def test_failure_when_stored_value_differs_from_target(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT}],
             [(AREA_NEW, "Nouvelle"), (AREA_OLD, "Ancienne")])
    # L'effet observé pointe vers une AUTRE area que celle demandée.
    _rig_effective_move(thingskit, monkeypatch, calls, AREA_OLD)

    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))
    assert rc != 0


# --- l'attente est une CONDITION OBSERVÉE, jamais une durée devinée -------
#
# BUG-032. Cette branche a divergé de `master` avant BUG-016 : elle portait
# `time.sleep(1.5)`, l'attente fixe que BUG-016 a mesurée fautive dans les
# deux sens — ~130× trop longue dans le cas courant, et TROP COURTE sur la
# queue (5026 ms mesurés le 2026-08-25 sur une écriture Things réussie). Sur
# cette queue, la commande sortait en ÉCHEC sur un déplacement pourtant fait,
# et l'appelant qui réessaie duplique dans les données de l'utilisateur.
#
# Le balayage d'AST de `tests/test_write_wait.py` interdit la forme ; ce qui
# suit garde le COMPORTEMENT, sur horloge virtuelle — aucun test d'ici ne
# dépend du temps réel.

MEASURED_TAIL = 5.026   # la queue mesurée le 2026-08-25 (BUG-016)
OLD_FIXED_WAIT = 1.5    # le plafond fixe que cette branche portait


class Clock:
    """Horloge virtuelle : `sleep` avance un compteur, personne n'attend."""

    def __init__(self, on_tick=None):
        self.elapsed = 0.0
        self.naps: list[float] = []
        self.on_tick = on_tick or (lambda: None)

    def sleep(self, seconds):
        self.naps.append(seconds)
        self.elapsed += seconds
        self.on_tick()


def _rig_move_landing_at(thingskit, monkeypatch, calls, area, seconds):
    """`osa` qui n'écrit rien tout de suite : l'effet atterrit à `seconds`
    d'horloge VIRTUELLE, comme la queue mesurée le fait en temps réel."""
    db_file = calls["db"]
    state = {"done": False}

    def _apply():
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set area=? where uuid=?", (area, PROJECT))
        con.commit()
        con.close()

    clock = Clock()

    def _tick():
        if not state["done"] and clock.elapsed >= seconds:
            state["done"] = True
            _apply()

    clock.on_tick = _tick
    monkeypatch.setattr(thingskit, "time", clock)
    monkeypatch.setattr(thingskit, "osa",
                        lambda script: (calls["osa"].append(script), (0, ""))[1])
    return clock


def test_an_effect_observed_after_the_old_fixed_wait_is_still_a_success(
        thingskit, monkeypatch, rigged):
    """Le faux négatif de BUG-016, rejoué sur `move-project`.

    Une attente FIXE de 1500 ms rend ici un échec sur un déplacement réussi :
    c'est exactement ce que cette assertion condamne. Elle tombe si la boucle
    bornée disparaît, quelle que soit la durée qui la remplace.
    """
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
               "area": AREA_OLD}],
             [(AREA_OLD, "Ancienne"), (AREA_NEW, "Nouvelle")])
    clock = _rig_move_landing_at(thingskit, monkeypatch, calls, AREA_NEW,
                                 MEASURED_TAIL)

    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))

    assert rc == 0
    assert clock.elapsed >= OLD_FIXED_WAIT, (
        "l'effet a été constaté avant l'ancien plafond — le cas n'est pas rejoué")


def test_no_wait_is_paid_when_the_effect_is_already_there(
        thingskit, monkeypatch, rigged):
    """L'autre moitié du défaut : ~130× trop long dans le cas courant.

    L'effet atterrit PENDANT l'appel `osa`, donc il est déjà là au premier
    sondage. Poser l'area cible en base AVANT l'appel ne testerait pas ceci :
    la commande sortirait par le court-circuit d'idempotence sans jamais
    atteindre l'attente, et l'assertion serait verte pour la mauvaise raison
    — mesuré, ce test-là passait encore avec une attente fixe réinstaurée.
    """
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
               "area": AREA_OLD}],
             [(AREA_OLD, "Ancienne"), (AREA_NEW, "Nouvelle")])
    clock = Clock()
    monkeypatch.setattr(thingskit, "time", clock)
    _rig_effective_move(thingskit, monkeypatch, calls, AREA_NEW)

    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))

    assert rc == 0
    assert len(calls["osa"]) == 1, "le court-circuit a court-circuité l'attente"
    assert clock.naps == [], "une attente a été payée alors que l'effet était là"


def test_an_effect_that_never_lands_still_fails_at_the_cap(
        thingskit, monkeypatch, rigged, capsys):
    """Le plafond atteint reste un ÉCHEC, jamais un « commande envoyée »."""
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
               "area": AREA_OLD}],
             [(AREA_OLD, "Ancienne"), (AREA_NEW, "Nouvelle")])
    clock = _rig_move_landing_at(thingskit, monkeypatch, calls, AREA_NEW, None)
    clock.on_tick = lambda: None

    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))

    assert rc != 0
    assert "ÉCHEC" in capsys.readouterr().err
    assert clock.elapsed >= thingskit.WRITE_TIMEOUT - 1e-6


def test_waiting_never_writes_a_single_byte_to_the_database(
        thingskit, monkeypatch, rigged):
    """La sonde LIT, elle n'écrit pas — y compris jusqu'au plafond."""
    calls, set_rows = rigged
    db_file = set_rows(
        [{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
          "area": AREA_OLD}],
        [(AREA_OLD, "Ancienne"), (AREA_NEW, "Nouvelle")])
    clock = _rig_move_landing_at(thingskit, monkeypatch, calls, AREA_NEW, None)
    clock.on_tick = lambda: None
    before = db_file.read_bytes()

    thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))

    assert db_file.read_bytes() == before


# --- la branche « ligne disparue » n'est plus du code que rien ne retient --
#
# Elle existait déjà et AUCUN test ne tombait quand on la neutralisait
# (mutation, BUG-032). Elle est atteignable : la ligne peut disparaître entre
# l'ordre et la relecture (suppression concurrente dans l'application), et
# elle ne se diagnostique pas comme « l'area relue diffère ».

def test_a_project_row_that_vanishes_after_the_move_is_a_named_failure(
        thingskit, monkeypatch, rigged, capsys):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
               "area": AREA_OLD}],
             [(AREA_OLD, "Ancienne"), (AREA_NEW, "Nouvelle")])
    db_file = calls["db"]

    def _vanish(script):
        calls["osa"].append(script)
        con = sqlite3.connect(db_file)
        con.execute("delete from TMTask where uuid=?", (PROJECT,))
        con.commit()
        con.close()
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _vanish)

    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))

    assert rc != 0
    err = capsys.readouterr().err
    assert "introuvable après l'opération" in err
    assert "area relue" not in err, (
        "la ligne disparue est diagnostiquée comme un écart d'area")


# --- idempotence : la relecture ne distingue pas « fait » de « rien fait » -
#
# `move-project` est la seule commande d'écriture du script dont la
# vérification post-action est verte AVANT même l'ordre : un projet déjà dans
# l'area cible satisfait le prédicat sans qu'aucun déplacement ait eu lieu.
# La commande annonçait alors « projet déplacé » pour une opération qui
# n'avait rien déplacé. La constitution tranche déjà ce cas (§ Conventions,
# « Idempotence par vérification préalable ») : succès, message DISTINCT, et
# aucune sollicitation de l'application — c'est l'absence d'appel qui est
# testée, pas seulement le code retour.

def test_a_project_already_in_the_target_area_is_a_no_op_without_any_solicitation(
        thingskit, monkeypatch, rigged, capsys):
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
               "area": AREA_NEW}],
             [(AREA_OLD, "Ancienne"), (AREA_NEW, "Nouvelle")])
    launched = []
    monkeypatch.setattr(thingskit, "ensure_running",
                        lambda: launched.append("ensure_running"))

    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))

    assert rc == 0
    assert calls["osa"] == [], "l'application a été sollicitée pour un no-op"
    assert launched == [], "l'application a été lancée pour un no-op"
    out = capsys.readouterr().out
    assert "déjà dans l'area" in out
    assert "projet déplacé" not in out, (
        "le message affirme un déplacement qui n'a pas eu lieu")


def test_the_no_op_message_is_not_reused_for_a_real_move(
        thingskit, monkeypatch, rigged, capsys):
    """Contre-épreuve du sur-court-circuit : un vrai déplacement passe."""
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
               "area": AREA_OLD}],
             [(AREA_OLD, "Ancienne"), (AREA_NEW, "Nouvelle")])
    _rig_effective_move(thingskit, monkeypatch, calls, AREA_NEW)

    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))

    assert rc == 0
    assert len(calls["osa"]) == 1
    out = capsys.readouterr().out
    assert "projet déplacé" in out
    assert "déjà dans l'area" not in out


def test_a_project_with_no_area_is_never_taken_for_an_already_placed_one(
        thingskit, monkeypatch, rigged):
    """`None` n'est pas l'identifiant de l'area cible — le court-circuit ne
    doit pas se déclencher sur une comparaison de valeurs fausses."""
    calls, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Cible", "type": TYPE_PROJECT,
               "area": None}],
             [(AREA_NEW, "Nouvelle")])
    _rig_effective_move(thingskit, monkeypatch, calls, AREA_NEW)

    rc = thingskit.cmd_move_project(_ns(project_id=PROJECT, area="Nouvelle"))

    assert rc == 0
    assert len(calls["osa"]) == 1
