"""`thingskit delete-task` — suppression destructive, sous garde-fous.

Établi par test le 2026-08-12 (probe manuelle sur une tâche jetable réelle,
hors suite pytest) : `delete to do id` déplace la tâche vers la Corbeille
Things (colonne `trashed` passe à 1, la ligne SUBSISTE en base, `status`
inchangé) — ce n'est PAS une destruction définitive, c'est récupérable via
"Put Back" dans l'app. Ces tests-ci ne touchent jamais la vraie app ni la
vraie base : `db_path` est redirigée vers une base SQLite jetable, et `osa`/
`ensure_running` sont mockés pour capturer l'invocation AppleScript sans
l'exécuter.
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
        row = {**defaults, **r}
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,project,heading,area,"
            "startDate,startBucket,deadline,reminderTime,status,notes) values "
            "(:uuid,:title,:type,:trashed,:project,:heading,:area,"
            ":startDate,:startBucket,:deadline,:reminderTime,:status,:notes)",
            row,
        )
    con.commit()
    con.close()
    return db_file


def _ns(id=None, title=None):
    return argparse.Namespace(id=id, title=title)


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    """Redirige db_path + neutralise ensure_running ; osa() reste au choix du test."""
    calls = {"osa": []}

    def _set_rows(rows):
        db_file = _make_db(tmp_path, rows)
        monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
        return db_file

    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)

    def _fake_osa_noop(script):
        calls["osa"].append(script)
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _fake_osa_noop)
    monkeypatch.setattr(thingskit, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))

    return calls, _set_rows


def test_missing_id_and_title_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "u1", "title": "Solo", "type": 0}])
    rc = thingskit.cmd_delete_task(_ns())
    assert rc != 0
    assert calls["osa"] == []


def test_malformed_uuid_refuses_before_any_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "u1", "title": "Solo", "type": 0}])
    rc = thingskit.cmd_delete_task(_ns(id="not a uuid!!"))
    assert rc != 0
    assert calls["osa"] == []


def test_title_ambiguous_refuses_no_deletion(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Doublon", "type": 0},
        {"uuid": "BBBBBBBBBBBBBBBBBBBBBB", "title": "Doublon", "type": 0},
    ])
    rc = thingskit.cmd_delete_task(_ns(title="Doublon"))
    assert rc != 0
    assert calls["osa"] == []


def test_title_no_match_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Autre chose", "type": 0}])
    rc = thingskit.cmd_delete_task(_ns(title="Introuvable"))
    assert rc != 0
    assert calls["osa"] == []


def test_nominal_path_calls_applescript_and_verifies(thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Cible", "type": 0}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))

    calls = []

    def _fake_osa(script):
        calls.append(script)
        # Simule l'effet réel constaté par test : la ligne subsiste, trashed passe à 1.
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set trashed=1 where uuid=?", (target,))
        con.commit()
        con.close()
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _fake_osa)

    rc = thingskit.cmd_delete_task(_ns(id=target))
    assert rc == 0
    assert len(calls) == 1
    assert "delete" in calls[0]
    assert target in calls[0]

    con = sqlite3.connect(db_file)
    trashed = con.execute("select trashed from TMTask where uuid=?", (target,)).fetchone()[0]
    con.close()
    assert trashed == 1


def test_nominal_path_title_resolves_when_unique(thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Unique", "type": 0}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))

    def _fake_osa(script):
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set trashed=1 where uuid=?", (target,))
        con.commit()
        con.close()
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _fake_osa)

    rc = thingskit.cmd_delete_task(_ns(title="Unique"))
    assert rc == 0


def test_failure_when_effect_not_observed(thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Cible", "type": 0}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))

    # osa "réussit" (rc=0) mais ne modifie rien en base : simule un appel envoyé
    # sans effet constaté. exit doit signifier "constaté", pas "commande envoyée".
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, ""))

    rc = thingskit.cmd_delete_task(_ns(id=target))
    assert rc != 0
