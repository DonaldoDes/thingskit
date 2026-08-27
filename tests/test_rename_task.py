"""`thingskit rename-task` — renommage du titre d'une tâche déjà créée (US-005).

Surface d'écriture : AppleScript ciblé (`set name of to do id … to "…"`),
symétrique de `cmd_set_notes` — mesuré le 2026-08-18 par `sdef
/Applications/Things3.app` : `name` est une propriété de la classe `to do`
SANS attribut `access` explicite (comme `notes`), donc rw par défaut ;
confirmé de bout en bout sur une tâche jetable réelle (écriture + relecture en
base, valeur exacte). Aucune automatisation d'interface, donc aucune garde de
caractères refusés dans un titre (`_refused_title_chars` ne s'applique qu'à
`create-heading`, seule commande qui pilote l'interface).

Invariant central : l'UUID de la tâche est INCHANGÉ après renommage — jamais
de suppression/recréation.

Ces tests ne touchent jamais l'application ni la vraie base : `db_path` est
redirigée vers une base SQLite jetable, `osa` / `ensure_running` / `time.sleep`
sont mockés.
"""
from __future__ import annotations

import argparse
import sqlite3

import pytest

from test_delete_task import _make_db


def _ns(id=None, title=None, new_title=None):
    return argparse.Namespace(id=id, title=title, new_title=new_title)


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    """Redirige db_path + neutralise ensure_running ; `osa` est un no-op qui
    enregistre ses appels (donc n'a aucun effet en base)."""
    calls = {"osa": []}

    def _set_rows(rows):
        db_file = _make_db(tmp_path, rows)
        monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
        return db_file

    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "osa",
                        lambda script: (calls["osa"].append(script), (0, ""))[1])
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    return calls, _set_rows


def _rig_effective_osa(thingskit, monkeypatch, db_file, calls, new_title):
    """`osa` qui simule l'effet réel : la ligne voit son `title` changer, mais
    JAMAIS son `uuid` — c'est l'invariant que ce module vérifie partout."""
    def _fake(script):
        calls.append(script)
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set title=? where uuid=?",
                    (new_title, "AAAAAAAAAAAAAAAAAAAAAA"))
        con.commit()
        con.close()
        return 0, ""
    monkeypatch.setattr(thingskit, "osa", _fake)


# --- adressage : refus, jamais « le premier » -------------------------------

def test_missing_id_and_title_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Solo"}])
    assert thingskit.cmd_rename_task(_ns(new_title="Nouveau")) != 0
    assert calls["osa"] == []


def test_missing_new_title_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Solo"}])
    assert thingskit.cmd_rename_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) != 0
    assert calls["osa"] == []


def test_malformed_uuid_refuses_before_any_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Solo"}])
    assert thingskit.cmd_rename_task(_ns(id="not a uuid!!", new_title="X")) != 0
    assert calls["osa"] == []


def test_title_no_match_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Autre chose"}])
    assert thingskit.cmd_rename_task(_ns(title="Introuvable", new_title="X")) != 0
    assert calls["osa"] == []


def test_title_ambiguous_refuses_no_rename(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Doublon"},
        {"uuid": "BBBBBBBBBBBBBBBBBBBBBB", "title": "Doublon"},
    ])
    assert thingskit.cmd_rename_task(_ns(title="Doublon", new_title="X")) != 0
    assert calls["osa"] == []


def test_unknown_id_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible"}])
    assert thingskit.cmd_rename_task(
        _ns(id="BBBBBBBBBBBBBBBBBBBBBB", new_title="X")) != 0
    assert calls["osa"] == []


# --- chemin nominal : id ------------------------------------------------

def test_nominal_by_id_sets_name_via_applescript_and_verifies(
        thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Ancien titre"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls, "Nouveau titre")

    assert thingskit.cmd_rename_task(_ns(id=target, new_title="Nouveau titre")) == 0
    assert len(calls) == 1
    script = calls[0]
    assert "set name of to do id" in script
    assert target in script
    assert "Nouveau titre" in script
    # Surface applicative, pas automatisation d'interface.
    assert "System Events" not in script
    assert "keystroke" not in script

    con = sqlite3.connect(db_file)
    row = con.execute("select uuid, title from TMTask where uuid=?",
                      (target,)).fetchone()
    con.close()
    assert row == (target, "Nouveau titre")


# --- chemin nominal : ancien titre --------------------------------------

def test_nominal_by_old_title(thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Ancien titre"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls, "Nouveau titre")

    assert thingskit.cmd_rename_task(
        _ns(title="Ancien titre", new_title="Nouveau titre")) == 0
    assert len(calls) == 1
    assert target in calls[0]


# --- invariant central : UUID inchangé -----------------------------------

def test_uuid_is_unchanged_after_rename(thingskit, monkeypatch, tmp_path):
    """L'invariant le plus important de cette commande : la tâche relue après
    coup EST la même ligne (même uuid), jamais une suppression/recréation."""
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Avant"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls, "Après")

    assert thingskit.cmd_rename_task(_ns(id=target, new_title="Après")) == 0

    con = sqlite3.connect(db_file)
    count = con.execute("select count(*) from TMTask").fetchone()[0]
    row = con.execute("select uuid from TMTask where title=?",
                      ("Après",)).fetchone()
    con.close()
    assert count == 1  # aucune ligne supplémentaire (pas de recréation)
    assert row == (target,)


# --- garde d'état : décidée pour CETTE opération, pas recopiée ----------
#
# Renommer n'écrit aucune décision par défaut (constitution : « une garde
# d'état se décide par opération, jamais par recopie »). Une tâche `completed`
# ou `canceled` accepte le renommage — même raisonnement que `set-notes` :
# corriger un titre inexact n'est pas reconvertir un état, c'est corriger un
# fait affiché. Seule la Corbeille est refusée : y écrire modifierait un objet
# que l'utilisateur a délibérément mis hors de vue.

OPEN, CANCELED, COMPLETED = 0, 2, 3


def test_completed_task_accepts_rename(thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Faite",
                                   "status": COMPLETED}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls, "Faite corrigée")

    assert thingskit.cmd_rename_task(_ns(id=target, new_title="Faite corrigée")) == 0
    assert len(calls) == 1


def test_canceled_task_accepts_rename(thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Annulée",
                                   "status": CANCELED}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls, "Annulée corrigée")

    assert thingskit.cmd_rename_task(
        _ns(id=target, new_title="Annulée corrigée")) == 0
    assert len(calls) == 1


def test_trashed_task_is_refused_by_id_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Jetée",
               "trashed": 1, "status": OPEN}])
    assert thingskit.cmd_rename_task(
        _ns(id="AAAAAAAAAAAAAAAAAAAAAA", new_title="X")) != 0
    assert calls["osa"] == []


def test_trashed_task_is_unreachable_by_title_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Jetée",
               "trashed": 1, "status": OPEN}])
    assert thingskit.cmd_rename_task(_ns(title="Jetée", new_title="X")) != 0
    assert calls["osa"] == []


# --- vérification post-action ----------------------------------------------

def test_failure_when_effect_not_observed(thingskit, rigged):
    """`osa` « réussit » (rc=0) mais la base ne montre rien : le code retour
    doit être non nul. `0` signifie « constaté fait », jamais « commande
    envoyée »."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible"}])
    assert thingskit.cmd_rename_task(
        _ns(id="AAAAAAAAAAAAAAAAAAAAAA", new_title="Nouveau")) != 0
    assert len(calls["osa"]) == 1  # l'action a bien été tentée


def test_failure_when_stored_value_differs(thingskit, monkeypatch, tmp_path):
    """La vérification exige l'égalité EXACTE du titre relu, pas « le titre a
    changé »."""
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Cible"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    # simule un atterrissage sur une valeur DIFFÉRENTE de celle demandée
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls, "Autre chose")

    assert thingskit.cmd_rename_task(
        _ns(id=target, new_title="Nouveau titre voulu")) != 0


# --- propriétés préservées --------------------------------------------------

def test_notes_when_deadline_and_project_are_preserved(
        thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [
        {"uuid": "PPPPPPPPPPPPPPPPPPPPPP", "title": "Le projet", "type": 1},
        {"uuid": target, "title": "Avant", "notes": "Des notes précieuses",
         "startDate": 20260819, "startBucket": 2, "deadline": None,
         "project": "PPPPPPPPPPPPPPPPPPPPPP"},
    ])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls, "Après")

    assert thingskit.cmd_rename_task(_ns(id=target, new_title="Après")) == 0

    con = sqlite3.connect(db_file)
    row = con.execute(
        "select notes, startDate, startBucket, deadline, project "
        "from TMTask where uuid=?", (target,)).fetchone()
    con.close()
    assert row == ("Des notes précieuses", 20260819, 2, None,
                   "PPPPPPPPPPPPPPPPPPPPPP")


# --- invariant zone sensible : aucune écriture SQL --------------------------

def test_no_sql_write_reaches_the_database(thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Cible"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, ""))

    before = db_file.read_bytes()
    thingskit.cmd_rename_task(_ns(id=target, new_title="Nouveau"))
    assert db_file.read_bytes() == before


def test_id_and_title_are_escaped_into_the_applescript(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible"}])
    thingskit.cmd_rename_task(
        _ns(id="AAAAAAAAAAAAAAAAAAAAAA", new_title='Titre avec "guillemets"'))
    assert calls["osa"]
    assert '"AAAAAAAAAAAAAAAAAAAAAA"' in calls["osa"][0]
    assert '\\"guillemets\\"' in calls["osa"][0]


def test_registered_in_cli_help(thingskit, run_cli):
    """La sous-commande est exposée par l'aide du CLI, et documentée dans le
    bloc Usage du module."""
    code, out, _ = run_cli(["--help"])
    assert code == 0
    assert "rename-task" in out
    assert "rename-task" in (thingskit.__doc__ or "")
    # L'aide GÉNÉRALE porte le bloc Usage du module : le nom y figure même si
    # la sous-commande n'est plus câblée au parseur. Mesuré le 2026-08-26 —
    # renommer `add("rename-task", …)` dans `bin/thingskit` laissait les SIX tests de
    # cette famille au vert, celui-ci compris. Seule l'invocation de la
    # sous-commande éprouve le câblage : argparse rend 2 si elle n'existe pas.
    code, _, err = run_cli(["rename-task", "--help"])
    assert code == 0, err
