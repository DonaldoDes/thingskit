"""`thingskit complete-task` — marquage d'une tâche comme terminée.

Surface d'écriture : AppleScript ciblé (`set status of to do id … to completed`),
et non automatisation d'interface — le dictionnaire AppleScript de Things expose
`status` comme propriété **rw** de la classe `to do`, avec l'énumération
`open` / `completed` / `canceled` (constaté le 2026-08-12 par `sdef
/Applications/Things3.app`, pas déduit de la documentation). C'est la surface la
moins invasive qui couvre le besoin : elle ne dépend d'aucun libellé de menu
localisé et ne peut pas taper dans le mauvais champ.

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


def _rig_effective_osa(thingskit, monkeypatch, db_file, calls, new_status=COMPLETED):
    """`osa` qui simule l'effet réel : la ligne voit son `status` changer."""
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
    assert thingskit.cmd_complete_task(_ns()) != 0
    assert calls["osa"] == []


def test_malformed_uuid_refuses_before_any_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Solo"}])
    assert thingskit.cmd_complete_task(_ns(id="not a uuid!!")) != 0
    assert calls["osa"] == []


def test_title_no_match_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Autre chose"}])
    assert thingskit.cmd_complete_task(_ns(title="Introuvable")) != 0
    assert calls["osa"] == []


def test_title_ambiguous_refuses_no_completion(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Doublon"},
        {"uuid": "BBBBBBBBBBBBBBBBBBBBBB", "title": "Doublon"},
    ])
    assert thingskit.cmd_complete_task(_ns(title="Doublon")) != 0
    assert calls["osa"] == []


def test_unknown_id_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible"}])
    assert thingskit.cmd_complete_task(_ns(id="BBBBBBBBBBBBBBBBBBBBBB")) != 0
    assert calls["osa"] == []


def test_project_is_not_a_task(thingskit, rigged):
    """Un projet porte le même `title` qu'une tâche pourrait porter : la
    résolution est typée (type=0), un projet n'est jamais complété par ici."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Chantier", "type": 1}])
    assert thingskit.cmd_complete_task(_ns(title="Chantier")) != 0
    assert calls["osa"] == []


# --- tâche sous heading : `project` vide en base ----------------------------

def test_task_under_heading_is_reachable_by_title(thingskit, monkeypatch, tmp_path):
    """Une tâche sous heading a sa colonne `project` VIDE (constat documenté en
    tête de `bin/thingskit`). La résolution par titre doit l'atteindre malgré
    cela — donc ne jamais joindre sur `project`."""
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [
        {"uuid": "PPPPPPPPPPPPPPPPPPPPPP", "title": "Le projet", "type": 1},
        {"uuid": "HHHHHHHHHHHHHHHHHHHHHH", "title": "Section", "type": 2,
         "project": "PPPPPPPPPPPPPPPPPPPPPP"},
        # project VIDE, heading renseigné : le cas réel constaté.
        {"uuid": target, "title": "Sous heading", "type": 0,
         "project": None, "heading": "HHHHHHHHHHHHHHHHHHHHHH"},
    ])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls)

    assert thingskit.cmd_complete_task(_ns(title="Sous heading")) == 0
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

    assert thingskit.cmd_complete_task(_ns(id=target)) == 0
    assert len(calls) == 1
    script = calls[0]
    assert "set status of to do id" in script
    assert "completed" in script
    assert target in script
    # Surface applicative, pas automatisation d'interface.
    assert "System Events" not in script
    assert "keystroke" not in script

    con = sqlite3.connect(db_file)
    st = con.execute("select status from TMTask where uuid=?", (target,)).fetchone()[0]
    con.close()
    assert st == COMPLETED


# --- idempotence ------------------------------------------------------------

def test_already_completed_is_success_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Déjà faite",
               "status": COMPLETED}])
    assert thingskit.cmd_complete_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) == 0
    assert calls["osa"] == []


# --- tâche annulée : refus explicite ---------------------------------------

def test_canceled_task_is_refused_without_any_osa_call(thingskit, rigged):
    """`canceled` n'est pas `completed` : c'est une décision de l'utilisateur
    (« on ne le fera pas »), distincte dans l'énumération du dictionnaire. La
    convertir en « terminée » réécrirait cette décision — ce n'est pas ce que
    défait `reopen-task` (2026-08-24), qui ramène en `open`, jamais
    directement en `completed`. Refus explicite, actionnable."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Abandonnée",
               "status": CANCELED}])
    assert thingskit.cmd_complete_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) != 0
    assert calls["osa"] == []


# --- tâche à la Corbeille : refus explicite, les deux adressages ------------
#
# Les deux formes d'adressage NE convergent PAS vers un point unique, et c'est
# constaté dans le code, pas supposé :
#   - `--id` traverse la garde `if trashed:` de `cmd_complete_task` ;
#   - `--title` n'y arrive jamais, parce que `_resolve_task_by_title` filtre
#     `trashed=0` en SQL : une tâche à la Corbeille est déjà « introuvable »
#     au moment de la résolution.
# Deux chemins, deux refus, donc deux tests — couvrir le seul chemin `--id`
# laisserait le second non gardé.

def test_trashed_task_is_refused_by_id_without_any_osa_call(thingskit, rigged):
    """Une tâche à la Corbeille n'est pas complétée : l'utilisateur a
    délibérément mis cet objet dans cet état, comme pour l'annulation. Le refus
    PRÉCÈDE toute sollicitation de l'application — un refus qui interviendrait
    après avoir écrit ne serait pas un refus."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Jetée",
               "trashed": 1, "status": OPEN}])
    assert thingskit.cmd_complete_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) != 0
    assert calls["osa"] == []


def test_trashed_task_is_unreachable_by_title_without_any_osa_call(thingskit, rigged):
    """Second chemin d'adressage : le refus ne se contourne pas par `--title`.
    La résolution ne voit que les tâches actives (`trashed=0`), donc refuse en
    « introuvable » — sans jamais atteindre la garde `if trashed:`."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Jetée",
               "trashed": 1, "status": OPEN}])
    assert thingskit.cmd_complete_task(_ns(title="Jetée")) != 0
    assert calls["osa"] == []


def test_trashed_homonym_neither_blocks_nor_diverts_the_active_task(
        thingskit, monkeypatch, tmp_path):
    """Corollaire du filtre `trashed=0` : un homonyme à la Corbeille ne rend pas
    le titre ambigu et ne détourne pas la complétion — c'est bien la tâche
    ACTIVE qui est visée."""
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

    assert thingskit.cmd_complete_task(_ns(title="Homonyme")) == 0
    assert len(calls) == 1
    assert active in calls[0]
    assert "BBBBBBBBBBBBBBBBBBBBBB" not in calls[0]


# --- vérification post-action ----------------------------------------------

def test_failure_when_effect_not_observed(thingskit, rigged):
    """`osa` « réussit » (rc=0) mais la base ne montre rien : le code retour
    doit être non nul. `0` signifie « constaté fait », jamais « commande
    envoyée »."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible"}])
    assert thingskit.cmd_complete_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) != 0
    assert len(calls["osa"]) == 1  # l'action a bien été tentée


def test_failure_when_status_lands_on_canceled(thingskit, monkeypatch, tmp_path):
    """La vérification porte sur la valeur EXACTE `completed`, pas sur « le
    statut a changé » : un atterrissage sur `canceled` est un échec."""
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Cible"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls, new_status=CANCELED)

    assert thingskit.cmd_complete_task(_ns(id=target)) != 0


# --- invariant zone sensible : aucune écriture SQL --------------------------

def test_no_sql_write_reaches_the_database(thingskit, monkeypatch, tmp_path):
    """Le CLI ne modifie jamais la base directement. Seul le mock `osa`
    (qui simule l'application) écrit ici ; si on le rend inerte, la base doit
    rester rigoureusement inchangée."""
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Cible"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, ""))

    before = db_file.read_bytes()
    thingskit.cmd_complete_task(_ns(id=target))
    assert db_file.read_bytes() == before


def test_id_is_escaped_into_the_applescript(thingskit, rigged):
    """L'identifiant est interpolé dans un script AppleScript : il passe par
    `_esc`, jamais brut. La garde de forme (`_TASK_ID_RE`) l'exclut déjà, mais
    l'échappement reste la ceinture."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible"}])
    thingskit.cmd_complete_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA"))
    assert calls["osa"] and '"AAAAAAAAAAAAAAAAAAAAAA"' in calls["osa"][0]


def test_registered_in_cli_help(thingskit, run_cli):
    """La sous-commande est exposée par l'aide du CLI, et documentée dans le
    bloc Usage du module."""
    code, out, _ = run_cli(["--help"])
    assert code == 0
    assert "complete-task" in out
    assert "complete-task" in (thingskit.__doc__ or "")
    # L'aide GÉNÉRALE porte le bloc Usage du module : le nom y figure même si
    # la sous-commande n'est plus câblée au parseur. Mesuré le 2026-08-26 —
    # renommer `add("complete-task", …)` dans `bin/thingskit` laissait les SIX tests de
    # cette famille au vert, celui-ci compris. Seule l'invocation de la
    # sous-commande éprouve le câblage : argparse rend 2 si elle n'existe pas.
    code, _, err = run_cli(["complete-task", "--help"])
    assert code == 0, err
