"""BUG-016 — l'attente post-écriture est une boucle de relecture bornée.

Ce que ces tests gardent, dans l'ordre d'importance :

1. La vérification post-action reste ENTIÈRE. Un effet non constaté au
   plafond sort toujours en code retour non nul, avec un message qui dit
   « échec » et non « commande envoyée » (constitution § Zones sensibles 1).
   Le correctif rend l'attente adaptative ; il ne rend la vérification ni
   optionnelle, ni indulgente.
2. Un effet constaté APRÈS l'ancien plafond de 1500 ms est un succès. C'est
   le faux négatif du Défaut 2 : une suppression mesurée à 5026 ms le
   2026-08-25 sortait en échec alors que Things avait bien supprimé — un
   appelant qui réessaie produit alors un doublon dans la base réelle.
3. Aucune attente fixe ne subsiste sur un chemin d'écriture. Gardé par
   balayage de l'AST du script, pas par relecture humaine : c'est ce qui
   ferme la cause plutôt que les instances.

Aucun test d'ici ne dépend du temps réel : `thingskit.time` est remplacé par
une HORLOGE VIRTUELLE dont `sleep` avance un compteur au lieu d'attendre, et
qui peut faire « atterrir » l'effet en base à un instant virtuel choisi.
"""
from __future__ import annotations

import argparse
import ast
import sqlite3
from pathlib import Path

import pytest


# La queue mesurée le 2026-08-25 (Mac Studio, Things 3.23) : une suppression
# sur dix a mis 5026 ms à être constatée en base, contre 23 ms de médiane.
MEASURED_TAIL = 5.026
# L'ancien plafond fixe des commandes d'écriture, celui que cette queue
# dépassait.
OLD_FIXED_WAIT = 1.5

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "bin" / "thingskit"

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
    status INTEGER,
    notes TEXT
);
"""

TARGET = "AAAAAAAAAAAAAAAAAAAAAA"


class _InertResult:
    """Ce que rend un lancement de fils NEUTRALISÉ.

    Ces stubs rendaient `None` : ils décrivaient un `subprocess.run` dont
    personne ne lisait le retour — ce qui a cessé d'être vrai le 2026-08-27,
    `_spawn` lisant le code retour pour dire un échec sans citer l'argv. Un
    stub qui ne peut pas porter ce que le code lit n'est pas un stub, c'est
    un trou : il fait passer pour un défaut du code ce qui est un défaut de
    la doublure.
    """
    returncode = 0
    stdout = ""
    stderr = ""


def _inert_run(*a, **kw):
    return _InertResult()


def _make_db(tmp_path, rows):
    db_file = tmp_path / "main.sqlite"
    con = sqlite3.connect(db_file)
    con.executescript(SCHEMA)
    defaults = dict(
        uuid=None, title=None, type=0, trashed=0, project=None, heading=None,
        area=None, startDate=None, startBucket=None, deadline=None,
        reminderTime=None, status=0, notes=None,
    )
    for r in rows:
        row = {**defaults, **r}
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,project,heading,area,"
            "startDate,startBucket,deadline,reminderTime,status,notes) values "
            "(:uuid,:title,:type,:trashed,:project,:heading,:area,"
            ":startDate,:startBucket,:deadline,:reminderTime,:status,:notes)",
            row,
        )
    con.commit()
    con.close()
    return db_file


class Clock:
    """Horloge virtuelle : `sleep` avance le temps, personne n'attend.

    `on_tick` est rejouée après chaque avance — c'est par elle que l'effet
    « atterrit » en base à un instant virtuel donné, sans qu'aucun test ne
    dépende du temps réel.
    """

    def __init__(self, on_tick=None):
        self.elapsed = 0.0
        self.naps: list[float] = []
        self.on_tick = on_tick or (lambda: None)

    def sleep(self, seconds):
        self.naps.append(seconds)
        self.elapsed += seconds
        self.on_tick()


def _write(db_file, sql, args=()):
    con = sqlite3.connect(db_file)
    con.execute(sql, args)
    con.commit()
    con.close()


def _lands_at(clock, apply_effect, seconds):
    """Applique l'effet en base dès que l'horloge virtuelle atteint `seconds`."""
    state = {"done": False}

    def hook():
        if not state["done"] and clock.elapsed >= seconds:
            state["done"] = True
            apply_effect()

    return hook


@pytest.fixture
def deletion(thingskit, monkeypatch, tmp_path):
    """Un `delete-task` prêt à jouer, dont l'effet atterrit quand on le décide.

    Rend `(run, clock_holder)` : `run(lands_at=<secondes virtuelles ou None>)`
    exécute la commande et rend son code retour.
    """
    db_file = _make_db(tmp_path, [{"uuid": TARGET, "title": "Cible", "type": 0}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, ""))
    holder = {}

    def run(lands_at):
        def apply_effect():
            _write(db_file, "update TMTask set trashed=1 where uuid=?", (TARGET,))

        clock = Clock()
        if lands_at is not None:
            clock.on_tick = _lands_at(clock, apply_effect, lands_at)
            if lands_at <= 0:
                apply_effect()
        holder["clock"] = clock
        holder["db"] = db_file
        monkeypatch.setattr(thingskit, "time", clock)
        return thingskit.cmd_delete_task(argparse.Namespace(id=TARGET, title=None))

    return run, holder


# ---------------------------------------------------------------------------
# 1. La boucle elle-même
# ---------------------------------------------------------------------------
def test_the_probe_runs_before_any_wait(thingskit, monkeypatch):
    """L'effet déjà là ne coûte pas une seule milliseconde d'attente."""
    clock = Clock()
    monkeypatch.setattr(thingskit, "time", clock)
    calls = []

    assert thingskit.wait_for_effect(lambda: calls.append(1) or "constaté")
    assert clock.naps == []
    assert len(calls) == 1


def test_it_returns_as_soon_as_the_effect_is_observed(thingskit, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(thingskit, "time", clock)
    seen = {"n": 0}

    def probe():
        seen["n"] += 1
        return "constaté" if seen["n"] == 3 else None

    assert thingskit.wait_for_effect(probe) == "constaté"
    assert seen["n"] == 3
    assert len(clock.naps) == 2
    assert clock.elapsed == pytest.approx(2 * thingskit.POLL_INTERVAL)


def test_it_gives_up_at_the_cap_and_reports_the_absence(thingskit, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(thingskit, "time", clock)

    assert not thingskit.wait_for_effect(lambda: None, timeout=0.1, interval=0.025)
    assert clock.elapsed == pytest.approx(0.1)


def test_the_last_nap_never_overshoots_the_cap(thingskit, monkeypatch):
    """Un plafond non multiple de l'intervalle reste un plafond."""
    clock = Clock()
    monkeypatch.setattr(thingskit, "time", clock)

    thingskit.wait_for_effect(lambda: False, timeout=0.06, interval=0.025)

    assert clock.elapsed == pytest.approx(0.06)
    assert clock.naps[-1] == pytest.approx(0.01)


def test_a_transient_read_error_does_not_abbreviate_the_wait(thingskit, monkeypatch):
    """`SQLITE_BUSY` pendant que l'application écrit vaut « pas encore », pas « non ».

    Sonder 600 fois au lieu de lire une fois multiplie les occasions de tomber
    sur une lecture transitoirement refusée. La traiter comme un constat
    négatif définitif rendrait la boucle MOINS fiable que l'attente fixe.
    """
    clock = Clock()
    monkeypatch.setattr(thingskit, "time", clock)
    seen = {"n": 0}

    def probe():
        seen["n"] += 1
        if seen["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "constaté"

    assert thingskit.wait_for_effect(probe) == "constaté"


def test_a_persistent_read_error_is_never_read_as_an_observed_effect(thingskit, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(thingskit, "time", clock)

    def probe():
        raise sqlite3.OperationalError("unable to open database file")

    assert not thingskit.wait_for_effect(probe, timeout=0.05, interval=0.025)


def test_a_non_sqlite_exception_is_not_swallowed(thingskit, monkeypatch):
    """Le filet ne couvre que la lecture — un défaut de programmation ressort."""
    clock = Clock()
    monkeypatch.setattr(thingskit, "time", clock)

    def probe():
        raise ValueError("sonde mal écrite")

    with pytest.raises(ValueError):
        thingskit.wait_for_effect(probe)


@pytest.mark.parametrize("exc", [
    sqlite3.ProgrammingError("Incorrect number of bindings supplied"),
    sqlite3.InterfaceError("Error binding parameter 0"),
])
def test_a_sql_programming_defect_surfaces_at_once_instead_of_looping(
        thingskit, monkeypatch, exc):
    """Un SQL fautif n'est pas une lecture transitoirement refusée.

    `except sqlite3.Error` couvrait aussi `ProgrammingError` et
    `InterfaceError` — un binding manquant produisait donc 15 s de boucle
    muette, puis un message d'échec qui accusait l'application au lieu de
    nommer le défaut. Aucun faux succès possible, mais un diagnostic rendu
    impossible. Le filet ne couvre que ce qui est RÉELLEMENT transitoire.
    """
    clock = Clock()
    monkeypatch.setattr(thingskit, "time", clock)

    def probe():
        raise exc

    with pytest.raises(type(exc)):
        thingskit.wait_for_effect(probe)
    assert clock.elapsed == 0.0, "la boucle a tourné sur un défaut de programmation"


# ---------------------------------------------------------------------------
# 2. Les constantes, au regard de la mesure (AC-6)
# ---------------------------------------------------------------------------
def test_the_cap_sits_well_above_the_measured_tail(thingskit):
    """5026 ms est un échantillon UNIQUE : la marge se prend du côté qui ne
    casse rien. Un plafond trop bas se paie en doublon dans la base réelle de
    l'utilisateur ; un plafond trop haut ne coûte que de la latence, et
    seulement sur le chemin déjà défaillant."""
    # Deux clauses, pas une : le PRINCIPE (le plafond couvre une queue deux fois
    # pire que tout ce qui a été observé) et la VALEUR arbitrée, épinglée pour
    # qu'elle ne redescende pas en silence.
    assert thingskit.WRITE_TIMEOUT >= 2 * MEASURED_TAIL
    assert thingskit.WRITE_TIMEOUT >= 15.0


def test_the_poll_interval_is_the_one_the_measure_was_taken_with(thingskit):
    """Les médianes de 11-23 ms ont été mesurées par sondage à 25 ms : c'est le
    seul intervalle pour lequel elles valent directement."""
    assert thingskit.POLL_INTERVAL <= 0.025


def test_the_launch_wait_does_not_worsen_the_previous_worst_case(thingskit):
    assert thingskit.LAUNCH_TIMEOUT <= 6.0


# ---------------------------------------------------------------------------
# 3. Sur une vraie commande d'écriture — zone sensible 1
# ---------------------------------------------------------------------------
def test_an_effect_observed_after_the_old_1500ms_cap_is_a_success(deletion):
    """Le cœur du Défaut 2 : 3000 ms > 1500 ms, et c'est un SUCCÈS."""
    run, holder = deletion

    rc = run(lands_at=3.0)

    assert rc == 0
    assert holder["clock"].elapsed > OLD_FIXED_WAIT
    assert holder["clock"].elapsed == pytest.approx(3.0, abs=0.03)


def test_the_measured_5026ms_tail_no_longer_produces_a_false_failure(deletion):
    """La queue réellement mesurée le 2026-08-25, rejouée à l'identique."""
    run, holder = deletion

    rc = run(lands_at=MEASURED_TAIL)

    assert rc == 0
    assert holder["clock"].elapsed == pytest.approx(MEASURED_TAIL, abs=0.03)


def test_the_wait_is_not_paid_when_the_effect_is_already_there(deletion):
    """Défaut 1 : le cas courant ne paie plus 1500 ms fixes."""
    run, holder = deletion

    rc = run(lands_at=0)

    assert rc == 0
    assert holder["clock"].naps == []


def test_an_effect_that_never_lands_still_fails_at_the_cap(deletion, thingskit, capsys):
    """La vérification post-action est intacte : pas de constat, pas de 0."""
    run, holder = deletion

    rc = run(lands_at=None)

    assert rc != 0
    assert holder["clock"].elapsed == pytest.approx(thingskit.WRITE_TIMEOUT)
    captured = capsys.readouterr()
    assert "ÉCHEC" in captured.err
    assert "Corbeille" not in captured.out


def test_an_effect_that_lands_just_after_the_cap_is_a_failure_not_a_success(
        deletion, thingskit):
    """Un dépassement de plafond ne se transforme jamais en succès."""
    run, _ = deletion

    rc = run(lands_at=thingskit.WRITE_TIMEOUT + 0.5)

    assert rc != 0


def test_waiting_never_writes_a_single_byte_to_the_database(deletion):
    """Invariant de zone sensible : aucune requête autre que `select`.

    La boucle multiplie les accès à la base — 600 lectures là où il y en avait
    une. Le fichier doit en ressortir rigoureusement inchangé, octet pour
    octet, y compris quand la boucle va jusqu'au plafond.
    """
    run, holder = deletion
    run(lands_at=0)  # amorce : renseigne holder["db"]
    db = Path(holder["db"])
    before = db.read_bytes()

    run(lands_at=None)

    assert db.read_bytes() == before


@pytest.mark.parametrize("lands_at", [0, 0.5, MEASURED_TAIL])
def test_a_success_is_always_a_constat_never_a_commande_envoyee(deletion, lands_at):
    """Quel que soit l'instant d'atterrissage, `0` implique l'effet en base."""
    run, holder = deletion

    rc = run(lands_at=lands_at)

    con = sqlite3.connect(holder["db"])
    trashed = con.execute("select trashed from TMTask where uuid=?", (TARGET,)).fetchone()[0]
    con.close()
    assert (rc == 0) and trashed == 1


# ---------------------------------------------------------------------------
# 4. Les autres commandes d'écriture échouent toujours sans constat
# ---------------------------------------------------------------------------
def _rig(thingskit, monkeypatch, tmp_path, rows):
    db_file = _make_db(tmp_path, rows)
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, ""))
    monkeypatch.setattr(thingskit, "time", Clock())
    return db_file


WRITE_CASES = {
    "complete-task": lambda t: t.cmd_complete_task(argparse.Namespace(id=TARGET, title=None)),
    "cancel-task": lambda t: t.cmd_cancel_task(argparse.Namespace(id=TARGET, title=None)),
    "rename-task": lambda t: t.cmd_rename_task(
        argparse.Namespace(id=TARGET, title=None, new_title="Nouveau")),
    "delete-task": lambda t: t.cmd_delete_task(argparse.Namespace(id=TARGET, title=None)),
    "set-notes": lambda t: t.cmd_set_notes(
        argparse.Namespace(project=None, project_id=None, task=None,
                           task_id=TARGET, notes="du texte")),
}


@pytest.mark.parametrize("name", sorted(WRITE_CASES))
def test_every_write_command_still_fails_when_its_effect_is_never_observed(
        name, thingskit, monkeypatch, tmp_path, capsys):
    _rig(thingskit, monkeypatch, tmp_path,
         [{"uuid": TARGET, "title": "Cible", "type": 0}])

    rc = WRITE_CASES[name](thingskit)

    assert rc != 0, f"{name} rend 0 sans avoir constaté son effet"
    assert "ÉCHEC" in capsys.readouterr().err


def test_setting_an_empty_note_succeeds_when_the_empty_value_is_observed(
        thingskit, monkeypatch, tmp_path):
    """Piège de véracité : `""` est une valeur légitime ET falsy.

    Une boucle qui sonde « la valeur relue » au lieu de « la valeur relue est
    celle demandée » attendrait le plafond puis échouerait sur un effacement
    de notes pourtant réussi — un faux négatif introduit par le correctif
    lui-même.
    """
    _rig(thingskit, monkeypatch, tmp_path,
         [{"uuid": TARGET, "title": "Cible", "type": 0, "notes": ""}])

    rc = thingskit.cmd_set_notes(
        argparse.Namespace(project=None, project_id=None, task=None,
                           task_id=TARGET, notes=""))

    assert rc == 0


# ---------------------------------------------------------------------------
# 5. `ensure_running` — condition observée, pas durée devinée
# ---------------------------------------------------------------------------
def test_ensure_running_stops_as_soon_as_the_process_appears(thingskit, monkeypatch):
    calls = []
    seen = {"n": 0}

    class _R:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = self.stderr = ""

    def fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        if argv[0] == thingskit.PGREP:
            seen["n"] += 1
            return _R(0 if seen["n"] >= 3 else 1)
        return _R(0)

    clock = Clock()
    monkeypatch.setattr(thingskit.subprocess, "run", fake_run)
    monkeypatch.setattr(thingskit, "time", clock)

    thingskit.ensure_running()

    # 3 sondages : la garde d'entrée, puis le premier tour de boucle (avant
    # toute attente), puis celui qui constate. Donc UNE seule sieste.
    assert seen["n"] == 3
    assert clock.elapsed == pytest.approx(thingskit.LAUNCH_POLL_INTERVAL)
    assert clock.elapsed < 6.0, "l'attente fixe de 6 s n'a pas été remplacée"
    assert len([c for c in calls if c[0] == thingskit.OPEN]) == 1


def test_ensure_running_relaunches_the_application_exactly_once(thingskit, monkeypatch):
    """La boucle re-sonde, elle ne relance pas : deux `open` ouvriraient deux
    fenêtres et brouilleraient le diagnostic."""
    calls = []

    class _R:
        returncode = 1
        stdout = stderr = ""

    def fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return _R()

    monkeypatch.setattr(thingskit.subprocess, "run", fake_run)
    monkeypatch.setattr(thingskit, "time", Clock())

    thingskit.ensure_running()

    assert len([c for c in calls if c[0] == thingskit.OPEN]) == 1


# ---------------------------------------------------------------------------
# 6. `create-heading` — l'AFFICHAGE aussi est une condition observée
# ---------------------------------------------------------------------------
# Le clic « Fichier > Nouvel en-tête » s'applique à la fenêtre au premier
# plan. Rien ne garantissait qu'elle affiche le projet visé : on envoyait
# `things:///show?id=<uuid>` puis on attendait une durée devinée.
#
# Sur `master` cette durée valait 6 s (`ensure_running`) + 1 s. Depuis que le
# lancement est CONSTATÉ, il ne restait que la seconde. Mesuré le 2026-08-25
# (Mac Studio, Things 3.23, 3 démarrages à froid, application quittée entre
# chaque) :
#
#   `pgrep -x Things3` matche à                57, 61, 62 ms
#   la fenêtre affiche enfin le projet à      795, 846, 970 ms
#   marge résiduelle avant le clic            ~130 à ~305 ms  (~6,9 s sur master)
#
# La vue n'a AUCUNE trace en base : il n'y a rien à relire. Elle est en
# revanche OBSERVABLE par la surface applicative — `name of window 1` rend le
# nom de la liste affichée (mesuré : « Aujourd'hui » avant l'URL, le titre du
# projet après). Les deux propriétés cachées du dictionnaire, `current list
# url` et `current list name`, rendent `missing value` sur cette version :
# mesurées puis écartées, pas déduites.
HEADING_PROJECT = "PPPPPPPPPPPPPPPPPPPPPP"
# La durée devinée que ce correctif remplace, conservée comme repère de test.
OLD_HEADING_SETTLE = 1.0
# Instant virtuel auquel la vue « atterrit » dans les tests de démarrage à
# froid — choisi au-delà de OLD_HEADING_SETTLE pour que l'ancienne durée
# devinée ne puisse pas faire passer le test par accident.
COLD_VIEW_LANDING = 2.5


def _heading_rig(thingskit, monkeypatch, tmp_path, shown, land_heading=True):
    """`create-heading` prêt à jouer sur horloge virtuelle.

    `shown(clock)` rend le nom de la liste que Things AFFICHE à l'instant
    virtuel courant. Aucun test d'ici ne dépend du temps réel ni du lancement
    effectif de l'application.
    """
    db_file = _make_db(tmp_path, [
        {"uuid": HEADING_PROJECT, "title": "Projet A", "type": 1}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit.subprocess, "run", _inert_run)
    clock = Clock()
    monkeypatch.setattr(thingskit, "time", clock)
    ui = {"clicked_at": None, "scripts": [], "probes": 0}

    def _fake_osa(script):
        ui["scripts"].append(script)
        if "keystroke" in script:
            ui["clicked_at"] = clock.elapsed
            if land_heading:
                _write(db_file,
                       "insert into TMTask (uuid,title,type,trashed,project) "
                       "values ('H9','Section',2,0,?)", (HEADING_PROJECT,))
            return 0, "OK"
        ui["probes"] += 1
        name = shown(clock)
        if name == "Projet A":
            return 0, "OK"
        return 1, (f"execution error: {thingskit._WRONG_VIEW_MARKER}: "
                   f"{name} (-2700)")

    monkeypatch.setattr(thingskit, "osa", _fake_osa)
    return clock, ui


def _run_heading(thingskit):
    return thingskit.cmd_create_heading(
        argparse.Namespace(title="Section", project="Projet A"))


def _typed(ui):
    return [s for s in ui["scripts"] if "keystroke" in s]


def test_the_ui_is_not_driven_before_the_target_view_is_actually_displayed(
        thingskit, monkeypatch, tmp_path):
    """Démarrage à froid : la vue n'affiche le projet qu'après l'ancienne durée.

    C'est le scénario que le correctif BUG-016 avait rendu possible en
    remplaçant les 6 s de `ensure_running` par un `pgrep` qui rend la main en
    ~60 ms : il ne restait qu'une seconde devinée entre l'URL d'affichage et
    le clic de menu.
    """
    clock, ui = _heading_rig(
        thingskit, monkeypatch, tmp_path,
        shown=lambda c: "Projet A" if c.elapsed >= COLD_VIEW_LANDING else "Aujourd’hui")

    rc = _run_heading(thingskit)

    assert rc == 0
    assert len(_typed(ui)) == 1
    assert ui["clicked_at"] >= COLD_VIEW_LANDING
    assert ui["clicked_at"] > OLD_HEADING_SETTLE, (
        "le clic est parti dans la fenêtre que l'ancienne durée devinée "
        "laissait ouverte")


def test_nothing_is_clicked_or_typed_when_the_target_view_never_appears(
        thingskit, monkeypatch, tmp_path, capsys):
    """Le pire cas de la zone sensible 2 : taper le titre au mauvais endroit.

    Le refus doit précéder le clic — un en-tête parasite créé dans un autre
    projet est un effet de bord irréversible que la vérification post-action
    constate sans pouvoir le défaire, et que l'appelant duplique en
    réessayant.
    """
    clock, ui = _heading_rig(thingskit, monkeypatch, tmp_path,
                             shown=lambda c: "Aujourd’hui")

    rc = _run_heading(thingskit)

    assert rc != 0
    assert _typed(ui) == [], "l'automatisation a été pilotée sans vue constatée"
    assert ui["clicked_at"] is None
    assert clock.elapsed == pytest.approx(thingskit.HEADING_VIEW_TIMEOUT)
    err = capsys.readouterr().err
    assert "Projet A" in err and "Aujourd’hui" in err


def test_nothing_is_typed_when_another_project_is_displayed(
        thingskit, monkeypatch, tmp_path, capsys):
    """La vue restaurée au lancement est souvent un AUTRE projet, pas une liste."""
    clock, ui = _heading_rig(thingskit, monkeypatch, tmp_path,
                             shown=lambda c: "Projet B")

    rc = _run_heading(thingskit)

    assert rc != 0
    assert _typed(ui) == []
    assert "Projet B" in capsys.readouterr().err


def test_a_probe_that_cannot_be_answered_never_counts_as_a_shown_view(
        thingskit, monkeypatch, tmp_path):
    """Fail-closed : une sonde en erreur vaut « pas encore », jamais « oui »."""
    clock, ui = _heading_rig(thingskit, monkeypatch, tmp_path,
                             shown=lambda c: None)

    rc = _run_heading(thingskit)

    assert rc != 0
    assert _typed(ui) == []


def test_no_wait_is_paid_when_the_view_already_shows_the_target(
        thingskit, monkeypatch, tmp_path):
    """BUG-016 n'est pas défait : la condition déjà remplie ne coûte rien."""
    clock, ui = _heading_rig(thingskit, monkeypatch, tmp_path,
                             shown=lambda c: "Projet A")

    rc = _run_heading(thingskit)

    assert rc == 0
    assert clock.naps == []
    assert ui["clicked_at"] == 0.0


def test_the_view_probe_is_bounded_on_the_applescript_side(thingskit):
    """Un AppleEvent adressé à une application EN COURS DE LANCEMENT BLOQUE.

    Établi sur pièce le 2026-08-25, 3 démarrages à froid : il ne rend pas
    `-600`, il attend d'être servi — 699, 753 et 854 ms mesurés, rc=0. Sans
    borne côté AppleScript, l'attente ne serait donc plus bornée par le
    plafond de la boucle mais par le délai d'AppleEvent par défaut
    d'`osascript` (120 s), et le plafond ne voudrait plus rien dire.
    """
    script = thingskit._build_view_probe_script("Projet A")
    assert f"with timeout of {thingskit.HEADING_VIEW_PROBE_TIMEOUT:d} seconds" in script
    assert "end timeout" in script


def test_the_displayed_project_is_checked_again_inside_the_ui_script(thingskit):
    """La sonde précède le script ; le script re-décide au moment du clic.

    Sans ce second contrôle, la fenêtre pourrait changer entre la sonde et le
    clic — et le trou existe aussi quand Things tourne DÉJÀ, cas où aucune
    attente n'a jamais été payée.
    """
    script = thingskit._build_heading_script("Section", "Projet A")
    assert thingskit._WRONG_VIEW_MARKER in script
    assert (script.index(thingskit._WRONG_VIEW_MARKER)
            < script.index("click menu item targetLabel")
            < script.index("keystroke"))


@pytest.mark.parametrize("builder", ["_build_view_probe_script", "_build_heading_script"])
def test_the_view_comparison_is_case_and_diacritics_sensitive(thingskit, builder):
    """AppleScript compare les chaînes SANS casse ni diacritiques par défaut.

    Mesuré le 2026-08-25 : `"AUJOURDHUI" is "aujourdhui"` rend `true`. Sans
    `considering`, deux projets ne différant que par la casse ou un accent
    seraient confondus — et le titre irait dans le mauvais.
    """
    fn = getattr(thingskit, builder)
    script = fn("Projet A") if builder == "_build_view_probe_script" else fn("Section", "Projet A")
    assert "considering case and diacriticals" in script
    assert "end considering" in script


def test_the_project_title_is_escaped_in_the_view_comparison(thingskit):
    script = thingskit._build_view_probe_script('Projet "cité"')
    assert '\\"cité\\"' in script


# ---------------------------------------------------------------------------
# 7. Fermeture de la cause — balayage de l'AST (règle 12)
# ---------------------------------------------------------------------------
# Le balayage des attentes ne reconnaissait qu'UNE forme, `time.sleep(...)`
# en attribut du nom `time` : `from time import sleep` et `import time as t`
# lui échappaient entièrement. Il promettait donc plus qu'il ne vérifiait.
# Il lit désormais les IMPORTS pour établir par quels noms `sleep` est
# atteignable, et refuse tout import qu'il ne connaît pas — même discipline
# que la liste blanche de `tests/test_fast_path_partition.py` : une nouvelle
# surface d'attente (`asyncio.sleep`, `anyio.sleep`) force à relire cette
# garde au lieu de passer inaperçue.

# Imports de `bin/thingskit` relus le 2026-08-25 : aucun ne porte de
# primitive d'attente autre que `time.sleep`.
_REVIEWED_IMPORTS = frozenset({
    "__future__", "argparse", "datetime", "json", "re", "sqlite3",
    "subprocess", "sys", "time", "unicodedata", "urllib", "pathlib",
})


def _script_source() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _imported_roots(tree):
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                roots.add(al.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _sleep_bindings(tree):
    """Noms par lesquels `time.sleep` est atteignable, quelle que soit la forme.

    Rend `(alias de module, noms liés directement à sleep)`.
    """
    modules, direct = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                if al.name == "time" or al.name.startswith("time."):
                    modules.add((al.asname or al.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module == "time":
            for al in node.names:
                if al.name == "sleep":
                    direct.add(al.asname or "sleep")
                elif al.name == "*":
                    direct.add("sleep")
    return modules, direct


def _sleep_sites(source=None):
    """Chaque attente fixe du source, avec sa fonction englobante."""
    tree = ast.parse(source if source is not None else _script_source())
    modules, direct = _sleep_bindings(tree)
    sites = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):
            f = node.func
            hit = (
                (isinstance(f, ast.Attribute) and f.attr == "sleep"
                 and isinstance(f.value, ast.Name) and f.value.id in modules)
                or (isinstance(f, ast.Name) and f.id in direct)
            )
            if hit:
                sites.append((self.stack[-1] if self.stack else "<module>",
                              node.args[0] if node.args else None,
                              node.lineno))
            self.generic_visit(node)

    V().visit(tree)
    return sites


def test_no_fixed_wait_remains_on_any_write_path():
    """Aucune attente fixe ne subsiste, et il n'y a PLUS d'exemption.

    Le seul site légitime est l'intervalle de sondage de `wait_for_effect`,
    qui EST la boucle. L'exemption du délai d'affichage de `create-heading`
    a disparu avec la durée qu'elle couvrait : cette condition-là est
    désormais observée elle aussi (§ 6).
    """
    offenders = [f"{func} (ligne {lineno})"
                 for func, _arg, lineno in _sleep_sites()
                 if func != "wait_for_effect"]
    assert offenders == [], (
        "attente fixe résiduelle sur un chemin d'écriture : " + ", ".join(offenders))


def test_the_guessed_display_wait_no_longer_exists(thingskit):
    """Sa disparition est le fait, pas sa non-utilisation."""
    assert not hasattr(thingskit, "HEADING_UI_SETTLE")


_CONTROL_SLEEP_FORMS = '''
import time as t
from time import sleep
from time import sleep as nap

def cmd_alias(a):
    t.sleep(1)

def cmd_direct(a):
    sleep(2)

def cmd_renamed(a):
    nap(3)

def cmd_innocent(a):
    return sorted([3, 1])
'''


def test_the_sweep_sees_every_form_by_which_sleep_can_be_reached():
    """Contre-épreuve : la garde doit FLAGUER ce qu'elle prétend couvrir.

    Aucune de ces formes n'est employée aujourd'hui par `bin/thingskit` —
    c'est précisément pour ça qu'un balayage qui ne les voit pas passe pour
    vert : il ne promet quelque chose que sur la forme qu'il sait lire.
    """
    flagged = {func for func, _arg, _ln in _sleep_sites(_CONTROL_SLEEP_FORMS)}
    assert flagged == {"cmd_alias", "cmd_direct", "cmd_renamed"}


def test_the_script_imports_stay_within_the_reviewed_set():
    """Un nouvel import peut porter une primitive d'attente que rien ne lit."""
    unknown = _imported_roots(ast.parse(_script_source())) - _REVIEWED_IMPORTS
    assert unknown == set(), (
        f"imports non relus : {sorted(unknown)} — vérifier qu'aucun ne porte "
        "de primitive d'attente avant de l'ajouter à _REVIEWED_IMPORTS")


# --- qui doit attendre : dérivé de l'AST, plus tenu à la main ---------------
#
# La liste en dur qui tenait ce rôle ne couvrait que les commandes qu'on avait
# pensé à y inscrire : une nouvelle commande d'écriture qui n'attendrait RIEN
# du tout échappait aux deux gardes, faute d'y figurer. L'obligation se
# dérive donc de ce que la fonction fait réellement — solliciter
# l'application —, jamais de son nom.

# Les deux fonctions par lesquelles toute sollicitation passe (constitution
# § Conventions : « `osa()` et `time.sleep` sont les points d'injection »).
# Elles SONT l'invocation, donc elles ne peuvent pas attendre leur propre
# effet. L'exemption est nominative faute de discriminant structurel —
# `ensure_running` invoque `open` exactement de la même façon — et une
# contre-épreuve vérifie qu'elle ne s'étend pas à une troisième fonction.
_INJECTION_POINTS = frozenset({"osa", "url_open"})
# MÊME objet, pas un second littéral : les fonctions par lesquelles on ATTEINT
# l'application sont exactement celles qu'on EXEMPTE de l'obligation d'attendre
# — elles SONT l'invocation. Deux littéraux identiques mais non liés pouvaient
# diverger en silence à la première fonction ajoutée à l'un des deux.
_APP_HELPERS = _INJECTION_POINTS
_APP_CONSTANTS = frozenset({"OPEN", "OSASCRIPT"})

# Plancher de la dérivation, SERRÉ sur le compte réel — une marge le rendrait
# aveugle à ce qu'il est censé voir : la disparition d'une commande d'écriture.
# Il ne se devine pas, il se MESURE, et la commande est celle-ci :
#
#     python3 -c "import importlib.util,sys; sys.path.insert(0,'.'); \
#       s=importlib.util.spec_from_file_location('t','tests/test_write_wait.py'); \
#       m=importlib.util.module_from_spec(s); s.loader.exec_module(m); \
#       print(len(m._reaching_and_waiting()[0]))"
#
# 13 le 2026-08-25 (BUG-016) ; 14 le 2026-08-26, `cmd_move_project` ajoutant
# une quatorzième fonction qui sollicite l'application (BUG-032). Relever ce
# plancher fait PARTIE de l'ajout d'une commande d'écriture : sans cela la
# garde reste verte en ayant cessé de protéger.
#
# ⚠️ PIÈGE DE FUSION : si une AUTRE branche ajoute elle aussi une commande
# d'écriture et relève cette même ligne à la même valeur (`move-project` et
# `reopen-task` l'ont chacune relevée à 14, indépendamment), git fusionne
# les deux lignes identiques SANS signaler de conflit — alors que le compte
# réel après les deux fusions a augmenté de DEUX, pas d'une. Cette valeur ne
# se reprend JAMAIS d'une résolution de conflit : elle se REMESURE, par la
# commande inscrite juste au-dessus.
#
# Mesuré le 2026-08-26 sur la fusion séquentielle de `feat/move-project` +
# `feat/reopen-task` dans `master`, dans les DEUX résolutions possibles du
# `@pytest.mark.parametrize` de
# `test_the_derivation_floor_notices_a_command_that_disappeared` (seul autre
# site en conflit non cosmétique — il énumère nommément les commandes
# récentes) :
#
#   - résolution CORRECTE (planchers remesurés à 15/16, parametrize portant
#     l'UNION des quatre noms `cmd_move_task`, `cmd_create_heading`,
#     `cmd_move_project`, `cmd_reopen_task`) :
#         735 collectés — 1 failed, 733 passed, 1 skipped
#     l'unique échec est celui, préexistant et sans rapport, de `master`
#     (`test_invocation_through_the_bundle_launcher_is_let_through`).
#   - résolution NAÏVE (planchers laissés à 14/15, même parametrize à
#     quatre noms) :
#         735 collectés — 5 failed, 729 passed, 1 skipped
#     les 4 échecs supplémentaires sont les 4 cas PARAMÉTRÉS de
#     `test_the_derivation_floor_notices_a_command_that_disappeared`.
#
# Trois choses à en retenir :
#   1. la résolution du parametrize ne change que le NOMBRE de cas qui
#      crient — 4 avec l'union des noms, 3 si l'on n'en garde que trois —
#      jamais leur existence : `_source_without(X)` fait tomber `reaching`
#      de 15 à 14 quel que soit `X`, et `14 < 14` est faux pour tout `X`.
#      Le signal ne disparaît donc pas avec une résolution partielle ; il
#      perd un cas sur quatre ;
#   2. l'assertion PRINCIPALE, `assert len(reaching) >= _MIN_REACHING`,
#      reste VRAIE et silencieuse dans les DEUX résolutions — ce n'est
#      jamais elle qui protège ici, c'est un test paramétré distinct, qui
#      dépend lui-même d'avoir été résolu correctement ;
#   3. `_MIN_FAILURE_BRANCHES` (ci-dessous) n'a AUCUN test équivalent — pour
#      elle, la résolution naïve ne produit STRICTEMENT AUCUN symptôme,
#      dans aucune des deux résolutions du parametrize.
#
# Planchers réels mesurés par la commande ci-dessus : 15 (`_MIN_REACHING`)
# et 16 (`_MIN_FAILURE_BRANCHES`, mesurée par `_rereads_in_failure_branch()[1]`).
_MIN_REACHING = 15


def _module_functions(tree):
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _names_used(node):
    """Appels et noms du sous-arbre, fonctions imbriquées comprises."""
    called, named = set(), set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                called.add(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                called.add(n.func.attr)
        elif isinstance(n, ast.Name):
            named.add(n.id)
    return called, named


def _reaching_and_waiting(source=None):
    """`({fonction: par quoi elle atteint l'application}, {fonctions qui attendent})`."""
    tree = ast.parse(source if source is not None else _script_source())
    reaching, waiting = {}, set()
    for name, node in _module_functions(tree).items():
        called, named = _names_used(node)
        by = sorted((called & _APP_HELPERS) | (named & _APP_CONSTANTS))
        if by and name not in _INJECTION_POINTS:
            reaching[name] = by
        if "wait_for_effect" in called:
            waiting.add(name)
    return reaching, waiting


def test_every_function_that_reaches_the_application_waits_through_the_loop():
    reaching, waiting = _reaching_and_waiting()
    offenders = {n: by for n, by in reaching.items() if n not in waiting}
    assert offenders == {}, (
        f"sollicite l'application sans attendre par la boucle bornée : {offenders}")
    # Une dérivation devenue aveugle doit ÉCHOUER, jamais se taire : sans ce
    # plancher, un balayage qui ne verrait plus rien passerait pour vert.
    assert len(reaching) >= _MIN_REACHING, f"dérivation suspecte : {sorted(reaching)}"


_CONTROL_UNWAITING = '''
def cmd_ghost(a):
    rc, out = osa("tell application \\"Things3\\" to do something")
    return 0
'''

_CONTROL_BARE_SUBPROCESS = '''
def cmd_spawn(a):
    subprocess.run([OPEN, "things:///json?data=[]"], check=False)
    return 0
'''


@pytest.mark.parametrize("source,name", [
    (_CONTROL_UNWAITING, "cmd_ghost"),
    (_CONTROL_BARE_SUBPROCESS, "cmd_spawn"),
])
def test_the_derivation_flags_a_command_that_reaches_without_waiting(source, name):
    """Contre-épreuve, dans les deux formes : par `osa()` et par `subprocess`.

    La seconde vérifie aussi que l'exemption des points d'injection ne
    s'étend pas à une troisième fonction qui invoquerait `open` elle-même.
    """
    reaching, waiting = _reaching_and_waiting(source)
    assert name in reaching
    assert name not in waiting


def test_a_command_that_delegates_its_write_hands_the_wait_to_a_direct_waiter():
    """`append-notes` n'attend pas lui-même : il relit, concatène et délègue.

    Dérivé lui aussi — la délégation se constate sur le graphe d'appel, elle
    n'est plus déclarée à la main.
    """
    tree = ast.parse(_script_source())
    funcs = _module_functions(tree)
    reaching, waiting = _reaching_and_waiting()
    delegating = {}
    for name, node in funcs.items():
        if not name.startswith("cmd_") or name in reaching:
            continue
        called, _named = _names_used(node)
        helpers = called & set(reaching)
        if helpers:
            delegating[name] = helpers
    assert delegating, "aucune commande déléguante — la dérivation ne voit plus rien"
    for name, helpers in delegating.items():
        assert helpers <= waiting, (
            f"{name} délègue son écriture à {sorted(helpers - waiting)}, "
            "qui n'attend pas par la boucle bornée")


def test_no_call_site_throws_away_what_the_bounded_loop_observed():
    """Une attente dont on jette le retour n'attend pas : elle temporise.

    Le site qui le faisait — `cmd_move_task` — recomposait ensuite son verdict
    à côté, en dupliquant le prédicat que la sonde venait d'évaluer. Deux
    copies divergent : celle qui attend cesse de décrire celle qui juge.
    Balayé sur la CLASSE, pas sur l'instance signalée — 1 site sur 14 au
    2026-08-25.
    """
    tree = ast.parse(_script_source())
    discarded = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "wait_for_effect"
    ]
    assert discarded == [], (
        f"retour de wait_for_effect ignoré aux lignes {discarded}")


_CONTROL_DISCARDED_WAIT = '''
def cmd_temporise(a):
    wait_for_effect(lambda: False)
    return 0
'''


def test_the_discarded_wait_guard_flags_a_control_site():
    """Contre-épreuve : la garde doit voir la forme qu'elle prétend interdire."""
    tree = ast.parse(_CONTROL_DISCARDED_WAIT)
    discarded = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "wait_for_effect"
    ]
    assert discarded, "la garde ne voit pas un retour jeté"


def test_the_two_names_for_the_injection_points_cannot_diverge():
    """`_INJECTION_POINTS` (ce qui est EXEMPTÉ) et `_APP_HELPERS` (ce par quoi
    on ATTEINT l'application) portaient le même littéral sans lien : ajouter
    une fonction à l'une sans l'autre passait inaperçu, et la dérivation se
    mettait soit à exempter ce qu'elle devait compter, soit l'inverse."""
    assert _APP_HELPERS is _INJECTION_POINTS


def _source_without(function_name, source=None):
    """Le script privé d'UNE fonction — mutation de contre-épreuve."""
    src = source if source is not None else _script_source()
    lines = src.splitlines(keepends=True)
    for n in ast.parse(src).body:
        if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == function_name):
            return "".join(lines[:n.lineno - 1] + lines[n.end_lineno:])
    raise AssertionError(f"{function_name} introuvable dans le script")


@pytest.mark.parametrize("disappeared",
                         ["cmd_move_task", "cmd_create_heading", "cmd_move_project",
                          "cmd_reopen_task"])
def test_the_derivation_floor_notices_a_command_that_disappeared(disappeared):
    """Le plancher n'a de valeur que s'il est SERRÉ : à une marge près, la
    disparition d'une commande d'écriture passait sous le seuil sans rien
    déclencher."""
    reaching, _ = _reaching_and_waiting(_source_without(disappeared))

    assert disappeared not in reaching
    assert len(reaching) < _MIN_REACHING, (
        f"le plancher ne voit pas la disparition de {disappeared}")


# --- la branche d'échec ne relit pas la base -------------------------------
#
# Seconde classe, distincte de la précédente : le retour de la boucle est bien
# lu, mais le message qu'on compose ensuite REDEMANDE à la base ce que la sonde
# venait d'observer. Entre les deux, l'effet peut atterrir — le message affirme
# alors un échec en citant la valeur attendue, et fait douter du code retour,
# qui lui est juste. Six sites étaient en défaut au 2026-08-25 (`cmd_move_task`,
# `cmd_reschedule_task`, puis `_write_task_notes`, `cmd_complete_task`,
# `cmd_rename_task`, `cmd_cancel_task`) ; la garde remplace le balayage humain
# qui n'en avait vu que deux.

def _scoped_functions(tree):
    """Toute fonction du script, IMBRIQUÉES COMPRISES, avec les noms de
    fonction visibles depuis son corps.

    Rend `{chemin qualifié: (nœud, {nom appelable: chemin qualifié})}` — le
    module lui-même porte le chemin vide.

    Le collecteur qui tenait ce rôle ne lisait que `tree.body`. Il ignorait
    donc les définitions imbriquées, c'est-à-dire exactement la forme qu'avaient
    `cmd_move_task` et `cmd_reschedule_task` avant leur correction : leur
    branche d'échec appelait `_move_problem()` / `_schedule_problems()`,
    définies DANS le corps de la commande. Les deux commandes portent encore un
    helper imbriqué — la voie de régression était ouverte, pas hypothétique.

    La résolution passe par la CHAÎNE DE PORTÉES, pas par un index plat de
    noms : deux helpers homonymes dans deux commandes distinctes sont deux
    fonctions distinctes, et une table plate les confondrait en silence.
    """
    scoped = {}

    def defs_of(node, path):
        return {n.name: path + (n.name,) for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def walk(node, path, enclosing):
        scope = dict(enclosing)
        scope.update(defs_of(node, path))
        scoped[path] = (node, scope)
        for n in node.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(n, path + (n.name,), scope)

    walk(tree, (), {})
    return scoped


def _statements_owned_by(node):
    """Les nœuds du corps de `node`, sans descendre dans ses fonctions
    imbriquées : leur corps appartient à LEUR portée, pas à celle-ci.

    Sans cette frontière, une branche d'échec vivant dans un helper imbriqué
    serait comptée deux fois par le plancher de la dérivation.
    """
    stack = list(node.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield n
        stack.extend(ast.iter_child_nodes(n))


def _called_names(node):
    """Noms appelés dans le corps PROPRE de `node`, ses imbriquées exclues."""
    return {n.func.id for n in _statements_owned_by(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def _db_reading_functions(tree, scoped=None):
    """`q` et sa clôture transitive : tout ce par quoi le script LIT la base.

    Rend des CHEMINS qualifiés, pas des noms — un helper imbriqué n'est
    atteignable que depuis la portée qui le définit.

    Transitive, parce que le défaut se cache exactement là : `_write_task_notes`
    n'appelait pas `q`, il appelait `_read_task_notes`. Une garde qui ne
    connaîtrait que `q` aurait déclaré le site sain.
    """
    scoped = scoped if scoped is not None else _scoped_functions(tree)
    seed = scoped[()][1].get("q")
    readers = {seed} if seed is not None else set()
    grew = True
    while grew:
        grew = False
        for path, (node, scope) in scoped.items():
            if not path or path in readers:
                continue
            if any(scope.get(name) in readers for name in _called_names(node)):
                readers.add(path)
                grew = True
    return readers


def _rereads_in_failure_branch(source=None):
    """`(sites en défaut, nombre de branches d'échec inspectées)`.

    Le second terme est le plancher de la dérivation : une garde qui ne
    trouverait plus aucune branche à inspecter doit ÉCHOUER, pas se taire.
    """
    tree = ast.parse(source if source is not None else _script_source())
    scoped = _scoped_functions(tree)
    readers = _db_reading_functions(tree, scoped)
    found, branches = [], 0
    for _path, (node, scope) in scoped.items():
        # Les lecteurs ATTEIGNABLES depuis cette portée : le helper imbriqué
        # d'une autre commande n'en fait pas partie.
        visible = {name for name, target in scope.items() if target in readers}
        for stmt in _statements_owned_by(node):
            if not isinstance(stmt, ast.If):
                continue
            test = stmt.test
            if not (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
                    and isinstance(test.operand, ast.Call)
                    and isinstance(test.operand.func, ast.Name)
                    and test.operand.func.id == "wait_for_effect"):
                continue
            branches += 1
            # `stmt.body` SEUL : la branche d'échec. Le prédicat de la sonde,
            # lui, lit la base — c'est son rôle — et vit hors du `if`.
            for s in stmt.body:
                for inner in ast.walk(s):
                    if (isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Name)
                            and inner.func.id in visible):
                        found.append((inner.lineno, inner.func.id))
    offenders = [f"{name} (ligne {lineno})" for lineno, name in sorted(found)]
    return offenders, branches


# Compte réel des `if not wait_for_effect(...)` du script. Serré, pas margé :
# une marge rendrait la garde aveugle à la disparition d'une branche d'échec,
# qui est précisément ce qu'elle doit voir. Même mesure que `_MIN_REACHING`,
# par `_rereads_in_failure_branch()[1]` — 14 le 2026-08-25, 15 le 2026-08-26
# avec la branche d'échec de `cmd_move_project` (BUG-032). Ce plancher-ci est
# de la MÊME classe que le précédent : les deux se relèvent ensemble, sans
# quoi le second continue de passer en ayant cessé de compter juste.
#
# ⚠️ PIÈGE DE FUSION : même avertissement que `_MIN_REACHING` ci-dessus, et
# ICI le cas est le pire des deux. Deux branches qui ajoutent chacune une
# commande d'écriture et relèvent cette ligne à la même valeur fusionnent
# SANS conflit : le compte réel grimpe de deux, cette ligne n'en enregistre
# qu'un, et `assert branches >= _MIN_FAILURE_BRANCHES` reste VRAI (16 >= 15)
# — silencieusement. Ne JAMAIS reprendre cette valeur d'une résolution de
# conflit : la REMESURER par la commande DÉDIÉE ci-dessous — PAS celle de
# `_MIN_REACHING` ci-dessus, qui calcule `_reaching_and_waiting()[0]` et
# rend donc l'AUTRE constante :
#
#     python3 -c "import importlib.util,sys; sys.path.insert(0,'.'); \
#       s=importlib.util.spec_from_file_location('t','tests/test_write_wait.py'); \
#       m=importlib.util.module_from_spec(s); s.loader.exec_module(m); \
#       print(m._rereads_in_failure_branch()[1])"
#
# Mesuré le 2026-08-26 sur la même fusion séquentielle simulée que
# `_MIN_REACHING` ci-dessus (`feat/move-project` + `feat/reopen-task`),
# dans les deux mêmes résolutions du parametrize (seul autre point de
# conflit non cosmétique) :
#   - résolution correcte (planchers 15/16, union des 4 noms) :
#         735 collectés — 1 failed, 733 passed, 1 skipped
#   - résolution naïve (planchers 14/15, union des 4 noms) :
#         735 collectés — 5 failed, 729 passed, 1 skipped
#
# Trois choses à en retenir, spécifiques à CETTE constante :
#   1. contrairement à `_MIN_REACHING`, aucun des 4 échecs supplémentaires
#      de la résolution naïve ne porte sur `_MIN_FAILURE_BRANCHES` — les 4
#      sont les 4 cas paramétrés de
#      `test_the_derivation_floor_notices_a_command_that_disappeared`, qui
#      ne teste QUE `_MIN_REACHING` (`_reaching_and_waiting()`), jamais
#      `_rereads_in_failure_branch()` ;
#   2. l'assertion principale `assert branches >= _MIN_FAILURE_BRANCHES`
#      reste vraie et silencieuse dans les DEUX résolutions ;
#   3. **`_MIN_FAILURE_BRANCHES` n'a AUCUN méta-test équivalent à celui de
#      `_MIN_REACHING`** — une résolution naïve sur cette constante-ci ne
#      produit STRICTEMENT AUCUN symptôme, dans aucune des deux résolutions
#      du parametrize : on ne peut pas compter sur la suite pour la
#      rattraper, à la différence de `_MIN_REACHING` qui a au moins une
#      chance : un cas paramétré sur quatre continue de crier, quelle que
#      soit la résolution du parametrize (cf. point 1 du bloc ci-dessus).
#
# Planchers réels : 15 (`_MIN_REACHING`) et 16 (`_MIN_FAILURE_BRANCHES`).
_MIN_FAILURE_BRANCHES = 16


def test_no_failure_branch_asks_the_database_again_what_the_probe_observed():
    offenders, branches = _rereads_in_failure_branch()
    assert offenders == [], (
        "seconde lecture de la base dans la branche d'échec d'un "
        f"wait_for_effect : {offenders} — le message doit citer la valeur "
        "capturée par la sonde")
    assert branches >= _MIN_FAILURE_BRANCHES, (
        f"dérivation suspecte : {branches} branches d'échec inspectées, "
        f"{_MIN_FAILURE_BRANCHES} attendues au minimum")


_CONTROL_DIRECT_REREAD = '''
def q(sql, args=()):
    return []

def cmd_reread(a):
    if not wait_for_effect(lambda: False):
        rows = q("select title from TMTask where uuid=?", (a.id,))
        print(f"ÉCHEC : {rows}")
        return 1
    return 0
'''

_CONTROL_INDIRECT_REREAD = '''
def q(sql, args=()):
    return []

def _read_title(task_id):
    return q("select title from TMTask where uuid=?", (task_id,))

def cmd_reread_through_a_helper(a):
    if not wait_for_effect(lambda: False):
        print(f"ÉCHEC : {_read_title(a.id)}")
        return 1
    return 0
'''

_CONTROL_PROBE_ONLY_READ = '''
def q(sql, args=()):
    return []

def cmd_clean(a):
    seen = {"observed": None}

    def _settled():
        seen["observed"] = q("select title from TMTask where uuid=?", (a.id,))
        return bool(seen["observed"])

    if not wait_for_effect(_settled):
        print(f"ÉCHEC : {seen['observed']}")
        return 1
    return 0
'''


_CONTROL_NESTED_REREAD = '''
def q(sql, args=()):
    return []

def cmd_reread_through_a_nested_helper(a):
    def _problem():
        rows = q("select title from TMTask where uuid=?", (a.id,))
        return None if rows else "absent"

    if not wait_for_effect(lambda: _problem() is None):
        print(f"ÉCHEC : {_problem()}")
        return 1
    return 0
'''


@pytest.mark.parametrize("source,expected", [
    (_CONTROL_DIRECT_REREAD, "q"),
    (_CONTROL_INDIRECT_REREAD, "_read_title"),
    (_CONTROL_NESTED_REREAD, "_problem"),
])
def test_the_reread_guard_flags_the_three_forms_it_claims_to_cover(source, expected):
    """Contre-épreuve, dans les TROIS formes que la garde revendique.

    La deuxième est celle du défaut réel : la relecture passe par un helper,
    pas par `q` — une garde non transitive la manque. La troisième est celle
    qu'avaient `cmd_move_task` et `cmd_reschedule_task` avant leur correction :
    le helper est défini DANS le corps de la commande. Un collecteur limité à
    `tree.body` ne le voit pas et déclare le site sain ; les deux commandes
    portent toujours un helper imbriqué, donc la voie est ouverte.
    """
    offenders, branches = _rereads_in_failure_branch(source)
    assert branches == 1
    assert [o.split(" ")[0] for o in offenders] == [expected]


def test_the_reread_guard_leaves_the_probe_alone():
    """Contre-contre-épreuve : sans elle, la garde interdirait à la sonde de
    lire la base, c'est-à-dire de faire son travail — et le seul moyen de la
    rendre verte serait de ne plus rien vérifier."""
    offenders, branches = _rereads_in_failure_branch(_CONTROL_PROBE_ONLY_READ)
    assert branches == 1
    assert offenders == []
