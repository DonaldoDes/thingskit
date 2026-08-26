"""`thingskit reschedule-task` — replanification d'une tâche déjà créée (US-002).

Surface d'écriture : AppleScript ciblé, choisie sur ce que les surfaces
exposent RÉELLEMENT — mesuré le 2026-08-17 sur une tâche jetable réelle, pas
déduit de la documentation :

  - Le schéma d'URL (`things:///json` avec `"operation": "update"`) est un
    **no-op SILENCIEUX** sans jeton d'authentification : `open` réussit, rien
    ne change en base. C'est le pire mode d'échec du projet — il est écarté,
    pas « tenté ».
  - `activation date` est déclarée `access="r"` : la poser lève -10006. Le
    `when` se change donc par la commande `schedule`, jamais par la propriété.
  - `date "2026-09-05"` est interprété selon la LOCALE de la session
    AppleScript : mesuré, il a produit 2011-03-19. Toute date est donc
    construite programmatiquement (`set year/month/day of (current date)`),
    jamais par un littéral de date.
  - `set due date … to missing value` lève -1700 ; c'est `delete due date` qui
    retire l'échéance (mesuré : deadline -> NULL).
  - `schedule … for (current date)` pose start=2 + date du jour, soit « À
    venir » — PAS « Aujourd'hui » (start=1 + date). Aujourd'hui s'obtient par
    `move … to list`, dont les libellés sont LOCALISÉS (`exists list "Anytime"`
    -> false, `"À tout moment"` -> true sur ce poste).
  - Aucune liste « Ce soir » / « Evening » n'existe (`exists list` -> false
    pour les trois libellés candidats), et aucune surface AppleScript n'expose
    `reminderTime` : `evening` et le suffixe `@HH:MM` sont REFUSÉS, jamais
    silencieusement ignorés.
  - `schedule` sur une tâche `completed` RÉUSSIT et la laisse `completed` :
    la date est posée sur un objet qui n'apparaît dans aucune liste. D'où le
    refus en amont (cf. tests de garde d'état).

Ces tests ne touchent jamais l'application ni la vraie base : `db_path` est
redirigée vers une base jetable, `osa`/`ensure_running`/`time.sleep` mockés.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3

import pytest

from test_tasks_json import _make_db


TARGET = "AAAAAAAAAAAAAAAAAAAAAA"
OTHER = "BBBBBBBBBBBBBBBBBBBBBB"
OPEN, CANCELED, COMPLETED = 0, 2, 3


def _ns(id=None, title=None, when=None, deadline=None, clear_deadline=False):
    return argparse.Namespace(id=id, title=title, when=when, deadline=deadline,
                              clear_deadline=clear_deadline)


def _encode(iso: str) -> int:
    """Inverse de `decode_things_date`, pour fabriquer l'état attendu en base."""
    y, m, d = (int(x) for x in iso.split("-"))
    return (y << 16) | (m << 12) | (d << 7)


def _today() -> str:
    return dt.date.today().isoformat()


def _tomorrow() -> str:
    return (dt.date.today() + dt.timedelta(days=1)).isoformat()


@pytest.fixture
def rigged(thingskit, monkeypatch, tmp_path):
    """`osa` inerte qui enregistre ses appels — aucun effet en base."""
    calls = {"osa": [], "db": None}

    def _set_rows(rows):
        db_file = _make_db(tmp_path, rows)
        monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
        calls["db"] = db_file
        return db_file

    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "osa",
                        lambda script: (calls["osa"].append(script), (0, ""))[1])
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    return calls, _set_rows


def _rig_effective_osa(thingskit, monkeypatch, calls, **cols):
    """`osa` qui simule l'effet réel de l'application sur la ligne cible."""
    db_file = calls["db"]

    def _fake(script):
        calls["osa"].append(script)
        con = sqlite3.connect(db_file)
        for col, val in cols.items():
            con.execute(f"update TMTask set {col}=? where uuid=?", (val, TARGET))
        con.commit()
        con.close()
        return 0, ""

    monkeypatch.setattr(thingskit, "osa", _fake)


# --- interprétation de --when (pure) ---------------------------------------

@pytest.mark.parametrize("value,kind", [
    ("today", "today"),
    ("tomorrow", "date"),
    ("anytime", "anytime"),
    ("someday", "someday"),
    ("2026-09-05", "date"),
])
def test_parse_when_accepts_the_same_vocabulary_as_add_task(thingskit, value, kind):
    parsed, err = thingskit._parse_when(value)
    assert err is None, err
    assert parsed[0] == kind


def test_parse_when_tomorrow_resolves_to_the_next_calendar_day(thingskit):
    parsed, err = thingskit._parse_when("tomorrow")
    assert err is None
    assert parsed == ("date", _tomorrow())


def test_parse_when_today_iso_is_routed_to_the_today_list(thingskit):
    """`schedule` pour la date du jour pose start=2 (« À venir »), mesuré —
    pas « Aujourd'hui ». Une date ISO valant aujourd'hui doit donc emprunter la
    même voie que le mot-clé `today`, sinon la tâche atterrit dans la mauvaise
    liste."""
    parsed, err = thingskit._parse_when(_today())
    assert err is None
    assert parsed[0] == "today"


def test_parse_when_refuses_evening_no_such_list_exists(thingskit):
    parsed, err = thingskit._parse_when("evening")
    assert parsed is None
    assert err and "evening" in err.lower()


def test_parse_when_refuses_alarm_suffix_no_applescript_surface(thingskit):
    parsed, err = thingskit._parse_when("2026-09-05@09:00")
    assert parsed is None
    assert err and "@" in err


@pytest.mark.parametrize("value", ["2026-13-01", "2026-02-30", "05/09/2026",
                                   "demain", "", "2026-9-5", "20260905"])
def test_parse_when_refuses_malformed_values(thingskit, value):
    parsed, err = thingskit._parse_when(value)
    assert parsed is None and err


@pytest.mark.parametrize("value", ["2026-13-01", "2026-02-30", "05/09/2026",
                                   "", "2026-9-5", "today", "2026-09-05@09:00"])
def test_parse_deadline_refuses_anything_but_an_iso_day(thingskit, value):
    iso, err = thingskit._parse_deadline(value)
    assert iso is None and err


def test_parse_deadline_accepts_iso(thingskit):
    iso, err = thingskit._parse_deadline("2026-09-25")
    assert err is None and iso == "2026-09-25"


# --- construction du script (pure) -----------------------------------------

def test_date_is_built_programmatically_never_as_a_locale_literal(thingskit):
    """`date "2026-09-05"` a été mesuré comme donnant 2011-03-19 : le script ne
    doit contenir aucun littéral de date, mais un assemblage year/month/day."""
    script = thingskit._build_reschedule_script(TARGET, ("date", "2026-09-05"),
                                                "2026-09-25", False)
    assert 'date "2026-09-05"' not in script
    assert "set year of" in script and "set month of" in script
    assert "2026" in script and "9" in script and "5" in script


def test_deadline_removal_uses_delete_never_missing_value(thingskit):
    """`set due date … to missing value` lève -1700 (mesuré) ; `delete due
    date` retire l'échéance."""
    script = thingskit._build_reschedule_script(TARGET, None, None, True)
    assert "delete due date" in script
    assert "missing value" not in script


def test_list_labels_are_tried_and_never_a_single_hardcoded_literal(thingskit):
    """`exists list "Anytime"` -> false sur un poste français : les libellés de
    liste sont LOCALISÉS, comme les libellés de menu de `create-heading`. Le
    script essaie chaque libellé connu et n'agit que sur celui qui existe."""
    for kind in ("today", "anytime", "someday"):
        labels = thingskit.THINGS_LIST_LABELS[kind]
        assert len(labels) > 1, kind
        script = thingskit._build_reschedule_script(TARGET, (kind, None), None, False)
        for label in labels:
            assert thingskit._esc(label) in script
        assert "exists list" in script
        assert thingskit._NO_LIST_MARKER in script


def test_no_list_found_is_reported_not_silently_ignored(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    thingskit_module = thingskit
    thingskit_module.osa  # rigged
    ok, msg = thingskit._interpret_reschedule_outcome(
        1, f"execution error: {thingskit._NO_LIST_MARKER}")
    assert ok is False and "libellé" in msg


def test_id_is_escaped_into_the_applescript(thingskit):
    script = thingskit._build_reschedule_script(TARGET, ("anytime", None), None, False)
    assert f'"{TARGET}"' in script


# --- adressage : refus explicite, jamais « la première » -------------------

def test_missing_id_and_title_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Solo"}])
    assert thingskit.cmd_reschedule_task(_ns(when="anytime")) != 0
    assert calls["osa"] == []


def test_no_change_requested_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Solo"}])
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET)) != 0
    assert calls["osa"] == []


def test_malformed_uuid_refuses_before_any_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Solo"}])
    assert thingskit.cmd_reschedule_task(_ns(id="pas un uuid !!", when="anytime")) != 0
    assert calls["osa"] == []


def test_unknown_id_refuses_before_any_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Solo"}])
    assert thingskit.cmd_reschedule_task(_ns(id=OTHER, when="anytime")) != 0
    assert calls["osa"] == []


def test_title_no_match_refuses(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Solo"}])
    assert thingskit.cmd_reschedule_task(_ns(title="Absente", when="anytime")) != 0
    assert calls["osa"] == []


def test_ambiguous_title_refuses_without_touching_anything(thingskit, rigged):
    calls, set_rows = rigged
    db = set_rows([{"uuid": TARGET, "title": "Homonyme"},
                   {"uuid": OTHER, "title": "Homonyme"}])
    before = db.read_bytes()
    assert thingskit.cmd_reschedule_task(_ns(title="Homonyme", when="anytime")) != 0
    assert calls["osa"] == []
    assert db.read_bytes() == before


def test_title_resolution_reaches_a_task_under_a_heading(thingskit, monkeypatch, rigged):
    """`_resolve_task_by_title` ne joint jamais sur `project` : une tâche sous
    heading a cette colonne VIDE et resterait inatteignable."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Sous heading", "project": None,
               "heading": "HHHHHHHHHHHHHHHHHHHHHH"}])
    _rig_effective_osa(thingskit, monkeypatch, calls, start=1, startDate=None)
    assert thingskit.cmd_reschedule_task(_ns(title="Sous heading", when="anytime")) == 0


def test_invalid_when_refuses_before_any_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="2026-02-30")) != 0
    assert calls["osa"] == []


# --- gardes d'état : décidées pour CETTE opération --------------------------

def test_trashed_task_is_refused_by_id_without_any_osa_call(thingskit, rigged):
    """Poser une date sur un objet jeté : il n'apparaît dans aucune liste et
    disparaît au vidage de la corbeille. Le refus PRÉCÈDE tout envoi — un refus
    après écriture n'est pas un refus."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Jetée", "trashed": 1}])
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="anytime")) != 0
    assert calls["osa"] == []


def test_trashed_task_is_unreachable_by_title_without_any_osa_call(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Jetée", "trashed": 1}])
    assert thingskit.cmd_reschedule_task(_ns(title="Jetée", when="anytime")) != 0
    assert calls["osa"] == []


@pytest.mark.parametrize("status", [COMPLETED, CANCELED])
def test_terminal_task_is_refused_without_any_osa_call(thingskit, rigged, status):
    """MESURÉ le 2026-08-17 : `schedule` sur une tâche `completed` retourne 0,
    pose bien la date, et laisse le statut `completed` — la date atterrit sur un
    objet qui n'apparaît dans aucune liste de planification. C'est le mode
    d'échec « commande envoyée ≠ effet utile ». La vérification post-action ne
    le rattraperait PAS (la date est bien constatée en base) : d'où un refus en
    amont, propre à cette opération."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Terminée", "status": status}])
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="tomorrow")) != 0
    assert calls["osa"] == []


def test_trashed_homonym_neither_blocks_nor_diverts_the_active_task(thingskit,
                                                                   monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Homonyme"},
              {"uuid": OTHER, "title": "Homonyme", "trashed": 1}])
    _rig_effective_osa(thingskit, monkeypatch, calls, start=1, startDate=None)
    assert thingskit.cmd_reschedule_task(_ns(title="Homonyme", when="anytime")) == 0
    assert TARGET in calls["osa"][0] and OTHER not in calls["osa"][0]


# --- effet constaté : 0 ne signifie jamais « commande envoyée » ------------

def test_when_date_observed_in_db_succeeds_by_id(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "start": 1, "startDate": None}])
    _rig_effective_osa(thingskit, monkeypatch, calls,
                       start=2, startDate=_encode("2026-09-05"))
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="2026-09-05")) == 0


def test_deadline_observed_in_db_succeeds_by_title(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Par titre"}])
    _rig_effective_osa(thingskit, monkeypatch, calls, deadline=_encode("2026-09-25"))
    assert thingskit.cmd_reschedule_task(_ns(title="Par titre",
                                             deadline="2026-09-25")) == 0


def test_today_lands_on_start_1_with_the_day_date(thingskit, monkeypatch, rigged):
    """Table de vérité mesurée : « Aujourd'hui » = start=1 AVEC date. start=2 +
    date du jour serait « À venir » — un quasi-succès n'est pas un succès."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    _rig_effective_osa(thingskit, monkeypatch, calls,
                       start=1, startDate=_encode(_today()))
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="today")) == 0


def test_today_landing_on_upcoming_is_a_failure_not_a_near_success(thingskit,
                                                                  monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    _rig_effective_osa(thingskit, monkeypatch, calls,
                       start=2, startDate=_encode(_today()))
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="today")) != 0


def test_someday_requires_start_2_without_date(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "start": 1,
               "startDate": _encode("2026-09-05")}])
    _rig_effective_osa(thingskit, monkeypatch, calls, start=2, startDate=None)
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="someday")) == 0


def test_failure_when_effect_not_observed(thingskit, rigged):
    """`osa` inerte : la commande a « été envoyée » mais rien n'est constaté en
    base — code retour non nul, jamais 0."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "start": 1, "startDate": None}])
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="2026-09-05")) != 0
    assert calls["osa"], "la surface applicative doit bien avoir été sollicitée"


def test_failure_when_stored_date_differs_by_one_day(thingskit, monkeypatch, rigged):
    """La vérification porte sur la valeur EXACTE, jamais sur « la date a
    changé » — le piège mesuré était précisément une date décalée (littéral
    interprété selon la locale)."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    _rig_effective_osa(thingskit, monkeypatch, calls,
                       start=2, startDate=_encode("2026-09-06"))
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="2026-09-05")) != 0


def test_failure_when_only_one_of_the_two_dates_landed(thingskit, monkeypatch, rigged):
    """`--when` et `--deadline` dans le même appel : un succès partiel est un
    échec, sinon l'appelant croit les deux posées."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    _rig_effective_osa(thingskit, monkeypatch, calls,
                       start=2, startDate=_encode("2026-09-05"))
    assert thingskit.cmd_reschedule_task(
        _ns(id=TARGET, when="2026-09-05", deadline="2026-09-25")) != 0


def test_applescript_failure_is_reported_before_any_claim_of_success(thingskit,
                                                                    monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    monkeypatch.setattr(thingskit, "osa", lambda s: (1, "execution error: -1728"))
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="anytime")) != 0


# --- retrait d'une date déjà posée -----------------------------------------

def test_clear_deadline_is_observed_as_absent_in_db(thingskit, monkeypatch, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "deadline": _encode("2026-09-25")}])
    _rig_effective_osa(thingskit, monkeypatch, calls, deadline=None)
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, clear_deadline=True)) == 0
    row = sqlite3.connect(calls["db"]).execute(
        "select deadline from TMTask where uuid=?", (TARGET,)).fetchone()
    assert row[0] is None


def test_clear_deadline_fails_when_the_deadline_survives(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "deadline": _encode("2026-09-25")}])
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, clear_deadline=True)) != 0


def test_clearing_a_when_date_is_observed_as_absent_in_db(thingskit, monkeypatch, rigged):
    """`--when anytime` EST le retrait d'une date de planification : mesuré,
    `move … to list "À tout moment"` remet startDate à NULL."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "start": 2,
               "startDate": _encode("2026-09-05")}])
    _rig_effective_osa(thingskit, monkeypatch, calls, start=1, startDate=None)
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="anytime")) == 0
    row = sqlite3.connect(calls["db"]).execute(
        "select startDate from TMTask where uuid=?", (TARGET,)).fetchone()
    assert row[0] is None


def test_deadline_and_clear_deadline_together_are_refused(thingskit, rigged):
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    assert thingskit.cmd_reschedule_task(
        _ns(id=TARGET, deadline="2026-09-25", clear_deadline=True)) != 0
    assert calls["osa"] == []


# --- l'UUID ne change jamais ------------------------------------------------

def test_uuid_is_unchanged_by_a_reschedule(thingskit, monkeypatch, rigged):
    """Raison d'être de l'US : un `things:///` déjà posé dans le vault doit
    survivre à la replanification."""
    calls, set_rows = rigged
    db = set_rows([{"uuid": TARGET, "title": "Cible"}])
    _rig_effective_osa(thingskit, monkeypatch, calls,
                       start=2, startDate=_encode("2026-09-05"))
    assert thingskit.cmd_reschedule_task(_ns(id=TARGET, when="2026-09-05")) == 0
    rows = sqlite3.connect(db).execute("select uuid from TMTask").fetchall()
    assert rows == [(TARGET,)]


# --- invariants transverses -------------------------------------------------

def test_no_sql_write_reaches_the_database(thingskit, monkeypatch, tmp_path):
    """Le CLI ne modifie jamais la base : `osa` rendu inerte, le fichier doit
    rester rigoureusement inchangé, octet pour octet."""
    db_file = _make_db(tmp_path, [{"uuid": TARGET, "title": "Cible"}])
    monkeypatch.setattr(thingskit, "db_path", lambda: db_file)
    monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
    monkeypatch.setattr(thingskit, "time",
                        type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(thingskit, "osa", lambda script: (0, ""))

    before = db_file.read_bytes()
    thingskit.cmd_reschedule_task(_ns(id=TARGET, when="2026-09-05"))
    thingskit.cmd_reschedule_task(_ns(id=TARGET, deadline="2026-09-25"))
    thingskit.cmd_reschedule_task(_ns(id=TARGET, clear_deadline=True))
    assert db_file.read_bytes() == before


def test_url_scheme_update_is_never_used(thingskit, monkeypatch, rigged):
    """Mesuré : `things:///json` avec `operation: update` est un no-op
    SILENCIEUX sans jeton d'authentification. Cette surface ne doit pas être
    sollicitée — un ordre envoyé sans effet est exactement ce que le projet
    refuse."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible"}])
    urls = []
    monkeypatch.setattr(thingskit, "url_open",
                        lambda payload, background=True: urls.append(payload))
    thingskit.cmd_reschedule_task(_ns(id=TARGET, when="2026-09-05"))
    assert urls == []


def test_registered_in_cli_help(thingskit, run_cli):
    """La sous-commande est exposée par l'aide du CLI et documentée dans le
    bloc Usage du module."""
    code, out, _ = run_cli(["--help"])
    assert code == 0
    assert "reschedule-task" in out
    assert "reschedule-task" in (thingskit.__doc__ or "")
    # L'aide GÉNÉRALE porte le bloc Usage du module : le nom y figure même si
    # la sous-commande n'est plus câblée au parseur. Mesuré le 2026-08-26 —
    # renommer `add("reschedule-task", …)` dans `bin/thingskit` laissait les SIX tests de
    # cette famille au vert, celui-ci compris. Seule l'invocation de la
    # sous-commande éprouve le câblage : argparse rend 2 si elle n'existe pas.
    code, _, err = run_cli(["reschedule-task", "--help"])
    assert code == 0, err


# --- la course entre le dernier sondage et la composition du message -------

def test_the_failure_message_uses_the_observed_problems_not_a_fresh_query(
        thingskit, monkeypatch, rigged, capsys):
    """Même forme que `cmd_move_task`, moins visible : un second appel rend une
    liste VIDE, et `" ; ".join([])` produit une chaîne vide — l'échec ne nomme
    plus l'écart constaté."""
    calls, set_rows = rigged
    db_file = set_rows([{"uuid": TARGET, "title": "Cible", "start": 1,
                         "startDate": None}])

    def _wait_then_land(probe, *args, **kwargs):
        observed = probe()          # dernier sondage : rien n'a encore atterri
        con = sqlite3.connect(db_file)
        con.execute("update TMTask set start=?, startDate=? where uuid=?",
                    (2, _encode("2026-09-05"), TARGET))
        con.commit()
        con.close()
        return observed

    monkeypatch.setattr(thingskit, "wait_for_effect", _wait_then_land)

    rc = thingskit.cmd_reschedule_task(_ns(id=TARGET, when="2026-09-05"))

    assert rc != 0
    err = capsys.readouterr().err
    assert "when :" in err, err
    assert "commande envoyée' : ." not in err, err


def test_a_database_unreadable_throughout_is_said_not_invented(
        thingskit, monkeypatch, rigged, capsys):
    """Même repli que `cmd_move_task`, jamais exercé jusqu'ici : `" ; ".join([])`
    produit une chaîne vide, et l'échec cesserait de nommer quoi que ce soit."""
    calls, set_rows = rigged
    set_rows([{"uuid": TARGET, "title": "Cible", "start": 1, "startDate": None}])

    real_q = thingskit.q
    state = {"sent": False}

    def _q(sql, args=()):
        if state["sent"]:
            raise sqlite3.OperationalError("database is locked")
        return real_q(sql, args)

    def _osa(script):
        state["sent"] = True
        return 0, ""

    monkeypatch.setattr(thingskit, "q", _q)
    monkeypatch.setattr(thingskit, "osa", _osa)

    rc = thingskit.cmd_reschedule_task(_ns(id=TARGET, when="2026-09-05"))

    assert rc != 0
    err = capsys.readouterr().err
    assert "lecture de la base refusée pendant toute l'attente" in err, err
