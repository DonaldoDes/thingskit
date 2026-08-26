"""Les écritures ne doivent pas voler le focus applicatif.

`open <url>` et `open -a App` ACTIVENT l'application ciblée : chaque
`add-task` / `create-project` faisait passer Things au premier plan et
faisait perdre à l'utilisateur le focus de l'app sur laquelle il travaille.
`man open` : « -g  Do not bring the application to the foreground. »

Exception assumée : `create-heading` pilote le MENU de l'interface (Fichier >
Nouvel en-tête) et a BESOIN du premier plan — son `open things:///show?id=`
et son `activate` AppleScript restent activants. Cf. constitution.md
§ Zones sensibles.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def spy_run(monkeypatch, thingskit):
    calls = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return _R()

    monkeypatch.setattr(thingskit.subprocess, "run", fake_run)
    return calls


def test_url_open_ne_ramene_pas_things_au_premier_plan(thingskit, spy_run):
    thingskit.url_open([{"type": "to-do", "attributes": {"title": "ZZ-TEST-focus"}}])

    assert len(spy_run) == 1
    argv = spy_run[0]
    assert argv[0] == thingskit.OPEN
    assert "-g" in argv, f"`open` sans -g active l'application : {argv}"
    assert argv[-1].startswith("things:///json?data=")


def test_url_open_activant_sur_demande_explicite(thingskit, spy_run):
    """L'échappatoire existe pour les commandes qui pilotent l'interface."""
    thingskit.url_open([{"type": "to-do", "attributes": {}}], background=False)

    assert "-g" not in spy_run[0]


def test_ensure_running_lance_things_en_arriere_plan(thingskit, monkeypatch, spy_run):
    monkeypatch.setattr(thingskit.time, "sleep", lambda *_: None)

    thingskit.ensure_running()

    # 1er appel : pgrep (fake renvoie returncode 0 → app déjà lancée)
    assert spy_run[0][0] == thingskit.PGREP
    assert len(spy_run) == 1


def test_ensure_running_app_absente_ouvre_en_arriere_plan(thingskit, monkeypatch):
    calls = []

    class _R:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""

    def fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return _R(1 if argv[0] == thingskit.PGREP else 0)

    monkeypatch.setattr(thingskit.subprocess, "run", fake_run)
    monkeypatch.setattr(thingskit.time, "sleep", lambda *_: None)

    thingskit.ensure_running()

    launch = [c for c in calls if c[0] == thingskit.OPEN]
    assert len(launch) == 1
    assert "-g" in launch[0], f"`open -a` sans -g vole le focus : {launch[0]}"
    assert "Things3" in launch[0]


def test_create_heading_conserve_son_passage_au_premier_plan(thingskit):
    """Non-goal explicite : create-heading DOIT activer l'application."""
    import inspect

    src = inspect.getsource(thingskit.cmd_create_heading)
    assert 'things:///show?id=' in src
    ligne = next(l for l in src.splitlines() if "things:///show?id=" in l)
    assert '"-g"' not in ligne and "'-g'" not in ligne, (
        "create-heading pilote le menu et a besoin du premier plan : "
        f"{ligne.strip()}"
    )
