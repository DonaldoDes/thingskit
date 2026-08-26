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
from conftest import requires_conforming_bundle

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "thingskit"
LAUNCHER = Path.home() / ".local" / "bin" / "thingskit"

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
    assert "app.sowell.thingskit" in proc.stderr


# ------------------------------------------------------- garde, en unitaire


def test_guard_lets_through_an_interpreter_that_satisfies_the_requirement(thingskit):
    run = _FakeRun(0)
    assert thingskit.code_identity_refusal("/any/exe", runner=run) is None


def test_guard_refuses_an_interpreter_that_fails_the_requirement(thingskit):
    run = _FakeRun(3)
    message = thingskit.code_identity_refusal("/opt/homebrew/bin/python3", runner=run)
    assert message is not None
    assert "~/.local/bin/thingskit" in message


def test_guard_opposes_the_bundle_requirement_to_the_running_interpreter(thingskit):
    run = _FakeRun(0)
    thingskit.code_identity_refusal("/some/python3", runner=run)
    assert run.cmd[0] == "/usr/bin/codesign", "chemin absolu : PATH ne doit pas détourner le vérificateur"
    assert "--strict" in run.cmd
    assert f"-R={thingskit.CODE_REQUIREMENT}" in run.cmd
    assert run.cmd[-1] == "/some/python3"


def test_guard_refuses_when_codesign_cannot_even_be_run(thingskit):
    def boom(cmd, **kw):
        raise OSError("codesign absent")

    assert thingskit.code_identity_refusal("/some/python3", runner=boom) is not None


def test_the_requirement_matches_the_one_the_build_signs_with(thingskit):
    """Anti-dérive : le script et le build parlent de la MÊME identité."""
    assert thingskit.CODE_REQUIREMENT == bundle.CODE_REQUIREMENT
    assert thingskit.BUNDLE_IDENTIFIER == bundle.BUNDLE_IDENTIFIER
    assert thingskit.TEAM_IDENTIFIER == bundle.TEAM_IDENTIFIER


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
    exe = f"{bundle.INSTALL_PATH}/Contents/MacOS/thingskit"
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
    thingskit.code_identity_refusal("/some/python3", runner=run)
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
    message = thingskit.code_identity_refusal("/some/python3", runner=hanging)
    elapsed = time.monotonic() - started
    assert seen["timeout"] == 0.5, "la constante doit être lue à l'appel, pas figée au `def`"
    assert elapsed < 10, f"la garde a pendu {elapsed:.1f}s"
    assert message is not None and "~/.local/bin/thingskit" in message


def test_the_guard_refuses_on_a_non_oserror_failure(thingskit):
    """`ValueError` (chemin à octet nul) : refus nommé, pas de propagation."""
    def boom(cmd, **kw):
        raise ValueError("embedded null byte")

    message = thingskit.code_identity_refusal("/some/python3", runner=boom)
    assert message is not None
    assert "app.sowell.thingskit" in message


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
         f"-R={_bundle.CODE_REQUIREMENT}", APPLE_THIRD_PARTY_BINARY],
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
    assert thingskit.code_identity_refusal(APPLE_THIRD_PARTY_BINARY) is not None
