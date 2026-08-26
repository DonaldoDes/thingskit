"""`thingskit create-area` — la seule sous-commande d'écriture restée sans test.

C'est ce vide qui a laissé passer BUG-033 : `a.name` était interpolé brut dans
le littéral AppleScript remis à `osascript`, quand les 17 autres sites
appliquaient `_esc`. Une relecture ne distingue pas 17 de 18 ; un test le fait.

Ces tests ne touchent jamais l'application ni la vraie base : `db_path` est
redirigée vers une base SQLite jetable, `osa` et `ensure_running` sont mockés.
La couverture est alignée sur celle des autres commandes d'écriture — surface
appelée, idempotence, échec quand l'effet n'est pas constaté en base, et
adversité sur l'échappement (§ Zones sensibles n° 1 et n° 2).
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
    status INTEGER,
    notes TEXT
);
"""

AREA = "RRRRRRRRRRRRRRRRRRRRRR"
AREA2 = "SSSSSSSSSSSSSSSSSSSSSS"


def _make_db(tmp_path, area_rows=()):
    db_file = tmp_path / "main.sqlite"
    con = sqlite3.connect(db_file)
    con.executescript(SCHEMA)
    for uuid, title in area_rows:
        con.execute("insert into TMArea (uuid, title) values (?, ?)", (uuid, title))
    con.commit()
    con.close()
    return db_file


def _ns(name):
    return argparse.Namespace(name=name)


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    """`osa` inerte qui enregistre les scripts reçus — aucun effet en base."""
    calls = {"osa": [], "db": None, "ensure_running": 0}

    def _set_rows(area_rows=()):
        db_file = _make_db(tmp_path, area_rows)
        monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
        calls["db"] = db_file
        return db_file

    def _count_ensure():
        calls["ensure_running"] += 1

    monkeypatch.setattr(thingskit, "ensure_running", _count_ensure)
    monkeypatch.setattr(thingskit, "osa",
                        lambda script: (calls["osa"].append(script), (0, ""))[1])
    # Horloge virtuelle : `wait_for_effect` décompte son plafond par la durée
    # DEMANDÉE, pas par l'horloge murale. Neutraliser `sleep` laisse donc les
    # 600 sondages se dérouler à l'identique, sans coûter 15 s au test — même
    # motif que `tests/test_write_wait.py`.
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    return calls, _set_rows


def _rig_effective_creation(thingskit, monkeypatch, calls, uuid=AREA):
    """`osa` qui simule l'effet réel : l'area apparaît dans `TMArea`."""
    db_file = calls["db"]

    def _fake(script):
        calls["osa"].append(script)
        name = script.split('name:"', 1)[1].rsplit('"}', 1)[0]
        con = sqlite3.connect(db_file)
        con.execute("insert into TMArea (uuid, title) values (?, ?)",
                    (uuid, name.replace('\\"', '"').replace("\\\\", "\\")))
        con.commit()
        con.close()
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _fake)


# --- nominal ---------------------------------------------------------------

def test_creating_an_area_calls_the_applescript_surface_and_returns_zero(
        thingskit, monkeypatch, rigged, capsys):
    calls, set_rows = rigged
    set_rows()
    _rig_effective_creation(thingskit, monkeypatch, calls)

    assert thingskit.cmd_create_area(_ns("Cible")) == 0
    assert len(calls["osa"]) == 1
    assert 'make new area with properties {name:"Cible"}' in calls["osa"][0]
    # Le nom est RENDU, jamais recopié brut (BUG-026) : la confirmation le
    # cite, comme `add-task` le fait de son titre depuis BUG-005.
    assert "area créée : 'Cible'" in capsys.readouterr().out


def test_the_application_is_started_before_the_write(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows()
    _rig_effective_creation(thingskit, monkeypatch, calls)

    thingskit.cmd_create_area(_ns("Cible"))
    assert calls["ensure_running"] == 1


def test_an_existing_area_is_idempotent_and_never_reaches_the_application(
        thingskit, rigged, capsys):
    calls, set_rows = rigged
    set_rows([(AREA, "Déjà là")])

    assert thingskit.cmd_create_area(_ns("Déjà là")) == 0
    assert calls["osa"] == []
    assert calls["ensure_running"] == 0
    assert "existe déjà" in capsys.readouterr().out


def test_failure_when_effect_not_observed(thingskit, rigged, capsys):
    """§ Zones sensibles n° 1 : `0` = effet constaté en base, jamais
    « commande envoyée ». `osa` réussit ici, la base ne bouge pas."""
    calls, set_rows = rigged
    set_rows()

    assert thingskit.cmd_create_area(_ns("Cible")) != 0
    assert len(calls["osa"]) == 1
    assert "ÉCHEC" in capsys.readouterr().err


def test_the_created_area_is_read_back_from_the_database_not_from_the_return(
        thingskit, monkeypatch, rigged):
    """La relecture porte sur le NOM demandé : une area d'un autre nom apparue
    entre-temps ne vaut pas constat."""
    calls, set_rows = rigged
    set_rows()

    def _fake(script):
        calls["osa"].append(script)
        con = sqlite3.connect(calls["db"])
        con.execute("insert into TMArea (uuid, title) values (?, ?)",
                    (AREA2, "Autre chose"))
        con.commit()
        con.close()
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _fake)
    assert thingskit.cmd_create_area(_ns("Cible")) != 0


# --- adversité : l'échappement (BUG-033) -----------------------------------

@pytest.mark.parametrize("name", [
    'foo" & (do shell script "touch /tmp/pwned") & "',
    'a"b',
    "back\\slash",
    'guillemet final "',
    'saut\nde ligne',
    'retour\rchariot',
    'inversion\u202electure',
    '',
])
def test_a_hostile_name_never_escapes_the_script_string_literal(
        thingskit, monkeypatch, rigged, name):
    """Le corps ne doit contenir aucun caractère qui SORTE du littéral : ni
    guillemet nu, ni saut de ligne nu. Sinon le reste du nom devient du code.

    Le docstring disait « aucun saut de ligne nu » là où l'assertion ne testait
    que le LF. La classe des terminateurs d'instruction AppleScript en compte
    deux, LF et CR — et ce test a longtemps couvert le premier seulement.
    La différence entre les deux est MESURÉE, pas supposée (2026-08-26,
    osascript sur ce poste) :

        osascript -e $'return "a\rdo shell script \\"echo PWNED\\""'
          -> rend la chaîne ENTIÈRE, rc=0 : le CR ne referme rien
        osascript -e $'return "a\rb"' | xxd
          -> 610d 620a : le CR est transporté OCTET POUR OCTET

    Le CR est donc bien dans la paramétrisation — c'est une classe hostile — et
    l'assertion NE lui demande PAS d'être échappé : `_esc` ne l'échappe pas, et
    la constitution (§ Ce que le projet garantit, `_esc`) tranche déjà que
    refuser une classe qui passe est un sur-refus, aussi fautif que laisser
    passer une classe qui casse.

    Le nom VIDE et U+202E sont là au même titre : ni l'un ni l'autre ne sort du
    littéral, et aucun n'est refusé. Les inscrire ici fixe ce constat par test
    plutôt que de le laisser à la relecture.

    L'épreuve porte sur le script REMIS à `osascript`, pas sur un message.
    """
    calls, set_rows = rigged
    set_rows()
    _rig_effective_creation(thingskit, monkeypatch, calls)

    thingskit.cmd_create_area(_ns(name))
    script = calls["osa"][0]
    body = script.split('name:"', 1)[1].rsplit('"}', 1)[0]
    # Tout guillemet du corps est précédé de son antislash d'échappement.
    for index, char in enumerate(body):
        if char == '"':
            assert index and body[index - 1] == "\\", script
    assert "\n" not in body, script
    # Le littéral se referme exactement une fois, à la fin.
    assert script.endswith('"}')
    assert script.count("tell application") == 1, script


@pytest.mark.parametrize("name", [
    'retour\rchariot',
    'inversion\u202electure',
    'accents éàü et tiret — long',
])
def test_a_name_carrying_a_transported_class_is_created_verbatim(
        thingskit, monkeypatch, rigged, name):
    """La contrepartie du non-refus : ce qui n'est pas échappé doit arriver
    INTACT. Une garde qui laisserait passer CR ou U+202E en les MUTILANT serait
    aussi fautive qu'un sur-refus — l'utilisateur obtiendrait une area d'un
    autre nom que celui qu'il a demandé, avec un code retour 0.
    """
    calls, set_rows = rigged
    set_rows()
    _rig_effective_creation(thingskit, monkeypatch, calls)

    assert thingskit.cmd_create_area(_ns(name)) == 0
    con = sqlite3.connect(calls["db"])
    titles = [r[0] for r in con.execute("select title from TMArea")]
    con.close()
    assert titles == [name]


def test_registered_in_cli_help(thingskit, run_cli):
    """La sous-commande est exposée par l'aide du CLI, et documentée dans le
    bloc Usage du module.

    `create-area` était la seule commande d'écriture sans ce contrôle, alors
    que `complete-task`, `cancel-task`, `rename-task`, `reschedule-task` et
    `reopen-task` l'ont tous. Le câblage existe (`bin/thingskit`, `add(
    "create-area", cmd_create_area)`) — rien ne le gardait.
    """
    code, out, _ = run_cli(["--help"])
    assert code == 0
    assert "create-area" in out
    assert "create-area" in (thingskit.__doc__ or "")
    # L'aide GÉNÉRALE porte le bloc Usage du module : le nom y figure même si
    # la sous-commande n'est plus câblée au parseur. Mesuré le 2026-08-26 —
    # renommer `add("create-area", …)` dans `bin/thingskit` laissait les SIX tests de
    # cette famille au vert, celui-ci compris. Seule l'invocation de la
    # sous-commande éprouve le câblage : argparse rend 2 si elle n'existe pas.
    code, _, err = run_cli(["create-area", "--help"])
    assert code == 0, err


def test_a_name_carrying_a_quote_is_still_created_verbatim(
        thingskit, monkeypatch, rigged, capsys):
    """L'échappement ne doit pas mutiler la donnée : le nom reste celui demandé.

    Sans cela, une garde qui filtrerait les guillemets passerait ce module tout
    en créant une area d'un autre nom que celui de l'utilisateur.
    """
    calls, set_rows = rigged
    set_rows()
    _rig_effective_creation(thingskit, monkeypatch, calls)

    assert thingskit.cmd_create_area(_ns('L\'"important"')) == 0
    con = sqlite3.connect(calls["db"])
    titles = [r[0] for r in con.execute("select title from TMArea")]
    con.close()
    assert titles == ['L\'"important"']


def test_the_injected_payload_is_not_a_separate_applescript_statement(
        thingskit, monkeypatch, rigged):
    """Reproduction de BUG-033 : le script ne doit contenir qu'UN `tell`, et
    aucun `do shell script` issu du nom."""
    calls, set_rows = rigged
    set_rows()
    _rig_effective_creation(thingskit, monkeypatch, calls)

    payload = 'x" \ndo shell script "touch /tmp/pwned'
    thingskit.cmd_create_area(_ns(payload))
    script = calls["osa"][0]
    assert script.count("tell application") == 1
    assert 'do shell script "touch' not in script.replace('\\"', "")
