"""BUG-016 — le message d'échec cite ce que la SONDE a observé, jamais une
seconde lecture de la base.

`3fa3656` a fermé la course sur `cmd_move_task` et `cmd_reschedule_task`.
Quatre sites portaient la même cause et n'avaient pas été balayés :
`_write_task_notes` (donc `set-notes` / `append-notes`), `cmd_complete_task`,
`cmd_rename_task`, `cmd_cancel_task`. Tous relisaient la base APRÈS l'échec de
`wait_for_effect` pour composer leur message.

Le symptôme n'y est pas un `None` littéral comme sur `move`, mais une
**contradiction interne** : l'effet atterrit entre le dernier sondage et la
composition, la relecture rend alors la valeur ATTENDUE, et le message affirme
un échec en montrant deux valeurs identiques —

    ÉCHEC renommage tâche 'Ancien' (…) : titre constaté en base = 'Nouveau',
    attendu 'Nouveau' — code retour = échec, pas 'commande envoyée'.

C'est pire qu'un message vague : il fait douter du code retour, qui lui est
juste (§ Zones sensibles 1 — un plafond atteint EST un échec).

Le second groupe couvre la branche de repli : quand la lecture de la base est
refusée pendant TOUTE l'attente, la sonde n'a jamais rien observé, et le
message doit le dire au lieu de composer un écart qu'il n'a pas constaté.
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

TARGET = "AAAAAAAAAAAAAAAAAAAAAA"
NOTES = "du texte"


def _make_db(tmp_path, rows):
    db_file = tmp_path / "main.sqlite"
    con = sqlite3.connect(db_file)
    con.executescript(SCHEMA)
    defaults = dict(
        uuid=None, title=None, type=0, trashed=0, project=None, heading=None,
        area=None, startDate=None, startBucket=None, deadline=None,
        reminderTime=None, status=0, notes=None,
    )
    for r in rows:
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,project,heading,area,"
            "startDate,startBucket,deadline,reminderTime,status,notes) values "
            "(:uuid,:title,:type,:trashed,:project,:heading,:area,"
            ":startDate,:startBucket,:deadline,:reminderTime,:status,:notes)",
            {**defaults, **r},
        )
    con.commit()
    con.close()
    return db_file


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    """Une tâche `Ancien` ouverte, sans notes, dans une base jetable.

    `time` est une horloge virtuelle : `sleep` n'attend pas, donc les 600
    sondages du plafond ne coûtent rien au temps réel.
    """
    db_file = _make_db(tmp_path, [{"uuid": TARGET, "title": "Ancien",
                                   "type": 0, "notes": ""}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, ""))
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    return db_file


def _land(db_file, sql, args):
    con = sqlite3.connect(db_file)
    con.execute(sql, args)
    con.commit()
    con.close()


def _wait_then_land(thingskit, monkeypatch, db_file, sql, args):
    """Remplace `wait_for_effect` par : un dernier sondage AVANT l'atterrissage,
    puis l'effet atterrit, puis on rend la valeur (fausse) observée.

    C'est exactement la course réelle : la boucle abandonne au plafond, et
    l'écriture aboutit dans l'intervalle qui la sépare du `print`.
    """
    def _fake(probe, *a, **kw):
        observed = probe()
        _land(db_file, sql, args)
        return observed

    monkeypatch.setattr(thingskit, "wait_for_effect", _fake)


def _refuse_reads_after_the_write(thingskit, monkeypatch):
    """La base devient illisible dès que l'ordre est envoyé, et le reste.

    `sqlite3.OperationalError` est ce que `_probe_once` avale comme « pas
    encore constaté ». La sonde n'observe donc RIEN de toute l'attente, et le
    site d'appel n'a aucune valeur à citer.
    """
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


_NO_OBSERVATION = "lecture de la base refusée pendant toute l'attente"


def _set_notes(thingskit):
    return thingskit.cmd_set_notes(
        argparse.Namespace(project=None, project_id=None, task=None,
                           task_id=TARGET, notes=NOTES))


def _rename(thingskit):
    return thingskit.cmd_rename_task(
        argparse.Namespace(id=TARGET, title=None, new_title="Nouveau"))


def _complete(thingskit):
    return thingskit.cmd_complete_task(
        argparse.Namespace(id=TARGET, title=None))


def _cancel(thingskit):
    return thingskit.cmd_cancel_task(argparse.Namespace(id=TARGET, title=None))


# ---------------------------------------------------------------------------
# 1. La course : l'effet atterrit entre le dernier sondage et le message
# ---------------------------------------------------------------------------
def test_the_rename_failure_never_shows_the_expected_title_as_observed(
        thingskit, monkeypatch, rigged, capsys):
    _wait_then_land(thingskit, monkeypatch, rigged,
                    "update TMTask set title=? where uuid=?",
                    ("Nouveau", TARGET))

    rc = _rename(thingskit)

    assert rc != 0
    err = capsys.readouterr().err
    assert "= 'Nouveau', attendu 'Nouveau'" not in err, (
        "le message affirme un échec en montrant deux valeurs identiques : " + err)
    assert "'Ancien'" in err, (
        "le message doit citer le titre que la sonde a observé : " + err)


def test_the_completion_failure_never_shows_completed_as_observed(
        thingskit, monkeypatch, rigged, capsys):
    _wait_then_land(thingskit, monkeypatch, rigged,
                    "update TMTask set status=? where uuid=?",
                    (thingskit.STATUS_COMPLETED, TARGET))

    rc = _complete(thingskit)

    assert rc != 0
    err = capsys.readouterr().err
    assert "= completed, attendu 'completed'" not in err, err
    assert "open" in err, (
        "le message doit citer le statut que la sonde a observé : " + err)


def test_the_cancellation_failure_never_shows_canceled_as_observed(
        thingskit, monkeypatch, rigged, capsys):
    _wait_then_land(thingskit, monkeypatch, rigged,
                    "update TMTask set status=? where uuid=?",
                    (thingskit.STATUS_CANCELED, TARGET))

    rc = _cancel(thingskit)

    assert rc != 0
    err = capsys.readouterr().err
    assert "= canceled, attendu 'canceled'" not in err, err
    assert "open" in err, err


def test_the_notes_failure_never_shows_two_identical_lengths(
        thingskit, monkeypatch, rigged, capsys):
    """Ici la contradiction est arithmétique : « (8 caractères) diffère de
    celle demandée (8 caractères) »."""
    _wait_then_land(thingskit, monkeypatch, rigged,
                    "update TMTask set notes=? where uuid=?", (NOTES, TARGET))

    rc = _set_notes(thingskit)

    assert rc != 0
    err = capsys.readouterr().err
    assert f"({len(NOTES)} caractères) diffère de celle demandée " \
           f"({len(NOTES)} caractères)" not in err, err
    assert "(0 caractères)" in err, (
        "le message doit citer la longueur que la sonde a observée : " + err)


# ---------------------------------------------------------------------------
# 2. Le repli : la sonde n'a rien pu observer de toute l'attente
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,run", [
    ("set-notes", _set_notes),
    ("complete-task", _complete),
    ("rename-task", _rename),
    ("cancel-task", _cancel),
])
def test_a_database_unreadable_throughout_is_said_not_invented(
        name, run, thingskit, monkeypatch, rigged, capsys):
    """Branche atteignable et jamais couverte avant cette passe.

    Sans elle, le message composerait un écart que la sonde n'a pas constaté —
    ou, sur les sites convertis par capture, une chaîne vide.
    """
    _refuse_reads_after_the_write(thingskit, monkeypatch)

    rc = run(thingskit)

    assert rc != 0, f"{name} rend 0 sans avoir rien constaté"
    err = capsys.readouterr().err
    assert "ÉCHEC" in err
    assert _NO_OBSERVATION in err, err
