"""`thingskit create-heading` — ajout d'un en-tête à un projet existant.

Aucune surface API (schéma d'URL, commande `json`, AppleScript) ne permet
d'ajouter un heading à un projet DÉJÀ créé — constaté sur pièce (cf. docstring
module). Seule l'automatisation d'interface (menu Fichier > Nouvel en-tête)
le permet. Ces tests-ci ne pilotent jamais réellement l'interface : `osa` est
mocké pour capturer/simuler son résultat, `db_path` redirigée vers une base
SQLite jetable. Le pilotage réel de l'UI (clic menu + frappe clavier) n'est
PAS testable automatiquement — voir constitution.md § Zones sensibles.
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

from conftest import InertResult, inert_run
import pytest



SCRIPT_SOURCE = Path(__file__).resolve().parent.parent / "bin" / "thingskit"


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
    status INTEGER
);
"""


def _make_db(tmp_path, rows, area_rows=()):
    db_file = tmp_path / "main.sqlite"
    con = sqlite3.connect(db_file)
    con.executescript(SCHEMA)
    defaults = dict(
        uuid=None, title=None, type=0, trashed=0, project=None, heading=None,
        area=None, startDate=None, startBucket=None, deadline=None,
        reminderTime=None, status=0,
    )
    for r in rows:
        row = {**defaults, **r}
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,project,heading,area,"
            "startDate,startBucket,deadline,reminderTime,status) values "
            "(:uuid,:title,:type,:trashed,:project,:heading,:area,"
            ":startDate,:startBucket,:deadline,:reminderTime,:status)",
            row,
        )
    for uuid, title in area_rows:
        con.execute("insert into TMArea (uuid, title) values (?, ?)", (uuid, title))
    con.commit()
    con.close()
    return db_file


def _ns(title="Nouveau Heading", project="Mon Projet"):
    return argparse.Namespace(title=title, project=project)


def _is_view_probe(script: str) -> bool:
    """La sonde d'AFFICHAGE se distingue du script d'automatisation : elle ne
    clique ni ne tape rien, elle lit le nom de la liste affichée.

    Les deux passent par `osa()`, donc un mock qui ne les distingue pas
    répondrait au hasard sur l'un des deux — et un test « vert » cesserait de
    couvrir ce qu'il annonce.
    """
    return "keystroke" not in script


def _osa_answering_the_view_probe(on_ui):
    """`osa` de test : la vue affiche bien le projet, l'UI répond `on_ui`."""
    def _fake(script):
        if _is_view_probe(script):
            return 0, "OK"
        return on_ui(script)
    return _fake


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    """Redirige db_path + neutralise ensure_running/open ; osa() reste au choix du test.

    `calls["osa"]` ne recense que les scripts d'AUTOMATISATION D'INTERFACE —
    ceux qui cliquent et tapent. Les sondes d'affichage vont dans
    `calls["probe"]` : c'est ce qui permet aux assertions « l'application n'a
    pas été sollicitée » de continuer à dire exactement ce qu'elles disaient.
    """
    calls = {"osa": [], "open": [], "probe": []}

    def _set_rows(rows, area_rows=()):
        db_file = _make_db(tmp_path, rows, area_rows)
        monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
        return db_file

    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(
        thingskit.subprocess, "run",
        lambda args, **kw: (calls["open"].append(args), InertResult())[1],
    )
    monkeypatch.setattr(thingskit, "time",
                         type("T", (), {"sleep": staticmethod(lambda s: None)}))

    def _fake_osa_noop(script):
        if _is_view_probe(script):
            calls["probe"].append(script)
            return 0, "OK"
        calls["osa"].append(script)
        return 0, "OK"

    monkeypatch.setattr(thingskit, "osa", _fake_osa_noop)

    return calls, _set_rows


# ---------------------------------------------------------------------------
# Résolution projet / refus sur ambiguïté (AC-5)
# ---------------------------------------------------------------------------

def test_project_not_found_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([])
    rc = thingskit.cmd_create_heading(_ns(project="Introuvable"))
    assert rc != 0
    assert calls["osa"] == []


def test_project_ambiguous_refuses_no_ui_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": "P1", "title": "Doublon", "type": 1},
        {"uuid": "P2", "title": "Doublon", "type": 1},
    ])
    rc = thingskit.cmd_create_heading(_ns(project="Doublon"))
    assert rc != 0
    assert calls["osa"] == []


# ---------------------------------------------------------------------------
# Idempotence (AC-4)
# ---------------------------------------------------------------------------

def test_heading_already_exists_is_idempotent_no_ui_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([
        {"uuid": "P1", "title": "Projet de démonstration", "type": 1},
        {"uuid": "H1", "title": "Pilote assistant interne", "type": 2, "project": "P1"},
    ])
    rc = thingskit.cmd_create_heading(
        _ns(title="Pilote assistant interne",
            project="Projet de démonstration"))
    assert rc == 0
    assert calls["osa"] == []


def test_heading_existing_in_other_project_is_created_in_target(
        thingskit, monkeypatch, tmp_path):
    """Un heading homonyme dans UN AUTRE projet ne doit ni bloquer (faux
    positif d'idempotence) ni détourner la création : le heading est créé
    dans le projet cible, et l'homonyme de l'autre projet reste unique.
    """
    db_file = _make_db(tmp_path, [
        {"uuid": "P1", "title": "Projet A", "type": 1},
        {"uuid": "P2", "title": "Projet B", "type": 1},
        {"uuid": "H1", "title": "Section", "type": 2, "project": "P2"},
    ])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    opened = []
    monkeypatch.setattr(thingskit.subprocess, "run",
                         lambda args, **kw: (opened.append(args),
                                            InertResult())[1])
    monkeypatch.setattr(thingskit, "time",
                         type("T", (), {"sleep": staticmethod(lambda s: None)}))

    def _ui(script):
        con = sqlite3.connect(db_file)
        con.execute("insert into TMTask (uuid,title,type,trashed,project) "
                    "values ('H9','Section',2,0,'P1')")
        con.commit()
        con.close()
        return 0, "OK"

    monkeypatch.setattr(thingskit, "osa", _osa_answering_the_view_probe(_ui))

    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))
    assert rc == 0
    # L'UI a bien été pilotée sur le BON projet (deeplink vers P1, pas P2).
    assert any("things:///show?id=P1" in str(args) for args in opened)
    con = sqlite3.connect(db_file)
    rows = con.execute("select project from TMTask where type=2 and "
                        "title='Section' order by project").fetchall()
    con.close()
    assert rows == [("P1",), ("P2",)]


# ---------------------------------------------------------------------------
# Vérification post-action conditionne le code retour (AC-6)
# ---------------------------------------------------------------------------

def test_ui_succeeds_but_heading_not_observed_fails(thingskit, monkeypatch, tmp_path):
    db_file = _make_db(tmp_path, [{"uuid": "P1", "title": "Projet A", "type": 1}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit.subprocess, "run", inert_run)
    monkeypatch.setattr(thingskit, "time",
                         type("T", (), {"sleep": staticmethod(lambda s: None)}))
    # osa "réussit" (rc=0, "OK") mais ne modifie rien en base : commande envoyée
    # sans effet constaté. Le code retour doit signifier "constaté", pas "envoyé".
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, "OK"))

    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))
    assert rc != 0


def test_nominal_path_creates_and_verifies(thingskit, monkeypatch, tmp_path):
    db_file = _make_db(tmp_path, [{"uuid": "P1", "title": "Projet A", "type": 1}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit.subprocess, "run", inert_run)
    monkeypatch.setattr(thingskit, "time",
                         type("T", (), {"sleep": staticmethod(lambda s: None)}))

    def _ui(script):
        con = sqlite3.connect(db_file)
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,project) "
            "values ('H9','Section',2,0,'P1')")
        con.commit()
        con.close()
        return 0, "OK"

    monkeypatch.setattr(thingskit, "osa", _osa_answering_the_view_probe(_ui))

    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))
    assert rc == 0


def test_ui_reports_accessibility_denied_fails_explicitly(thingskit, monkeypatch, tmp_path,
                                                          capsys):
    db_file = _make_db(tmp_path, [{"uuid": "P1", "title": "Projet A", "type": 1}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit.subprocess, "run", inert_run)
    monkeypatch.setattr(thingskit, "time",
                         type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(
        thingskit, "osa",
        _osa_answering_the_view_probe(
            lambda script: (1, "System Events got an error: Things3 is not "
                               "allowed assistive access.")))

    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))
    assert rc != 0
    # `rc != 0` seul passait AUSSI sur un échec générique : le test ne
    # couvrait pas ce que son nom promet — que le refus de permission soit
    # DIAGNOSTIQUÉ, pas seulement subi.
    err = capsys.readouterr().err
    assert "Accessibilité" in err, err
    assert "Réglages Système" in err, (
        "le message n'est plus actionnable : il nomme la cause sans dire quoi "
        f"faire — {err}")


# ---------------------------------------------------------------------------
# Fonctions pures : sélection de libellé de menu / interprétation du résultat
# ---------------------------------------------------------------------------

def test_build_heading_script_embeds_all_known_labels(thingskit):
    script = thingskit._build_heading_script("Ma Section", "Projet A")
    for label in thingskit.HEADING_MENU_LABELS:
        assert label in script


def test_build_heading_script_escapes_title(thingskit):
    script = thingskit._build_heading_script('Titre "cité"', "Projet A")
    assert '\\"cité\\"' in script


def test_interpret_ui_outcome_success(thingskit):
    ok, msg = thingskit._interpret_ui_outcome(0, "OK")
    assert ok is True


def test_interpret_ui_outcome_no_label_found(thingskit):
    ok, msg = thingskit._interpret_ui_outcome(
        1, f"error: {thingskit._NO_LABEL_MARKER}")
    assert ok is False
    assert "libellé" in msg


def test_interpret_ui_outcome_accessibility_denied(thingskit):
    ok, msg = thingskit._interpret_ui_outcome(
        1, "Not authorized to send Apple events.")
    assert ok is False
    assert "Accessibilité" in msg


def test_interpret_ui_outcome_wrong_view(thingskit):
    """Le refus AVANT le clic se lit comme tel, pas comme un échec mystère."""
    ok, msg = thingskit._interpret_ui_outcome(
        1, f"execution error: {thingskit._WRONG_VIEW_MARKER}: Aujourd’hui (-2700)")
    assert ok is False
    assert "rien n'a été cliqué ni tapé" in msg
    assert "Aujourd’hui" in msg


def test_the_displayed_project_is_refused_before_any_keystroke(
        thingskit, monkeypatch, tmp_path, capsys):
    """Zone sensible 2 : le titre ne doit jamais partir dans un autre projet.

    La sonde ne voit jamais le projet visé → aucun script d'automatisation
    n'est envoyé, donc aucun clic et aucune frappe.
    """
    db_file = _make_db(tmp_path, [{"uuid": "P1", "title": "Projet A", "type": 1}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit.subprocess, "run", inert_run)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    sent = []

    def _fake(script):
        sent.append(script)
        return 1, f"execution error: {thingskit._WRONG_VIEW_MARKER}: Projet B (-2700)"

    monkeypatch.setattr(thingskit, "osa", _fake)

    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))

    assert rc != 0
    assert [s for s in sent if not _is_view_probe(s)] == []
    assert "Projet B" in capsys.readouterr().err


def test_interpret_ui_outcome_unknown_failure(thingskit):
    ok, msg = thingskit._interpret_ui_outcome(1, "erreur mystère 42")
    assert ok is False
    assert "erreur mystère 42" in msg


# ---------------------------------------------------------------------------
# Projet à la Corbeille : la résolution filtre trashed=0 — jamais attesté
# ---------------------------------------------------------------------------

def test_trashed_project_refuses_no_ui_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": "P1", "title": "Projet A", "type": 1, "trashed": 1}])
    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))
    assert rc != 0
    assert calls["osa"] == []
    assert calls["open"] == []


# ---------------------------------------------------------------------------
# Classe de caractères refusée dans un titre : refus AVANT toute activation
#
# **Le motif d'origine est RÉFUTÉ.** Il était : « `keystroke` les tape, et dans
# le champ de saisie un retour à la ligne vaut validation — en-tête tronqué,
# frappe orpheline ». Cela valait de la frappe. Depuis le collage, c'est faux :
# mesuré le 2026-08-27 sur la vraie base, un titre "AVANT-é\nAPRES-ù\tTAB\x1bESC"
# collé puis validé arrive OCTET POUR OCTET, un seul en-tête, rien de tronqué.
#
# Ce qui reste est une DÉCISION : le CLI ne fabrique pas d'objet dont le nom
# porte la classe qu'il refuse d'émettre brute sur sa propre sortie. La classe
# est donc `_REFUSED_CATEGORIES`, la même, et non une seconde liste — elle
# valait `("Cc","Zl","Zp")` jusqu'au 2026-08-27, ce qui était un SOUS-refus :
# U+202E (inversion de sens de lecture) passait dans un titre alors qu'il est
# refusé en sortie.
# ---------------------------------------------------------------------------

REFUSED_IN_TITLE = [
    "\n", "\r", "\t", "\x0b", "\x0c", "\x1b", "\x7f",   # Cc
    "\u2028", "\u2029",                                  # Zl / Zp
    "\u202e", "\u200b",                                  # Cf — ajoutés à l'alignement
]


@pytest.mark.parametrize("ch", REFUSED_IN_TITLE)
def test_refused_title_class_is_rejected_before_any_activation(thingskit, rigged, ch):
    calls, set_rows = rigged
    set_rows([{"uuid": "P1", "title": "Projet A", "type": 1}])
    rc = thingskit.cmd_create_heading(
        _ns(title=f"Avant{ch}Après", project="Projet A"))
    assert rc != 0
    # Ni frappe clavier, ni activation/deeplink : rien n'atteint l'application.
    assert calls["osa"] == []
    assert calls["open"] == []


def test_the_title_class_is_the_one_refused_on_output_not_a_second_list(thingskit):
    """Une seconde liste dérive de la première. Le principe écrit dans le code
    dit « la classe qu'il refuse d'émettre brute » : il doit s'agir de CELLE-LÀ.
    """
    for categorie in thingskit._REFUSED_CATEGORIES:
        assert categorie in {"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"}
    # U+202E : le cas qui rendait la phrase fausse avant l'alignement.
    assert thingskit._refused_title_chars("a\u202eb") == ["U+202E"]
    assert thingskit._refused_title_chars("a\u200bb") == ["U+200B"]


def test_the_refusal_message_does_not_carry_the_refuted_motive(
        thingskit, rigged, capsys):
    """La seule surface que l'utilisateur LIT. Elle était restée sur « ils
    seraient tapés dans le champ de saisie et y vaudraient validation (heading
    tronqué, frappe orpheline) » alors que plus rien n'est tapé — et aucun test
    ne l'assied, contrairement à tous les autres messages de cette commande.
    """
    calls, set_rows = rigged
    set_rows([{"uuid": "P1", "title": "Projet A", "type": 1}])
    thingskit.cmd_create_heading(_ns(title="Avant\u202eAprès", project="Projet A"))
    err = capsys.readouterr().err

    for mort in ("saisissables au clavier", "seraient tapés", "tronqué",
                 "frappe orpheline", "keystroke"):
        assert mort not in err, err
    assert "U+202E" in err
    assert "n'a pas été sollicitée" in err


@pytest.mark.parametrize("title", [
    'Titre "cité"', "Chemin C:\\dossier", "Éléments accentués — tiret cadratin",
    "emoji 🙂", "espaces    multiples", "espace insécable\u00a0!",
])
def test_typable_titles_are_accepted(thingskit, rigged, title):
    """Contre-épreuve du sur-refus. L'espace insécable (U+00A0, catégorie Zs)
    est ajouté à l'alignement de classe : c'était le seul caractère que
    l'ancienne borne `str.isprintable()` citait sur la base réelle — 2 titres
    sur 902, 100 % de faux positifs (§ Zones sensibles 1). La classe alignée
    ne le refuse pas, et ce test l'épingle dans ce sens-là.
    """
    calls, set_rows = rigged
    set_rows([{"uuid": "P1", "title": "Projet A", "type": 1}])
    thingskit.cmd_create_heading(_ns(title=title, project="Projet A"))
    assert len(calls["osa"]) == 1  # le garde ne doit pas sur-refuser


def test_refused_title_chars_lists_offenders(thingskit):
    assert thingskit._refused_title_chars("propre") == []
    assert thingskit._refused_title_chars("a\nb\tc") == ["U+000A", "U+0009"]


# ---------------------------------------------------------------------------
# Homonymie projet / area — le nom de fenêtre ne les distingue pas
#
# La garde d'affichage compare `name of window 1` au TITRE du projet. Une area
# portant le même titre produit exactement le même nom de fenêtre : la garde
# est satisfaite par la mauvaise vue. Ce n'est pas théorique — mesuré le
# 2026-08-25 sur la base réelle, « Conventions du vault » est à la fois un
# projet et une area, sur 59 projets et 20 areas.
# ---------------------------------------------------------------------------

def test_resolve_refuses_a_project_sharing_its_title_with_an_area(thingskit, rigged):
    """Même règle que `_resolve_find_target` : une cible ambiguë est une
    erreur, jamais un signal dégradé."""
    calls, set_rows = rigged
    set_rows([{"uuid": "P1", "title": "Conventions du vault", "type": 1}],
             [("A1", "Conventions du vault")])

    uuid, err = thingskit._resolve_project_for_heading("Conventions du vault")

    assert uuid is None
    assert err is not None and "AMBIGU" in err


def test_an_area_named_otherwise_does_not_block_the_project(thingskit, rigged):
    """Contre-épreuve du sur-refus : le refus porte sur l'HOMONYMIE, pas sur
    l'existence d'areas."""
    calls, set_rows = rigged
    set_rows([{"uuid": "P1", "title": "Projet A", "type": 1}],
             [("A1", "Une autre area"), ("A2", "Encore une autre")])

    assert thingskit._resolve_project_for_heading("Projet A") == ("P1", None)


def test_area_homonym_refuses_before_any_solicitation(thingskit, rigged, capsys):
    """Le refus tombe AVANT `open`, avant la sonde et avant l'automatisation :
    rien n'est ouvert, rien n'est cliqué, rien n'est tapé."""
    calls, set_rows = rigged
    set_rows([{"uuid": "P1", "title": "Conventions du vault", "type": 1}],
             [("A1", "Conventions du vault")])

    rc = thingskit.cmd_create_heading(
        _ns(title="Section", project="Conventions du vault"))

    assert rc != 0
    assert calls["open"] == [], "l'application a été sollicitée malgré le refus"
    assert calls["probe"] == []
    assert calls["osa"] == []
    err = capsys.readouterr().err
    assert "area" in err.lower()
    assert "fenêtre" in err, "le motif du refus n'est pas nommé"


# ---------------------------------------------------------------------------
# La comparaison d'affichage, EXÉCUTÉE — sonde contrôlée, pas relecture
#
# La lecture de la fenêtre est remplacée par une valeur choisie : ce qui est
# exécuté est la comparaison RÉELLE produite par `_shown_list_comparison`,
# sans dépendre de Things ni d'aucune fenêtre.
# ---------------------------------------------------------------------------

_READ_LINE = '  tell application "Things3" to set shownList to name of window 1\n'

requires_osascript = pytest.mark.skipif(
    shutil.which("osascript") is None, reason="osascript absent (hors macOS)")


def _run_comparison(thingskit, project_title, shown):
    script = thingskit._shown_list_comparison(project_title)
    assert _READ_LINE in script, script
    script = script.replace(
        _READ_LINE, f'  set shownList to "{thingskit._esc(shown)}"\n', 1)
    proc = subprocess.run([shutil.which("osascript"), "-e", script + 'return "OK"\n'],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


@requires_osascript
def test_the_comparison_accepts_the_exact_title(thingskit):
    rc, out = _run_comparison(thingskit, "Projet A", "Projet A")
    assert rc == 0, out


@requires_osascript
def test_the_comparison_refuses_a_case_only_difference(thingskit):
    """`considering case` est le morceau porteur : sans lui la comparaison par
    défaut rend vrai (mesuré : `"AUJOURDHUI" is "aujourdhui"` -> true)."""
    rc, out = _run_comparison(thingskit, "Aujourdhui", "AUJOURDHUI")
    assert rc != 0
    assert thingskit._WRONG_VIEW_MARKER in out


@requires_osascript
def test_the_comparison_refuses_an_accent_only_difference(thingskit):
    rc, out = _run_comparison(thingskit, "Été", "Ete")
    assert rc != 0
    assert thingskit._WRONG_VIEW_MARKER in out


@requires_osascript
def test_zero_window_is_refused_for_an_ordinary_title(thingskit):
    rc, out = _run_comparison(thingskit, "Projet A", "")
    assert rc != 0
    assert thingskit._NO_WINDOW_MARKER in out


@requires_osascript
def test_zero_window_is_refused_even_when_the_project_title_is_empty(thingskit):
    """Le trou que la comparaison seule laissait ouvert : `"" is not ""` rend
    faux, donc zéro fenêtre + titre vide FRANCHISSAIT la garde. Non atteignable
    aujourd'hui (aucun projet à titre vide), mais « fail-closed par
    construction » ne peut pas dépendre du contenu de la base."""
    rc, out = _run_comparison(thingskit, "", "")
    assert rc != 0, "zéro fenêtre + titre vide a franchi la garde"
    assert thingskit._NO_WINDOW_MARKER in out


# ---------------------------------------------------------------------------
# Premier plan — condition observée, plus une durée devinée
#
# La garde d'affichage établit que la FENÊTRE 1 DE THINGS montre le projet ;
# elle n'établit jamais que Things est l'application au premier plan du
# SYSTÈME. Or `keystroke` de System Events frappe l'app frontmost du système.
# ---------------------------------------------------------------------------

def test_the_script_observes_the_foreground_instead_of_guessing_a_delay(thingskit):
    script = thingskit._build_heading_script("Section", "Projet A")

    assert "delay 0.3" not in script, "la durée devinée est toujours là"
    assert 'frontmost of application "Things3"' in script
    assert script.index("frontmost") < script.index("click menu item"), script


def test_the_foreground_wait_is_bounded_by_a_finite_loop(thingskit):
    script = thingskit._build_heading_script("Section", "Projet A")
    assert (f"repeat with _i from 1 to {thingskit.HEADING_FRONTMOST_ATTEMPTS:d}"
            in script), script


def test_the_frontmost_budget_is_the_delays_alone_not_the_worst_case(thingskit):
    """ATTEMPTS x POLL_INTERVAL borne les `delay` CUMULES, pas la boucle.

    Le code et la constitution annoncaient « 5 s de plafond » : faux, corrige
    le 2026-08-25. Chaque iteration emet EN PLUS un AppleEvent borne
    individuellement par le `with timeout` englobant, et `osa()` ne borne rien
    cote Python — le pire cas reel est ATTEMPTS x (PROBE_TIMEOUT +
    POLL_INTERVAL). Ce test epingle les TROIS termes ensemble, parce que c'est
    leur dissociation qui avait produit le chiffre faux : chacun pris a part
    etait juste.

    L'invariant, lui, n'a jamais bouge : la boucle termine et echoue ferme.
    """
    delays = (thingskit.HEADING_FRONTMOST_ATTEMPTS
              * thingskit.HEADING_FRONTMOST_POLL_INTERVAL)
    assert delays == 5.0, "budget de delais cumules"

    script = thingskit._frontmost_check("ZZ_MARKER",
                                        thingskit.HEADING_FRONTMOST_ATTEMPTS)
    assert (f"with timeout of {thingskit.HEADING_VIEW_PROBE_TIMEOUT:d} seconds"
            in script), script

    worst = (thingskit.HEADING_FRONTMOST_ATTEMPTS
             * (thingskit.HEADING_VIEW_PROBE_TIMEOUT
                + thingskit.HEADING_FRONTMOST_POLL_INTERVAL))
    assert worst == 505.0, (
        f"pire cas reel = {worst} s — la constitution en annonce 505, "
        "la rectifier avant de changer une constante")


def test_nothing_bounds_the_ui_script_on_the_python_side(thingskit):
    """Le terme que personne ne recalculait : `osa()` ne passe AUCUN `timeout=`.

    C'est ce qui rend le pire cas ci-dessus reellement atteignable. L'absence
    est DELIBEREE — le script clique un menu puis tape le titre, le tuer en
    cours laisserait un en-tete cree sans titre. Si un `timeout=` apparait un
    jour, ce test le signale : le pire cas annonce change, et la justification
    ecrite dans la constitution avec lui.
    """
    tree = ast.parse(SCRIPT_SOURCE.read_text())
    osa = next(n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "osa")
    runs = [n for n in ast.walk(osa)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "run"]
    assert runs, "osa() n'appelle plus subprocess.run — relire la borne"
    for call in runs:
        assert not any(kw.arg == "timeout" for kw in call.keywords), (
            "osa() borne desormais son sous-processus : mettre a jour le pire "
            "cas annonce pour _frontmost_check (code + constitution)")


def test_the_foreground_is_reasserted_between_the_click_and_the_keystroke(thingskit):
    """Le contrôle en tête du script ne couvre PAS l'instant dangereux : entre
    lui et la frappe il y a le clic de menu et son délai fixe."""
    script = thingskit._build_heading_script("Section", "Projet A")
    click = script.index("click menu item")
    key = script.index("keystroke")
    positions = [m.start() for m in re.finditer("frontmost", script)]

    assert any(click < p < key for p in positions), script


def test_the_reassertion_before_the_keystroke_does_not_wait(thingskit):
    """Après le clic il n'y a plus rien à attendre : si le premier plan a été
    perdu, patienter ne ferait que taper plus tard dans la mauvaise app."""
    script = thingskit._build_heading_script("Section", "Projet A")
    click = script.index("click menu item")
    tail = script[click:]

    assert "repeat with _i from 1 to 1\n" in tail, tail


def test_not_frontmost_before_the_click_is_named_as_such(thingskit):
    ok, msg = thingskit._interpret_ui_outcome(
        1, f'error "{thingskit._NOT_FRONTMOST_MARKER}" number -2700')

    assert not ok
    assert "premier plan" in msg
    assert "rien n'a été cliqué ni tapé" in msg


def test_focus_lost_after_the_click_does_not_claim_the_title_was_typed(thingskit):
    ok, msg = thingskit._interpret_ui_outcome(
        1, f'error "{thingskit._LOST_FOCUS_MARKER}" number -2700')

    assert not ok
    assert "rien n'a été tapé" in msg
    assert "sans titre" in msg.lower(), (
        "le message tait l'en-tête vide que le clic a pu créer")


def test_no_window_is_not_confused_with_a_wrong_view(thingskit):
    ok, msg = thingskit._interpret_ui_outcome(
        1, f'error "{thingskit._NO_WINDOW_MARKER}: " number -2700')

    assert not ok
    assert "aucune fenêtre" in msg.lower()


def test_ui_reports_not_frontmost_fails_explicitly(thingskit, monkeypatch, tmp_path,
                                                   capsys):
    db_file = _make_db(tmp_path, [{"uuid": "P1", "title": "Projet A", "type": 1}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit.subprocess, "run", inert_run)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(
        thingskit, "osa",
        _osa_answering_the_view_probe(
            lambda script: (1, f'error "{thingskit._NOT_FRONTMOST_MARKER}"')))

    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))

    assert rc != 0
    assert "premier plan" in capsys.readouterr().err


# --- la garde de premier plan, EXÉCUTÉE -------------------------------------
#
# Même technique que pour la comparaison d'affichage : la lecture de l'état
# réel est remplacée par une valeur contrôlée, ce qui tourne étant
# l'AppleScript RÉELLEMENT produit. Sans cela la garde n'était vérifiée que
# par relecture de son texte — or c'est son COMPORTEMENT qui doit refuser.

_FRONT_LINE = '      if (frontmost of application "Things3") then\n'


def _run_frontmost_check(thingskit, is_front, attempts):
    script = thingskit._frontmost_check("ZZ_MARKER", attempts)
    assert _FRONT_LINE in script, script
    script = script.replace(
        _FRONT_LINE, f'      if ({"true" if is_front else "false"}) then\n', 1)
    proc = subprocess.run([shutil.which("osascript"), "-e", script + 'return "OK"\n'],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


@requires_osascript
def test_the_foreground_check_lets_through_when_things_is_frontmost(thingskit):
    rc, out = _run_frontmost_check(thingskit, True,
                                   thingskit.HEADING_FRONTMOST_ATTEMPTS)
    assert rc == 0, out


@requires_osascript
def test_the_foreground_check_refuses_when_things_is_not_frontmost(thingskit):
    """Le cas que le délai fixe de 300 ms laissait passer : la frappe partait
    dans l'application réellement au premier plan."""
    rc, out = _run_frontmost_check(thingskit, False, 2)

    assert rc != 0, "la garde a laissé passer une frappe hors de Things"
    assert "ZZ_MARKER" in out


# ---------------------------------------------------------------------------
# Saisie par COLLAGE, jamais par frappe caractère par caractère
#
# `keystroke "<titre>"` demande à System Events de retrouver, pour CHAQUE
# caractère, une combinaison de touches qui le produise sur la disposition
# courante. Quand il n'y en a pas, il ne renonce pas : il frappe le code de
# touche virtuel 0, qui est `a` sur cette disposition. Mesuré le 2026-08-27 sur
# la vraie base, par la commande elle-même :
#
#     thingskit create-heading --project "…" "Éprouvé — formalités é à ù ç"
#     -> en base : 'aprouva — formalitas a a a ç'
#
# É, é, à, ù -> `a` ; `ç` et `—` intacts (`ç` est une touche unique sur cette
# disposition, les autres exigent une touche morte). Ce n'est donc PAS un
# défaut d'Unicode : c'est la COMPOSITION qui échoue, et elle échoue en
# silence, avec rc=0 côté AppleScript.
#
# Le collage est une opération unique, indépendante de la disposition. Mesuré
# le même jour, même projet jetable : titre demandé `PROBE-é-à-ù-ç`, titre relu
# `PROBE-é-à-ù-ç`, caractère pour caractère.
# ---------------------------------------------------------------------------

PASTE_CHORD = 'keystroke "v" using {command down}'


def test_the_title_never_reaches_a_literal_keystroke(thingskit):
    """Le titre ne doit apparaître QUE dans l'écriture du presse-papiers.

    Une frappe littérale du titre est le défaut mesuré : elle ne transporte
    pas les caractères composés.
    """
    title = "Éprouvé — formalités é à ù ç"
    script = thingskit._build_heading_script(title, "Projet A")

    assert f'keystroke "{title}"' not in script
    assert PASTE_CHORD in script
    assert f'set the clipboard to "{title}"' in script


def test_the_only_keystroke_left_is_the_paste_chord(thingskit):
    """Balayage, pas échantillon : toute autre frappe rouvrirait la classe."""
    script = thingskit._build_heading_script("Éprouvé é", "Projet A")
    frappes = re.findall(r'keystroke [^\n]*', script)

    assert frappes == [PASTE_CHORD], frappes


def test_the_pasted_title_still_goes_through_the_escape_function(thingskit):
    """Le titre part toujours dans un littéral AppleScript : `_esc` reste
    obligatoire — le presse-papiers ne change rien à l'évasion de littéral."""
    script = thingskit._build_heading_script('Il a dit "non" \\ ici', "Projet A")

    assert 'set the clipboard to "Il a dit \\"non\\" \\\\ ici"' in script


# ---------------------------------------------------------------------------
# HEADING_PASTE_COMMIT_DELAY — ce que la constante GARANTIT, pas sa valeur
#
# Un test qui assènerait `== 0.4` ne protégerait rien : il recopierait le code
# et rougirait au premier réglage légitime sans avoir rien gardé. Ce qui est
# épinglé ici est ce que le réglage ne doit pas cesser d'assurer :
#   - une pause EXISTE entre le collage et sa validation, et elle est ENTRE
#     les deux — la déplacer ou la retirer valide un champ où le collage n'a
#     pas encore atterri, donc un en-tête au titre VIDE ;
#   - elle est RÉELLE : `delay 0` rend la main immédiatement, ce qui est
#     exactement l'absence de pause sous une forme qui en a l'air ;
#   - la valeur émise DÉRIVE de la constante, elle n'est pas recopiée au site
#     d'émission — sinon la constante ne règle plus rien.
#
# Une quatrième propriété a été envisagée puis ÉCARTÉE sur mesure : exiger une
# forme décimale simple, au motif qu'une notation scientifique ne serait pas
# lue par AppleScript. Elle l'est — mesuré le 2026-08-27 :
#
#     osascript -e 'if false then
#     delay 1e-05
#     end if
#     return "OK"'        -> OK
#
# Il n'y a donc pas de défaut à garder de ce côté, et un test l'aurait interdit
# sans raison.
# ---------------------------------------------------------------------------

PASTE_COMMIT = "key code 36"


def test_a_pause_separates_the_paste_from_its_commit(thingskit):
    """L'ordre est la propriété : coller, ATTENDRE, valider."""
    script = thingskit._paste_lines("Section")
    delais = re.findall(r"delay [^\n]*", script)

    assert len(delais) == 1, delais
    assert script.index(PASTE_CHORD) < script.index(delais[0]) \
        < script.index(PASTE_COMMIT)


def test_the_pause_between_paste_and_commit_is_real(thingskit):
    """Relue DANS le script émis, pas dans la constante : c'est la valeur qui
    part réellement à AppleScript qui doit être une attente, pas celle qu'on
    croit avoir réglée."""
    script = thingskit._paste_lines("Section")
    valeur = float(re.search(r"delay (\S+)", script).group(1))

    assert valeur > 0, valeur


def test_the_emitted_delay_derives_from_the_constant(thingskit, monkeypatch):
    """Tue la mutation « recopier le littéral au site d'émission » : une
    constante que le script n'emploie pas ne règle rien, et rien ne le dirait.
    """
    monkeypatch.setattr(thingskit, "HEADING_PASTE_COMMIT_DELAY", 1.25)
    script = thingskit._paste_lines("Section")

    assert re.findall(r"delay [^\n]*", script) == ["delay 1.25"], script


def test_the_constant_carries_the_reason_it_is_a_guessed_duration(thingskit):
    """La constante est une durée DEVINÉE — la seule du chemin de collage —
    et ce statut se déclare à côté d'elle. Sans ce commentaire, un lecteur la
    prend pour une durée mesurée et la réduit sans savoir ce qu'il risque.
    """
    source = SCRIPT_SOURCE.read_text(encoding="utf-8")
    tete = source[:source.index("HEADING_PASTE_COMMIT_DELAY =")]
    bloc = tete[tete.rindex("\n\n") :]

    assert "DEVIN" in bloc.upper(), bloc
    assert "VIDE" in bloc.upper(), bloc


def test_the_clipboard_is_saved_before_being_overwritten(thingskit):
    script = thingskit._build_heading_script("Section", "Projet A")

    assert script.index("the clipboard as record") < \
        script.index('set the clipboard to "Section"')


def test_an_unrestorable_clipboard_is_refused_before_it_is_overwritten(thingskit):
    """Ce qu'on ne sait pas rendre, on ne l'écrase pas : le refus précède
    l'écriture du presse-papiers ET le clic."""
    script = thingskit._build_heading_script("Section", "Projet A")
    refus = script.index(thingskit._CLIPBOARD_UNSAVEABLE_MARKER)

    assert refus < script.index('set the clipboard to "Section"')
    assert refus < script.index("click menu item")


def test_an_empty_clipboard_is_left_empty_not_carrying_our_title(thingskit):
    """Rien à rendre ne veut pas dire « garder le nôtre » : le titre ne doit
    pas survivre à la commande dans le presse-papiers de l'utilisateur."""
    script = thingskit._build_heading_script("Section", "Projet A")

    assert 'set the clipboard to ""' in script


def test_interpret_ui_outcome_names_the_unrestorable_clipboard(thingskit):
    ok, msg = thingskit._interpret_ui_outcome(
        1, f'error "{thingskit._CLIPBOARD_UNSAVEABLE_MARKER}" number -2700')

    assert not ok
    assert "presse-papiers" in msg
    assert "rien n'a été cliqué ni tapé" in msg


# ---------------------------------------------------------------------------
# La vérification post-action refuse un titre qui DIFFÈRE de celui demandé
#
# Elle le refusait déjà (`_find_heading` compare le titre exact), mais son
# message taisait ce qui avait été créé : l'utilisateur restait avec un objet
# parasite dans sa base et aucun nom pour le retrouver. Deux en-têtes
# `…formalitas` en double dans la vraie base disent exactement cela — un appel
# en échec, réessayé.
# ---------------------------------------------------------------------------

def _rigged_ui_creating(thingskit, monkeypatch, tmp_path, created_title):
    db_file = _make_db(tmp_path, [{"uuid": "P1", "title": "Projet A", "type": 1}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit.subprocess, "run", inert_run)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))

    def _ui(script):
        if created_title is not None:
            con = sqlite3.connect(db_file)
            con.execute(
                "insert into TMTask (uuid,title,type,trashed,project) "
                "values (?,?,2,0,'P1')", ("H-PARASITE", created_title))
            con.commit()
            con.close()
        return 0, "OK"

    monkeypatch.setattr(thingskit, "osa", _osa_answering_the_view_probe(_ui))
    return db_file


def test_a_heading_created_under_another_title_fails_and_names_it(
        thingskit, monkeypatch, tmp_path, capsys):
    """La corruption mesurée, rejouée : l'automatisation rend « OK » et crée un
    en-tête d'un AUTRE nom. Code retour non nul, et le message NOMME l'objet
    parasite — sinon l'utilisateur ne peut pas le retrouver pour l'effacer."""
    _rigged_ui_creating(thingskit, monkeypatch, tmp_path,
                        "aprouva — formalitas a a a ç")

    rc = thingskit.cmd_create_heading(
        _ns(title="Éprouvé — formalités é à ù ç", project="Projet A"))
    err = capsys.readouterr().err

    assert rc != 0
    assert "aprouva — formalitas a a a ç" in err
    assert "H-PARASITE" in err


def test_no_stray_is_reported_when_the_ui_created_nothing(
        thingskit, monkeypatch, tmp_path, capsys):
    """Contre-épreuve du sur-signalement : sans objet parasite, le message ne
    doit pas en inventer un."""
    _rigged_ui_creating(thingskit, monkeypatch, tmp_path, None)

    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))
    err = capsys.readouterr().err

    assert rc != 0
    assert "H-PARASITE" not in err
    assert "autre titre" not in err


def test_a_preexisting_heading_of_another_title_is_never_taken_for_a_stray(
        thingskit, monkeypatch, tmp_path, capsys):
    """L'objet parasite se relève par DIFFÉRENCE avec l'état d'avant l'appel.
    Un en-tête qui était déjà là ne prouve rien sur ce que l'appel a fait."""
    db_file = _make_db(tmp_path, [
        {"uuid": "P1", "title": "Projet A", "type": 1},
        {"uuid": "H-VIEUX", "title": "Déjà là", "type": 2, "project": "P1"},
    ])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit.subprocess, "run", inert_run)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, "OK"))

    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))
    err = capsys.readouterr().err

    assert rc != 0
    assert "H-VIEUX" not in err


def test_an_unreadable_database_is_not_reported_as_nothing_created(
        thingskit, monkeypatch, tmp_path, capsys):
    """« Jamais observé » n'est pas « rien de créé ». Base illisible pendant
    toute l'attente : le message doit le DIRE, pas affirmer un vide."""
    db_file = _make_db(tmp_path, [{"uuid": "P1", "title": "Projet A", "type": 1}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit.subprocess, "run", inert_run)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, "OK"))

    reel = thingskit.q
    etat = {"muet": False}

    def _q(sql, args=()):
        if etat["muet"]:
            raise sqlite3.OperationalError("database is locked")
        return reel(sql, args)

    # Le relevé d'AVANT passe ; toute lecture ultérieure est muette.
    def _osa(script):
        etat["muet"] = True
        return 0, "OK"

    monkeypatch.setattr(thingskit, "osa", _osa)
    monkeypatch.setattr(thingskit, "q", _q)

    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))
    err = capsys.readouterr().err

    assert rc != 0
    assert "illisible" in err
    assert "aucun en-tête nouveau" not in err


def test_the_failure_message_uses_the_observed_state_not_a_fresh_query(
        thingskit, monkeypatch, tmp_path, capsys):
    """Course : l'en-tête atterrit APRÈS le dernier sondage. Une relecture dans
    la branche d'échec citerait le BON titre en annonçant qu'il est mauvais.
    Le relevé vient de la sonde, donc le message reste juste."""
    db_file = _make_db(tmp_path, [{"uuid": "P1", "title": "Projet A", "type": 1}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit.subprocess, "run", inert_run)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, "OK"))

    reel = thingskit.wait_for_effect

    # `cmd_create_heading` attend DEUX fois : la vue affichée, puis l'en-tête.
    # C'est la seconde qui nous intéresse.
    pose = {"appels": 0}

    def _wait(sonde, **kw):
        verdict = reel(sonde, **kw)
        pose["appels"] += 1
        if pose["appels"] == 2:                 # l'effet atterrit APRÈS
            con = sqlite3.connect(db_file)
            con.execute("insert into TMTask (uuid,title,type,trashed,project) "
                        "values ('H-TARDIF','Section',2,0,'P1')")
            con.commit()
            con.close()
        return verdict

    monkeypatch.setattr(thingskit, "wait_for_effect", _wait)

    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))
    err = capsys.readouterr().err

    assert rc != 0
    assert "H-TARDIF" not in err
    assert "AUTRE titre" not in err


# ---------------------------------------------------------------------------
# Presse-papiers : ces propriétés s'EXÉCUTENT, elles ne se relisent pas
#
# Les premières versions de ces tests étaient des assertions textuelles sur le
# script produit. Elles ne pouvaient pas voir que le `try` autour de
# `clipboard info` était fail-OPEN — un `clipboard info` qui erre laissait
# `hadClip` faux, sautait la sauvegarde ET le refus, et le contenu de
# l'utilisateur était écrasé sans filet. Une garde qu'on relit n'est pas une
# garde qu'on éprouve.
#
# Le motif est celui de `_run_comparison` : ce qui tourne est l'AppleScript
# RÉELLEMENT produit. Aucun test de ce fichier n'écrit dans le presse-papiers
# réel.
#
# Ce que le banc substitue, et il substitue DEUX choses, pas une :
#   - `_sans_pasteboard` ne touche que les primitives de pasteboard — c'est le
#     cas commun, et le double System Events y reste intact ;
#   - `_sans_system_events` neutralise EN PLUS le bloc System Events lui-même,
#     et les tests qui l'emploient sont donc moins fidèles : ni frappe, ni
#     validation, ni délai réels. Ils n'ont pas le choix — ils éprouvent la
#     remise du presse-papiers sur les DEUX chemins de `_paste_lines`, dont le
#     chemin d'erreur, qui ne s'atteint qu'en faisant ÉCHOUER le collage.
#     Laisser ce bloc intact frapperait l'application au premier plan du poste.
#
# La formulation d'avant disait « seules les trois primitives de pasteboard
# remplacées » et valait pour le premier cas seulement. Trois tests relevaient
# du second au moment où elle a été écrite : un lecteur qui s'y fiait croyait
# le double System Events intact partout.
# ---------------------------------------------------------------------------

ERRE = "(item 5 of {})"          # expression AppleScript qui LÈVE à l'exécution


def _sans_pasteboard(script, info="{}", record='"CONTENU"', remise_erre=False):
    """Neutralise les trois primitives de pasteboard, et rien d'autre."""
    assert "(clipboard info)" in script or "the clipboard" in script, script
    script = script.replace("(clipboard info)", info)
    script = script.replace("the clipboard as record", record)
    script = script.replace("set the clipboard to savedClip",
                            f"set fakeClip to {ERRE}" if remise_erre
                            else "set fakeClip to savedClip")
    script = script.replace("set the clipboard to", "set fakeClip to")
    return 'set fakeClip to "INTACT"\n' + script


def _sans_system_events(thingskit, script, collage="set fakeKeys to 1",
                        validation="set fakeKeys to 2"):
    """Neutralise le bloc System Events — AU-DELÀ des trois primitives de
    pasteboard, cf. l'en-tête de section. UN seul site de définition : trois
    tests le faisaient chacun de leur côté, avec la même chaîne de `replace`.

    Le délai remplacé DÉRIVE de la constante. Recopié en littéral — ce qu'il
    était —, il cesse de matcher au premier réglage de
    `HEADING_PASTE_COMMIT_DELAY`, et le banc se met alors à DORMIR la durée
    réelle sans qu'aucun test ne rougisse.
    """
    return (script
            .replace('tell application "System Events"', "tell me")
            .replace(PASTE_CHORD, collage)
            .replace(PASTE_COMMIT, validation)
            .replace(f"delay {thingskit.HEADING_PASTE_COMMIT_DELAY}", "delay 0"))


def _joue(corps):
    proc = subprocess.run([shutil.which("osascript"), "-e", corps],
                          capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


# --- prise du presse-papiers : trois états, `inconnu` vaut REFUS -------------

@requires_osascript
def test_an_empty_clipboard_is_seen_as_empty(thingskit):
    rc, out = _joue(_sans_pasteboard(thingskit._clipboard_capture_lines(), info="{}")
                    + 'return clipState & "/" & (savedClip as text)\n')
    assert rc == 0, out
    assert out.startswith("empty/"), out


@requires_osascript
def test_a_saveable_clipboard_is_saved(thingskit):
    rc, out = _joue(_sans_pasteboard(thingskit._clipboard_capture_lines(),
                                     info='{"x"}', record='"CONTENU"')
                    + 'return clipState & "/" & savedClip\n')
    assert rc == 0, out
    assert out == "present/CONTENU", out


@requires_osascript
def test_an_unsaveable_clipboard_is_refused(thingskit):
    rc, out = _joue(_sans_pasteboard(thingskit._clipboard_capture_lines(),
                                     info='{"x"}', record=ERRE)
                    + 'return "PAS REFUSE"\n')
    assert rc != 0, out
    assert thingskit._CLIPBOARD_UNSAVEABLE_MARKER in out, out
    assert "PAS REFUSE" not in out


@requires_osascript
def test_an_unreadable_clipboard_state_is_a_refusal_not_an_empty_clipboard(thingskit):
    """LE bloqueur : `clipboard info` qui erre — pasteboard détenu par un autre
    processus, contenu qu'il ne sait pas décrire. « Je n'ai pas pu lire » n'est
    PAS « il est vide » : le premier vaut refus, le second laisse passer."""
    rc, out = _joue(_sans_pasteboard(thingskit._clipboard_capture_lines(), info=ERRE)
                    + 'return "PAS REFUSE : " & clipState\n')
    assert rc != 0, out
    assert thingskit._CLIPBOARD_UNKNOWN_MARKER in out, out
    assert "PAS REFUSE" not in out
    assert thingskit._CLIPBOARD_UNSAVEABLE_MARKER not in out   # deux causes, deux marqueurs


def test_the_two_clipboard_refusals_are_distinct_and_diagnose_differently(thingskit):
    assert thingskit._CLIPBOARD_UNKNOWN_MARKER != thingskit._CLIPBOARD_UNSAVEABLE_MARKER
    _, inconnu = thingskit._interpret_ui_outcome(
        1, f'error "{thingskit._CLIPBOARD_UNKNOWN_MARKER}" number -2700')
    _, insauvable = thingskit._interpret_ui_outcome(
        1, f'error "{thingskit._CLIPBOARD_UNSAVEABLE_MARKER}" number -2700')
    assert inconnu != insauvable
    for msg in (inconnu, insauvable):
        assert "presse-papiers" in msg
        assert "rien n'a été cliqué ni tapé" in msg


# --- remise du presse-papiers : elle ne peut ni échouer en silence, ----------
# --- ni remplacer l'erreur qu'elle accompagne -------------------------------

@requires_osascript
@pytest.mark.parametrize("chemin", ["succès", "erreur"])
def test_the_restore_puts_the_saved_content_back(thingskit, chemin):
    corps = ('set savedClip to "ORIGINE"\n'
             + _sans_pasteboard(thingskit._clipboard_restore_lines(chemin))
             + 'return fakeClip & "/" & (clipRestored as text)\n')
    rc, out = _joue(corps)
    assert rc == 0, out
    assert out == "ORIGINE/true", out


@requires_osascript
@pytest.mark.parametrize("chemin", ["succès", "erreur"])
def test_a_failing_restore_neither_raises_nor_passes_unnoticed(thingskit, chemin):
    """Une remise qui échoue ne doit ni remonter (elle remplacerait l'erreur
    d'origine et ferait disparaître son marqueur), ni se taire (le contenu de
    l'utilisateur est perdu et le titre peut demeurer dans le pasteboard)."""
    corps = ('set savedClip to "ORIGINE"\n'
             + _sans_pasteboard(thingskit._clipboard_restore_lines(chemin),
                                remise_erre=True)
             + 'return "SUITE/" & (clipRestored as text)\n')
    rc, out = _joue(corps)
    assert rc == 0, out                      # ne remonte pas
    assert out == "SUITE/false", out         # ne se tait pas


@requires_osascript
def test_a_failing_restore_never_swallows_the_original_marker(thingskit):
    """Le bloqueur 2 : sans garde, l'erreur de la remise REMPLAÇAIT `errMsg` et
    `_LOST_FOCUS_MARKER` — dont tout l'objet est d'avertir qu'un en-tête sans
    titre a pu être créé — n'atteignait plus `_interpret_ui_outcome`."""
    script = _sans_system_events(
        thingskit, thingskit._paste_lines("Section"),
        collage=f'error "{thingskit._LOST_FOCUS_MARKER}" number -2700',
        validation="set fakeKeys to 0")
    corps = ('set savedClip to "ORIGINE"\n'
             + _sans_pasteboard(script, remise_erre=True))
    rc, out = _joue(corps)

    assert rc != 0, out
    assert thingskit._LOST_FOCUS_MARKER in out, out
    assert thingskit._CLIPBOARD_NOT_RESTORED_MARKER in out, out
    ok, msg = thingskit._interpret_ui_outcome(rc, out)
    assert not ok
    assert "en-tête SANS TITRE" in msg          # le marqueur d'origine parle encore
    assert "presse-papiers" in msg              # et la remise ratée est dite


@requires_osascript
def test_a_failing_restore_on_the_success_path_does_not_turn_a_write_into_a_failure(
        thingskit):
    """Le collage a réussi, l'en-tête existe. Une remise ratée est un
    AVERTISSEMENT, jamais un échec : rendre un échec ferait réessayer
    l'appelant, donc un doublon en base — le mode d'échec que ce commit
    corrige, réintroduit par son propre correctif."""
    script = _sans_system_events(thingskit, thingskit._paste_lines("Section"))
    corps = 'set savedClip to "ORIGINE"\n' + _sans_pasteboard(script, remise_erre=True)
    rc, out = _joue(corps)

    assert rc == 0, out
    assert out.startswith("OK"), out
    assert thingskit._CLIPBOARD_NOT_RESTORED_MARKER in out, out
    ok, msg = thingskit._interpret_ui_outcome(rc, out)
    assert ok                                   # succès, PAS échec
    assert msg != "ok"                          # mais pas silencieux


@requires_osascript
def test_the_bench_leaves_no_live_delay_behind(thingskit):
    """Contre-épreuve du banc lui-même : si le motif de délai cesse de matcher,
    les tests ci-dessus DORMENT la durée réelle au lieu d'échouer. Un banc qui
    se dégrade en silence est pire qu'un banc qui tombe.
    """
    neutralise = _sans_system_events(thingskit, thingskit._paste_lines("Section"))

    assert re.findall(r"delay [^\n]*", neutralise) == ["delay 0"], neutralise


def test_the_nominal_paste_returns_a_plain_ok(thingskit):
    """Contre-épreuve : sans incident, aucun avertissement ne doit apparaître."""
    script = _sans_system_events(thingskit, thingskit._paste_lines("Section"))
    rc, out = _joue('set savedClip to "ORIGINE"\n' + _sans_pasteboard(script))

    assert rc == 0, out
    assert out == "OK", out
    assert thingskit._interpret_ui_outcome(rc, out) == (True, "ok")


def test_a_clipboard_warning_is_printed_without_failing_the_command(
        thingskit, monkeypatch, tmp_path, capsys):
    db_file = _make_db(tmp_path, [{"uuid": "P1", "title": "Projet A", "type": 1}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit.subprocess, "run", inert_run)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))

    def _ui(script):
        con = sqlite3.connect(db_file)
        con.execute("insert into TMTask (uuid,title,type,trashed,project) "
                    "values ('H9','Section',2,0,'P1')")
        con.commit()
        con.close()
        return 0, "OK " + thingskit._CLIPBOARD_NOT_RESTORED_MARKER

    monkeypatch.setattr(thingskit, "osa", _osa_answering_the_view_probe(_ui))

    rc = thingskit.cmd_create_heading(_ns(title="Section", project="Projet A"))
    sortie = capsys.readouterr()

    assert rc == 0
    assert "heading créé" in sortie.out
    assert "presse-papiers" in sortie.err


# --- fenêtre d'exposition : le titre n'est écrit qu'au dernier moment --------

def test_the_refusal_precedes_the_click_but_the_title_is_written_after_it(thingskit):
    """Deux exigences distinctes, portées par deux emplacements distincts.

    Le REFUS doit précéder le clic — c'est lui qui garantit qu'aucun objet
    n'est créé. L'ÉCRITURE du titre, elle, n'a rien à faire là : la mettre
    avant le clic exposait le titre pendant l'énumération de menu, le clic et
    un délai, et sur les chemins d'échec `NO_LABEL` et `LOST_FOCUS` où aucun
    collage n'a lieu.
    """
    script = thingskit._build_heading_script("Section", "Projet A")
    clic = script.index("click menu item")

    assert script.index(thingskit._CLIPBOARD_UNKNOWN_MARKER) < clic
    assert script.index(thingskit._CLIPBOARD_UNSAVEABLE_MARKER) < clic
    assert script.index('set the clipboard to "Section"') > clic
    # rien entre l'écriture et le collage que le collage lui-même
    entre = script[script.index('set the clipboard to "Section"'):
                   script.index(PASTE_CHORD)]
    assert "click menu item" not in entre
    assert "frontmost" not in entre
    assert "delay" not in entre, entre


def test_both_restore_paths_are_present_and_labelled(thingskit):
    """Les deux chemins se distinguent par un LIBELLÉ, pas par un compte
    d'occurrences d'`end try`. Ce test a affirmé ce compte trois fois et les
    trois étaient fausses : deux à l'écriture, la troisième périmée par un
    ajout de bloc ultérieur. Le compte n'est plus nommé — un nombre recopié se
    périme, une propriété qu'on nomme, non.
    """
    script = thingskit._build_heading_script("Section", "Projet A")

    assert "remise du presse-papiers (chemin : succès)" in script
    assert "remise du presse-papiers (chemin : erreur)" in script
    assert script.index("remise du presse-papiers (chemin : erreur)") < \
        script.index("remise du presse-papiers (chemin : succès)")
