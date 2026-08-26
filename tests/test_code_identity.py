"""BUG-009 — l'entrée CLI du script source refuse tout interpréteur qui ne porte
pas l'identité de code du bundle signé (ADR-001, INV-001-1).

Le discriminant est la SIGNATURE de l'interpréteur en cours d'exécution, pas
une variable d'environnement ni un chemin : `codesign --verify --strict -R=…`
opposé à `sys.executable`. Un critère qu'on peut satisfaire par accident — une
variable posée, un nom de fichier, un répertoire parent — ne vaudrait rien ici,
puisque c'est précisément l'identité de code que TCC évalue.

Aucun test de ce fichier ne dépend de Things ni de la vraie base (C-4) ; les
deux tests qui touchent au bundle installé sont sautés en son absence.
"""

import os
import re
import subprocess
import time
import sys
from pathlib import Path

import pytest

from build import bundle
from conftest import INSTALLED_BUNDLE, requires_conforming_bundle

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "thingskit"
LAUNCHER = Path.home() / ".local" / "bin" / "thingskit"

# Identite de fixture (ADR-003). Aucune valeur reelle : le depot est public, et
# le script source n'en porte plus aucune.
FAKE_ID = "app.example.thingskit"
FAKE_TEAM = "TEAM000001"
REQUIREMENT = bundle.code_requirement(FAKE_ID, FAKE_TEAM)


def _sealed_bundle(tmp_path, identifier, team, name="thingskit.app"):
    """Arborescence minimale d'un bundle portant son fichier d'identite.

    Rend le chemin de l'interpreteur, celui dont la garde derive le fichier.
    """
    contents = tmp_path / name / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir(parents=True)
    if identifier is not None:
        bundle.write_code_identity(contents, identifier, team)
    exe = contents / "MacOS" / "thingskit"
    exe.write_text("", encoding="utf-8")
    return exe

# ---------------------------------------------------------------- BUG-017
# Une assertion LEXICALE sur la sortie d'un sous-processus n'éprouve pas ce
# qu'elle croit : l'interpréteur embarqué dans le bundle colore la sortie
# d'`argparse` (Python 3.14), et intercale des séquences ANSI ENTRE les mots.
# `usage: thingskit` n'y est plus une chaîne contiguë — mesuré le 2026-08-26 :
#
#   b'\x1b[1;34musage: \x1b[0m\x1b[1;35mthingskit\x1b[0m [\x1b[32m-h\x1b[0m]'
#
# Le défaut est de DEUX directions, et la seconde est la plus dangereuse : une
# assertion de présence vire au rouge (on la voit), une assertion d'ABSENCE
# vire au vert alors que la chaîne interdite est bien là, simplement coupée en
# deux par une séquence. Le remède porte donc sur la SORTIE, une fois pour
# toutes, et non sur l'assertion fautive : `_launcher_run` décolore les deux
# flux, et toute assertion présente ou future de ce module porte sur du texte
# où seul le contenu subsiste.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _decolored(text: str) -> str:
    """Texte débarrassé des séquences d'échappement ANSI (CSI)."""
    return _ANSI_RE.sub("", text)


def _launcher_run(*argv: str) -> subprocess.CompletedProcess:
    """Invoque le lanceur installé et rend ses flux DÉCOLORÉS."""
    proc = subprocess.run([str(LAUNCHER), *argv], capture_output=True, text=True)
    return subprocess.CompletedProcess(
        proc.args, proc.returncode,
        _decolored(proc.stdout), _decolored(proc.stderr),
    )


class _FakeRun:
    """Sonde `subprocess.run` : enregistre la commande, rend le code voulu."""

    def __init__(self, returncode):
        self.returncode = returncode
        self.cmd = None

    def __call__(self, cmd, **kw):
        self.cmd = cmd
        return subprocess.CompletedProcess(cmd, self.returncode, "", "")


# ---------------------------------------------------------------- BUG-009-01


def test_direct_cli_invocation_by_an_arbitrary_interpreter_is_refused():
    """L'invocation CLI directe du source par le python du venv est refusée."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "areas"],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert proc.stdout == ""
    assert "~/.local/bin/thingskit" in proc.stderr


def test_the_refusal_carries_its_own_exit_code_distinct_from_the_nominal_paths():
    """125 : ni 0/1 (`main`), ni 2 (argparse), ni 126 (sceau, lanceur)."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "areas"], capture_output=True, text=True
    )
    assert proc.returncode == 125
    assert proc.returncode != bundle.SEAL_REFUSAL_CODE


def test_the_refusal_precedes_argument_parsing(thingskit):
    """La garde passe AVANT argparse : un argument invalide rend 125, pas 2.

    C'est ce qui établit qu'aucune commande — donc aucun accès à la base de
    Things — ne peut s'exécuter sous une identité de passage.
    """
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--sûrement-pas-une-option"],
        capture_output=True, text=True,
    )
    assert proc.returncode == thingskit.IDENTITY_REFUSAL_CODE


def test_the_refusal_names_the_cause_and_the_supported_entry_point():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "areas"], capture_output=True, text=True
    )
    assert "thingskit:" in proc.stderr
    assert "identite de code" in proc.stderr
    assert "~/.local/bin/thingskit" in proc.stderr


# ------------------------------------------------------- garde, en unitaire


def test_guard_lets_through_an_interpreter_that_satisfies_the_requirement(thingskit):
    run = _FakeRun(0)
    assert thingskit.code_identity_refusal(
        "/any/exe", runner=run, requirement=REQUIREMENT) is None


def test_guard_refuses_an_interpreter_that_fails_the_requirement(thingskit):
    run = _FakeRun(3)
    message = thingskit.code_identity_refusal(
        "/opt/homebrew/bin/python3", runner=run, requirement=REQUIREMENT)
    assert message is not None
    assert "~/.local/bin/thingskit" in message


def test_guard_opposes_the_bundle_requirement_to_the_running_interpreter(thingskit):
    run = _FakeRun(0)
    thingskit.code_identity_refusal(
        "/some/python3", runner=run, requirement=REQUIREMENT)
    assert run.cmd[0] == "/usr/bin/codesign", "chemin absolu : PATH ne doit pas détourner le vérificateur"
    assert "--strict" in run.cmd
    assert f"-R={REQUIREMENT}" in run.cmd
    assert run.cmd[-1] == "/some/python3"


def test_the_guard_reads_its_requirement_from_the_sealed_file(thingskit, tmp_path):
    """Sans exigence injectée, elle vient du fichier scellé du bundle — jamais
    d'une constante du script, qui n'en porte plus aucune."""
    exe = _sealed_bundle(tmp_path, FAKE_ID, FAKE_TEAM)
    run = _FakeRun(0)
    assert thingskit.code_identity_refusal(str(exe), runner=run) is None
    assert f"-R={REQUIREMENT}" in run.cmd


def test_guard_refuses_when_codesign_cannot_even_be_run(thingskit):
    def boom(cmd, **kw):
        raise OSError("codesign absent")

    assert thingskit.code_identity_refusal(
        "/some/python3", runner=boom, requirement=REQUIREMENT) is not None


def test_the_requirement_is_composed_the_same_way_on_both_sides(thingskit):
    """Anti-dérive : le script et le build composent la MÊME exigence à partir
    des mêmes valeurs. Ils ne partagent plus de constante — ADR-003 les retire
    du script —, donc l'accord se mesure au lieu de se relire."""
    assert (thingskit.compose_code_requirement(FAKE_ID, FAKE_TEAM)
            == bundle.code_requirement(FAKE_ID, FAKE_TEAM))


# ---------------------------------------------------------------- BUG-009-02


def test_importing_the_script_as_a_module_is_untouched_by_the_guard(thingskit):
    """La garde porte sur l'entrée CLI, jamais sur l'import (conftest)."""
    assert callable(thingskit.main)
    assert callable(thingskit.cmd_areas)


def test_the_guard_is_not_evaluated_at_import_time():
    """Un import dans un interpréteur arbitraire ne refuse rien et ne sort pas."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util,sys;"
         "from importlib.machinery import SourceFileLoader;"
         f"l=SourceFileLoader('t', {str(SCRIPT)!r});"
         "s=importlib.util.spec_from_loader('t', l);"
         "m=importlib.util.module_from_spec(s); l.exec_module(m);"
         "print('IMPORT-OK')"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "IMPORT-OK" in proc.stdout


# ---------------------------------------------------------------- BUG-009-03


@requires_conforming_bundle
@pytest.mark.skipif(
    not LAUNCHER.exists(), reason="lanceur non installé sur ce poste (C-4)"
)
def test_invocation_through_the_bundle_launcher_is_let_through():
    """Le lanceur passe la garde : rc 0, sans dépendre de Things (C-4).

    Ce que le test établit, et qu'aucune de ses trois assertions ne peut
    perdre : la garde d'identité de code laisse passer une invocation par le
    lanceur (rc 0, pas de message de refus), et l'aide rendue est UTILISABLE —
    elle nomme le programme et énumère ses sous-commandes.
    """
    proc = _launcher_run("--help")
    assert proc.returncode == 0, proc.stderr
    assert "usage: thingskit" in proc.stdout
    assert "areas" in proc.stdout and "add-task" in proc.stdout
    assert "~/.local/bin/thingskit" not in proc.stderr


def test_the_decolorisation_strips_what_argparse_interleaves():
    """La correction ne vaut que si elle traite la FORME RÉELLE mesurée.

    Le texte ci-dessous est la sortie littérale de
    `~/.local/bin/thingskit --help` sur un poste portant le bundle, relevée le
    2026-08-26. Sans décoloration, `usage: thingskit` n'y est pas.
    """
    real = ("\x1b[1;34musage: \x1b[0m\x1b[1;35mthingskit\x1b[0m "
            "[\x1b[32m-h\x1b[0m]\n")
    assert "usage: thingskit" not in real
    assert _decolored(real) == "usage: thingskit [-h]\n"


def test_the_decolorisation_leaves_plain_text_untouched():
    """Une sortie non colorée — le cas d'un poste où `NO_COLOR` est posé — ne
    doit pas être altérée : sinon la garde changerait ce qu'elle éprouve."""
    plain = "usage: thingskit [-h]\n\nrefus : ~/.local/bin/thingskit\n"
    assert _decolored(plain) == plain
    assert _decolored(_decolored(plain)) == plain


def test_the_decolorisation_closes_the_absence_direction_too():
    """Le mode d'échec SILENCIEUX : une chaîne interdite coupée en deux par une
    séquence passe une assertion `not in` sur le texte brut. C'est un vert qui
    ment, et aucune relecture ne le voit."""
    split = "refus : ~/.local/\x1b[0mbin/thingskit"
    assert "~/.local/bin/thingskit" not in split  # le faux vert, reproduit
    assert "~/.local/bin/thingskit" in _decolored(split)


@requires_conforming_bundle
def test_the_real_bundle_interpreter_satisfies_the_requirement(thingskit):
    """Le vrai `codesign`, opposé au vrai interpréteur embarqué : rc 0."""
    exe = f"{INSTALLED_BUNDLE}/Contents/MacOS/thingskit"
    assert thingskit.code_identity_refusal(exe) is None


# ---------------------------------------------------------------- BUG-011-01
# La garde doit REFUSER, jamais PENDRE ni PLANTER. Un `codesign` bloqué
# (volume réseau, `AppleMobileFileIntegrity` occupé) faisait pendre toute
# invocation sans message ; une exception hors `OSError` sortait en traceback,
# avec un code 1 indiscernable d'un échec de `main()`.


class _RecordingRun:
    """Sonde qui enregistre les mots-clés reçus, pas seulement la commande."""

    def __init__(self, returncode=0):
        self.returncode = returncode
        self.cmd = None
        self.kwargs = None

    def __call__(self, cmd, **kw):
        self.cmd, self.kwargs = cmd, kw
        return subprocess.CompletedProcess(cmd, self.returncode, "", "")


def test_the_guard_bounds_the_time_it_gives_codesign(thingskit):
    """Un `timeout` est transmis à l'invocation, et il est fini et positif."""
    run = _RecordingRun()
    thingskit.code_identity_refusal(
        "/some/python3", runner=run, requirement=REQUIREMENT)
    assert "timeout" in run.kwargs, "sans timeout, un codesign bloqué fait pendre le CLI"
    timeout = run.kwargs["timeout"]
    assert isinstance(timeout, (int, float)) and 0 < timeout < 60


def test_a_codesign_that_hangs_is_refused_instead_of_hanging_the_cli(thingskit, monkeypatch):
    """Bout en bout : un vérificateur qui pend est coupé, et rend le refus.

    Aucun stub qui lève : le runner exécute un VRAI processus bloquant et se
    contente d'honorer le `timeout` que la garde lui passe — si la garde n'en
    passait pas, ce test pendrait au lieu de rougir.
    """
    monkeypatch.setattr(thingskit, "CODESIGN_TIMEOUT", 0.5)
    seen = {}

    def hanging(cmd, **kw):
        seen["timeout"] = kw.get("timeout")
        return subprocess.run(["/bin/sleep", "30"], **kw)

    started = time.monotonic()
    message = thingskit.code_identity_refusal(
        "/some/python3", runner=hanging, requirement=REQUIREMENT)
    elapsed = time.monotonic() - started
    assert seen["timeout"] == 0.5, "la constante doit être lue à l'appel, pas figée au `def`"
    assert elapsed < 10, f"la garde a pendu {elapsed:.1f}s"
    assert message is not None and "~/.local/bin/thingskit" in message


def test_the_guard_refuses_on_a_non_oserror_failure(thingskit):
    """`ValueError` (chemin à octet nul) : refus nommé, pas de propagation."""
    def boom(cmd, **kw):
        raise ValueError("embedded null byte")

    message = thingskit.code_identity_refusal(
        "/some/python3", runner=boom, requirement=REQUIREMENT)
    assert message is not None
    assert "~/.local/bin/thingskit" in message


def test_a_non_oserror_failure_exits_with_the_identity_refusal_code(thingskit):
    """Le code de sortie du refus (125) ne se confond pas avec un échec de
    `main()` (1) : exercé sur le VRAI bloc `__main__`, via `runpy`."""
    child = (
        "import runpy, subprocess, sys\n"
        "def boom(*a, **k): raise ValueError('embedded null byte')\n"
        "subprocess.run = boom\n"
        "try:\n"
        f"    runpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n"
        "except SystemExit as exc:\n"
        "    print('EXIT', exc.code)\n"
    )
    proc = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True)
    assert "Traceback" not in proc.stderr, proc.stderr
    assert f"EXIT {thingskit.IDENTITY_REFUSAL_CODE}" in proc.stdout, proc.stdout
    assert "execution refusee" in proc.stderr


# ---------------------------------------------------------------- BUG-011-02
# `-R` porte le contrôle : opposé au VRAI `codesign` et à un VRAI binaire
# Apple tiers (`/bin/ls`, présent sur tout macOS, signé par Apple mais sous
# une autre identité que le bundle). Aucune sonde ici.

APPLE_THIRD_PARTY_BINARY = "/bin/ls"


@pytest.mark.skipif(
    not os.path.exists("/usr/bin/codesign") or not os.path.exists(APPLE_THIRD_PARTY_BINARY),
    reason="poste sans `codesign` ou sans le binaire témoin (C-4)",
)
def test_the_requirement_is_what_rejects_an_apple_signed_third_party_binary():
    """Sans `-R`, codesign valide `/bin/ls` (rc 0) : « une » signature suffit.
    Avec l'exigence du bundle, il le refuse : c'est `-R` qui porte le contrôle,
    et lui seul — la seule structure de commande ne l'établissait pas."""
    from build import bundle as _bundle

    without = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", APPLE_THIRD_PARTY_BINARY],
        capture_output=True, text=True,
    ).returncode
    with_requirement = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict",
         f"-R={_bundle.code_requirement(FAKE_ID, FAKE_TEAM)}",
         APPLE_THIRD_PARTY_BINARY],
        capture_output=True, text=True,
    ).returncode
    assert without == 0, "témoin invalide : ce binaire n'est pas signé"
    assert with_requirement != 0, "-R ne discrimine pas : le contrôle ne vaut rien"


@pytest.mark.skipif(
    not os.path.exists("/usr/bin/codesign") or not os.path.exists(APPLE_THIRD_PARTY_BINARY),
    reason="poste sans `codesign` ou sans le binaire témoin (C-4)",
)
def test_the_guard_refuses_an_apple_signed_third_party_binary_for_real(thingskit):
    """La garde elle-même, sans sonde : un binaire Apple valide mais étranger
    au bundle est refusé."""
    assert thingskit.code_identity_refusal(
        APPLE_THIRD_PARTY_BINARY, requirement=REQUIREMENT) is not None


# ------------------------------------------------------------------ ADR-003
# L'identite de code attendue n'est plus une constante du script : elle est
# LUE dans le fichier scelle du bundle qui porte l'interpreteur. INV-003-1 —
# aucune configuration absente, vide, illisible ou malformee ne peut produire
# une execution : les six formes degenerees sont opposees ici au chemin de
# lecture, sur le modele de la branche `except` de la garde.


def test_the_identity_file_sits_beside_the_resolved_interpreter(thingskit, tmp_path):
    """Chemin ABSOLU derive de `sys.executable` RESOLU : un lien symbolique
    ne doit pas deplacer la source de l'attente."""
    exe = _sealed_bundle(tmp_path, FAKE_ID, FAKE_TEAM)
    link = tmp_path / "lien-vers-thingskit"
    link.symlink_to(exe)
    expected = exe.parent.parent / "Resources" / thingskit.CODE_IDENTITY_FILE
    assert Path(thingskit.code_identity_path(str(exe))) == expected
    assert Path(thingskit.code_identity_path(str(link))) == expected


def test_an_absent_identity_file_refuses_the_execution(thingskit, tmp_path):
    exe = _sealed_bundle(tmp_path, None, None)
    run = _FakeRun(0)
    message = thingskit.code_identity_refusal(str(exe), runner=run)
    assert message is not None
    assert run.cmd is None, "le refus precede toute invocation de codesign"


def test_an_unreadable_identity_file_refuses_the_execution(thingskit, tmp_path):
    exe = _sealed_bundle(tmp_path, None, None)
    (exe.parent.parent / "Resources" / thingskit.CODE_IDENTITY_FILE).mkdir()
    assert thingskit.code_identity_refusal(str(exe), runner=_FakeRun(0)) is not None


def test_an_undecodable_identity_file_refuses_the_execution(thingskit, tmp_path):
    exe = _sealed_bundle(tmp_path, None, None)
    (exe.parent.parent / "Resources" / thingskit.CODE_IDENTITY_FILE).write_bytes(
        b"bundle_identifier = \xff\xfe\n")
    assert thingskit.code_identity_refusal(str(exe), runner=_FakeRun(0)) is not None


DEGENERATE_IDENTITY_FILES = [
    ("", "vide"),
    ("# rien\n", "commentaires seuls"),
    ("bundle_identifier\n", "aucun separateur"),
    (f"bundle_identifier = {FAKE_ID}\n", "champ manquant"),
    (f"team_identifier = {FAKE_TEAM}\n", "champ manquant"),
    (f"bundle_identifier =\nteam_identifier = {FAKE_TEAM}\n", "valeur vide"),
    (f"bundle_identifier = {FAKE_ID}\nteam_identifier =   \n", "valeur blanche"),
    (f'bundle_identifier = app.evil" or true\nteam_identifier = {FAKE_TEAM}\n',
     "clause injectee"),
    (f"bundle_identifier = {FAKE_ID}\nteam_identifier = team000001\n", "hors forme"),
    (f"bundle_identifier = {FAKE_ID}\nteam_identifier = {FAKE_TEAM}\n"
     "install_path = /tmp/x.app\n", "champ inconnu"),
    (f"bundle_identifier = {FAKE_ID}\nbundle_identifier = app.autre\n"
     f"team_identifier = {FAKE_TEAM}\n", "champ duplique"),
    (f"bundle_identifier = {FAKE_ID}\x00\nteam_identifier = {FAKE_TEAM}\n",
     "octet nul"),
]


@pytest.mark.parametrize(
    "text,label", DEGENERATE_IDENTITY_FILES,
    ids=[label for _text, label in DEGENERATE_IDENTITY_FILES],
)
def test_a_degenerate_identity_file_refuses_the_execution(
        thingskit, tmp_path, text, label):
    """Fail-closed sur la CLASSE, pas sur le cas rencontre : chacune de ces
    formes laisserait autrement le CLI s'executer sous une attente que
    personne n'a ecrite."""
    exe = _sealed_bundle(tmp_path, None, None)
    (exe.parent.parent / "Resources" / thingskit.CODE_IDENTITY_FILE).write_text(
        text, encoding="utf-8")
    run = _FakeRun(0)
    assert thingskit.code_identity_refusal(str(exe), runner=run) is not None, label
    assert run.cmd is None, "aucune verification n'est lancee sur une attente non etablie"


def test_a_wellformed_identity_file_is_let_through(thingskit, tmp_path):
    """Contre-epreuve : un fail-closed qui refuse TOUT ne prouve rien."""
    exe = _sealed_bundle(tmp_path, FAKE_ID, FAKE_TEAM)
    run = _FakeRun(0)
    assert thingskit.code_identity_refusal(str(exe), runner=run) is None
    assert run.cmd is not None


def test_the_configuration_refusal_names_the_file_and_the_entry_point(
        thingskit, tmp_path):
    exe = _sealed_bundle(tmp_path, None, None)
    message = thingskit.code_identity_refusal(str(exe), runner=_FakeRun(0))
    assert thingskit.CODE_IDENTITY_FILE in message
    assert "~/.local/bin/thingskit" in message


def test_the_refusal_never_relays_a_control_sequence_from_the_file(
        thingskit, tmp_path):
    """Le fichier peut n'etre pas scelle — c'est precisement le cas ou la
    garde refuse. Sa valeur ne doit donc pas atteindre le terminal telle
    quelle : la conversion est `!r`, jamais un filtre de caracteres enumeres.
    """
    exe = _sealed_bundle(tmp_path, None, None)
    (exe.parent.parent / "Resources" / thingskit.CODE_IDENTITY_FILE).write_text(
        "bundle_identifier = app.evil\x1b[2K\rtout va bien\n"
        f"team_identifier = {FAKE_TEAM}\n", encoding="utf-8")
    message = thingskit.code_identity_refusal(str(exe), runner=_FakeRun(0))
    assert "\x1b" not in message and "\r" not in message


def test_the_identity_refusal_of_a_bare_interpreter_carries_its_own_exit_code():
    """Bout en bout : le python du venv ne vit dans aucun bundle, donc aucune
    attente n'est etablie — 125, et pas un code du chemin nominal."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "areas"], capture_output=True, text=True)
    assert proc.returncode == 125
    assert proc.stdout == ""
    assert "Traceback" not in proc.stderr


# ------------------------------------------------------------------ ADR-003
# Le neutraliseur du SITE d'interpolation, éprouvé pour lui-même.
#
# Défaut relevé en review (2026-08-26) : `_requirement_value` n'était exercé
# par aucun test. Un `return value` posé en tête de la fonction — qui la rend
# passe-plat et supprime tout refus — laissait la suite ENTIÈRE verte (912
# passed, mesuré). Le docstring affirmait pourtant que « le contrôle est
# réaffirmé au site d'interpolation » : une affirmation d'établissement
# qu'aucune relecture de code ne réfute, puisque le code, lui, était correct.
#
# Ces tests visent `compose_code_requirement`, qui ne PARSE pas — un test
# écrit contre `parse_code_identity` ne tue pas la mutation, puisqu'il
# n'atteint jamais le site d'interpolation.

INJECTED_IDENTIFIERS = [
    'app.evil" or true',                        # clause injectée dans l'exigence
    'app.evil" and anchor apple',
    'app.evil"',
    "app evil",
    "app.evil\x1b[2K",
    "app.evil\x00",
    "app.evil‮",
    "-app.evil",
    "",
    "   ",
    "app." + "e" * 200,
]

INJECTED_TEAMS = [
    'X" or true',
    'X" and anchor trusted',
    "team000001",
    "TEAM00001",
    "TEAM0000012",
    "TEAM 00001",
    "TEAM00000\x1b",
    "",
    "   ",
]


@pytest.mark.parametrize("identifier", INJECTED_IDENTIFIERS)
def test_the_composition_refuses_a_hostile_identifier_at_the_site(
        thingskit, identifier):
    """La forme est réaffirmée LÀ OÙ la valeur rencontre le gabarit.

    `parse_code_identity` l'a déjà refusée en amont sur tout chemin de
    production : c'est de la défense en profondeur, et une défense en
    profondeur non testée n'est pas une défense — c'est une phrase.
    """
    with pytest.raises(ValueError):
        thingskit.compose_code_requirement(identifier, FAKE_TEAM)


@pytest.mark.parametrize("team", INJECTED_TEAMS)
def test_the_composition_refuses_a_hostile_team_at_the_site(thingskit, team):
    with pytest.raises(ValueError):
        thingskit.compose_code_requirement(FAKE_ID, team)


def test_the_refusal_names_the_offending_value_without_relaying_it(thingskit):
    """Le message CONVERTIT la valeur (`!r`) : elle est d'origine non
    contrôlée, et une séquence de contrôle recopiée ferait lire au mainteneur
    autre chose que ce que la garde a refusé."""
    with pytest.raises(ValueError) as exc:
        thingskit.compose_code_requirement("app.evil\x1b[2K\rautre", FAKE_TEAM)
    assert "\x1b" not in str(exc.value) and "\r" not in str(exc.value)


@pytest.mark.parametrize("identifier,team", [
    ("app.example.thingskit", "TEAM000001"),
    ("thingskit", "OLDTEAM001"),
    ("app.example.tools.thingskit-2", "NEWTEAM002"),
])
def test_the_composition_accepts_a_wellformed_pair(thingskit, identifier, team):
    """Contre-épreuve : un neutraliseur qui refuserait TOUT ne prouve rien."""
    requirement = thingskit.compose_code_requirement(identifier, team)
    assert f'identifier "{identifier}"' in requirement
    assert f'certificate leaf[subject.OU]="{team}"' in requirement


def test_no_composed_requirement_can_carry_an_injected_clause(thingskit):
    """La propriété, énoncée en une fois : aucune valeur refusée par la forme
    ne produit d'exigence, donc aucune ne peut y greffer une clause."""
    composed = []
    for identifier in INJECTED_IDENTIFIERS:
        try:
            composed.append(thingskit.compose_code_requirement(identifier, FAKE_TEAM))
        except ValueError:
            pass
    assert composed == []


def test_the_cli_does_not_derive_a_path_from_its_own_location():
    """INV-003-7, seconde route : le balayage de littéraux ne verrait pas un
    chemin RECONSTRUIT. `__file__` est la voie par laquelle un script atteint
    son propre dépôt — donc `build/` — sans jamais le nommer."""
    assert "__file__" not in SCRIPT.read_text(encoding="utf-8")
