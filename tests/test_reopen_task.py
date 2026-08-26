"""`thingskit reopen-task` — remise en `open` d'une tâche `completed` ou
`canceled`.

Surface d'écriture : AppleScript ciblé (`set status of to do id … to open`),
symétrique de `cmd_complete_task` / `cmd_cancel_task` — même énumération
`status` (rw, `open`/`completed`/`canceled`), aucune automatisation
d'interface.

Différence délibérée avec `complete-task`/`cancel-task` : ces deux commandes
refusent explicitement de convertir l'état opposé, parce que « completed » et
« canceled » sont chacune une décision affirmée sans opération inverse
exposée — c'était vrai jusqu'ici. `reopen-task` EST cette opération inverse,
pour le cas où la décision elle-même était une erreur (fermeture en lot
erronée, par exemple) : elle accepte donc indifféremment `completed` ET
`canceled` comme état de départ, les deux menant au même état cible `open`.
Seule la Corbeille reste refusée, par le même raisonnement que partout
ailleurs dans ce module.

Ces tests ne touchent jamais l'application ni la vraie base : `db_path` est
redirigée vers une base SQLite jetable, `osa` / `ensure_running` / `time.sleep`
sont mockés.
"""
from __future__ import annotations

import argparse
import ast
import re
import sqlite3
from pathlib import Path

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


def _rig_effective_osa(thingskit, monkeypatch, db_file, calls, new_status=OPEN):
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
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Solo", "status": COMPLETED}])
    assert thingskit.cmd_reopen_task(_ns()) != 0
    assert calls["osa"] == []


def test_malformed_uuid_refuses_before_any_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Solo", "status": COMPLETED}])
    assert thingskit.cmd_reopen_task(_ns(id="not a uuid!!")) != 0
    assert calls["osa"] == []


def test_title_no_match_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Autre chose",
               "status": COMPLETED}])
    assert thingskit.cmd_reopen_task(_ns(title="Introuvable")) != 0
    assert calls["osa"] == []


def test_title_ambiguous_refuses_no_reopen(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Doublon", "status": COMPLETED},
        {"uuid": "BBBBBBBBBBBBBBBBBBBBBB", "title": "Doublon", "status": COMPLETED},
    ])
    assert thingskit.cmd_reopen_task(_ns(title="Doublon")) != 0
    assert calls["osa"] == []


def test_unknown_id_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible", "status": COMPLETED}])
    assert thingskit.cmd_reopen_task(_ns(id="BBBBBBBBBBBBBBBBBBBBBB")) != 0
    assert calls["osa"] == []


def test_project_is_not_a_task(thingskit, rigged):
    """Un projet porte le même `title` qu'une tâche pourrait porter : la
    résolution est typée (type=0), un projet n'est jamais rouvert par ici."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Chantier", "type": 1,
               "status": COMPLETED}])
    assert thingskit.cmd_reopen_task(_ns(title="Chantier")) != 0
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
        {"uuid": target, "title": "Sous heading", "type": 0, "status": COMPLETED,
         "project": None, "heading": "HHHHHHHHHHHHHHHHHHHHHH"},
    ])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls)

    assert thingskit.cmd_reopen_task(_ns(title="Sous heading")) == 0
    assert len(calls) == 1
    assert target in calls[0]


# --- chemin nominal, sur les DEUX états de départ ---------------------------

def test_nominal_reopens_completed_task(thingskit, monkeypatch, tmp_path):
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Cible", "status": COMPLETED}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls)

    assert thingskit.cmd_reopen_task(_ns(id=target)) == 0
    assert len(calls) == 1
    script = calls[0]
    assert "set status of to do id" in script
    assert "open" in script
    assert target in script
    # Surface applicative, pas automatisation d'interface.
    assert "System Events" not in script
    assert "keystroke" not in script

    con = sqlite3.connect(db_file)
    st = con.execute("select status from TMTask where uuid=?", (target,)).fetchone()[0]
    con.close()
    assert st == OPEN


def test_nominal_reopens_canceled_task(thingskit, monkeypatch, tmp_path):
    """La différence de raison d'être avec complete-task/cancel-task : ici
    `canceled` n'est PAS refusé, c'est un état de départ nominal au même titre
    que `completed`."""
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Cible", "status": CANCELED}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls)

    assert thingskit.cmd_reopen_task(_ns(id=target)) == 0
    con = sqlite3.connect(db_file)
    st = con.execute("select status from TMTask where uuid=?", (target,)).fetchone()[0]
    con.close()
    assert st == OPEN


# --- idempotence -------------------------------------------------------------

def test_already_open_is_success_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Déjà ouverte",
               "status": OPEN}])
    assert thingskit.cmd_reopen_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) == 0
    assert calls["osa"] == []


# --- tâche à la Corbeille : refus explicite, les deux adressages ------------

def test_trashed_task_is_refused_by_id_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Jetée",
               "trashed": 1, "status": COMPLETED}])
    assert thingskit.cmd_reopen_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) != 0
    assert calls["osa"] == []


def test_trashed_task_is_unreachable_by_title_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Jetée",
               "trashed": 1, "status": COMPLETED}])
    assert thingskit.cmd_reopen_task(_ns(title="Jetée")) != 0
    assert calls["osa"] == []


def test_trashed_homonym_neither_blocks_nor_diverts_the_active_task(
        thingskit, monkeypatch, tmp_path):
    active = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [
        {"uuid": active, "title": "Homonyme", "status": COMPLETED},
        {"uuid": "BBBBBBBBBBBBBBBBBBBBBB", "title": "Homonyme", "trashed": 1,
         "status": COMPLETED},
    ])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls)

    assert thingskit.cmd_reopen_task(_ns(title="Homonyme")) == 0
    assert len(calls) == 1
    assert active in calls[0]
    assert "BBBBBBBBBBBBBBBBBBBBBB" not in calls[0]


# --- vérification post-action ------------------------------------------------

def test_failure_when_effect_not_observed(thingskit, rigged):
    """`osa` « réussit » (rc=0) mais la base ne montre rien : le code retour
    doit être non nul. `0` signifie « constaté fait », jamais « commande
    envoyée »."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible", "status": COMPLETED}])
    assert thingskit.cmd_reopen_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA")) != 0
    assert len(calls["osa"]) == 1  # l'action a bien été tentée


def test_no_sql_write_reaches_the_database(thingskit, monkeypatch, tmp_path):
    """Le CLI ne modifie jamais la base directement. Seul le mock `osa`
    (qui simule l'application) écrit ici ; si on le rend inerte, la base doit
    rester rigoureusement inchangée."""
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    db_file = _make_db(tmp_path, [{"uuid": target, "title": "Cible", "status": COMPLETED}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, ""))

    before = db_file.read_bytes()
    thingskit.cmd_reopen_task(_ns(id=target))
    assert db_file.read_bytes() == before


def test_id_is_escaped_into_the_applescript(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible", "status": COMPLETED}])
    thingskit.cmd_reopen_task(_ns(id="AAAAAAAAAAAAAAAAAAAAAA"))
    assert calls["osa"] and '"AAAAAAAAAAAAAAAAAAAAAA"' in calls["osa"][0]


def test_registered_in_cli_help(thingskit, run_cli):
    """La sous-commande est exposée par l'aide du CLI, et documentée dans le
    bloc Usage du module."""
    code, out, _ = run_cli(["--help"])
    assert code == 0
    assert "reopen-task" in out
    assert "reopen-task" in (thingskit.__doc__ or "")
    # L'aide GÉNÉRALE porte le bloc Usage du module : le nom y figure même si
    # la sous-commande n'est plus câblée au parseur. Mesuré le 2026-08-26 —
    # renommer `add("reopen-task", …)` dans `bin/thingskit` laissait les SIX tests de
    # cette famille au vert, celui-ci compris. Seule l'invocation de la
    # sous-commande éprouve le câblage : argparse rend 2 si elle n'existe pas.
    code, _, err = run_cli(["reopen-task", "--help"])
    assert code == 0, err


# --- adversarial : entrées non prévues / limites ----------------------------
# Zone sensible #1 (constitution.md) : écriture dans la base d'un gestionnaire
# de tâches personnel. Ces tests s'ajoutent aux tests nominaux ci-dessus,
# ils ne les remplacent pas.

def test_adversarial_empty_string_id_refuses(thingskit, rigged):
    """Chaîne vide plutôt que None : ne doit pas passer la garde de forme."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible", "status": COMPLETED}])
    assert thingskit.cmd_reopen_task(_ns(id="")) != 0
    assert calls["osa"] == []


def test_adversarial_sql_injection_in_title_does_not_match_and_does_not_crash(
        thingskit, rigged):
    """Un titre forgé pour ressembler à une injection SQL est un titre comme
    un autre pour la résolution paramétrée — ne doit ni planter, ni matcher
    par effet de bord, ni déclencher d'écriture."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible", "status": COMPLETED}])
    injected = "Cible'; DROP TABLE TMTask; --"
    assert thingskit.cmd_reopen_task(_ns(title=injected)) != 0
    assert calls["osa"] == []


def test_adversarial_applescript_metacharacters_in_title_do_not_break_resolution(
        thingskit, rigged):
    """Un titre contenant des guillemets et un antislash — caractères
    significatifs pour AppleScript — ne doit pas empêcher la résolution ni
    fuiter dans un script non échappé côté résolution par titre (l'écriture
    elle-même n'interpole jamais le titre, seul l'UUID y passe)."""
    calls, set_rows = rigged
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    set_rows([{"uuid": target, "title": 'Cible "spéciale" \\ test',
               "status": COMPLETED}])
    assert thingskit.cmd_reopen_task(_ns(title='Cible "spéciale" \\ test')) != 0
    # aucun osa: la vérification post-action échoue car l'effet n'est pas
    # observé (osa mocké en no-op) — le point testé est l'absence de crash et
    # l'absence de faux succès, pas la réussite.
    assert len(calls["osa"]) == 1


def test_adversarial_uuid_case_sensitivity_does_not_match_a_different_case(
        thingskit, rigged):
    """La résolution par --id est un match exact ; une casse différente ne
    doit pas être traitée comme le même identifiant Things (les UUID Things
    sont sensibles à la casse dans ce schéma)."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible", "status": COMPLETED}])
    assert thingskit.cmd_reopen_task(_ns(id="aaaaaaaaaaaaaaaaaaaaaa")) != 0
    assert calls["osa"] == []


def test_adversarial_reopen_does_not_touch_a_different_row(
        thingskit, monkeypatch, tmp_path):
    """Deux tâches complétées en base : rouvrir l'une ne doit affecter QUE
    celle-ci — pas de mutation en lot par accident de requête mal filtrée."""
    target = "AAAAAAAAAAAAAAAAAAAAAA"
    other = "BBBBBBBBBBBBBBBBBBBBBB"
    db_file = _make_db(tmp_path, [
        {"uuid": target, "title": "Cible", "status": COMPLETED},
        {"uuid": other, "title": "Autre", "status": COMPLETED},
    ])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))

    def _fake(script):
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set status=? where uuid=?", (OPEN, target))
        con.commit()
        con.close()
        return 0, ""
    monkeypatch.setattr(thingskit, "osa", _fake)

    assert thingskit.cmd_reopen_task(_ns(id=target)) == 0
    con = sqlite3.connect(db_file)
    st_other = con.execute("select status from TMTask where uuid=?",
                           (other,)).fetchone()[0]
    con.close()
    assert st_other == COMPLETED


# ---------------------------------------------------------------------------
# Les messages des commandes VOISINES — la classe, pas l'instance signalée
# ---------------------------------------------------------------------------
#
# BUG-032. `complete-task`, `cancel-task` et `reschedule-task` refusent
# chacune un état terminal, et leur message DISAIT à l'utilisateur d'aller le
# défaire dans l'interface graphique — ou qu'aucune opération inverse
# n'existait. `reopen-task` est cette opération : ces trois messages sont
# désormais faux, et un message faux sur un refus est pire qu'un message
# vague, puisqu'il envoie l'utilisateur au mauvais endroit.
#
# Le correctif de `cancel-task` (675b750) n'était couvert par AUCUN test :
# rejoué contre son parent `d650c76`, la suite rendait `36 passed`. Les trois
# messages sont donc gardés ENSEMBLE, par assertion sur la chaîne EXACTE —
# une assertion sur « reopen-task » seul laisserait passer un message qui
# nommerait la commande tout en continuant de renvoyer vers l'application.

_REDIRECTION = "reopen-task"

# (nom, commande, ligne de base, fragment EXACT attendu sur stderr)
_NEIGHBOUR_REFUSALS = [
    (
        "complete-task",
        lambda t, ns: t.cmd_complete_task(ns),
        {"status": CANCELED},
        "Pour rouvrir une tâche fermée par erreur : reopen-task.",
    ),
    (
        "cancel-task",
        lambda t, ns: t.cmd_cancel_task(ns),
        {"status": COMPLETED},
        "Pour rouvrir une tâche fermée par erreur : reopen-task.",
    ),
    (
        "reschedule-task",
        lambda t, ns: t.cmd_reschedule_task(
            argparse.Namespace(id=ns.id, title=None, when="today",
                               deadline=None, clear_deadline=False)),
        {"status": COMPLETED},
        "La rouvrir par reopen-task si elle doit être replanifiée.",
    ),
]


@pytest.mark.parametrize("name,run,row,expected",
                         _NEIGHBOUR_REFUSALS,
                         ids=[c[0] for c in _NEIGHBOUR_REFUSALS])
def test_a_neighbour_refusal_points_at_reopen_task_not_at_the_gui(
        name, run, row, expected, thingskit, rigged, capsys):
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible", **row}])

    rc = run(thingskit, _ns(id="AAAAAAAAAAAAAAAAAAAAAA"))

    assert rc != 0
    err = capsys.readouterr().err
    assert expected in err, f"{name} : message attendu absent — reçu {err!r}"
    assert calls["osa"] == [], f"{name} a sollicité l'application sur un refus"


@pytest.mark.parametrize("name,run,row,expected",
                         _NEIGHBOUR_REFUSALS,
                         ids=[c[0] for c in _NEIGHBOUR_REFUSALS])
def test_a_neighbour_refusal_never_sends_the_user_to_the_application(
        name, run, row, expected, thingskit, rigged, capsys):
    """Le fond, pas la forme : nommer `reopen-task` ne suffit pas si le
    message continue par ailleurs de renvoyer vers l'interface graphique."""
    calls, set_rows = rigged
    set_rows([{"uuid": "AAAAAAAAAAAAAAAAAAAAAA", "title": "Cible", **row}])

    run(thingskit, _ns(id="AAAAAAAAAAAAAAAAAAAAAA"))

    err = capsys.readouterr().err
    for misleading in ("rouvrir la tâche dans things",
                       "la rouvrir dans things",
                       "aucune opération inverse"):
        assert misleading not in err.lower(), (
            f"{name} renvoie encore vers l'application : {misleading!r}")


def test_the_class_of_refusals_is_swept_not_sampled(thingskit):
    """Le balayage, pas l'échantillon : aucun autre site du script ne doit
    renvoyer l'utilisateur vers l'application pour rouvrir une tâche.

    C'est cette assertion-ci qui a trouvé `cmd_reschedule_task`, que la revue
    soupçonnait sans l'avoir retrouvé — deux sites sur trois avaient été
    corrigés, et rien n'aurait signalé le troisième.
    """
    residual = _misleading_literals(_script_source(thingskit))
    assert residual == [], (
        f"sites renvoyant encore vers l'application : {residual}")


_MISLEADING = (
    re.compile(r"rouvrir (la tâche )?dans things", re.IGNORECASE),
    re.compile(r"aucune opération inverse n'existe", re.IGNORECASE),
)


def _script_source(thingskit):
    return Path(thingskit.__file__).read_text(encoding="utf-8")


def _docstring_nodes(tree):
    """Les constantes qui SONT des docstrings — narration, pas message.

    La distinction n'est pas cosmétique : le docstring de `cmd_reopen_task`
    CITE l'ancienne formulation pour dire qu'elle a cessé d'être vraie. Un
    balayage par ligne le confond avec un message d'erreur, et le seul moyen
    de le rendre vert serait d'effacer l'explication.
    """
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            out.add(id(body[0].value))
    return out


def _misleading_literals(source):
    """`(ligne, littéral)` de toute chaîne du script — docstrings exclus —
    qui renvoie encore l'utilisateur vers l'application."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings
                and any(rx.search(node.value) for rx in _MISLEADING)):
            found.append((node.lineno, node.value))
    return sorted(found)


def test_the_sweep_of_refusals_flags_what_it_claims_to_cover():
    """Contre-épreuve : sans elle, un balayage qui ne verrait plus rien
    passerait pour vert. Les deux formes réellement rencontrées y sont, et
    la narration en docstring ne doit PAS y être."""
    control = '''
"""Rouvrir la tâche dans Things si elle doit être terminée."""

def cmd_gui(a):
    print("Rouvrir la tâche dans Things si elle doit être terminée.")

def cmd_none(a):
    """Aucune opération inverse n'existe pour la défaire (narration)."""
    print("Aucune opération inverse n'existe pour la défaire.")

def cmd_clean(a):
    print("Pour rouvrir une tâche fermée par erreur : reopen-task.")
'''
    flagged = [literal for _lineno, literal in _misleading_literals(control)]
    assert len(flagged) == 2, flagged
    assert all("narration" not in f for f in flagged)


# ---------------------------------------------------------------------------
# L'attente est une CONDITION OBSERVÉE, jamais une durée devinée (BUG-032)
# ---------------------------------------------------------------------------
#
# La fixture `rigged` ci-dessus neutralise `time` : aucun de ses tests ne peut
# voir un défaut d'attente, et c'est pourquoi la garde de `test_write_wait.py`
# balaie l'AST. Ce qui suit garde le COMPORTEMENT, avec une horloge VIRTUELLE
# dont `sleep` avance un compteur — aucun test d'ici ne dépend du temps réel.
#
# Cette branche a divergé de master avant BUG-016 : elle portait
# `time.sleep(1.5)`, plafond que la queue mesurée le 2026-08-25 dépasse
# (5026 ms sur une écriture Things RÉUSSIE). Sur cette queue, `reopen-task`
# sortait en échec sur une réouverture pourtant faite — et l'appelant qui
# réessaie duplique dans les données de l'utilisateur.

MEASURED_TAIL = 5.026
OLD_FIXED_WAIT = 1.5
TARGET = "AAAAAAAAAAAAAAAAAAAAAA"


class Clock:
    """Horloge virtuelle : `sleep` avance un compteur, personne n'attend."""

    def __init__(self):
        self.elapsed = 0.0
        self.naps: list[float] = []
        self.on_tick = lambda: None

    def sleep(self, seconds):
        self.naps.append(seconds)
        self.elapsed += seconds
        self.on_tick()


@pytest.fixture
def reopening(thingskit, monkeypatch, tmp_path):
    """Un `reopen-task` dont l'effet atterrit à l'instant VIRTUEL choisi.

    Rend `(run, holder)` : `run(lands_at=<secondes ou None>)` -> code retour.
    """
    db_file = _make_db(tmp_path, [{"uuid": TARGET, "title": "Cible",
                                   "status": COMPLETED}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, ""))
    holder = {"db": db_file}

    def _apply():
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set status=? where uuid=?", (OPEN, TARGET))
        con.commit()
        con.close()

    def run(lands_at):
        clock = Clock()
        state = {"done": False}
        if lands_at is not None:
            if lands_at <= 0:
                _apply()
            else:
                def _tick():
                    if not state["done"] and clock.elapsed >= lands_at:
                        state["done"] = True
                        _apply()
                clock.on_tick = _tick
        holder["clock"] = clock
        monkeypatch.setattr(thingskit, "time", clock)
        return thingskit.cmd_reopen_task(_ns(id=TARGET))

    return run, holder


def test_an_effect_observed_after_the_old_fixed_wait_is_still_a_success(reopening):
    """Le faux négatif de BUG-016, rejoué sur `reopen-task`.

    Une attente FIXE de 1500 ms rend ici un échec sur une réouverture
    réussie : c'est exactement ce que cette assertion condamne.
    """
    run, holder = reopening

    rc = run(lands_at=MEASURED_TAIL)

    assert rc == 0
    assert holder["clock"].elapsed >= OLD_FIXED_WAIT, (
        "l'effet a été constaté avant l'ancien plafond — le cas n'est pas rejoué")


def test_no_wait_is_paid_when_the_effect_is_already_there(
        thingskit, monkeypatch, tmp_path):
    """L'autre moitié du défaut : ~130× trop long dans le cas courant.

    L'effet atterrit PENDANT l'appel `osa`, donc il est déjà là au premier
    sondage. Poser `open` en base AVANT l'appel ne testerait pas ceci : la
    commande sortirait par son idempotence (« déjà ouverte ») sans jamais
    atteindre l'attente, et l'assertion serait verte pour la mauvaise raison
    — mesuré, ce test-là passait encore avec une attente fixe réinstaurée.
    """
    db_file = _make_db(tmp_path, [{"uuid": TARGET, "title": "Cible",
                                   "status": COMPLETED}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    clock = Clock()
    monkeypatch.setattr(thingskit, "time", clock)
    calls = []
    _rig_effective_osa(thingskit, monkeypatch, db_file, calls)

    rc = thingskit.cmd_reopen_task(_ns(id=TARGET))

    assert rc == 0
    assert len(calls) == 1, "l'idempotence a court-circuité l'attente"
    assert clock.naps == [], (
        "une attente a été payée alors que l'effet était déjà là")


def test_an_effect_that_never_lands_still_fails_at_the_cap(reopening, thingskit,
                                                           capsys):
    """Le plafond atteint reste un ÉCHEC, jamais un « commande envoyée »."""
    run, holder = reopening

    rc = run(lands_at=None)

    assert rc != 0
    err = capsys.readouterr().err
    assert "ÉCHEC" in err
    assert "statut constaté en base = completed" in err, (
        "le message ne cite pas la valeur que la sonde a observée")
    assert holder["clock"].elapsed >= thingskit.WRITE_TIMEOUT - 1e-6


def test_waiting_never_writes_a_single_byte_to_the_database(reopening, thingskit):
    """La sonde LIT, elle n'écrit pas — y compris jusqu'au plafond.

    Part d'une base en `completed` (état de départ de la fixture, jamais
    pré-appliqué) : une seule invocation dont l'effet n'atterrit jamais
    (`lands_at=None`), pour que le sondage aille réellement jusqu'au
    plafond au lieu de sortir par le court-circuit d'idempotence
    (`if status == STATUS_OPEN: return 0`). Poser `open` en base avant la
    mesure de référence — comme l'ancienne version le faisait avec
    `run(lands_at=0)` — laissait la ligne déjà ouverte pour la seconde
    invocation, qui ressortait alors par ce court-circuit sans jamais
    atteindre `wait_for_effect` : une sonde mutée pour écrire en base ne
    faisait pas tomber ce test.
    """
    run, holder = reopening
    before = Path(holder["db"]).read_bytes()

    rc = run(lands_at=None)

    assert rc != 0
    assert Path(holder["db"]).read_bytes() == before
    assert holder["clock"].elapsed >= thingskit.WRITE_TIMEOUT - 1e-6, (
        "l'horloge n'a pas couru jusqu'au plafond — le court-circuit "
        "d'idempotence a été atteint au lieu de l'attente")
