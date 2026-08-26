"""`thingskit cancel-task` — annulation d'une tâche (`status` -> `canceled`).

Symétrique de `complete-task`, mais pas sa copie : Things distingue trois
états (`open`/`completed`/`canceled`) qui ne portent pas le même fait.
« Terminée » affirme que le travail a été fait ; « annulée » que l'utilisateur
a décidé de ne pas le faire. Marquer « terminée » une tâche abandonnée
inscrirait un fait faux dans l'historique — d'où cette commande distincte.

Gardes d'état, décidées par symétrie et non par recopie :
  - déjà `canceled`  -> idempotent, succès sans solliciter l'application
    (même logique que `complete-task` sur une tâche déjà `completed`).
  - `completed`      -> REFUS. Annuler une tâche déjà terminée réécrirait
    le fait « le travail a été fait » sans qu'on le demande — ce n'est pas
    ce que défait `reopen-task` (2026-08-24), qui ramène en `open`, jamais
    directement en `canceled`. Même prudence que `complete-task` refusant
    `canceled` : en cas de doute, ne rien faire coûte moins cher que de
    réécrire un historique.
  - Corbeille        -> refus, comme pour `complete-task`/`delete-task`.

Ces tests ne touchent jamais l'application ni la vraie base : `db_path` est
redirigée vers une base SQLite jetable, `osa` / `ensure_running` / `time.sleep`
sont mockés.
"""
from __future__ import annotations

import argparse
import sqlite3

import pytest

from test_delete_task import _make_db


OPEN, CANCELED, COMPLETED = 0, 2, 3


def _ns(id=None, title=None):
    return argparse.Namespace(id=id, title=title)


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
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


def _rig_effective_osa(thingskit, monkeypatch, db_file, calls, new_status=CANCELED):
    def _fake(script):
        calls.append(script)
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set status=? where uuid=?",
                    (new_status, "AAAAAAAAAAAAAAAAAAAAAA"))
        con.commit()
        con.close()
        return 0, ""
    monkeypatch.setattr(thingskit, "osa", _fake)


# --- adressage : refus, jamais « le premier » -------------------------------

def test_missing_id_and_title_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Solo"}])
    assert thingskit.cmd_cancel_task(_ns()) != 0
    assert calls["osa"] == []


def test_malformed_uuid_refuses_before_any_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Solo"}])
    assert thingskit.cmd_cancel_task(_ns(id="not a uuid!!")) != 0
    assert calls["osa"] == []


def test_title_no_match_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Autre chose"}])
    assert thingskit.cmd_cancel_task(_ns(title="Introuvable")) != 0
    assert calls["osa"] == []


def test_title_ambiguous_refuses_no_cancellation(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Doublon"},
        {"uuid": "BBBBBBBBBBBBBBBBBBBBBB", "title": "Doublon"},
    ])
    assert thingskit.cmd_cancel_task(_ns(title="Doublon")) != 0
    assert calls["osa"] == []


def test_unknown_id_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible"}])
    assert thingskit.cmd_cancel_task(_ns(id="BBBBBBBBBBBBBBBBBBBBBB")) != 0
    assert calls["osa"] == []


def test_project_is_not_a_task(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Chantier", "type": 1}])
    assert thingskit.cmd_cancel_task(_ns(title="Chantier")) != 0
    assert calls["osa"] == []


# --- tâche sous heading : `project` vide en base ----------------------------

def test_task_under_heading_is_reachable_by_title(thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [
        {"uuid": "PPPPPPPPPPPPPPPPPPPPPP", "title": "Le projet", "type": 1},
        {"uuid": "HHHHHHHHHHHHHHHHHHHHHH", "title": "Section", "type": 2,
         "project": "PPPPPPPPPPPPPPPPPPPPPP"},
        {"uuid": target, "title": "Sous heading", "type": 0,
         "project": None, "heading": "HHHHHHHHHHHHHHHHHHHHHH"},
    ])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls)

    assert thingskit.cmd_cancel_task(_ns(title="Sous heading")) == 0
    assert len(calls) == 1
    assert target in calls[0]


# --- chemin nominal ---------------------------------------------------------

def test_nominal_sets_status_via_applescript_and_verifies(thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Cible"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls)

    assert thingskit.cmd_cancel_task(_ns(id=target)) == 0
    assert len(calls) == 1
    script = calls[0]
    assert "set status of to do id" in script
    assert "canceled" in script
    assert target in script
    assert "System Events" not in script
    assert "keystroke" not in script

    con = sqlite3.connect(db_file)
    st = con.execute("select status from TMTask where uuid=?", (target,)).fetchone()[0]
    con.close()
    assert st == CANCELED


# --- idempotence -------------------------------------------------------------

def test_already_canceled_is_success_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Déjà annulée",
               "status": CANCELED}])
    assert thingskit.cmd_cancel_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) == 0
    assert calls["osa"] == []


# --- tâche terminée : refus explicite ---------------------------------------

def test_completed_task_is_refused_without_any_osa_call(thingskit, rigged):
    """Symétrique du refus `canceled` de `complete-task` : `completed` est un
    fait acquis (le travail a été fait), l'annuler réécrirait ce fait sans
    qu'on le demande — `reopen-task` (2026-08-24) ne défait pas ce refus,
    elle ramène en `open`, jamais directement en `canceled`."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Terminée",
               "status": COMPLETED}])
    assert thingskit.cmd_cancel_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) != 0
    assert calls["osa"] == []


# --- tâche à la Corbeille : refus explicite, les deux adressages ------------

def test_trashed_task_is_refused_by_id_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Jetée",
               "trashed": 1, "status": OPEN}])
    assert thingskit.cmd_cancel_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) != 0
    assert calls["osa"] == []


def test_trashed_task_is_unreachable_by_title_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Jetée",
               "trashed": 1, "status": OPEN}])
    assert thingskit.cmd_cancel_task(_ns(title="Jetée")) != 0
    assert calls["osa"] == []


def test_trashed_homonym_neither_blocks_nor_diverts_the_active_task(
        thingskit, monkeypatch, tmp_path):
    active = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [
        {"uuid": active, "title": "Homonyme"},
        {"uuid": "BBBBBBBBBBBBBBBBBBBBBB", "title": "Homonyme", "trashed": 1},
    ])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls)

    assert thingskit.cmd_cancel_task(_ns(title="Homonyme")) == 0
    assert len(calls) == 1
    assert active in calls[0]
    assert "BBBBBBBBBBBBBBBBBBBBBB" not in calls[0]


# --- vérification post-action -----------------------------------------------

def test_failure_when_effect_not_observed(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible"}])
    assert thingskit.cmd_cancel_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) != 0
    assert len(calls["osa"]) == 1  # l'action a bien été tentée


def test_failure_when_status_lands_on_completed(thingskit, monkeypatch, tmp_path):
    """La vérification porte sur la valeur EXACTE `canceled`, pas sur « le
    statut a changé » : un atterrissage sur `completed` est un échec."""
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Cible"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls, new_status=COMPLETED)

    assert thingskit.cmd_cancel_task(_ns(id=target)) != 0


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
    thingskit.cmd_cancel_task(_ns(id=target))
    assert db_file.read_bytes() == before


def test_id_is_escaped_into_the_applescript(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible"}])
    thingskit.cmd_cancel_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA"))
    assert calls["osa"] and '"AAAAAAAAAAAAAAAAAAAAAA"' in calls["osa"][0]


def test_registered_in_cli_help(thingskit, run_cli):
    """La sous-commande est exposée par l'aide du CLI, et documentée dans le
    bloc Usage du module."""
    code, out, _ = run_cli(["--help"])
    assert code == 0
    assert "cancel-task" in out
    assert "cancel-task" in (thingskit.__doc__ or "")
    # L'aide GÉNÉRALE porte le bloc Usage du module : le nom y figure même si
    # la sous-commande n'est plus câblée au parseur. Mesuré le 2026-08-26 —
    # renommer `add("cancel-task", …)` dans `bin/thingskit` laissait les SIX tests de
    # cette famille au vert, celui-ci compris. Seule l'invocation de la
    # sous-commande éprouve le câblage : argparse rend 2 si elle n'existe pas.
    code, _, err = run_cli(["cancel-task", "--help"])
    assert code == 0, err
