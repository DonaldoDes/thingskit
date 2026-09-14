"""`thingskit delete-project` — suppression d'un projet, avec refus rendu par
l'acte lui-même.

Surface d'écriture MESURÉE le 2026-09-14 sur deux projets jetables réels
(`ZZ-probe-delete-project`, `ZZ-probe-guard`), pas déduite de la
documentation (constitution § « Toute affirmation … est constatée par
test ») :

- `tell application "Things3" to delete project id "<uuid>"` pose
  `trashed=1` sur la ligne du projet (`TMTask`, `type=1`) ; la ligne
  SUBSISTE. C'est la Corbeille Things, récupérable par « Put Back », pas une
  destruction définitive — même constat que `delete-task`.
- `project id "<uuid d'une tâche>"` et `project id "<uuid inexistant>"`
  rendent tous deux l'erreur AppleScript -1728 (`rc=1`). L'acte refuse donc
  de lui-même une cible qui n'est pas un projet, ou qui a disparu entre la
  résolution et le geste.
- `count (to dos of p)` compte AUSSI les tâches terminées (2 sur le projet
  de sonde) ; `count (to dos of p whose status is open)` n'en compte qu'une.
  C'est cette seconde forme qui porte la condition de refus.
- **Ce qui motive le refus** : les tâches d'un projet supprimé ne sont PAS
  marquées `trashed` en base (mesuré : `trashed=0` après coup), et
  deviennent pourtant INATTEIGNABLES par AppleScript
  (`delete to do id "<uuid>"` → -1728). Elles disparaissent de
  l'application tout en restant visibles de `thingskit tasks`, qui lit la
  base. Supprimer un projet portant des tâches ouvertes les échoue donc
  silencieusement, sans recours par le CLI.

Ces tests ne touchent jamais l'application ni la vraie base : `db_path` est
redirigée vers une base SQLite jetable, `osa`/`ensure_running`/`time.sleep`
mockés. Le mock d'`osa` tient le rôle de l'APPLICATION : c'est lui qui
détient la vérité sur les tâches ouvertes, et la base jetable peut le
contredire — c'est ainsi qu'on éprouve que la condition est réévaluée dans
l'acte, et non lue en base avant lui.
"""
from __future__ import annotations

import argparse
import re
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
    startDate INTEGER,
    startBucket INTEGER,
    deadline INTEGER,
    reminderTime INTEGER,
    status INTEGER,
    notes TEXT
);
"""

TYPE_TASK, TYPE_PROJECT, TYPE_HEADING = 0, 1, 2
STATUS_OPEN, STATUS_COMPLETED = 0, 3

PROJECT = "PPPPPPPPPPPPPPPPPPPPPP"
PROJECT2 = "QQQQQQQQQQQQQQQQQQQQQQ"
TASK = "TTTTTTTTTTTTTTTTTTTTTT"
TASK2 = "UUUUUUUUUUUUUUUUUUUUUU"
HEADING = "HHHHHHHHHHHHHHHHHHHHHH"
AREA = "RRRRRRRRRRRRRRRRRRRRRR"


def _make_db(tmp_path, task_rows, area_rows=()):
    db_file = tmp_path / "main.sqlite"
    con = sqlite3.connect(db_file)
    con.executescript(SCHEMA)
    defaults = dict(
        uuid=None, title=None, type=0, trashed=0, project=None, heading=None,
        area=None, startDate=None, startBucket=None, deadline=None,
        reminderTime=None, status=0, notes=None,
    )
    for r in task_rows:
        row = {**defaults, **r}
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,project,heading,area,"
            "startDate,startBucket,deadline,reminderTime,status,notes) values "
            "(:uuid,:title,:type,:trashed,:project,:heading,:area,"
            ":startDate,:startBucket,:deadline,:reminderTime,:status,:notes)",
            row,
        )
    for uuid, title in area_rows:
        con.execute("insert into TMArea (uuid, title) values (?,?)", (uuid, title))
    con.commit()
    con.close()
    return db_file


def _ns(project=None, project_id=None, delete_open_tasks=False):
    return argparse.Namespace(project=project, project_id=project_id,
                              delete_open_tasks=delete_open_tasks)


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    """L'APPLICATION en doublure : elle détient les tâches ouvertes, et c'est
    elle qui répond au script — jamais la base jetable.

    `app_open` est la liste des identifiants de tâches ouvertes que
    l'application voit AU MOMENT de l'acte. `None` (défaut) signifie « la
    doublure se cale sur la base », le cas ordinaire ; une valeur explicite
    permet de FAIRE DIVERGER l'application de la base, ce qui est le seul
    moyen d'éprouver que la condition n'est pas lue en base avant l'acte.
    """
    state = {"osa": [], "db": None, "app_open": None, "effective": True,
             "rc": 0, "out": ""}

    def _set_rows(task_rows, area_rows=()):
        db_file = _make_db(tmp_path, task_rows, area_rows)
        monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
        state["db"] = db_file
        return db_file

    def _open_ids():
        if state["app_open"] is not None:
            return list(state["app_open"])
        con = sqlite3.connect(state["db"])
        rows = con.execute(
            "select uuid from TMTask where project=? and type=? and status=? "
            "and trashed=0", (PROJECT, TYPE_TASK, STATUS_OPEN)).fetchall()
        con.close()
        return [r[0] for r in rows]

    def _fake_osa(script):
        state["osa"].append(script)
        if state["rc"] != 0:
            return state["rc"], state["out"]
        return 0, _as_the_application_would(script, _open_ids(),
                                            state, PROJECT)

    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "osa", _fake_osa)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    return state, _set_rows


def _expected_of(script: str):
    """Le jeu d'identifiants que le script EXIGE de retrouver, ou `None`
    quand il n'en exige aucun."""
    marker = re.search(r"set attendu to \{(.*?)\}", script)
    if marker is None:
        return None
    body = marker.group(1).strip()
    if not body:
        return []
    return [x.strip().strip('"') for x in body.split(",") if x.strip()]


def _as_the_application_would(script: str, ids: list[str], state, project_id):
    """Exécute le script comme l'application l'exécuterait.

    C'est le point sur lequel cette doublure ne transige pas : elle n'applique
    aucun contrat qu'elle connaîtrait d'avance — elle LIT les gardes du script
    et s'y plie, ligne à ligne, jusqu'au `delete p`. Une doublure qui refusait
    « parce que c'est le contrat » aurait validé un script SANS garde : mesuré
    le 2026-09-14, la garde retirée du script, 26 des 27 tests restaient verts.
    """
    joined = ",".join(ids)
    expected = _expected_of(script)
    for raw in script.splitlines():
        line = raw.strip()
        if line == "delete p":
            if state["effective"]:
                con = sqlite3.connect(state["db"])
                con.execute("update TMTask set trashed=1 where uuid=?",
                            (project_id,))
                con.commit()
                con.close()
            return f"DELETED:{joined}"
        if line.startswith('if (count ids) > 0 then return "OPEN:"'):
            if ids:
                return f"OPEN:{joined}"
        elif line.startswith('if (count ids) is not (count attendu) then '
                             'return "CHANGED:"'):
            if expected is None or len(expected) != len(ids):
                return f"CHANGED:{joined}"
        elif line.startswith('if ids does not contain (e as text) then '
                             'return "CHANGED:"'):
            if expected is None or sorted(expected) != sorted(ids):
                return f"CHANGED:{joined}"
    return "-1728 : le script n'a jamais supprimé"


def _project_row(state, uuid=PROJECT):
    con = sqlite3.connect(state["db"])
    row = con.execute("select trashed from TMTask where uuid=?", (uuid,)).fetchone()
    con.close()
    return row


# --- adressage de la cible ---------------------------------------------

def test_missing_project_and_project_id_refuses(thingskit, rigged):
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Solo", "type": TYPE_PROJECT}])
    assert thingskit.cmd_delete_project(_ns()) != 0
    assert state["osa"] == []


def test_project_and_project_id_both_given_refuses(thingskit, rigged):
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Solo", "type": TYPE_PROJECT}])
    assert thingskit.cmd_delete_project(_ns(project="Solo", project_id=PROJECT)) != 0
    assert state["osa"] == []


def test_malformed_project_id_refuses_before_any_call(thingskit, rigged):
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Solo", "type": TYPE_PROJECT}])
    assert thingskit.cmd_delete_project(_ns(project_id="pas un uuid!!")) != 0
    assert state["osa"] == []


def test_project_id_not_found_refuses(thingskit, rigged):
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT2, "title": "Autre", "type": TYPE_PROJECT}])
    assert thingskit.cmd_delete_project(_ns(project_id=PROJECT)) != 0
    assert state["osa"] == []


def test_project_title_no_match_refuses(thingskit, rigged):
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Autre chose", "type": TYPE_PROJECT}])
    assert thingskit.cmd_delete_project(_ns(project="Introuvable")) != 0
    assert state["osa"] == []


def test_ambiguous_title_refuses_and_lists_the_candidates(
        thingskit, rigged, capsys):
    """AC-3 : un titre ambigu ne fait jamais choisir « le premier » — il
    refuse ET rend la liste des candidats, sans quoi l'utilisateur n'a aucun
    moyen de désambiguïser."""
    state, set_rows = rigged
    set_rows([
        {"uuid": PROJECT, "title": "Doublon", "type": TYPE_PROJECT},
        {"uuid": PROJECT2, "title": "Doublon", "type": TYPE_PROJECT},
    ])

    rc = thingskit.cmd_delete_project(_ns(project="Doublon"))

    err = capsys.readouterr().err
    assert rc != 0
    assert state["osa"] == []
    assert "AMBIGU" in err
    assert PROJECT in err and PROJECT2 in err, (
        "le refus ne nomme pas les candidats : l'utilisateur ne peut pas "
        f"lever l'ambiguïté — {err!r}")


def test_empty_string_title_refuses(thingskit, rigged):
    """Adversarial : un titre vide ne doit matcher aucune ligne — pas de
    comportement de type wildcard."""
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Autre chose", "type": TYPE_PROJECT}])
    assert thingskit.cmd_delete_project(_ns(project="")) != 0
    assert state["osa"] == []


def test_title_with_sql_metacharacters_matches_nothing(thingskit, rigged):
    """Adversarial : les métacaractères SQL sont littéraux (requête
    paramétrée) — ils n'élargissent jamais la sélection."""
    state, set_rows = rigged
    set_rows([
        {"uuid": PROJECT, "title": "Normal", "type": TYPE_PROJECT},
        {"uuid": PROJECT2, "title": "%' OR '1'='1", "type": TYPE_PROJECT},
    ])
    assert thingskit.cmd_delete_project(_ns(project="%")) != 0
    assert state["osa"] == []


def test_project_id_targeting_a_task_refuses(thingskit, rigged):
    state, set_rows = rigged
    set_rows([{"uuid": TASK, "title": "Une tâche", "type": TYPE_TASK}])
    assert thingskit.cmd_delete_project(_ns(project_id=TASK)) != 0
    assert state["osa"] == []


def test_project_id_targeting_a_heading_refuses(thingskit, rigged):
    state, set_rows = rigged
    set_rows([{"uuid": HEADING, "title": "Un heading", "type": TYPE_HEADING}])
    assert thingskit.cmd_delete_project(_ns(project_id=HEADING)) != 0
    assert state["osa"] == []


def test_an_already_trashed_project_is_a_no_op_without_solicitation(
        thingskit, rigged, capsys):
    """Idempotence, même contrat que `delete-task` : déjà à la Corbeille =
    succès, et l'application n'est pas sollicitée."""
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Déjà jeté", "type": TYPE_PROJECT,
               "trashed": 1}])

    rc = thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    assert rc == 0
    assert state["osa"] == []
    assert "Corbeille" in capsys.readouterr().out


# --- le refus sur tâches ouvertes est rendu PAR L'ACTE ------------------

def test_a_project_with_open_tasks_is_not_deleted(thingskit, rigged, capsys):
    """AC-2 : le refus, et rien n'est supprimé."""
    state, set_rows = rigged
    set_rows([
        {"uuid": PROJECT, "title": "Chantier", "type": TYPE_PROJECT},
        {"uuid": TASK, "title": "Reste à faire", "type": TYPE_TASK,
         "project": PROJECT, "status": STATUS_OPEN},
        {"uuid": TASK2, "title": "Fini", "type": TYPE_TASK,
         "project": PROJECT, "status": STATUS_COMPLETED},
    ])

    rc = thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    err = capsys.readouterr().err
    assert rc != 0
    assert _project_row(state) == (0,), "le projet a été supprimé malgré le refus"
    assert "1" in err, f"le refus ne dit pas combien : {err!r}"
    assert "Reste à faire" in err, f"le refus ne dit pas lesquelles : {err!r}"
    assert "Fini" not in err, (
        "une tâche TERMINÉE n'est pas une tâche ouverte — la citer dans le "
        f"refus le rend faux : {err!r}")


def test_the_refusal_is_the_application_answer_not_a_prior_database_read(
        thingskit, rigged, capsys):
    """Cœur de la garantie (règle 14) : la condition est réévaluée DANS
    l'acte.

    La base dit « aucune tâche ouverte » ; l'application, au moment du geste,
    en voit une. Une commande qui aurait décidé sur la base AVANT d'agir
    supprimerait. Celle-ci doit refuser — et le projet rester intact.
    """
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Chantier", "type": TYPE_PROJECT}])
    state["app_open"] = [TASK]

    rc = thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    assert rc != 0
    assert state["osa"] != [], "l'acte n'a jamais eu lieu — rien n'a été réévalué"
    assert _project_row(state) == (0,)
    assert TASK in capsys.readouterr().err


def test_a_stale_database_never_produces_a_false_refusal(
        thingskit, rigged, capsys):
    """Symétrique du précédent, et tout aussi nécessaire : la base porte
    trois tâches ouvertes, l'application n'en voit aucune (elles viennent
    d'être terminées ailleurs). Refuser serait un faux négatif — et la preuve
    qu'un contrôle préalable en base subsiste."""
    state, set_rows = rigged
    set_rows([
        {"uuid": PROJECT, "title": "Chantier", "type": TYPE_PROJECT},
        {"uuid": TASK, "title": "A", "type": TYPE_TASK, "project": PROJECT},
        {"uuid": TASK2, "title": "B", "type": TYPE_TASK, "project": PROJECT},
    ])
    state["app_open"] = []

    rc = thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    assert rc == 0, capsys.readouterr().err
    assert _project_row(state) == (1,)


def test_the_guard_travels_inside_the_script_before_the_delete(thingskit,
                                                               rigged):
    """La condition n'est pas seulement évaluée au bon moment : elle est
    portée par le script lui-même, et le `delete` vient APRÈS son refus.

    Sans cette épreuve, un script qui compterait puis supprimerait sans
    tester passerait les tests ci-dessus tant que la doublure se comporte
    bien — la garde vivrait côté Python, c'est-à-dire hors de l'acte.
    """
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Chantier", "type": TYPE_PROJECT}])

    thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    script = state["osa"][-1]
    assert "whose status is open" in script
    assert script.index("return \"OPEN:") < script.index("delete p"), (
        "le `delete` précède le refus : la garde ne garde rien")


def test_exactly_one_solicitation_counts_and_deletes(thingskit, rigged):
    """Un seul aller-retour : compter puis supprimer en deux appels
    rouvrirait la fenêtre entre le constat et le geste."""
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Chantier", "type": TYPE_PROJECT}])

    assert thingskit.cmd_delete_project(_ns(project_id=PROJECT)) == 0
    assert len(state["osa"]) == 1


# --- suppression nominale -----------------------------------------------

def test_a_project_without_open_tasks_is_deleted(thingskit, rigged, capsys):
    state, set_rows = rigged
    set_rows([
        {"uuid": PROJECT, "title": "Terminé", "type": TYPE_PROJECT},
        {"uuid": TASK, "title": "Fini", "type": TYPE_TASK, "project": PROJECT,
         "status": STATUS_COMPLETED},
    ])

    rc = thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    out = capsys.readouterr().out
    assert rc == 0
    assert _project_row(state) == (1,)
    assert "Corbeille" in out and "Terminé" in out


def test_a_project_resolved_by_title_is_deleted(thingskit, rigged):
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Unique", "type": TYPE_PROJECT}])

    assert thingskit.cmd_delete_project(_ns(project="Unique")) == 0
    assert _project_row(state) == (1,)


def test_failure_when_effect_not_observed(thingskit, rigged, capsys):
    """Un code retour 0 signifie « effet constaté en base », jamais
    « commande envoyée » (constitution § Zones sensibles 1)."""
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Chantier", "type": TYPE_PROJECT}])
    state["effective"] = False

    rc = thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    assert rc != 0
    assert "ÉCHEC" in capsys.readouterr().err


def test_an_applescript_error_is_never_a_success(thingskit, rigged, capsys):
    """Mesuré : `project id "<uuid inexistant>"` rend -1728. La cible peut
    avoir disparu entre la résolution en base et le geste — c'est l'acte qui
    le dit, et il ne doit jamais passer pour un succès."""
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Chantier", "type": TYPE_PROJECT}])
    state["rc"] = 1
    state["out"] = ('execution error: Erreur dans Things3 : Il est impossible '
                    'd’obtenir project id "…". (-1728)')

    rc = thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    assert rc != 0
    assert _project_row(state) == (0,)
    assert "ÉCHEC" in capsys.readouterr().err


# --- passage outre explicite --------------------------------------------

def test_the_override_is_never_triggered_by_default(thingskit, rigged):
    """Rien ne déclenche le passage outre : l'absence du drapeau produit la
    garde ordinaire, jamais l'exigence d'un jeu attendu."""
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Chantier", "type": TYPE_PROJECT}])

    thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    assert "attendu" not in state["osa"][-1]


def test_the_override_says_what_will_disappear_before_deleting(
        thingskit, rigged, capsys):
    """Le passage outre annonce, PUIS supprime — et ce qu'il annonce est ce
    que l'application a observé, pas ce que la base en disait."""
    state, set_rows = rigged
    set_rows([
        {"uuid": PROJECT, "title": "Chantier", "type": TYPE_PROJECT},
        {"uuid": TASK, "title": "Reste à faire", "type": TYPE_TASK,
         "project": PROJECT, "status": STATUS_OPEN},
    ])

    rc = thingskit.cmd_delete_project(
        _ns(project_id=PROJECT, delete_open_tasks=True))

    out = capsys.readouterr().out
    assert rc == 0
    assert _project_row(state) == (1,)
    assert out.index("Reste à faire") < out.index("Corbeille"), (
        f"ce qui disparaît n'est pas annoncé AVANT la suppression : {out!r}")


def test_the_override_still_re_evaluates_the_set_in_the_act(
        thingskit, rigged, capsys):
    """Le passage outre ne désarme pas la réévaluation : le second acte
    exige de retrouver EXACTEMENT le jeu annoncé. Si une tâche ouverte
    s'ajoute entre l'annonce et le geste, il refuse — l'utilisateur n'a pas
    consenti à celle-là."""
    state, set_rows = rigged
    set_rows([
        {"uuid": PROJECT, "title": "Chantier", "type": TYPE_PROJECT},
        {"uuid": TASK, "title": "Reste à faire", "type": TYPE_TASK,
         "project": PROJECT, "status": STATUS_OPEN},
    ])
    real_open = state["app_open"]

    # L'application voit une tâche de plus au SECOND acte — exactement la
    # course que la réévaluation existe pour attraper.
    original = thingskit.osa
    seen = {"n": 0}

    def _osa(script):
        seen["n"] += 1
        if seen["n"] >= 2:
            state["app_open"] = [TASK, TASK2]
        return original(script)

    state["app_open"] = [TASK]
    thingskit.osa = _osa
    try:
        rc = thingskit.cmd_delete_project(
            _ns(project_id=PROJECT, delete_open_tasks=True))
    finally:
        thingskit.osa = original
        state["app_open"] = real_open

    assert rc != 0
    assert _project_row(state) == (0,), "supprimé malgré un jeu qui a changé"
    assert "ÉCHEC" in capsys.readouterr().err


def test_the_override_on_a_project_without_open_tasks_still_works(
        thingskit, rigged):
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": "Vide", "type": TYPE_PROJECT}])

    assert thingskit.cmd_delete_project(
        _ns(project_id=PROJECT, delete_open_tasks=True)) == 0
    assert _project_row(state) == (1,)


# --- rendu borné d'une valeur non contrôlée ------------------------------

def test_a_hostile_title_never_reaches_the_output_raw(thingskit, rigged,
                                                      capsys):
    """Zone sensible n° 1 : un titre stocké est une valeur non contrôlée."""
    state, set_rows = rigged
    set_rows([
        {"uuid": PROJECT, "title": "Chantier", "type": TYPE_PROJECT},
        {"uuid": TASK, "title": "A\x1b[2Jefface", "type": TYPE_TASK,
         "project": PROJECT, "status": STATUS_OPEN},
    ])

    thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    assert "\x1b" not in capsys.readouterr().err


def test_the_project_id_is_escaped_in_the_script(thingskit, rigged):
    """Le titre n'est jamais interpolé dans l'AppleScript — seul l'UUID
    l'est, et il passe par `_esc`."""
    state, set_rows = rigged
    set_rows([{"uuid": PROJECT, "title": 'Guille"met', "type": TYPE_PROJECT}])

    thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    assert 'Guille' not in state["osa"][-1]


def test_waiting_never_writes_a_single_byte_to_the_database(thingskit, rigged):
    """La vérification est une LECTURE — la base ne bouge pas sous elle."""
    state, set_rows = rigged
    db_file = set_rows([{"uuid": PROJECT, "title": "Chantier",
                         "type": TYPE_PROJECT}])
    state["effective"] = False
    before = db_file.read_bytes()

    thingskit.cmd_delete_project(_ns(project_id=PROJECT))

    assert db_file.read_bytes() == before
