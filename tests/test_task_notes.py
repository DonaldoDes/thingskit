"""`thingskit set-notes` / `append-notes` sur une TÂCHE.

Les deux sous-commandes ne savaient écrire que les notes d'un **projet**. Elles
acceptent désormais aussi une tâche, par `--task TITRE` ou `--task-id UUID`.

Surface d'écriture : AppleScript ciblé (`set notes of to do id … to …`), et non
automatisation d'interface — le dictionnaire AppleScript de Things expose
`notes` comme propriété de la classe `to do` sans attribut `access`, donc **rw**
par défaut, au même titre que `name` (constaté le 2026-08-12 par
`sdef /Applications/Things3.app`, pas déduit de la documentation). Aucune frappe
clavier n'est donc en jeu : un saut de ligne dans des notes est un contenu
légitime, pas un risque de validation prématurée comme pour `create-heading`.

Gardes d'état — décidées, pas héritées de `complete-task` :
  - **Corbeille** : REFUS, avant toute sollicitation de l'application. Écrire
    dans un objet que l'utilisateur a jeté produit un contenu invisible dans
    l'app et perdu au premier vidage de corbeille.
  - **terminée** / **annulée** : AUTORISÉ. Annoter n'est pas changer d'état :
    aucune décision de l'utilisateur n'est réécrite, et documenter *après coup*
    (compte-rendu, lien vers le livrable, motif d'abandon) est précisément
    l'usage attendu. C'est la différence de nature avec `complete-task`, qui
    refuse `canceled` parce qu'il *convertirait* cet état.

Ces tests ne touchent jamais l'application ni la vraie base : `db_path` est
redirigée vers une base SQLite jetable, `osa` / `ensure_running` / `time.sleep`
sont mockés.
"""
from __future__ import annotations

import argparse
import re
import sqlite3

import pytest

from test_delete_task import _make_db


OPEN, CANCELED, COMPLETED = 0, 2, 3

TARGET = "AAAAAAAAAAAAAAAAAAAAAA"

# Le contenu réellement visé par le besoin : accents, tiret cadratin, saut de
# ligne, et une URI percent-encodée dont le `%C3%AE` ne doit pas être décodé,
# ré-encodé ni tronqué en route.
RICH = ("Guide de questions de cadrage : obsidian://open?vault=basic-memory"
        "&file=projects/Accompagnement%20IA%20ma%C3%AEtris%C3%A9e%20SoWell"
        "\nDeuxième ligne — avec accents, tiret cadratin et \"guillemets\"")


def _ns_set(project=None, task=None, task_id=None, notes=""):
    return argparse.Namespace(project=project, task=task, task_id=task_id,
                              notes=notes)


def _ns_app(project=None, task=None, task_id=None, line=""):
    return argparse.Namespace(project=project, task=task, task_id=task_id,
                              line=line)


def _unesc(s: str) -> str:
    """Inverse de `_esc` — reconstitue la chaîne source depuis sa forme
    échappée dans le script AppleScript. Sert à simuler ce que l'application
    aurait effectivement stocké."""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _payload_of(script: str) -> tuple[str, str]:
    """Extrait (uuid, notes) du script `set notes of to do id "…" to "…"`."""
    m = re.match(r'^.*set notes of to do id "([^"]+)" to "(.*)"$', script, re.S)
    assert m, f"script inattendu : {script!r}"
    return m.group(1), _unesc(m.group(2))


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    """`osa` inerte qui enregistre ses appels — n'a AUCUN effet en base."""
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


@pytest.fixture
def live(thingskit, monkeypatch, tmp_path):
    """`osa` qui simule FIDÈLEMENT l'application : il applique en base
    exactement la valeur que le script transporte, déséchappée. Le test prouve
    donc aussi que le script porte le bon contenu, pas seulement que la
    commande sort en 0."""
    calls: list[str] = []

    def _setup(rows):
        db_file = _make_db(tmp_path, rows)
        monkeypatch.setattr(thingskit, "db_path", lambda: db_file)

        def _fake(script):
            calls.append(script)
            uuid, notes = _payload_of(script)
            con = sqlite3.connect(db_file)
            con.execute("update TMTask set notes=? where uuid=?", (notes, uuid))
            con.commit()
            con.close()
            return 0, ""

        monkeypatch.setattr(thingskit, "osa", _fake)
        return db_file

    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    return calls, _setup


def _notes(db_file, uuid=TARGET):
    con = sqlite3.connect(db_file)
    try:
        return con.execute("select notes from TMTask where uuid=?",
                           (uuid,)).fetchone()[0]
    finally:
        con.close()


# --- AC1 : adressage par titre et par identifiant, refus sur ambiguïté ------

def test_set_notes_by_id_writes_via_targeted_applescript(thingskit, live):
    calls, setup = live
    db = setup([{"uuid": TARGET, "title": "Cible"}])
    assert thingskit.cmd_set_notes(_ns_set(task_id=TARGET, notes="Bonjour")) == 0
    assert len(calls) == 1
    assert "set notes of to do id" in calls[0]
    # Surface applicative ciblée, pas automatisation d'interface.
    assert "System Events" not in calls[0]
    assert "keystroke" not in calls[0]
    assert _notes(db) == "Bonjour"


def test_set_notes_by_title_writes(thingskit, live):
    calls, setup = live
    db = setup([{"uuid": TARGET, "title": "Cible"}])
    assert thingskit.cmd_set_notes(_ns_set(task="Cible", notes="Bonjour")) == 0
    assert _notes(db) == "Bonjour"


def test_append_notes_by_title_writes(thingskit, live):
    calls, setup = live
    db = setup([{"uuid": TARGET, "title": "Cible", "notes": "Déjà là"}])
    assert thingskit.cmd_append_notes(_ns_app(task="Cible", line="Suite")) == 0
    assert _notes(db) == "Déjà là\nSuite"


def test_task_under_heading_is_reachable_by_title(thingskit, live):
    """Une tâche sous heading a sa colonne `project` VIDE (constat documenté en
    tête de `bin/thingskit`) : la résolution ne doit jamais joindre dessus."""
    calls, setup = live
    db = setup([
        {"uuid": "PPPPPPPPPPPPPPPPPPPPPP", "title": "Le projet", "type": 1},
        {"uuid": "HHHHHHHHHHHHHHHHHHHHHH", "title": "Section", "type": 2,
         "project": "PPPPPPPPPPPPPPPPPPPPPP"},
        {"uuid": TARGET, "title": "Sous heading", "type": 0,
         "project": None, "heading": "HHHHHHHHHHHHHHHHHHHHHH"},
    ])
    assert thingskit.cmd_set_notes(_ns_set(task="Sous heading", notes="X")) == 0
    assert _notes(db) == "X"


@pytest.mark.parametrize("cmd,ns", [
    ("cmd_set_notes", _ns_set(task="Doublon", notes="X")),
    ("cmd_append_notes", _ns_app(task="Doublon", line="X")),
])
def test_ambiguous_title_refuses_without_any_osa_call(thingskit, rigged, cmd, ns):
    calls, set_rows = rigged
    db = set_rows([
        {"uuid": TARGET, "title": "Doublon"},
        {"uuid": "BBBBBBBBBBBBBBBBBBBBBB", "title": "Doublon"},
    ])
    assert getattr(thingskit, cmd)(ns) != 0
    assert calls["osa"] == []
    assert _notes(db) is None


@pytest.mark.parametrize("cmd,ns", [
    ("cmd_set_notes", _ns_set(task="Introuvable", notes="X")),
    ("cmd_append_notes", _ns_app(task="Introuvable", line="X")),
])
def test_unmatched_title_refuses_without_any_osa_call(thingskit, rigged, cmd, ns):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Autre chose"}])
    assert getattr(thingskit, cmd)(ns) != 0
    assert calls["osa"] == []


def test_malformed_task_id_refuses_before_any_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    assert thingskit.cmd_set_notes(_ns_set(task_id="pas un id !!", notes="X")) != 0
    assert calls["osa"] == []


def test_unknown_task_id_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    assert thingskit.cmd_set_notes(
        _ns_set(task_id="BBBBBBBBBBBBBBBBBBBBBB", notes="X")) != 0
    assert calls["osa"] == []


def test_project_is_not_a_task(thingskit, rigged):
    """La résolution est typée : `--task` ne vise jamais un projet, même
    homonyme — sinon `--task` deviendrait un alias silencieux de `--project`."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Chantier", "type": 1}])
    assert thingskit.cmd_set_notes(_ns_set(task="Chantier", notes="X")) != 0
    assert calls["osa"] == []


def test_project_addressing_still_works(thingskit, monkeypatch, tmp_path):
    """Non-régression : la voie `--project` écrit toujours ses notes, par la
    surface `set notes of project`, distincte de celle des tâches."""
    db_file = _make_db(tmp_path, [
        {"uuid": "PPPPPPPPPPPPPPPPPPPPPP", "title": "Chantier", "type": 1}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    calls: list[str] = []

    def _fake(script):
        calls.append(script)
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set notes=? where title=?",
                    ("Notes projet", "Chantier"))
        con.commit()
        con.close()
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _fake)
    assert thingskit.cmd_set_notes(
        _ns_set(project="Chantier", notes="Notes projet")) == 0
    assert calls and "set notes of project" in calls[0]
    assert _notes(db_file, "PPPPPPPPPPPPPPPPPPPPPP") == "Notes projet"


# --- AC2 : append ne perd rien, à l'octet près ------------------------------

def test_append_preserves_existing_notes_byte_for_byte(thingskit, live):
    """Le point le plus important : un append qui écrase est un défaut
    silencieux et irrécupérable. On pose un contenu riche, on appose une ligne,
    et on compare l'INTÉGRALITÉ de l'avant/après — pas seulement la présence de
    la nouvelle ligne."""
    calls, setup = live
    db = setup([{"uuid": TARGET, "title": "Cible"}])

    assert thingskit.cmd_set_notes(_ns_set(task_id=TARGET, notes=RICH)) == 0
    before = _notes(db)
    assert before == RICH

    added = "Ajout — ligne postérieure avec accent î et %C3%AE"
    assert thingskit.cmd_append_notes(_ns_app(task_id=TARGET, line=added)) == 0
    after = _notes(db)

    assert after == before + "\n" + added
    assert after[:len(before)] == before          # préfixe intact, octet à octet
    assert after.encode() == (before + "\n" + added).encode()


def test_append_on_empty_notes_does_not_prefix_a_newline(thingskit, live):
    calls, setup = live
    db = setup([{"uuid": TARGET, "title": "Cible", "notes": None}])
    assert thingskit.cmd_append_notes(_ns_app(task_id=TARGET, line="Seule")) == 0
    assert _notes(db) == "Seule"


def test_append_is_idempotent_when_line_already_present(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "notes": "a\nDéjà\nb"}])
    assert thingskit.cmd_append_notes(_ns_app(task_id=TARGET, line="Déjà")) == 0
    assert calls["osa"] == []


# --- AC3 : gardes d'état ----------------------------------------------------

@pytest.mark.parametrize("cmd,ns", [
    ("cmd_set_notes", _ns_set(task_id=TARGET, notes="X")),
    ("cmd_append_notes", _ns_app(task_id=TARGET, line="X")),
])
def test_trashed_task_is_refused_by_id_without_any_osa_call(
        thingskit, rigged, cmd, ns):
    """Le refus PRÉCÈDE toute sollicitation de l'application : un refus qui
    interviendrait après écriture ne serait pas un refus."""
    calls, set_rows = rigged
    db = set_rows([{"uuid": TARGET, "title": "Jetée", "trashed": 1,
                    "notes": "intact"}])
    assert getattr(thingskit, cmd)(ns) != 0
    assert calls["osa"] == []
    assert _notes(db) == "intact"


def test_trashed_task_is_unreachable_by_title_without_any_osa_call(
        thingskit, rigged):
    """Second chemin d'adressage : la garde ne se contourne pas par `--task`.
    Le résolveur filtre `trashed=0`, donc refuse en « introuvable »."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Jetée", "trashed": 1}])
    assert thingskit.cmd_set_notes(_ns_set(task="Jetée", notes="X")) != 0
    assert calls["osa"] == []


@pytest.mark.parametrize("status,label", [(COMPLETED, "terminée"),
                                          (CANCELED, "annulée")])
def test_completed_and_canceled_tasks_accept_notes(thingskit, live, status, label):
    """Annoter n'est pas changer d'état. Contrairement à `complete-task`, qui
    refuse `canceled` parce qu'il convertirait cette décision, écrire des notes
    ne réécrit rien — documenter après coup est l'usage attendu."""
    calls, setup = live
    db = setup([{"uuid": TARGET, "title": f"Tâche {label}", "status": status}])
    assert thingskit.cmd_append_notes(
        _ns_app(task_id=TARGET, line="compte-rendu")) == 0
    assert _notes(db) == "compte-rendu"


# --- AC4 : fidélité des caractères, transportée jusqu'au script -------------

@pytest.mark.parametrize("payload", [
    "accents éàîôü ÉÀ ç",
    "tiret — cadratin",
    "deux\nlignes",
    "a\tb",
    'guillemet " et \\ antislash',
    "obsidian://open?vault=basic-memory&file=x%20y%C3%AEz",
    "emoji ✅",
    "retour\rchariot",
    "crlf\r\nwindows",
    "s\u00e9pa\u2028rateur",
])
def test_payload_reaches_the_script_unaltered(thingskit, live, payload):
    """La chaîne portée par le script AppleScript, déséchappée, est exactement
    la chaîne source — aucune classe de caractère n'est altérée en route."""
    calls, setup = live
    db = setup([{"uuid": TARGET, "title": "Cible"}])
    assert thingskit.cmd_set_notes(_ns_set(task_id=TARGET, notes=payload)) == 0
    _, transported = _payload_of(calls[0])
    assert transported == payload
    assert _notes(db) == payload


def test_no_character_class_is_refused_upfront(thingskit, live):
    """Aucun refus préalable sur les caractères, et c'est une décision MESURÉE,
    pas déduite : la sonde de bout en bout du 2026-08-12 (tâche jetable réelle)
    a relu en base exactement la chaîne envoyée pour chacune des classes
    ci-dessus, CR et CRLF compris. Refuser une classe qui passe serait un
    sur-refus. Le garde de `create-heading` (`_untypable_chars`) ne s'applique
    pas ici : son risque venait de `keystroke`, pas de `_esc`."""
    calls, setup = live
    db = setup([{"uuid": TARGET, "title": "Cible"}])
    hostile = "a\rb\r\nc\td\x1be"
    assert thingskit.cmd_set_notes(_ns_set(task_id=TARGET, notes=hostile)) == 0
    assert _notes(db) == hostile


# --- vérification post-action (invariant zone sensible n°1) -----------------

def test_failure_when_effect_not_observed(thingskit, rigged):
    """`osa` « réussit » (rc=0) mais la base ne montre rien : code retour non
    nul. `0` signifie « constaté écrit », jamais « commande envoyée »."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    assert thingskit.cmd_set_notes(_ns_set(task_id=TARGET, notes="X")) != 0
    assert len(calls["osa"]) == 1  # l'action a bien été tentée


def test_failure_when_stored_value_differs(thingskit, monkeypatch, tmp_path):
    """La vérification porte sur l'ÉGALITÉ EXACTE, pas sur « les notes ont
    changé » : une valeur tronquée est un échec."""
    db_file = _make_db(tmp_path, [{"uuid": TARGET, "title": "Cible"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))

    def _truncating(script):
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set notes=? where uuid=?", ("Bon", TARGET))
        con.commit()
        con.close()
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _truncating)
    assert thingskit.cmd_set_notes(_ns_set(task_id=TARGET, notes="Bonjour")) != 0


def test_no_sql_write_reaches_the_database(thingskit, monkeypatch, tmp_path):
    """Le CLI ne modifie jamais la base directement : `osa` rendu inerte, le
    fichier doit rester rigoureusement inchangé, octet pour octet."""
    db_file = _make_db(tmp_path, [{"uuid": TARGET, "title": "Cible"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, ""))

    before = db_file.read_bytes()
    thingskit.cmd_set_notes(_ns_set(task_id=TARGET, notes="X"))
    thingskit.cmd_append_notes(_ns_app(task_id=TARGET, line="Y"))
    assert db_file.read_bytes() == before


# Rejet d'usage par argparse : distinct de 0/1 (`main`) et de 125 (garde
# d'identité de code). C'est ce code qui atteste que la règle d'exclusivité a
# été appliquée, et non qu'un échec quelconque est survenu.
ARGPARSE_USAGE_ERROR = 2


# --- surface CLI ------------------------------------------------------------

def test_cli_exposes_task_addressing(thingskit, run_cli):
    for cmd in ("set-notes", "append-notes"):
        code, out, _ = run_cli([cmd, "--help"])
        assert code == 0
        assert "--task" in out
        assert "--task-id" in out
    assert "set-notes --task" in (thingskit.__doc__ or "")


def test_cli_requires_exactly_one_target(run_cli):
    """`--project` et `--task` sont exclusifs, et l'un des trois est requis :
    aucune écriture ne peut partir sans cible explicite.

    L'assertion porte sur le REJET PAR ARGPARSE — code 2 et message nommant la
    règle violée — pas sur un code non nul quelconque : un plantage sans
    rapport avec l'exclusivité des cibles satisfaisait l'ancienne forme."""
    none, out, err = run_cli(["set-notes", "--notes", "X"])
    assert none == ARGPARSE_USAGE_ERROR, err
    assert out == ""
    assert "one of the arguments" in err and "--task" in err

    both, out, err = run_cli(["set-notes", "--project", "P", "--task", "T",
                              "--notes", "X"])
    assert both == ARGPARSE_USAGE_ERROR, err
    assert out == ""
    assert "not allowed with" in err and "--task" in err
