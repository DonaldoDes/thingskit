"""Le schéma d'URL et son SECRET — `url_open`, `_uri_scheme_token`.

L'opération `update` du schéma d'URL exige un jeton d'authentification
(`TMSettings.uriSchemeAuthenticationToken`, lu en `mode=ro`). Ce jeton est un
secret : il ouvre l'écriture sur la base Things de l'utilisateur à quiconque
le détient.

**Ce fichier existe parce que la propriété « le jeton ne sort jamais » a été
AFFIRMÉE avant d'être établie, et qu'elle était fausse.** Deux tests de
non-fuite existaient bien (`tests/test_move_task.py`), mais ils remplaçaient
`url_open` par un faux : ils n'exerçaient donc JAMAIS le processus fils. Or
`subprocess.run(argv, check=False)` sans capture laisse le fils hériter des
descripteurs 1 et 2 du parent, et `/usr/bin/open` imprime **l'URL complète,
jeton compris** sur son stderr quand LaunchServices ne résout pas le schéma.
Reproduit le 2026-08-27 avec un `open` substitué qui écrit son argv :

    Unable to find application for URL -g things:///json?auth-token=SECRET-…

La branche est atteignable en usage nominal : `ensure_running()` ne s'arrête
pas quand Things ne démarre pas (« la vérification post-action tranchera »),
donc une application déplacée ou non enregistrée suffit — et le jeton vient
d'être lu.

Les tests de ce fichier exercent le VRAI `url_open` et, pour ceux qui portent
sur la fuite, le VRAI `subprocess.run` contre un exécutable jetable. Un test
qui remplace la frontière qu'il prétend garder ne garde rien.
"""
from __future__ import annotations

import sqlite3

import pytest


TOKEN = "SECRET-jeton-de-test-42"


@pytest.fixture
def spy_run(monkeypatch, thingskit):
    """Enregistre l'`argv` remis à `subprocess.run`, sans rien exécuter."""
    calls = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, *args, **kwargs):
        calls.append({"argv": list(argv), "kwargs": kwargs})
        return _R()

    monkeypatch.setattr(thingskit.subprocess, "run", fake_run)
    return calls


@pytest.fixture
def fake_open(tmp_path, monkeypatch, thingskit):
    """Un `open` JETABLE qui recrache son argv sur stderr et échoue.

    C'est le comportement mesuré de `/usr/bin/open` face à un schéma que
    LaunchServices ne résout pas — le seul chemin par lequel le jeton
    quittait le processus.
    """
    script = tmp_path / "fake_open"
    script.write_text('#!/bin/sh\n'
                      'echo "Unable to find application for URL $@" >&2\n'
                      'echo "argv-sur-stdout $@"\n'
                      'exit 1\n')
    script.chmod(0o755)
    monkeypatch.setattr(thingskit, "OPEN", str(script))
    return script


# --- le jeton atteint bien l'URL ----------------------------------------

def test_the_token_reaches_the_url_handed_to_the_process(thingskit, spy_run):
    """La SEULE ligne dont dépend l'efficacité de la fonctionnalité.

    Sa neutralisation laissait la suite entièrement verte : le passage du
    jeton au SITE D'APPEL était épinglé, son arrivée dans l'URL ne l'était
    pas. Or un `update` sans jeton est un no-op silencieux que `open` rend
    malgré tout en 0 — l'exact mode d'échec que ce projet refuse.
    """
    thingskit.url_open([{"type": "to-do", "operation": "update", "id": "X"}],
                       auth_token=TOKEN)
    url = spy_run[0]["argv"][-1]
    assert "auth-token=" in url
    assert "SECRET-jeton-de-test-42" in url


def test_a_token_needing_encoding_is_percent_encoded(thingskit, spy_run):
    thingskit.url_open([{"type": "to-do"}], auth_token="a+b&c=d e")
    url = spy_run[0]["argv"][-1]
    assert "auth-token=a%2Bb%26c%3Dd%20e" in url
    # Contre-épreuve : le jeton brut ne doit pas casser la structure de l'URL.
    assert url.count("&") == 1


def test_no_token_no_auth_parameter(thingskit, spy_run):
    """Contre-épreuve du sur-ajout : les commandes SANS jeton gardent leur URL
    inchangée — c'est la forme qu'épingle déjà `test_no_focus_steal.py`."""
    thingskit.url_open([{"type": "to-do"}])
    assert spy_run[0]["argv"][-1].startswith("things:///json?data=")
    assert "auth-token" not in spy_run[0]["argv"][-1]


# --- le jeton ne sort par AUCUNE des deux sorties ------------------------

def test_the_child_output_never_reaches_our_streams_when_a_token_is_carried(
        thingskit, fake_open, capfd):
    """VRAI `subprocess.run`, VRAI processus fils, capture au niveau des
    DESCRIPTEURS : une redirection Python pure ne verrait pas le fils."""
    thingskit.url_open([{"type": "to-do", "operation": "update", "id": "X"}],
                       auth_token=TOKEN)
    out, err = capfd.readouterr()
    assert "SECRET-jeton-de-test-42" not in out + err
    assert "Unable to find application" not in out + err
    assert "argv-sur-stdout" not in out + err


def test_the_child_output_never_reaches_our_streams_without_a_token(
        thingskit, fake_open, capfd):
    """La capture porte sur la CLASSE — la sortie d'un fils qui s'échappe —
    pas sur la seule instance qui portait le secret. Une URL sans jeton porte
    quand même les titres de l'utilisateur."""
    thingskit.url_open([{"type": "to-do", "attributes": {"title": "Coucou"}}])
    out, err = capfd.readouterr()
    assert "Unable to find application" not in out + err
    assert "argv-sur-stdout" not in out + err


def test_a_failing_url_open_says_so_without_citing_the_url(thingskit,
                                                           fake_open, capfd):
    """Capturer sans rien dire changerait un défaut visible en défaut muet.
    Le message existe, et il ne cite ni l'URL, ni le jeton, ni la sortie brute
    du fils."""
    thingskit.url_open([{"type": "to-do", "operation": "update", "id": "X"}],
                       auth_token=TOKEN)
    out, err = capfd.readouterr()
    assert err.strip(), "un échec d'ouverture doit être dit"
    assert "SECRET-jeton-de-test-42" not in err
    assert "things:///json" not in err
    assert "Unable to find application" not in err


def test_a_successful_url_open_stays_silent(thingskit, tmp_path, monkeypatch,
                                            capfd):
    """Contre-épreuve du sur-bruit : le cas nominal ne dit rien."""
    script = tmp_path / "silent_open"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    monkeypatch.setattr(thingskit, "OPEN", str(script))
    thingskit.url_open([{"type": "to-do"}], auth_token=TOKEN)
    out, err = capfd.readouterr()
    assert out == "" and err == ""


# --- lecture du jeton : un schéma inattendu se refuse, il ne plante pas ---

def _db(tmp_path, ddl, rows=()):
    f = tmp_path / "main.sqlite"
    con = sqlite3.connect(f)
    con.executescript(ddl)
    for sql, args in rows:
        con.execute(sql, args)
    con.commit()
    con.close()
    return f


def test_a_schema_without_the_settings_table_is_a_refusal_not_a_traceback(
        thingskit, monkeypatch, tmp_path):
    """Le filet `except sqlite3.Error` n'était joué par AUCUN test : toutes
    les fixtures créaient `TMSettings`. Sur un schéma qui ne la porte pas —
    une version de Things antérieure à la colonne, une base de secours — la
    commande mourait sur une trace Python au lieu du refus composé."""
    db = _db(tmp_path, "CREATE TABLE TMTask (uuid TEXT);")
    monkeypatch.setattr(thingskit, "db_path", lambda: db)
    token, err = thingskit._uri_scheme_token("aucun déplacement effectué")
    assert token is None
    assert err and "jeton" in err.lower()


def test_a_settings_table_without_the_column_is_a_refusal_too(
        thingskit, monkeypatch, tmp_path):
    db = _db(tmp_path, "CREATE TABLE TMSettings (uuid TEXT PRIMARY KEY);",
             [("insert into TMSettings (uuid) values (?)", ("s",))])
    monkeypatch.setattr(thingskit, "db_path", lambda: db)
    token, err = thingskit._uri_scheme_token("aucun déplacement effectué")
    assert token is None and err


def test_an_empty_settings_table_is_a_refusal(thingskit, monkeypatch, tmp_path):
    db = _db(tmp_path, "CREATE TABLE TMSettings (uuid TEXT PRIMARY KEY, "
                       "uriSchemeAuthenticationToken TEXT);")
    monkeypatch.setattr(thingskit, "db_path", lambda: db)
    token, err = thingskit._uri_scheme_token("aucun déplacement effectué")
    assert token is None and err


def test_a_present_token_is_returned_without_error(thingskit, monkeypatch,
                                                   tmp_path):
    """Contre-épreuve du sur-refus : le cas nominal passe."""
    db = _db(tmp_path, "CREATE TABLE TMSettings (uuid TEXT PRIMARY KEY, "
                       "uriSchemeAuthenticationToken TEXT);",
             [("insert into TMSettings values (?, ?)", ("s", TOKEN))])
    monkeypatch.setattr(thingskit, "db_path", lambda: db)
    token, err = thingskit._uri_scheme_token("aucun déplacement effectué")
    assert token == TOKEN and err is None


def test_the_refusal_message_never_carries_a_token(thingskit, monkeypatch,
                                                   tmp_path):
    """Un message de refus se compose du CONTEXTE, jamais de la valeur lue —
    même quand cette valeur est celle qui manque."""
    db = _db(tmp_path, "CREATE TABLE TMSettings (uuid TEXT PRIMARY KEY, "
                       "uriSchemeAuthenticationToken TEXT);",
             [("insert into TMSettings values (?, ?)", ("s", ""))])
    monkeypatch.setattr(thingskit, "db_path", lambda: db)
    _, err = thingskit._uri_scheme_token("aucun déplacement effectué")
    assert "uriSchemeAuthenticationToken" not in err


# =======================================================================
# La CLASSE : aucun processus fils n'écrit sur nos descripteurs
# =======================================================================
#
# `777c4fc` a fermé la fuite de `url_open` et a écrit que « la capture porte
# sur la CLASSE ». C'était FAUX au moment où c'était écrit : sur les cinq
# sites de lancement du script, deux n'attrapaient rien — `open -g -a Things3`
# (argv littéral, bénin) et surtout `open things:///show?id=<uuid>` dans
# `cmd_create_heading`, dont l'argv porte une valeur d'origine non contrôlée.
# Ce dernier portait même un commentaire invoquant « même neutralisation que
# `url_open` » : une parité vraie AVANT le correctif, rompue par lui.
#
# Le test ci-dessous ne vérifie pas les deux sites connus — il vérifie la
# PROPRIÉTÉ, sur tous les sites présents et à venir. C'est la seule forme qui
# ne redevienne pas fausse au prochain ajout.

import ast

from conftest import SCRIPT_PATH, child_spawn_sites


#: Les DEUX artefacts exécutables du dépôt. `build/bundle.py` y a été ajouté
#: le 2026-08-27 après mesure : ses huit lancements bornent déjà leurs deux
#: flux, et il manipule la sortie de `codesign`, qui porte l'identité de
#: signature — précisément la valeur personnelle que `test_bundle.py` interdit
#: de publier. Une garde qui ne couvre que le fichier où le défaut a été
#: trouvé couvre l'incident, pas la classe.
GUARDED_EXECUTABLES = (SCRIPT_PATH, SCRIPT_PATH.parent.parent / "build" / "bundle.py")


@pytest.mark.parametrize("artefact", GUARDED_EXECUTABLES, ids=lambda p: p.name)
def test_every_child_spawn_captures_its_output(artefact):
    sites = child_spawn_sites(artefact.read_text())
    assert sites, f"aucun lancement de fils trouvé dans {artefact.name} — le " \
                  "recensement a cessé de voir"
    nus = [ln for ln, borne in sites if not borne]
    assert nus == [], (
        f"{len(nus)} lancement(s) de fils sur {len(sites)} dans "
        f"{artefact.name} laissent leur sortie atteindre NOS descripteurs, "
        f"lignes {nus} — ce que le fils écrit sort sur le terminal de "
        "l'utilisateur, sans passer par une seule ligne de ce programme.")


def test_the_spawn_helper_keeps_the_child_output_off_our_streams(
        thingskit, fake_open, capfd):
    """Le helper, exercé pour lui-même : c'est par lui que passent désormais
    les lancements dont la sortie ne nous appartient pas."""
    thingskit._spawn([thingskit.OPEN, "things:///show?id=UUID-NON-CONTROLE"])
    out, err = capfd.readouterr()
    assert "Unable to find application" not in out + err
    assert "argv-sur-stdout" not in out + err
    assert "UUID-NON-CONTROLE" not in out + err


def test_the_spawn_helper_says_the_return_code_without_citing_the_argv(
        thingskit, fake_open, capfd):
    thingskit._spawn([thingskit.OPEN, "things:///show?id=UUID-NON-CONTROLE"])
    err = capfd.readouterr()[1]
    assert err.strip(), "un échec de lancement doit être dit"
    assert "UUID-NON-CONTROLE" not in err
    assert "things:///" not in err


def test_the_spawn_helper_stays_silent_on_success(thingskit, tmp_path,
                                                  monkeypatch, capfd):
    script = tmp_path / "silent"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    thingskit._spawn([str(script)])
    assert capfd.readouterr() == ("", "")


@pytest.mark.parametrize("hostile", ["\x1b[2K\r", "\n", "‮"])
def test_the_spawn_label_is_bounded_even_though_every_caller_passes_a_literal(
        thingskit, fake_open, capfd, hostile):
    """Mutant survivant du 2026-08-27, tué plutôt que déclaré équivalent.

    Retirer `_rendered` du libellé laissait tout vert : les TROIS sites
    d'appel passent une chaîne littérale (`schéma d'URL`, `lancement de
    Things`, `affichage du projet`), donc le mutant était équivalent SUR CE
    DOMAINE. Le déclarer équivalent aurait fait dépendre une propriété de
    sortie de l'inventaire des appelants du jour — exactement la forme
    d'affirmation que ce lot existe pour corriger. Le quatrième appelant n'a
    pas à relire cette garde pour ne pas la casser.
    """
    thingskit._spawn([thingskit.OPEN, "x"], f"étape{hostile}terminée")
    err = capfd.readouterr()[1].rstrip("\n")
    assert err, "l'échec doit être dit"
    assert hostile not in err


# =======================================================================
# Le RECENSEMENT lui-même, exercé au lieu d'être attesté
# =======================================================================
#
# `544ceae` a ajouté « aucun site nu » comme garde de classe. Mesuré au tour
# suivant : sur le code sain, `nus == []` est satisfait À VIDE — rien ne
# prouvait que le recenseur sache reconnaître un site nu, puisqu'on ne lui en
# avait jamais montré un. Un double mutant (lancement nu REMIS dans
# `ensure_running` + prédicat du recenseur forcé) laissait `1134 passed`.
#
# Deux défauts distincts, tous deux de la même famille — une garde neuve
# vérifiée avec les instruments d'avant :
#
#   1. le prédicat de capture était une DISJONCTION : `stdout=` seul suffisait
#      à déclarer un site sûr, alors que `stderr` restait hérité — et `stderr`
#      est le canal, le seul, par lequel le jeton sortait ;
#   2. le recensement ÉNUMÉRAIT des noms d'appel (`subprocess.{run,call,
#      check_call,Popen}`), là où `_is_inert_argv_element`, écrit dans le même
#      commit, appliquait la doctrine inverse — borner ce qui est SÛR. La
#      règle et sa violation dans le même diff.
#
# Le corpus ci-dessous porte les formes MESURÉES comme échappant au
# recensement d'alors. Il est construit sur le modèle de `SCOPE_AND_SINK_FORMS`
# (`tests/test_untrusted_rendering.py`), qui, lui, tue ses mutants.

NAKED_SPAWN_FORMS = {
    # --- axe 1 : un seul flux borné n'est pas une capture -------------------
    "stdout_seul_laisse_stderr_herite": '''
import subprocess
def cmd_x(a):
    subprocess.run(["/usr/bin/open", a.title], check=False,
                   stdout=subprocess.DEVNULL)
''',
    "stderr_seul_laisse_stdout_herite": '''
import subprocess
def cmd_x(a):
    subprocess.run(["/usr/bin/open", a.title], check=False,
                   stderr=subprocess.DEVNULL)
''',
    # --- axe 2 : le recensement énumérait des noms -------------------------
    "check_output_herite_stderr_par_construction": '''
import subprocess
def cmd_x(a):
    subprocess.check_output(["/usr/bin/open", a.title])
''',
    "import_from_subprocess": '''
from subprocess import run
def cmd_x(a):
    run(["/usr/bin/open", a.title], check=False)
''',
    "module_importe_sous_un_alias": '''
import subprocess as sp
def cmd_x(a):
    sp.run(["/usr/bin/open", a.title], check=False)
''',
    "os_system": '''
import os
def cmd_x(a):
    os.system("/usr/bin/open " + a.title)
''',
    "argv_en_mot_cle": '''
import subprocess
def cmd_x(a):
    subprocess.run(args=["/usr/bin/open", a.title], check=False)
''',
    "lancement_par_indirection": '''
import subprocess
def cmd_x(a, runner=None):
    runner = runner or subprocess.run
    return runner(["/usr/bin/open", a.title], check=False)
''',
    "capture_output_a_faux": '''
import subprocess
def cmd_x(a):
    subprocess.run(["/usr/bin/open", a.title], check=False, capture_output=False)
''',
    "capture_output_a_valeur_indecidable": '''
import subprocess
def cmd_x(a, quiet=False):
    subprocess.run(["/usr/bin/open", a.title], check=False, capture_output=quiet)
''',
    "kwargs_opaques_sans_capture_declaree": '''
import subprocess
def cmd_x(a, **kw):
    subprocess.run(["/usr/bin/open", a.title], check=False, **kw)
''',
    "forme_nue_de_reference": '''
import subprocess
def cmd_x(a):
    subprocess.run(["/usr/bin/open", a.title], check=False)
''',
}

BOUNDED_SPAWN_FORMS = {
    "capture_output": '''
import subprocess
def cmd_x(a):
    subprocess.run(["/usr/bin/open", a.title], check=False, capture_output=True)
''',
    "les_deux_flux_nommes": '''
import subprocess
def cmd_x(a):
    subprocess.run(["/usr/bin/open", a.title], check=False,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
''',
    "capture_output_vrai_malgre_des_kwargs_opaques": '''
import subprocess
def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)
''',
    "check_output_avec_stderr_borne": '''
import subprocess
def cmd_x(a):
    subprocess.check_output(["/usr/bin/open", a.title],
                            stderr=subprocess.DEVNULL)
''',
}


@pytest.mark.parametrize("form", sorted(NAKED_SPAWN_FORMS))
def test_the_census_counts_each_naked_form_as_naked(form):
    """C'est LE test qui manquait : le recenseur est mis devant un site nu.

    Sans lui, `nus == []` sur le code sain est satisfait à vide, et forcer le
    prédicat à `True` ne fait rougir personne."""
    sites = child_spawn_sites(NAKED_SPAWN_FORMS[form])
    assert sites, f"forme `{form}` non RECENSÉE — le lancement est invisible"
    assert [ln for ln, borne in sites if not borne], (
        f"forme `{form}` recensée mais déclarée BORNÉE — son fils écrit "
        "encore sur nos descripteurs")


@pytest.mark.parametrize("form", sorted(BOUNDED_SPAWN_FORMS))
def test_the_census_does_not_cry_wolf_on_a_bounded_form(form):
    """Contre-épreuve du sur-refus : borner les DEUX flux suffit, et un
    recenseur qui refuserait tout serait aussi inutile qu'un recenseur qui
    n'accepterait rien."""
    sites = child_spawn_sites(BOUNDED_SPAWN_FORMS[form])
    assert sites, f"forme `{form}` non recensée"
    assert [ln for ln, borne in sites if not borne] == [], (
        f"forme `{form}` déclarée nue alors qu'elle borne ses deux flux")


def test_the_census_ignores_what_does_not_spawn():
    """Un attribut de `subprocess` qui n'est pas APPELÉ n'est pas un
    lancement — sinon `subprocess.DEVNULL` et l'annotation de type de `_spawn`
    peupleraient le recensement de fantômes."""
    source = '''
import subprocess
DEVNULL = subprocess.DEVNULL
def _spawn(argv) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=False, capture_output=True)
'''
    sites = child_spawn_sites(source)
    assert len(sites) == 1, f"fantômes recensés : {sites}"
    assert sites[0][1] is True
