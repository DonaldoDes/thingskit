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

from conftest import SCRIPT_PATH


def _spawn_sites(source: str):
    """(ligne, capture ?) pour chaque lancement de fils du script."""
    sites = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute)
                and f.attr in ("run", "call", "check_call", "Popen")
                and isinstance(f.value, ast.Name) and f.value.id == "subprocess"):
            continue
        kw = {k.arg for k in node.keywords}
        sites.append((node.lineno,
                      bool({"capture_output", "stdout", "stderr"} & kw)))
    return sites


def test_every_child_spawn_of_the_script_captures_its_output():
    source = SCRIPT_PATH.read_text()
    sites = _spawn_sites(source)
    assert sites, "aucun lancement de fils trouvé — le balayage a cessé de voir"
    nus = [ln for ln, capture in sites if not capture]
    assert nus == [], (
        f"{len(nus)} lancement(s) de fils sur {len(sites)} laissent leur "
        f"sortie atteindre NOS descripteurs, lignes {nus} — ce que le fils "
        "écrit sort sur le terminal de l'utilisateur, sans passer par une "
        "seule ligne de ce programme.")


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
