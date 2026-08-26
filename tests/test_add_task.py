"""`thingskit add-task` — résolution de la cible AVANT d'écrire, et constat du
RATTACHEMENT après coup (BUG-005).

Le défaut couvert ici : `--list` était recopié tel quel dans le payload du
schéma d'URL, sans jamais être résolu contre les projets et areas existants.
Une valeur qui ne correspond à rien fait atterrir la tâche en Inbox, et Things
ne le dit pas — la commande sortait en 0 avec un message de rangement. La
vérification post-écriture ne constatait que l'EXISTENCE de la tâche
(`resolve_uuid("task", titre)`), jamais son rattachement : elle était donc
verte dans le cas exact où le rangement n'avait pas eu lieu.

Deux moitiés, qui ne se remplacent pas :
  - refus AVANT toute sollicitation de l'application quand la cible ne résout
    pas (0 correspondance) ou résout à plusieurs (ambiguïté) ;
  - après l'écriture, la sonde exige qu'une tâche NOUVELLE de ce titre soit
    constatée SOUS la cible résolue. « Une tâche de ce titre existe » ne prouve
    rien : elle pouvait déjà être là, et elle peut être en Inbox.

Ces tests ne touchent jamais l'application ni la vraie base : `db_path` est
redirigée vers une base SQLite jetable, `url_open`/`osa`/`ensure_running`/
`time.sleep` mockés.
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
    startDate INTEGER,
    startBucket INTEGER,
    deadline INTEGER,
    reminderTime INTEGER,
    status INTEGER,
    notes TEXT
);
"""

TASK, PROJECT, HEADING = 0, 1, 2

PROJ_ID = "PPPPPPPPPPPPPPPPPPPPPP"
PROJ2_ID = "QQQQQQQQQQQQQQQQQQQQQQ"
AREA_ID = "AAAAAAAAAAAAAAAAAAAAAA"
HEAD_ID = "HHHHHHHHHHHHHHHHHHHHHH"


def _make_db(tmp_path, task_rows=(), area_rows=()):
    db_file = tmp_path / "main.sqlite"
    con = sqlite3.connect(db_file)
    con.executescript(SCHEMA)
    defaults = dict(
        uuid=None, title=None, type=0, trashed=0, project=None, heading=None,
        area=None, startDate=None, startBucket=None, deadline=None,
        reminderTime=None, status=0, notes=None,
    )
    for r in task_rows:
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,project,heading,area,"
            "startDate,startBucket,deadline,reminderTime,status,notes) values "
            "(:uuid,:title,:type,:trashed,:project,:heading,:area,"
            ":startDate,:startBucket,:deadline,:reminderTime,:status,:notes)",
            {**defaults, **r},
        )
    for uuid, title in area_rows:
        con.execute("insert into TMArea (uuid, title) values (?, ?)", (uuid, title))
    con.commit()
    con.close()
    return db_file


def _ns(title="Nouvelle tâche", list=None, heading=None, notes=None,
        when=None, deadline=None):
    return argparse.Namespace(title=title, list=list, heading=heading,
                              notes=notes, when=when, deadline=deadline)


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    """Application inerte : `url_open` et `osa` enregistrent, n'écrivent rien."""
    calls = {"url": [], "osa": [], "running": 0, "db": None}

    def _set_rows(task_rows=(), area_rows=()):
        db_file = _make_db(tmp_path, task_rows, area_rows)
        monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
        calls["db"] = db_file
        return db_file

    monkeypatch.setattr(thingskit, "ensure_running",
                        lambda: calls.__setitem__("running", calls["running"] + 1))
    monkeypatch.setattr(thingskit, "url_open",
                        lambda payload, **kw: calls["url"].append(payload))
    monkeypatch.setattr(thingskit, "osa",
                        lambda script: (calls["osa"].append(script), (0, ""))[1])
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    return calls, _set_rows


def _rig_landing(thingskit, monkeypatch, calls, uuid="NEWNEWNEWNEWNEWNEWNEW1",
                 project=None, area=None, heading=None):
    """`url_open` qui simule l'atterrissage RÉEL de la tâche, à l'endroit
    demandé par le test — y compris « nulle part », c'est-à-dire l'Inbox."""
    db_file = calls["db"]

    def _fake(payload, **kw):
        calls["url"].append(payload)
        title = payload[0]["attributes"]["title"]
        con = sqlite3.connect(db_file)
        con.execute(
            "insert into TMTask (uuid,title,type,trashed,project,heading,area,"
            "status) values (?,?,?,0,?,?,?,0)",
            (uuid, title, TASK, project, heading, area))
        con.commit()
        con.close()

    monkeypatch.setattr(thingskit, "url_open", _fake)


PROJECT_ROW = {"uuid": PROJ_ID, "title": "Projet cible", "type": PROJECT}
AREA_ROW = (AREA_ID, "Area cible")
HEADING_ROW = {"uuid": HEAD_ID, "title": "Section", "type": HEADING,
               "project": PROJ_ID}


# ---------------------------------------------------------------------------
# BUG-005-01 — cible inexistante : refus, aucun appel d'écriture
# ---------------------------------------------------------------------------
def test_an_unresolvable_list_is_refused_without_any_write_call(
        thingskit, rigged, capsys):
    """Le cas fondateur : `--list <uuid>` ne nomme aucun projet ni area.

    Le refus doit précéder TOUTE sollicitation de l'application — un refus
    après écriture n'est pas un refus : la tâche serait déjà en Inbox.
    """
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(list="LbGyL7Gop2uBtVDvSXjqVD"))

    assert rc != 0
    assert calls["url"] == [], "une écriture a été émise malgré le refus"
    assert calls["osa"] == []
    assert calls["running"] == 0, "l'application a été sollicitée malgré le refus"
    err = capsys.readouterr().err
    assert "LbGyL7Gop2uBtVDvSXjqVD" in err, err
    assert "aucune tâche créée" in err, err


def test_the_refusal_says_the_task_was_not_created_anywhere(
        thingskit, rigged, capsys):
    """Un refus muet sur le sort de la tâche invite à réessayer — donc à
    créer un doublon, exactement ce que le cas fondateur a produit."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])

    thingskit.cmd_add_task(_ns(list="Projet qui n'existe pas"))

    err = capsys.readouterr().err
    assert "ni un projet ni une area" in err, err


def test_an_unresolvable_heading_is_refused_without_any_write_call(
        thingskit, rigged, capsys):
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(list="Projet cible", heading="Absente"))

    assert rc != 0
    assert calls["url"] == []
    assert calls["running"] == 0
    assert "Absente" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# BUG-005-03 — cible ambiguë : refus, aucune écriture
# ---------------------------------------------------------------------------
def test_two_projects_of_the_same_title_are_refused_without_any_write_call(
        thingskit, rigged, capsys):
    calls, set_rows = rigged
    set_rows([PROJECT_ROW, {**PROJECT_ROW, "uuid": PROJ2_ID}], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(list="Projet cible"))

    assert rc != 0
    assert calls["url"] == []
    assert calls["running"] == 0
    err = capsys.readouterr().err
    assert "AMBIGU" in err, err
    assert "aucune tâche créée" in err, err


def test_a_title_borne_by_both_a_project_and_an_area_is_refused(
        thingskit, rigged, capsys):
    """Collision mesurée sur la base réelle (« Conventions du vault » est à la
    fois un projet et une area) : le schéma d'URL choisirait seul."""
    calls, set_rows = rigged
    set_rows([{"uuid": PROJ_ID, "title": "Conventions du vault", "type": PROJECT}],
             [(AREA_ID, "Conventions du vault")])

    rc = thingskit.cmd_add_task(_ns(list="Conventions du vault"))

    assert rc != 0
    assert calls["url"] == []
    assert "AMBIGU" in capsys.readouterr().err


def test_two_headings_of_the_same_title_are_refused_without_any_write_call(
        thingskit, rigged, capsys):
    calls, set_rows = rigged
    set_rows([PROJECT_ROW, HEADING_ROW,
              {**HEADING_ROW, "uuid": "HH2HH2HH2HH2HH2HH2HH2H"}], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(list="Projet cible", heading="Section"))

    assert rc != 0
    assert calls["url"] == []
    assert "AMBIGU" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# BUG-005-02 — cas nominal : le RATTACHEMENT est constaté, pas l'existence
# ---------------------------------------------------------------------------
def test_a_task_landing_in_the_target_project_is_a_success(
        thingskit, monkeypatch, rigged, capsys):
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=PROJ_ID)

    rc = thingskit.cmd_add_task(_ns(title="Écrire le rapport", list="Projet cible"))

    assert rc == 0, capsys.readouterr().err
    assert calls["url"], "aucune écriture émise dans le cas nominal"
    assert "tâche ajoutée" in capsys.readouterr().out


def test_a_task_that_lands_in_the_inbox_is_a_failure_not_a_success(
        thingskit, monkeypatch, rigged, capsys):
    """LE défaut de BUG-005 : la tâche EXISTE, donc l'ancienne vérification
    était verte — et elle était en Inbox."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=None, area=None)

    rc = thingskit.cmd_add_task(_ns(title="Écrire le rapport", list="Projet cible"))

    assert rc != 0, "une tâche atterrie en Inbox a été confirmée comme rangée"
    out, err = capsys.readouterr()
    assert "tâche ajoutée" not in out, out
    assert "project=None" in err, err


def test_a_task_that_lands_under_another_project_is_a_failure(
        thingskit, monkeypatch, rigged, capsys):
    calls, set_rows = rigged
    set_rows([PROJECT_ROW, {"uuid": PROJ2_ID, "title": "Autre projet",
                            "type": PROJECT}], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=PROJ2_ID)

    rc = thingskit.cmd_add_task(_ns(list="Projet cible"))

    assert rc != 0
    assert PROJ2_ID in capsys.readouterr().err


def test_a_task_landing_in_the_target_area_is_a_success(
        thingskit, monkeypatch, rigged, capsys):
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, area=AREA_ID)

    rc = thingskit.cmd_add_task(_ns(list="Area cible"))

    assert rc == 0, capsys.readouterr().err


def test_a_task_asked_for_a_project_but_landing_in_an_area_is_a_failure(
        thingskit, monkeypatch, rigged, capsys):
    """Un rangement « quelque part » n'est pas le rangement demandé."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, area=AREA_ID)

    rc = thingskit.cmd_add_task(_ns(list="Projet cible"))

    assert rc != 0
    assert "area=" in capsys.readouterr().err


def test_a_task_landing_under_the_target_heading_is_a_success(
        thingskit, monkeypatch, rigged, capsys):
    """Constat du docstring module : une tâche sous heading a `project` VIDE.
    La sonde ne peut donc pas exiger le projet dans ce cas."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW, HEADING_ROW], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, heading=HEAD_ID, project=None)

    rc = thingskit.cmd_add_task(_ns(list="Projet cible", heading="Section"))

    assert rc == 0, capsys.readouterr().err


def test_a_task_asked_under_a_heading_but_landing_beside_it_is_a_failure(
        thingskit, monkeypatch, rigged, capsys):
    calls, set_rows = rigged
    set_rows([PROJECT_ROW, HEADING_ROW], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=PROJ_ID, heading=None)

    rc = thingskit.cmd_add_task(_ns(list="Projet cible", heading="Section"))

    assert rc != 0
    assert "heading=None" in capsys.readouterr().err


def test_a_task_that_never_appears_is_a_failure(
        thingskit, rigged, capsys):
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(list="Projet cible"))

    assert rc != 0
    assert calls["url"], "l'écriture a bien été tentée"
    assert "aucune tâche" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Adversité — entrées non prévues sur une zone sensible (§ Zones sensibles 1)
# ---------------------------------------------------------------------------
def test_a_preexisting_task_at_the_target_never_vouches_for_the_write(
        thingskit, rigged, capsys):
    """Adversité. Une tâche du même titre EST DÉJÀ dans le projet visé et
    l'écriture n'aboutit pas : « une tâche de ce titre est bien rangée là »
    rendrait un faux succès sur une écriture qui n'a jamais eu lieu."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW,
              {"uuid": "OLDOLDOLDOLDOLDOLDOLD1", "title": "Écrire le rapport",
               "type": TASK, "project": PROJ_ID}], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(title="Écrire le rapport", list="Projet cible"))

    assert rc != 0, "une tâche préexistante a servi de preuve d'écriture"
    assert "aucune tâche" in capsys.readouterr().err


def test_a_preexisting_task_in_the_inbox_never_vouches_for_the_write(
        thingskit, rigged, capsys):
    """Adversité, variante : c'est exactement ce que faisait
    `resolve_uuid("task", titre)` — il trouvait la tâche, où qu'elle soit."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW,
              {"uuid": "OLDOLDOLDOLDOLDOLDOLD2", "title": "Écrire le rapport",
               "type": TASK}], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(title="Écrire le rapport", list="Projet cible"))

    assert rc != 0


def test_a_trashed_project_is_not_a_resolvable_target(
        thingskit, rigged, capsys):
    """Adversité. Un projet à la Corbeille n'apparaît dans aucune liste : y
    ranger reviendrait à ranger dans un objet invisible."""
    calls, set_rows = rigged
    set_rows([{**PROJECT_ROW, "trashed": 1}], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(list="Projet cible"))

    assert rc != 0
    assert calls["url"] == []


def test_a_trashed_homonym_does_not_make_the_live_project_ambiguous(
        thingskit, monkeypatch, rigged, capsys):
    """Adversité, contre-épreuve du sur-refus : la garde ne doit pas bloquer
    un projet sain au motif qu'un homonyme traîne à la Corbeille."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW, {**PROJECT_ROW, "uuid": PROJ2_ID, "trashed": 1}],
             [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=PROJ_ID)

    rc = thingskit.cmd_add_task(_ns(list="Projet cible"))

    assert rc == 0, capsys.readouterr().err


@pytest.mark.parametrize("value", ["%", "_", "Projet%", "'; drop table TMTask--"])
def test_a_wildcard_or_quote_in_the_list_is_matched_literally(
        thingskit, rigged, capsys, value):
    """Adversité. La résolution est un `=` paramétré, jamais un `like` ni une
    interpolation : `%` ne doit pas désigner « tous les projets », et un
    apostrophe ne doit pas atteindre le moteur SQL."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(list=value))

    assert rc != 0, f"{value!r} a résolu à une cible"
    assert calls["url"] == []


def test_an_empty_list_is_refused_without_any_write_call(
        thingskit, rigged, capsys):
    """Adversité. Le titre vide est le trou classique des comparaisons de
    chaînes (`"" is not ""` rend faux côté AppleScript, cf. § Zones
    sensibles 2) — ici il ne doit résoudre à rien."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(list=""))

    assert rc != 0
    assert calls["url"] == []


def test_no_sql_write_reaches_the_database_on_the_refusal_path(
        thingskit, rigged):
    """Adversité / invariant § Zones sensibles 1 : la base est ouverte en
    lecture seule, et rien de ce chemin ne l'écrit — comparé à l'octet."""
    calls, set_rows = rigged
    db_file = set_rows([PROJECT_ROW], [AREA_ROW])
    before = db_file.read_bytes()

    thingskit.cmd_add_task(_ns(list="Cible inexistante"))

    assert db_file.read_bytes() == before


def test_no_sql_write_reaches_the_database_on_the_nominal_path(
        thingskit, rigged):
    """Même invariant, sur le chemin qui va jusqu'à la vérification : la
    surface applicative est inerte, donc la base doit être INCHANGÉE."""
    calls, set_rows = rigged
    db_file = set_rows([PROJECT_ROW], [AREA_ROW])
    before = db_file.read_bytes()

    thingskit.cmd_add_task(_ns(list="Projet cible"))

    assert db_file.read_bytes() == before


def test_the_failure_message_uses_the_observed_placement_not_a_fresh_query(
        thingskit, monkeypatch, rigged, capsys):
    """Adversité — course. L'effet atterrit ENTRE le dernier sondage et la
    composition du message : une seconde lecture rendrait « aucun problème »
    et le message imprimerait littéralement `None`, cessant de dire pourquoi
    il échoue au moment où il en a le plus besoin (§ Zones sensibles 1)."""
    calls, set_rows = rigged
    db_file = set_rows([PROJECT_ROW], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=None)

    def _wait_then_land(probe, *args, **kwargs):
        observed = probe()          # dernier sondage : la tâche est en Inbox
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set project=? where type=? and title=?",
                    (PROJ_ID, TASK, "Écrire le rapport"))
        con.commit()
        con.close()
        return observed

    monkeypatch.setattr(thingskit, "wait_for_effect", _wait_then_land)

    rc = thingskit.cmd_add_task(_ns(title="Écrire le rapport", list="Projet cible"))

    assert rc != 0
    err = capsys.readouterr().err
    assert ": None" not in err, err
    assert "project=None" in err, err


def test_a_database_unreadable_throughout_is_said_not_invented(
        thingskit, monkeypatch, rigged, capsys):
    """Adversité. La sonde n'a rien pu observer de toute l'attente : le
    message doit le DIRE, pas imprimer `None` ni inventer un écart."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])
    monkeypatch.setattr(thingskit, "wait_for_effect",
                        lambda probe, *a, **k: None)

    rc = thingskit.cmd_add_task(_ns(list="Projet cible"))

    assert rc != 0
    err = capsys.readouterr().err
    assert thingskit._NO_OBSERVATION in err, err


# ---------------------------------------------------------------------------
# Projet terminé — ce que la garde fait, épinglé plutôt que laissé implicite
# ---------------------------------------------------------------------------
# Une tâche du vault attribue le même symptôme (atterrissage en Inbox sans
# erreur) à une cible `Completed`, cause DISTINCTE de « la valeur ne
# correspond à rien » : là, le projet existe. Ce que Things fait réellement
# d'un `list` nommant un projet terminé n'est PAS établi — l'établir impose
# d'écrire dans la base réelle de l'utilisateur. Ces deux tests épinglent donc
# ce que fait le CODE dans les deux mondes possibles, sans trancher lequel.
COMPLETED, CANCELED = 3, 2


@pytest.mark.parametrize("status", [COMPLETED, CANCELED])
def test_a_finished_project_is_still_a_resolvable_target(
        thingskit, monkeypatch, rigged, capsys, status):
    """Aucun filtre sur le statut, comme les quatre résolveurs de projet par
    titre du script (`trashed=0` seul) et comme `move-task`, qui accepte
    déjà une cible terminée. Un sur-refus priverait un usage légitime :
    consigner après coup dans un projet qu'on vient de clore."""
    calls, set_rows = rigged
    set_rows([{**PROJECT_ROW, "status": status}], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=PROJ_ID)

    rc = thingskit.cmd_add_task(_ns(list="Projet cible"))

    assert rc == 0, capsys.readouterr().err


def test_a_task_lost_to_a_finished_project_fails_and_names_that_status(
        thingskit, monkeypatch, rigged, capsys):
    """Si Things range malgré tout la tâche ailleurs, l'échec est désormais
    bruyant — et il cite le statut du projet visé, relevé AVANT l'écriture,
    pour que le lecteur ait la piste sous les yeux. C'est un FAIT sur la
    cible, pas une cause : le lien n'est pas établi."""
    calls, set_rows = rigged
    set_rows([{**PROJECT_ROW, "status": COMPLETED}], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=None)

    rc = thingskit.cmd_add_task(_ns(list="Projet cible"))

    assert rc != 0
    err = capsys.readouterr().err
    assert "completed" in err, err


def test_an_open_project_never_carries_a_status_note(
        thingskit, monkeypatch, rigged, capsys):
    """Contre-épreuve : la note ne s'invite pas dans le cas courant."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=None)

    thingskit.cmd_add_task(_ns(list="Projet cible"))

    assert "le projet visé est" not in capsys.readouterr().err


def test_the_resolution_precedes_every_solicitation_of_the_application(
        thingskit, rigged):
    """Adversité — ordre. `ensure_running` LANCE Things s'il ne tourne pas :
    le refus doit venir avant, sinon la commande a un effet visible pour
    l'utilisateur alors qu'elle prétend n'avoir rien fait."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])

    thingskit.cmd_add_task(_ns(list="Cible inexistante"))

    assert calls["running"] == 0


# ---------------------------------------------------------------------------
# Adversité sur le RENDU de sortie, pas sur la source du message
# ---------------------------------------------------------------------------
# Angle mort de la première passe : les 14 tests d'adversité éprouvaient d'où
# vient le message (la sonde, jamais une relecture) et jamais ce qu'il ÉMET.
# Or `print(f"tâche ajoutée : {a.title} → {where}")` rendait le titre BRUT.
# Les octets sont corrects, et c'est bien le problème : le terminal, lui,
# EXÉCUTE `\x1b[2K\r` — il efface la ligne, et l'utilisateur lit une
# confirmation qu'aucune partie du programme n'a écrite. C'est le dommage
# exact que BUG-005 ferme — « le message annonce un rangement qui n'a pas eu
# lieu » — restauré par un autre vecteur, avec un code retour 0 cette fois
# légitime. L'entrée n'est pas hypothétique : les titres de tâches de ce
# système viennent notamment de comptes rendus de réunion importés
# automatiquement, que personne ne relit avant qu'ils n'atteignent la commande.
#
# La classe est celle de `str.isprintable()`, sur laquelle `repr()` s'appuie :
# Cc (contrôles, dont ESC/CR/LF), Cf (dont les inversions de sens de lecture
# U+202E), Zl/Zp (U+2028/U+2029), Zs autres que l'espace, Cs, Co, Cn. Les
# accents et le `→` restent lisibles — mesuré, pas supposé.
SPOOF = "Ma tache\x1b[2K\rtâche ajoutée : AUTRE → AUTRE PROJET"


def _single_line(out: str) -> str:
    """Le corps du message, une fois retiré le `\n` que `print` ajoute.

    Sans cette distinction, un test qui cherche `"\n" not in out` échoue par
    construction et ne peut rien établir : l'invariant est que le message
    TIENT SUR UNE LIGNE — un saut de ligne venu du titre en ferait deux, et
    la seconde serait une ligne que le programme n'a pas voulu écrire.
    """
    assert out.endswith("\n") and out.count("\n") == 1, (
        f"le message ne tient pas sur une seule ligne : {out!r}")
    return out[:-1]

HOSTILE_RENDERINGS = [
    pytest.param(SPOOF, "\x1b", id="esc-erase-line"),
    pytest.param("Compte rendu\nrésumé", "\n", id="newline"),
    pytest.param("Titre‮gnitsil", "‮", id="bidi-override"),
]


@pytest.mark.parametrize("hostile,forbidden", HOSTILE_RENDERINGS)
def test_the_success_message_never_emits_a_control_sequence_from_the_title(
        thingskit, monkeypatch, rigged, capsys, hostile, forbidden):
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=PROJ_ID)

    rc = thingskit.cmd_add_task(_ns(title=hostile, list="Projet cible"))

    out = capsys.readouterr().out
    assert rc == 0, "le cas nominal doit rester un succès — on borne le RENDU"
    body = _single_line(out)
    assert forbidden not in body, (
        f"séquence de contrôle {forbidden!r} émise sur stdout : {out!r}")
    assert "tâche ajoutée" in body


@pytest.mark.parametrize("hostile,forbidden", HOSTILE_RENDERINGS)
def test_the_success_message_never_emits_a_control_sequence_from_the_list(
        thingskit, monkeypatch, rigged, capsys, hostile, forbidden):
    """Second vecteur : le titre du PROJET. Il vient de la base de Things,
    donc d'une saisie que cette commande ne contrôle pas davantage."""
    calls, set_rows = rigged
    set_rows([{**PROJECT_ROW, "title": hostile}], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=PROJ_ID)

    rc = thingskit.cmd_add_task(_ns(list=hostile))

    out = capsys.readouterr().out
    assert rc == 0
    assert forbidden not in _single_line(out), f"émise : {out!r}"


@pytest.mark.parametrize("hostile,forbidden", HOSTILE_RENDERINGS)
def test_the_success_message_never_emits_a_control_sequence_from_the_heading(
        thingskit, monkeypatch, rigged, capsys, hostile, forbidden):
    calls, set_rows = rigged
    set_rows([PROJECT_ROW, {**HEADING_ROW, "title": hostile}], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, heading=HEAD_ID)

    rc = thingskit.cmd_add_task(_ns(list="Projet cible", heading=hostile))

    out = capsys.readouterr().out
    assert rc == 0
    assert forbidden not in _single_line(out), f"émise : {out!r}"


def test_the_rendering_keeps_accents_and_arrows_readable(
        thingskit, monkeypatch, rigged, capsys):
    """Contre-épreuve du sur-échappement : borner le rendu ne doit pas rendre
    illisible un titre français ordinaire, ni le séparateur `›`."""
    calls, set_rows = rigged
    set_rows([{**PROJECT_ROW, "title": "Migration cmux → Ghostty"}], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, project=PROJ_ID)

    thingskit.cmd_add_task(_ns(title="Rédiger la synthèse — été 2026",
                               list="Migration cmux → Ghostty"))

    out = capsys.readouterr().out
    assert "Rédiger la synthèse — été 2026" in out, out
    assert "Migration cmux → Ghostty" in out, out


def test_the_failure_message_never_emits_a_control_sequence_either(
        thingskit, rigged, capsys):
    """La branche d'échec échappait déjà — c'est l'asymétrie interne qui a
    laissé passer le défaut. Épinglée, pour qu'elle ne s'inverse pas."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(title=SPOOF, list="Projet cible"))

    err = capsys.readouterr().err
    assert rc != 0
    assert "\x1b" not in err, err


# ---------------------------------------------------------------------------
# Adversité d'ENTRÉE sur les deux surfaces jamais couvertes
# ---------------------------------------------------------------------------
HOSTILE_SQL = ["%", "_", "Sec%", "'; drop table TMTask--", "' or '1'='1"]


@pytest.mark.parametrize("value", HOSTILE_SQL)
def test_a_hostile_heading_is_matched_literally_and_refused(
        thingskit, rigged, value):
    """`--heading` interroge `TMTask` par une requête DIFFÉRENTE de celle de
    `--list` (jointure sur `project` + `type=heading`) : elle a sa propre
    couverture, elle n'hérite pas de celle de `--list`."""
    calls, set_rows = rigged
    db_file = set_rows([PROJECT_ROW, HEADING_ROW], [AREA_ROW])

    rc = thingskit.cmd_add_task(_ns(list="Projet cible", heading=value))

    assert rc != 0, f"{value!r} a résolu à un heading"
    assert calls["url"] == []
    con = sqlite3.connect(db_file)
    assert con.execute("select count(*) from TMTask").fetchone()[0] == 2
    con.close()


def test_a_heading_whose_real_title_carries_those_characters_still_resolves(
        thingskit, monkeypatch, rigged, capsys):
    """Contre-épreuve du sur-refus : `%` littéral dans un vrai titre de
    heading doit se résoudre, pas être traité comme suspect."""
    calls, set_rows = rigged
    set_rows([PROJECT_ROW, {**HEADING_ROW, "title": "Sec%"}], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, heading=HEAD_ID)

    rc = thingskit.cmd_add_task(_ns(list="Projet cible", heading="Sec%"))

    assert rc == 0, capsys.readouterr().err


@pytest.mark.parametrize("value", HOSTILE_SQL)
def test_a_hostile_title_never_reaches_the_sql_engine(
        thingskit, rigged, value):
    """`_tasks_titled` est le lecteur que ce correctif INTRODUIT — il avait
    zéro couverture d'entrée adverse. Le titre y est un paramètre lié."""
    calls, set_rows = rigged
    db_file = set_rows([PROJECT_ROW,
                        {"uuid": "OLDOLDOLDOLDOLDOLDOLD3", "title": "Rapport Q1",
                         "type": TASK, "project": PROJ_ID}], [AREA_ROW])

    thingskit.cmd_add_task(_ns(title=value, list="Projet cible"))

    con = sqlite3.connect(db_file)
    assert con.execute("select count(*) from TMTask").fetchone()[0] == 2
    con.close()


def test_a_wildcard_title_never_lets_another_task_sign_the_write(
        thingskit, monkeypatch, rigged):
    """Le cas qui DISCRIMINE `=` de `like` : si le titre était un motif, une
    tâche d'un AUTRE titre apparue entre-temps sous la cible passerait pour
    la preuve que l'écriture a abouti."""
    calls, set_rows = rigged
    db_file = set_rows([PROJECT_ROW], [AREA_ROW])

    def _lands_something_else(payload, **kw):
        calls["url"].append(payload)
        con = sqlite3.connect(db_file)
        con.execute("insert into TMTask (uuid,title,type,trashed,project,"
                    "heading,area,status) values (?,?,?,0,?,NULL,NULL,0)",
                    ("INTRUS1INTRUS1INTRUS1", "Tout autre chose", TASK, PROJ_ID))
        con.commit()
        con.close()

    monkeypatch.setattr(thingskit, "url_open", _lands_something_else)

    rc = thingskit.cmd_add_task(_ns(title="%", list="Projet cible"))

    assert rc != 0, "une tâche d'un autre titre a signé l'écriture"


@pytest.mark.parametrize("value", HOSTILE_SQL)
def test_a_hostile_list_leaves_the_database_intact(thingskit, rigged, value):
    """Complément mesuré de `…_is_matched_literally` : le refus est acquis,
    l'intégrité de la base ne l'était pas — elle n'était jamais assérée."""
    calls, set_rows = rigged
    db_file = set_rows([PROJECT_ROW], [AREA_ROW])
    before = db_file.read_bytes()

    rc = thingskit.cmd_add_task(_ns(list=value))

    assert rc != 0
    assert db_file.read_bytes() == before


def test_the_observed_placement_renders_all_four_values_the_same_way(
        thingskit, monkeypatch, rigged, capsys):
    """Cohérence de rendu DANS une seule expression, pas modèle de menace.

    `f"{u} (project={p!r}, area={ar!r}, heading={h!r})"` rendait l'uuid brut
    quand ses trois voisins immédiats étaient convertis. L'atteignabilité
    n'est **pas établie** — les uuid viennent de Things, et ceux de la base
    réelle sont tous alphanumériques : ce test ne prétend donc couvrir aucun
    vecteur, et écrire un test d'adversité sur un vecteur non démontré
    donnerait une fausse impression de couverture. Ce qu'il épingle est
    l'uniformité elle-même : une expression où trois valeurs sur quatre
    passent par `!r` est un écart connu au milieu de la garde que ce
    correctif ajoute, et c'est à ce titre qu'il se ferme.
    """
    calls, set_rows = rigged
    set_rows([PROJECT_ROW, {"uuid": PROJ2_ID, "title": "Autre projet",
                            "type": PROJECT}], [AREA_ROW])
    _rig_landing(thingskit, monkeypatch, calls, uuid="UUID-OBSERVE",
                 project=PROJ2_ID)

    rc = thingskit.cmd_add_task(_ns(list="Projet cible"))

    err = capsys.readouterr().err
    assert rc != 0
    assert "'UUID-OBSERVE' (project=" in err, (
        "l'uuid observé est rendu brut alors que ses trois voisins de la "
        f"même expression sont convertis : {err!r}")
    assert f"project={PROJ2_ID!r}" in err, err
