"""Loads bin/thingskit (extensionless script) as an importable module.

Mirrors the loader pattern omnikit's own facade uses at runtime (sys.path
insertion), adapted for pytest since ``thingskit`` has no package to
import — it is one script, per the constat rapporté à l'étape 1.
"""
from __future__ import annotations

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
    return None


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
