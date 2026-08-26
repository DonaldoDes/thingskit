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
