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

import pytest


class _InertResult:
    """Ce que rend un lancement de fils NEUTRALISÉ.

    Les stubs de ce fichier rendaient `None`. Ils décrivaient un
    `subprocess.run` dont personne ne lisait le retour — ce qui a cessé
    d'être vrai le 2026-08-27, `_spawn` lisant le code retour pour dire un
    échec sans citer l'argv. Un stub qui ne peut pas porter ce que le code
    lit n'est pas un stub, c'est un trou.
    """
    returncode = 0
    stdout = ""
    stderr = ""


def _inert_run(*a, **kw):
    return _InertResult()


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
        lambda args, **kw: (calls["open"].append(args), _InertResult())[1],
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
                                            _InertResult())[1])
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
    monkeypatch.setattr(thingskit.subprocess, "run", _inert_run)
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
    monkeypatch.setattr(thingskit.subprocess, "run", _inert_run)
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
    monkeypatch.setattr(thingskit.subprocess, "run", _inert_run)
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
    monkeypatch.setattr(thingskit.subprocess, "run", _inert_run)
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
# Caractères non saisissables : refus AVANT toute activation de Things
#
# `_esc` transmet fidèlement ces caractères jusqu'à la valeur AppleScript
# (mesuré : un saut de ligne source arrive bien avec l'identifiant 10). C'est
# précisément le problème : `keystroke` les tape, et dans le champ de saisie
# d'un heading un retour à la ligne vaut validation — heading tronqué, reste
# du titre et `key code 36` partis hors du champ visé. Effet de bord déjà
# produit dans les données réelles, que la vérification post-action ne peut
# pas défaire.
# ---------------------------------------------------------------------------

UNTYPABLE = ["\n", "\r", "\t", "\x0b", "\x0c", "\x1b", "\x7f", "\u2028", "\u2029"]


@pytest.mark.parametrize("ch", UNTYPABLE)
def test_untypable_title_refused_before_any_activation(thingskit, rigged, ch):
    calls, set_rows = rigged
    set_rows([{"uuid": "P1", "title": "Projet A", "type": 1}])
    rc = thingskit.cmd_create_heading(
        _ns(title=f"Avant{ch}Après", project="Projet A"))
    assert rc != 0
    # Ni frappe clavier, ni activation/deeplink : rien n'atteint l'application.
    assert calls["osa"] == []
    assert calls["open"] == []


@pytest.mark.parametrize("title", [
    'Titre "cité"', "Chemin C:\\dossier", "Éléments accentués — tiret cadratin",
    "emoji 🙂", "espaces    multiples",
])
def test_typable_titles_are_accepted(thingskit, rigged, title):
    calls, set_rows = rigged
    set_rows([{"uuid": "P1", "title": "Projet A", "type": 1}])
    thingskit.cmd_create_heading(_ns(title=title, project="Projet A"))
    assert len(calls["osa"]) == 1  # le garde ne doit pas sur-refuser


def test_untypable_chars_lists_offenders(thingskit):
    assert thingskit._untypable_chars("propre") == []
    assert thingskit._untypable_chars("a\nb\tc") == ["U+000A", "U+0009"]


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
    lui et la frappe il y a le clic de menu et son `delay 0.4`."""
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
    monkeypatch.setattr(thingskit.subprocess, "run", _inert_run)
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
