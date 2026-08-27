"""Loads bin/thingskit (extensionless script) as an importable module.

Mirrors the loader pattern omnikit's own facade uses at runtime (sys.path
insertion), adapted for pytest since ``thingskit`` has no package to
import — it is one script, per the constat rapporté à l'étape 1.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "bin" / "thingskit"

# spec_from_file_location can't infer a loader for an extensionless file —
# the script has no ".py" suffix (it's a CLI facade, not a package module).
_loader = SourceFileLoader("thingskit_cli", str(SCRIPT_PATH))
_spec = importlib.util.spec_from_loader("thingskit_cli", _loader)
thingskit_cli = importlib.util.module_from_spec(_spec)
sys.modules["thingskit_cli"] = thingskit_cli
_loader.exec_module(thingskit_cli)

import pytest

# --------------------------------------------------------------- C-4 / INV-001-3
# Le bundle installe est un ETAT DE POSTE, pas une propriete du depot : il peut
# etre absent, ou anterieur au dernier chantier. Un test qui l'atteint doit
# sauter dans les deux cas — et l'adequation de ce saut se decide ICI, en un
# seul endroit, jamais dans la condition ad hoc de chaque `skipif`.
#
# Motif (2026-08-21) : `test_the_root_seal_does_not_vouch_for_the_shim` sautait
# sur `not isdir(...)` — la seule PRESENCE du bundle — alors que son corps exige
# un bundle post-ADR-002 portant le shim. Sur un poste en cours de mise a niveau,
# il echouait avant toute assertion, rendant l'ordre « suite verte puis build »
# intenable. La garde C-4 ne le voyait pas : elle constatait la presence d'un
# `skipif`, jamais ce qu'il decide.
INSTALLED_BUNDLE = "/Applications/thingskit.app"
SHIM_NAME = "thingskit-launch"
# ADR-003 : le bundle porte desormais l'identite de code qu'il exige de
# lui-meme, dans un fichier scelle. Un bundle anterieur n'en a pas, et les
# tests qui l'atteignent n'ont rien a eprouver dessus — meme motif que le
# shim d'ADR-002 ci-dessus, et meme remede : le saut se decide ICI.
CODE_IDENTITY_FILE = "code-identity"


def conforming_bundle_missing() -> str | None:
    """Raison pour laquelle le bundle installe ne peut pas servir de fixture.

    Rend `None` quand il peut. Seule fonction du depot autorisee a atteindre
    `/Applications/thingskit.app` hors d'un test garde — exemption structurelle
    de la garde C-4, attachee a ce mecanisme et non a un nom de fichier.
    """
    if not os.path.isdir(INSTALLED_BUNDLE):
        return f"{INSTALLED_BUNDLE} absent de ce poste (C-4)"
    if not os.path.isfile(os.path.join(INSTALLED_BUNDLE, "Contents", "MacOS", SHIM_NAME)):
        return (
            f"{INSTALLED_BUNDLE} anterieur a ADR-002 : shim `{SHIM_NAME}` absent. "
            "Reconstruire (`python3 -m build.bundle`) pour eprouver ces tests."
        )
    if not os.path.isfile(
            os.path.join(INSTALLED_BUNDLE, "Contents", "Resources", CODE_IDENTITY_FILE)):
        return (
            f"{INSTALLED_BUNDLE} anterieur a ADR-003 : fichier d'identite "
            f"`{CODE_IDENTITY_FILE}` absent. Reconstruire "
            "(`python3 -m build.bundle`) pour eprouver ces tests."
        )
    return None


def installed_bundle_requirement() -> str:
    """Exigence de code du bundle INSTALLE, lue dans son propre fichier scelle.

    Seconde et DERNIERE fonction autorisee a atteindre `/Applications/thingskit.app`
    hors d'un test garde — meme exemption structurelle que le predicat
    ci-dessus, attachee au mecanisme et non a un nom de fichier. Elle n'est
    appelee QUE depuis des tests gardes : sur un poste nu, elle n'est jamais
    evaluee.

    Depuis ADR-003, quelle identite le bundle exige de lui-meme est une donnee
    QU'IL PORTE. Un test qui la recomposerait depuis une constante du depot
    n'eprouverait plus l'artefact, mais la constante.
    """
    path = os.path.join(
        INSTALLED_BUNDLE, "Contents", "Resources", CODE_IDENTITY_FILE)
    with open(path, encoding="utf-8") as handle:
        identifier, team = thingskit_cli.parse_code_identity(handle.read())
    return thingskit_cli.compose_code_requirement(identifier, team)


requires_conforming_bundle = pytest.mark.skipif(
    conforming_bundle_missing() is not None,
    reason=conforming_bundle_missing() or "",
)


@pytest.fixture
def thingskit():
    return thingskit_cli


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Exécute l'analyse d'arguments du CLI DANS le processus de test.

    Depuis BUG-009, l'entrée CLI du script refuse tout interpréteur qui ne
    porte pas l'identité de code du bundle : lancer `bin/thingskit --help` en
    sous-processus sous le python des tests ne rend plus l'aide, mais le refus.
    L'aide et la validation d'arguments sont de l'`argparse` — donc du ressort
    de `main()`, qu'on appelle directement.
    """
    import contextlib
    import io

    out, err = io.StringIO(), io.StringIO()
    code = 0
    argv_backup = sys.argv
    sys.argv = ["thingskit", *argv]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = thingskit_cli.main() or 0
            except SystemExit as exc:  # argparse : --help (0), erreur d'usage (2)
                code = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.argv = argv_backup
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def run_cli():
    return _run_cli


# ---------------------------------------------------------------- lancements de fils
# Recensement PARTAGÉ des lancements de processus fils, et de ceux qui laissent
# leur sortie atteindre NOS descripteurs. Site de définition UNIQUE : deux
# copies de ce prédicat divergeraient, et c'est exactement le défaut que la
# garde qu'il sert existe pour fermer.
#
# Deux leçons y sont inscrites, chacune payée d'un tour de review (2026-08-27) :
#
#   1. **Un seul flux borné n'est pas une capture.** Le prédicat était une
#      disjonction — `capture_output` OU `stdout` OU `stderr`. Or `stdout=`
#      seul laisse `stderr` intégralement hérité, et `stderr` est le canal, le
#      seul, par lequel le jeton du schéma d'URL sortait. Mutation mesurée sur
#      le vrai script : `cmd_create_heading` réécrit en
#      `subprocess.run([...], stdout=subprocess.DEVNULL)` laissait la suite
#      ENTIÈREMENT verte alors que le site fuyait.
#   2. **Le recensement énumérait des noms d'appel** — `subprocess.{run, call,
#      check_call, Popen}` — là où `_is_inert_argv_element`, écrit dans le même
#      commit, appliquait la doctrine inverse : borner ce qui est SÛR. Cinq
#      formes lui échappaient, mesurées, dont `check_output` (qui hérite
#      `stderr` par construction) et le lancement par indirection, présent pour
#      de vrai dans `code_identity_refusal`.
#
# La borne est donc posée sur le MODULE, pas sur la fonction : tout appel
# atteignant `subprocess` par n'importe quel nom est un lancement, quel que
# soit l'attribut. Un attribut non appelé (`subprocess.DEVNULL`, l'annotation
# de type de `_spawn`) n'en est pas un.

#: Ce qu'`os` expose qui lance un processus. C'est une énumération, et elle
#: est assumée : la surface est celle de la bibliothèque standard, close et
#: hors de notre code — contrairement aux formes d'appel, qui sont les nôtres
#: et se sur-approximent. Ce qu'elle ne couvre pas est nommé dans
#: `constitution.md` § « ce que ce recensement ne tient pas ».
OS_SPAWN_MEMBERS = frozenset({
    "system", "popen", "execv", "execve", "execvp", "execvpe", "execl",
    "execle", "execlp", "execlpe", "spawnv", "spawnve", "spawnvp", "spawnl",
    "spawnle", "spawnlp", "posix_spawn", "posix_spawnp", "startfile",
})

#: Appels dont la sortie standard est capturée PAR CONSTRUCTION — c'est un
#: fait d'API, pas une forme d'écriture. Hors de cette liste, on exige les
#: deux flux explicitement : un appelable inconnu (indirection) est traité
#: comme ne capturant rien, ce qui est le sens d'erreur sûr.
STDOUT_CAPTURED_BY_CONSTRUCTION = frozenset({"check_output"})


def _spawn_bindings(tree):
    """(alias du module subprocess, noms d'appelables de lancement, alias d'os).

    Les noms d'appelables incluent ceux RÉ-LIÉS depuis une racine
    (`runner = runner or subprocess.run`) : ce qu'un nom porte est invisible
    au point d'appel, donc il vaut lancement jusqu'à preuve du contraire.
    """
    modules, members, os_aliases = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or alias.name)
                elif alias.name == "os":
                    os_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                members.add(alias.asname or alias.name)

    def _references_a_spawn(value) -> bool:
        for sub in ast.walk(value):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                    and sub.value.id in modules:
                return True
            if isinstance(sub, ast.Name) and sub.id in members:
                return True
        return False

    changed = True
    while changed:                       # point fixe : les liaisons s'enchaînent
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            # Une VALEUR d'appel est un résultat, pas un appelable : `r =
            # subprocess.run(...)` ne fait pas de `r` un lanceur.
            if value is None or isinstance(value, ast.Call):
                continue
            if not _references_a_spawn(value):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in members:
                    members.add(target.id)
                    changed = True
    return modules, members, os_aliases


def _callee_name(func) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def spawn_bounds_both_streams(node: "ast.Call") -> bool:
    """Le lancement borne-t-il SES DEUX flux ?

    Un `**kwargs` rend la question indécidable au point d'appel : on répond
    non, parce que le sens d'erreur sûr est de compter un site de trop, jamais
    un de moins.
    """
    explicit, opaque = {}, False
    for keyword in node.keywords:
        if keyword.arg is None:          # `**kwargs` : contenu indécidable ici
            opaque = True
        else:
            explicit[keyword.arg] = keyword.value

    capture = explicit.get("capture_output")
    if capture is not None:
        # La VALEUR compte, pas la présence du mot-clé : `capture_output=False`
        # ne borne rien, et une valeur calculée ne se décide pas au point
        # d'appel. `=True` borne en revanche les DEUX flux, et un `**kwargs`
        # ne peut pas le défaire — passer `stdout=`/`stderr=` en même temps
        # lève `ValueError` à l'exécution.
        return isinstance(capture, ast.Constant) and capture.value is True
    if opaque:
        return False
    if _callee_name(node.func) in STDOUT_CAPTURED_BY_CONSTRUCTION:
        return "stderr" in explicit
    return {"stdout", "stderr"} <= set(explicit)


def spawn_argv(node: "ast.Call"):
    """L'argv du lancement, qu'il soit positionnel ou passé par `args=`."""
    if node.args:
        return node.args[0]
    for kw in node.keywords:
        if kw.arg == "args":
            return kw.value
    return None


def is_child_spawn(node, bindings) -> bool:
    """Cet appel lance-t-il un processus fils ?

    La borne porte sur le MODULE atteint, jamais sur le nom de la fonction :
    n'importe quel attribut de `subprocess` compte, y compris ceux qu'on n'a
    pas pensé à écrire. C'est la même doctrine que `_is_inert_argv_element`,
    appliquée cette fois au bon endroit.
    """
    if not isinstance(node, ast.Call):
        return False
    modules, members, os_aliases = bindings
    func = node.func
    return bool(
        (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
         and func.value.id in modules)
        or (isinstance(func, ast.Name) and func.id in members)
        or (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id in os_aliases and func.attr in OS_SPAWN_MEMBERS)
    )


def spawn_bindings(tree):
    """Racines par lesquelles un lancement est atteignable dans ce source."""
    return _spawn_bindings(tree)


def child_spawn_sites(source: str) -> list[tuple[int, bool]]:
    """(ligne, les deux flux sont-ils bornés ?) pour chaque lancement de fils."""
    tree = ast.parse(source)
    bindings = _spawn_bindings(tree)
    return sorted({(n.lineno, spawn_bounds_both_streams(n))
                   for n in ast.walk(tree) if is_child_spawn(n, bindings)})


class InertResult:
    """Ce que rend un lancement de fils NEUTRALISÉ dans les tests.

    Les doublures rendaient `None` : elles décrivaient un `subprocess.run`
    dont personne ne lisait le retour — ce qui a cessé d'être vrai le
    2026-08-27, `_spawn` lisant le code retour pour dire un échec sans citer
    l'argv. Une doublure qui ne peut pas porter ce que le code lit n'est pas
    une doublure, c'est un trou : elle fait passer pour un défaut du code ce
    qui est un défaut de la doublure (mesuré — 16 tests mouraient sur
    `AttributeError`, aucun sur une assertion).

    Elle vit ICI, et non recopiée dans chaque fichier de test, pour la raison
    même qui a motivé `_spawn` : deux copies finissent par diverger.
    """
    returncode = 0
    stdout = ""
    stderr = ""


def inert_run(*args, **kwargs) -> InertResult:
    """Doublure de `subprocess.run` qui ne lance rien et rend un résultat."""
    return InertResult()
