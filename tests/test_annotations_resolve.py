"""Toute annotation nomme quelque chose que le module lie reellement.

Motif (2026-08-21) : `tests/test_bundle.py` annotait `def verify(target: Path)`
sans jamais importer `Path`. Sous Python 3.14 (PEP 649) l'annotation n'est
evaluee que si on la demande, donc le defaut etait INVISIBLE sur les deux
postes. Sous tout Python anterieur — et `requires-python` declare `>=3.11` —
c'est un `NameError` leve au moment ou la fonction est definie : ici, la
fonction etant imbriquee dans un test, au moment ou ce test s'execute.

La garde porte sur la CLASSE, pas sur l'instance : elle balaie tous les
fichiers Python du depot, pas le seul fichier fautif.

Sa limite, enoncee sans reserve : elle verifie qu'un nom est LIE quelque part
dans le module, sans modeliser les portees ni l'ordre d'execution. Un nom lie
dans une fonction voisine passerait. C'est un proxy pour le defaut reel
(annotation dont le nom n'est jamais importe), pas une preuve de resolution.
"""

import ast
import builtins
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _repo_python_sources() -> list[str]:
    paths = [os.path.join(REPO_ROOT, "bin", "thingskit")]
    for sub in ("tests", "build"):
        d = os.path.join(REPO_ROOT, sub)
        for name in sorted(os.listdir(d)):
            if name.endswith(".py"):
                paths.append(os.path.join(d, name))
    return paths


def _bound_names(tree: ast.AST) -> set[str]:
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            args = getattr(node, "args", None)
            if args is not None:
                for a in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                    bound.add(a.arg)
                for a in (args.vararg, args.kwarg):
                    if a is not None:
                        bound.add(a.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Global):
            bound.update(node.names)
    return bound


def _annotation_names(tree: ast.AST) -> list[tuple[str, int]]:
    """Noms-racines cites par une annotation, avec leur ligne."""
    found: list[tuple[str, int]] = []

    def collect(annotation) -> None:
        if annotation is None:
            return
        for sub in ast.walk(annotation):
            root = sub
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and isinstance(root.ctx, ast.Load):
                found.append((root.id, root.lineno))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            collect(node.returns)
            a = node.args
            for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg):
                if arg is not None:
                    collect(arg.annotation)
        elif isinstance(node, ast.AnnAssign):
            collect(node.annotation)
    return found


def _unbound_annotation_names(source: str) -> list[str]:
    """Noms cites par une annotation et lies nulle part dans le module."""
    tree = ast.parse(source)
    bound = _bound_names(tree) | set(dir(builtins))
    return [f"ligne {line} : {name}"
            for name, line in _annotation_names(tree) if name not in bound]


def test_the_sweep_flags_an_annotation_whose_name_is_never_imported():
    """La garde ne vaut que si elle DETECTE ce qu'elle interdit."""
    assert _unbound_annotation_names(
        "def outer():\n    def verify(target: Path) -> int:\n        return 0\n"
    ) == ["ligne 2 : Path"]


def test_the_sweep_does_not_cry_wolf_on_an_imported_name():
    """Un nom importe, un builtin et une annotation pointee sont legitimes —
    une garde qui refuse tout ne distingue plus rien."""
    assert _unbound_annotation_names(
        "import os.path\nfrom pathlib import Path\n"
        "def f(t: Path, n: int, s: os.PathLike) -> bool:\n    return True\n"
    ) == []


@pytest.mark.parametrize("path", _repo_python_sources(), ids=os.path.basename)
def test_every_annotation_names_something_the_module_binds(path):
    with open(path, encoding="utf-8") as fh:
        offenders = _unbound_annotation_names(fh.read())
    assert offenders == [], (
        f"{os.path.basename(path)} : annotation dont le nom n'est lie nulle part "
        f"— NameError sous tout Python anterieur a 3.14 : {offenders}"
    )
