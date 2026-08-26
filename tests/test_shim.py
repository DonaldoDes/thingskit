"""ADR-002 — le shim Mach-O qui décide de la responsabilité de processus.

Le shim est éprouvé en le COMPILANT et en l'EXÉCUTANT, jamais en relisant sa
source. Le disclaim est mesuré à la seule chose qui en réponde vraiment :
`responsibility_get_pid_responsible_for_pid(getpid()) == getpid()` dans le
processus final. Une garde qui vérifierait que la bonne liste est écrite dans
le bon fichier ne prouverait pas que le processus est son propre responsable —
et c'est cela, et cela seul, qui décide si l'invite TCC revient.

Contrainte C-4 / INV-001-3 reconduite : rien ici ne dépend de
`/Applications/thingskit.app`. Le shim est construit dans `tmp_path`, contre
une cible jetable.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from build import bundle
from conftest import installed_bundle_requirement, requires_conforming_bundle

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "bin" / "thingskit"

# ADR-003 : l'exigence de code n'est plus une constante du module — elle est
# composée depuis la configuration de construction. Les fixtures en emploient
# une, synthétique ; les tests qui touchent au bundle installé lisent la
# sienne dans son propre fichier scellé.
FAKE_ID = "app.example.thingskit"
FAKE_TEAM = "TEAM000001"
REQUIREMENT = bundle.code_requirement(FAKE_ID, FAKE_TEAM)


# ------------------------------------------------------------------ outillage


_PROBE_C = r"""
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
extern int responsibility_get_pid_responsible_for_pid(pid_t);
int main(int argc, char **argv) {
    pid_t me = getpid();
    printf("pid=%d\n", me);
    printf("disclaimed=%d\n",
           responsibility_get_pid_responsible_for_pid(me) == me ? 1 : 0);
    for (int i = 0; i < argc; i++) printf("argv=%s\n", argv[i]);
    const char *marker = getenv("THINGSKIT_PROBE_MARKER");
    printf("marker=%s\n", marker == NULL ? "" : marker);
    struct sigaction chld;
    sigaction(SIGCHLD, NULL, &chld);
    printf("sigchld_ignored=%d\n", chld.sa_handler == SIG_IGN ? 1 : 0);
    fprintf(stderr, "probe-stderr\n");
    for (int i = 1; i < argc; i++)
        if (strncmp(argv[i], "--exit=", 7) == 0) return atoi(argv[i] + 7);
    return 0;
}
"""


def _probe_bundle(tmp_path, name="thingskit.app"):
    """Faux `.app` dont l'exécutable est une sonde compilée.

    La sonde rend ce que seul le processus final peut dire : son PID et son
    propre responsable.
    """
    app = tmp_path / name
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir(parents=True)
    (app / "Contents" / "Resources" / "thingskit").write_text("# copie\n")
    src = tmp_path / "probe.c"
    src.write_text(_PROBE_C, encoding="utf-8")
    subprocess.run(
        [bundle.CC, "-o", str(app / "Contents" / "MacOS" / "thingskit"), str(src)],
        check=True, capture_output=True,
    )
    return app


def _stub_codesign(tmp_path, body="exit 0\n", name="codesign-stub"):
    stub = tmp_path / name
    stub.write_text(f"#!/bin/sh\n{body}")
    stub.chmod(0o755)
    return stub


def _shim(tmp_path, app, *, commands=("areas",), codesign=None, name="thingskit-launch"):
    source = bundle.shim_source(
        commands,
        app_path=str(app),
        requirement=REQUIREMENT,
        codesign=str(codesign if codesign is not None else _stub_codesign(tmp_path)),
    )
    return bundle.compile_shim(source, tmp_path / name)


def _fields(stdout: str) -> dict:
    out = {"argv": []}
    for line in stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "argv":
            out["argv"].append(value)
        else:
            out[key] = value
    return out


# --------------------------------------------------- INV-002-3 : source unique


def test_the_fast_path_list_is_read_from_the_script_not_written_by_hand():
    commands = bundle.read_fast_path_commands(SCRIPT_PATH)
    assert commands
    assert all(isinstance(name, str) and name for name in commands)


def test_the_shim_template_carries_no_subcommand_of_its_own(tmp_path):
    """INV-002-3 — le shim ne porte AUCUNE liste écrite à la main.

    Paramétré sur les sous-commandes réelles, pas sur un échantillon : une
    entrée recopiée dans le gabarit rendrait la source de vérité double, et
    c'est précisément le couplage que le balayage garde.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from test_fast_path_partition import declared_subcommands

    template = bundle.SHIM_TEMPLATE.read_text(encoding="utf-8")
    present = sorted(name for name in declared_subcommands() if name in template)
    assert present == [], f"sous-commandes écrites en dur dans le gabarit : {present}"


@pytest.mark.parametrize(
    "body,reason",
    [
        ("# rien\n", "absente"),
        ("FAST_PATH_COMMANDS = frozenset()\n", "vide"),
        ("FAST_PATH_COMMANDS = frozenset({})\n", "vide"),
        ("FAST_PATH_COMMANDS = _derive()\n", "non littérale"),
        ("FAST_PATH_COMMANDS = frozenset({1, 2})\n", "non textuelle"),
        ("FAST_PATH_COMMANDS = frozenset({'areas', ''})\n", "entrée vide"),
    ],
)
def test_the_build_refuses_a_fast_path_list_it_cannot_read(tmp_path, body, reason):
    """INV-002-3 — absente, vide ou illisible : le build échoue, jamais ne devine.

    Deviner ici reviendrait à choisir un régime pour l'utilisateur, dans le
    sens où l'erreur est silencieuse (une liste vide ferait tout basculer en
    régime lent sans le dire, une liste devinée ferait revenir l'invite).
    """
    script = tmp_path / "thingskit"
    script.write_text(body, encoding="utf-8")
    with pytest.raises(bundle.BundleError) as exc:
        bundle.read_fast_path_commands(script)
    assert "FAST_PATH_COMMANDS" in str(exc.value)


def test_an_unreadable_source_is_a_refusal_not_an_empty_list(tmp_path):
    with pytest.raises(bundle.BundleError):
        bundle.read_fast_path_commands(tmp_path / "absent")


def test_the_shim_source_refuses_to_be_generated_without_a_list():
    """Un shim sans table disclaimerait tout — l'option 1, sans l'avoir décidé."""
    with pytest.raises(bundle.BundleError):
        bundle.shim_source([], app_path="/tmp/x.app", requirement=REQUIREMENT)


@pytest.mark.parametrize("hostile", ['a"b', "a\\b", "a\nb"])
def test_the_shim_source_refuses_a_command_it_cannot_embed_safely(hostile):
    """Une sous-commande hostile ne doit pas pouvoir s'échapper de sa chaîne C."""
    with pytest.raises(bundle.BundleError):
        bundle.shim_source([hostile], app_path="/tmp/x.app",
                           requirement=REQUIREMENT)


def test_the_requirement_traverses_the_generation_with_its_quotes_intact():
    source = bundle.shim_source(
        ["areas"], app_path="/tmp/x.app", requirement=REQUIREMENT)
    assert REQUIREMENT.replace('"', '\\"') in source


# ------------------------------------- INV-002-1 : la partition, mesurée


def test_a_fast_path_subcommand_is_not_disclaimed(tmp_path):
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app, commands=("areas", "projects"))
    proc = subprocess.run([str(shim), "areas"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert _fields(proc.stdout)["disclaimed"] == "0"


def test_a_subcommand_outside_the_fast_path_is_disclaimed(tmp_path):
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app, commands=("areas", "projects"))
    proc = subprocess.run([str(shim), "set-notes"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert _fields(proc.stdout)["disclaimed"] == "1"


@pytest.mark.parametrize(
    "argv",
    [[], ["--help"], ["-h"], ["sous-commande-qui-n-existe-pas"], ["areas-"], ["Areas"],
     ["--bogus"], [""], ["areas", "extra"]],
    ids=repr,
)
def test_everything_the_shim_cannot_vouch_for_is_disclaimed(tmp_path, argv):
    """INV-002-1, fail-closed : le shim ne connaît QUE le régime rapide.

    L'inconnu, l'absent, l'invalide et le presque-juste (`areas-`, `Areas`)
    tombent tous en régime lent. Un oubli ne peut donc coûter qu'une lenteur
    mesurable, jamais une régression de consentement (ADR-002 § Decision 2).
    `["areas", "extra"]` garde le sens de lecture : c'est bien argv[1] qui
    décide, et un argument surnuméraire ne fait pas basculer le régime.
    """
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app, commands=("areas", "projects"))
    proc = subprocess.run([str(shim), *argv], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    expected = "0" if argv[:1] == ["areas"] else "1"
    assert _fields(proc.stdout)["disclaimed"] == expected


def test_the_real_partition_disclaims_exactly_the_osascript_subcommands(tmp_path):
    """Table paramétrée sur les sous-commandes RÉELLES, pas un échantillon.

    C'est le seul test qui joint les trois pièces : la liste déclarée dans
    `bin/thingskit`, le graphe d'appel qui dit qui atteint `osa()`, et le
    comportement mesuré du shim compilé.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from test_fast_path_partition import declared_subcommands, subcommands_reaching_osa

    app = _probe_bundle(tmp_path)
    commands = bundle.read_fast_path_commands(SCRIPT_PATH)
    shim = _shim(tmp_path, app, commands=sorted(commands))
    slow = subcommands_reaching_osa()
    observed, expected = {}, {}
    for name in sorted(declared_subcommands()):
        proc = subprocess.run([str(shim), name], capture_output=True, text=True)
        assert proc.returncode == 0, (name, proc.stderr)
        observed[name] = _fields(proc.stdout)["disclaimed"] == "1"
        expected[name] = name in slow
    assert observed == expected


# ------------------------------------- INV-002-4 : aucun processus intermédiaire


def test_the_shim_replaces_its_process_image_instead_of_forking(tmp_path):
    """Mesure du PID, pas lecture du mot-clé — reconduction d'INV-001-2."""
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app)
    proc = subprocess.Popen([str(shim), "set-notes"], stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate()
    assert int(_fields(out)["pid"]) == proc.pid


def test_the_fast_regime_forks_no_more_than_the_slow_one(tmp_path):
    """Les deux régimes traversent le même `SETEXEC` : la partition ne décide
    QUE du disclaim, jamais de la forme du lancement."""
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app, commands=("areas",))
    proc = subprocess.Popen([str(shim), "areas"], stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate()
    assert int(_fields(out)["pid"]) == proc.pid


ARGV_CASES = [
    ["areas"],
    ["add-task", "--title", "deux mots"],
    ["set-notes", "--task", ""],
    ["x", "  ", "a\tb", "accentué é", "--flag=vaut quelque chose"],
    [],
]


@pytest.mark.parametrize("argv", ARGV_CASES, ids=repr)
def test_the_shim_transmits_argv_without_alteration(tmp_path, argv):
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app)
    proc = subprocess.run([str(shim), *argv], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    received = _fields(proc.stdout)["argv"]
    assert received[1] == "-I"
    assert received[2] == str(app / "Contents" / "Resources" / "thingskit")
    assert received[3:] == argv


def test_the_shim_passes_isolation_then_the_embedded_script(tmp_path):
    """`-I` posé APRÈS le script deviendrait un argv inerte : la position est
    gardée, pas seulement la présence (reconduction ADR-001)."""
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app)
    proc = subprocess.run([str(shim)], capture_output=True, text=True)
    argv = _fields(proc.stdout)["argv"]
    assert argv[0] == str(app / "Contents" / "MacOS" / "thingskit")
    assert argv[1] == "-I"
    assert argv[2] == str(app / "Contents" / "Resources" / "thingskit")


@pytest.mark.parametrize("code", [0, 1, 2, 3, 42, 125])
def test_the_shim_preserves_the_exit_code(tmp_path, code):
    """C-1 — « 0 = effet constaté » doit remonter intact, dans les deux régimes."""
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app, commands=("areas",))
    for sub in ("areas", "set-notes"):
        proc = subprocess.run([str(shim), sub, f"--exit={code}"], capture_output=True)
        assert proc.returncode == code, (sub, proc.stderr)


def test_the_shim_leaves_stderr_to_the_interpreter(tmp_path):
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app)
    proc = subprocess.run([str(shim), "areas"], capture_output=True, text=True)
    assert proc.stderr.strip() == "probe-stderr"


# ------------------------------------- INV-002-5 : le sceau, avant tout


@pytest.mark.parametrize("subcommand", ["areas", "set-notes"])
def test_the_seal_is_checked_before_the_sealed_script_runs_in_both_regimes(
    tmp_path, subcommand
):
    """INV-002-5 — le contrôle précède le remplacement d'image, dans les DEUX
    régimes. Le régime rapide n'est pas un régime dégradé de contrôle."""
    app = _probe_bundle(tmp_path)
    ran = tmp_path / "ran"
    app_exe = app / "Contents" / "MacOS" / "thingskit"
    app_exe.unlink()
    app_exe.write_text(f'#!/bin/sh\ntouch "{ran}"\n')
    app_exe.chmod(0o755)
    shim = _shim(
        tmp_path, app, commands=("areas",),
        codesign=_stub_codesign(tmp_path, "exit 1\n", name="cs-ko"),
    )
    proc = subprocess.run([str(shim), subcommand], capture_output=True, text=True)
    assert proc.returncode == bundle.SEAL_REFUSAL_CODE
    assert not ran.exists(), "le script scellé a tourné malgré un sceau invalide"


def test_the_refusal_is_explicit_and_names_the_bundle(tmp_path):
    app = _probe_bundle(tmp_path)
    shim = _shim(
        tmp_path, app,
        codesign=_stub_codesign(tmp_path, "exit 1\n", name="cs-ko"),
    )
    proc = subprocess.run([str(shim), "areas"], capture_output=True, text=True)
    assert proc.stderr.strip(), "refus silencieux"
    assert "sceau" in proc.stderr.lower()
    assert "invalide" in proc.stderr.lower()
    assert str(app) in proc.stderr
    assert "bundle.py" in proc.stderr


def test_the_refusal_code_collides_with_no_nominal_code(tmp_path):
    """C-1 : `main()` rend 0/1, argparse 2, la garde d'identité 125."""
    app = _probe_bundle(tmp_path)
    shim = _shim(
        tmp_path, app,
        codesign=_stub_codesign(tmp_path, "exit 1\n", name="cs-ko"),
    )
    rc = subprocess.run([str(shim), "areas"], capture_output=True).returncode
    assert rc == bundle.SEAL_REFUSAL_CODE
    assert rc not in (0, 1, 2, 125)


def test_an_unavailable_verifier_is_a_refusal_never_a_passage(tmp_path):
    """Fermé par défaut : « je ne peux pas vérifier » n'est pas « c'est bon ».

    Transposition directe de `code_identity_refusal` (codesign inexécutable
    ⇒ refus), et du même mode d'échec que BUG-011.
    """
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app, codesign=tmp_path / "verificateur-absent")
    proc = subprocess.run([str(shim), "areas"], capture_output=True, text=True)
    assert proc.returncode == bundle.SEAL_REFUSAL_CODE
    assert proc.stderr.strip()
    assert "verificateur" in proc.stderr.lower() or "vérificateur" in proc.stderr.lower()


def test_a_verifier_that_hangs_is_a_refusal_not_a_hang(tmp_path):
    """BUG-011 transposé : un vérificateur bloqué ferait pendre TOUTE invocation
    du CLI sans message. Il refuse, il ne pend pas."""
    app = _probe_bundle(tmp_path)
    shim = _shim(
        tmp_path, app,
        codesign=_stub_codesign(tmp_path, "sleep 600\n", name="cs-hang"),
    )
    proc = subprocess.run(
        [str(shim), "areas"], capture_output=True, text=True,
        timeout=bundle.SHIM_CODESIGN_TIMEOUT_SECONDS + 30,
    )
    assert proc.returncode == bundle.SEAL_REFUSAL_CODE
    assert proc.stderr.strip()


def test_a_verifier_killed_by_a_signal_is_not_a_success(tmp_path):
    """`WIFEXITED` n'est pas `rc == 0` : un vérificateur tué doit refuser."""
    app = _probe_bundle(tmp_path)
    shim = _shim(
        tmp_path, app,
        codesign=_stub_codesign(tmp_path, "kill -9 $$\n", name="cs-killed"),
    )
    proc = subprocess.run([str(shim), "areas"], capture_output=True, text=True)
    assert proc.returncode == bundle.SEAL_REFUSAL_CODE


def test_the_shim_verifies_the_seal_through_an_absolute_path():
    """Un `codesign` résolu par PATH rendrait le contrôle détournable."""
    source = bundle.shim_source(
        ["areas"], app_path="/tmp/x.app", requirement=REQUIREMENT)
    assert '"/usr/bin/codesign"' in source


def test_the_shim_opposes_the_requirement_not_just_a_valid_signature():
    """Sans `-R`, `codesign` constate « une signature valide », jamais « LA »."""
    source = bundle.shim_source(
        ["areas"], app_path="/tmp/x.app", requirement=REQUIREMENT)
    assert '"--verify"' in source and '"--strict"' in source
    assert '"-R="' in source or '-R=' in source


@requires_conforming_bundle
def test_a_tampered_copy_of_the_real_bundle_is_refused_by_the_shim(tmp_path):
    """Adversité sur un VRAI sceau — sur une copie, jamais sur l'installé."""
    import shutil

    copy = tmp_path / "thingskit.app"
    shutil.copytree("/Applications/thingskit.app", copy, symlinks=True)
    shim = bundle.compile_shim(
        bundle.shim_source(["areas"], app_path=str(copy),
                           requirement=installed_bundle_requirement()),
        tmp_path / "shim"
    )
    ok = subprocess.run([str(shim), "areas"], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr

    with open(copy / "Contents" / "Resources" / "thingskit", "a") as fh:
        fh.write("\n# altere\n")
    ko = subprocess.run([str(shim), "areas"], capture_output=True, text=True)
    assert ko.returncode == bundle.SEAL_REFUSAL_CODE
    assert "sceau" in ko.stderr.lower()


# ------------------------------------- le lanceur `sh`, réduit à son exec


def test_the_launcher_execs_the_shim_and_nothing_else():
    script = bundle.launcher_script("/Applications/thingskit.app")
    exec_lines = [l for l in script.splitlines() if l.startswith("exec ")]
    assert len(exec_lines) == 1
    assert bundle.SHIM_NAME in exec_lines[0]
    assert '"$@"' in exec_lines[0]


def test_the_launcher_never_uses_open():
    """`open -a` ne transmet ni la sortie ni le code de sortie (ADR-001 § 2)."""
    script = bundle.launcher_script("/Applications/thingskit.app")
    assert "open -a" not in script and "open " not in script


def test_the_launcher_no_longer_duplicates_the_seal_check():
    """Le contrôle a MIGRÉ vers le shim : deux contrôles paieraient deux fois
    `codesign` sur le chemin chaud sans rien garder de plus."""
    script = bundle.launcher_script("/Applications/thingskit.app")
    assert "--verify" not in script


def test_the_launcher_replaces_its_process_image_instead_of_forking(tmp_path):
    """La chaîne entière est faite d'`exec` : `sh` -> shim -> interpréteur."""
    app = _probe_bundle(tmp_path)
    shim_dest = app / "Contents" / "MacOS" / bundle.SHIM_NAME
    _shim(tmp_path, app, name="tmp-shim")
    (tmp_path / "tmp-shim").replace(shim_dest)
    launcher = tmp_path / "launcher"
    launcher.write_text(bundle.launcher_script(str(app)))
    launcher.chmod(0o755)
    proc = subprocess.Popen([str(launcher), "areas"], stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate()
    assert int(_fields(out)["pid"]) == proc.pid


# ---------------- Durcissements du premier code qui s'exécute (review 2026-08-21)
#
# Ni l'un ni l'autre de ces deux défauts n'est exploitable, et aucun des deux
# n'a été trouvé par une mesure d'attaque : ils sortent d'une relecture du
# fichier dont tout le propos est que le vérificateur d'identité ne soit
# détournable ni par l'environnement, ni par l'état du processus appelant. Le
# coût de les fermer est de deux lignes ; celui de les laisser est d'avoir à
# réexpliquer, à chaque relecture, pourquoi ils ne comptent pas.


def _env_dumping_codesign(tmp_path, dump: Path):
    """Faux `codesign` qui accepte, en consignant l'environnement reçu."""
    stub = tmp_path / "codesign-dump-env"
    stub.write_text(f"#!/bin/sh\n/usr/bin/env > {dump}\nexit 0\n")
    stub.chmod(0o755)
    return stub


def test_the_seal_verifier_does_not_inherit_the_callers_environment(tmp_path):
    """Le vérificateur d'identité ne reçoit rien de l'appelant.

    `codesign` est protégé par SIP, donc les `DYLD_*` y sont ignorés et il n'y
    a pas d'exploitation directe à fermer ici. Mais transmettre l'environnement
    du terminal au processus dont le rôle est précisément d'établir l'identité
    de code est une surface gratuite, dans un fichier dont l'invariant tenu est
    que ce contrôle ne dépende PAS de l'environnement — c'est déjà le motif du
    chemin absolu `/usr/bin/codesign`, et c'est le même ici pour `PATH`,
    `DYLD_*` et tout le reste.
    """
    dump = tmp_path / "env.txt"
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app, codesign=_env_dumping_codesign(tmp_path, dump))
    env = dict(os.environ)
    env["THINGSKIT_PROBE_MARKER"] = "temoin-appelant"
    env["DYLD_LIBRARY_PATH"] = "/tmp/temoin-dyld-inexistant"
    proc = subprocess.run([str(shim), "areas"], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    received = dump.read_text(encoding="utf-8")
    assert "temoin-appelant" not in received, received
    assert "DYLD_LIBRARY_PATH" not in received, received


def test_the_sealed_command_still_receives_the_callers_environment(tmp_path):
    """Contre-épreuve du test précédent : l'environnement minimal est pour le
    VÉRIFICATEUR, jamais pour la commande. Un durcissement qui viderait aussi
    l'environnement du CLI casserait `TERM`, `LANG`, `HOME` et la base Things
    elle-même — un sur-refus, aussi fautif qu'un trou."""
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app)
    env = dict(os.environ)
    env["THINGSKIT_PROBE_MARKER"] = "temoin-appelant"
    proc = subprocess.run([str(shim), "areas"], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert _fields(proc.stdout)["marker"] == "temoin-appelant"


def _ignore_sigchld():  # pragma: no cover — s'exécute dans l'enfant forké
    import signal as _signal

    _signal.signal(_signal.SIGCHLD, _signal.SIG_IGN)


def test_the_shim_reclaims_sigchld_instead_of_inheriting_the_callers(tmp_path):
    """Le shim reprend la disposition de `SIGCHLD` avant de lancer quoi que ce soit.

    Ce qui est en jeu n'est PAS le contrôle de sceau. Mesuré le 2026-08-21 sur
    ce poste : un `SIGCHLD` à `SIG_IGN` posé par l'appelant ne casse PAS le
    `waitpid` du shim — `exec` remet la sémantique d'auto-récolte, et le
    contrôle rend `reaped == pid` comme en régime nominal (idem pour un
    `SA_NOCLDWAIT`). L'hypothèse d'un refus 126 sur un sceau valide ne se
    reproduit donc pas, et ce test ne l'affirme pas.

    Ce qui se reproduit, en revanche, est plus grave et vise le cœur du projet :
    `SIGCHLD` à `SIG_IGN` **dans le processus courant** fait rendre `0` à
    `subprocess.run` pour une commande qui a ÉCHOUÉ — mesuré, `/usr/bin/false`
    rend `1` sans, `0` avec. Or `osa()` invoque `osascript` par `subprocess`
    dans l'image finale. Une disposition héritée jusque-là ferait donc lire
    « osascript a réussi » sur un AppleScript en échec : très exactement le
    « commande envoyée ≠ effet constaté » que ce dépôt traite comme le pire
    mode de défaut, et que seul l'invariant de relecture rattraperait.

    Cette disposition ne traverse pas `exec` **aujourd'hui**, sur ce Darwin —
    donc le défaut n'est pas atteignable, et c'est une propriété de la
    plateforme, pas du dépôt. La reprendre explicitement coûte une ligne dans
    le premier code qui s'exécute, et cesse de faire dépendre l'invariant
    central d'une sémantique d'`exec` que personne ici ne contrôle.
    """
    app = _probe_bundle(tmp_path)
    shim = _shim(tmp_path, app, commands=("areas",))
    for sub in ("areas", "set-notes"):
        proc = subprocess.run(
            [str(shim), sub],
            capture_output=True,
            text=True,
            preexec_fn=_ignore_sigchld,
        )
        assert proc.returncode == 0, (sub, proc.returncode, proc.stderr)
        assert _fields(proc.stdout)["sigchld_ignored"] == "0", (
            f"{sub} : l'image finale a hérité d'un SIGCHLD à SIG_IGN"
        )


def test_the_seal_check_survives_a_caller_that_ignores_sigchld(tmp_path):
    """Contre-épreuve : le contrôle de sceau rend bien son verdict, pas un refus.

    Vert avant comme après le durcissement (cf. la mesure citée ci-dessus) —
    il fige que la reprise de `SIGCHLD` n'a pas cassé la récolte du
    vérificateur, ce qui serait le mode d'échec du remède.
    """
    app = _probe_bundle(tmp_path)
    shim = _shim(
        tmp_path, app, codesign=_stub_codesign(tmp_path, "exit 1\n", name="cs-ko")
    )
    proc = subprocess.run(
        [str(shim), "areas"], capture_output=True, text=True, preexec_fn=_ignore_sigchld
    )
    assert proc.returncode == bundle.SEAL_REFUSAL_CODE, proc.stderr
    assert "sceau" in proc.stderr.lower(), proc.stderr
