"""Zone sensible n° 3 — INV-002-2 / INV-002-3 : la partition rapide/lent.

Le shim Mach-O disclaime la responsabilité de processus pour toute
sous-commande qui n'est PAS déclarée en régime rapide (ADR-002 § Decision 2,
fail-closed). La garde de cette partition est ici : aucune sous-commande
déclarée rapide ne doit pouvoir atteindre `osa()`, sans quoi l'invite TCC
`kTCCServiceAppleEvents` revient — défaut silencieux, découvert par
l'utilisateur devant un dialogue.

Ce balayage suit la discipline de `test_no_executable_is_invoked_by_bare_name`
(§ Zones sensibles 3, « Portée exacte du balayage, et sa limite ») : **toute
forme qu'il ne sait pas résoudre est un offender, jamais un silence**. Jusqu'au
2026-08-18, « je ne sais pas lire » se lisait « rien à signaler » dans le
balayage voisin, et la garde promettait plus qu'elle ne tenait.

Sa limite, énoncée sans réserve : il ne voit pas une sollicitation
d'`osascript` qui passerait par un module tiers. Elle est fermée par la
liste blanche d'imports (`_STDLIB_ALLOWLIST`) : tout import hors de cette
liste est un offender, ce qui force la relecture de cette garde avant d'en
ajouter un. Il ne voit pas davantage un appel de méthode qui porterait une
référence liée à `osa` — fermé par la règle « une fonction de module employée
comme VALEUR est un offender », sauf à son unique site de déclaration
(`add(nom, fonction)`), que le balayage lit.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "bin" / "thingskit"

# La sollicitation d'AppleEvents passe par `osa()` (convention imposée par la
# constitution, § Conventions : « `osa()` et `time.sleep` sont les points
# d'injection »). Le balayage ne s'y fie pas seule : toute mention du nom de
# l'exécutable, par constante ou par littéral, sème aussi le régime lent —
# un `subprocess.run([OSASCRIPT, ...])` posé hors de `osa()` serait autrement
# invisible à une simple fermeture du graphe d'appel.
_OSA_SEEDS = ("osa",)
_OSASCRIPT_MARKERS = ("osascript", "OSASCRIPT")

_BUILTINS = frozenset(dir(builtins))

# Un module tiers pourrait solliciter `osascript` sans que rien ne le dise ici.
# Le balayage refuse donc ce qu'il ne connaît pas, plutôt que de le supposer
# inoffensif.
_STDLIB_ALLOWLIST = frozenset({
    "__future__", "argparse", "json", "os", "plistlib", "re", "shutil",
    "sqlite3", "subprocess", "sys", "time", "unicodedata", "urllib",
    "datetime", "pathlib", "textwrap", "typing", "collections", "itertools",
    "functools", "sysconfig", "tempfile", "contextlib", "hashlib",
})


# ------------------------------------------------------------------ outillage


def _parse(path: Path | str | None = None) -> ast.Module:
    return ast.parse(Path(path or SCRIPT_PATH).read_text(encoding="utf-8"))


def _module_functions(tree: ast.Module) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _module_bindings(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _local_bindings(scope: ast.AST) -> set[str]:
    """Noms liés DANS une fonction : paramètres, affectations, cibles diverses."""
    names: set[str] = set()
    args = getattr(scope, "args", None)
    if args is not None:
        for group in (args.posonlyargs, args.args, args.kwonlyargs):
            names.update(a.arg for a in group)
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                names.add(extra.arg)
    for node in ast.walk(scope):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        elif isinstance(node, ast.comprehension):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.withitem,)) and node.optional_vars is not None:
            for sub in ast.walk(node.optional_vars):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        elif isinstance(node, ast.Lambda):
            for group in (node.args.posonlyargs, node.args.args, node.args.kwonlyargs):
                names.update(a.arg for a in group)
    return names


def _dynamically_bound_names(scope: ast.AST) -> set[str]:
    """Noms dont la valeur vient d'une résolution que le balayage ne suit pas.

    `f = getattr(m, "osa")` puis `f("x")` est le contournement évident de la
    règle des appels : le nom est bien lié localement, mais ce qu'il désigne
    ne se lit pas. Il est donc traité comme non analysable, pas comme local.
    """
    names: set[str] = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        opaque = isinstance(value, (ast.Subscript, ast.Lambda, ast.IfExp)) or (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "getattr"
        )
        if not opaque:
            continue
        for target in node.targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
    return names


def _enclosing_scopes(tree: ast.Module) -> dict[int, ast.AST]:
    """Associe chaque nœud à la fonction de module qui le contient."""
    owner: dict[int, ast.AST] = {}
    for func in tree.body:
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(func):
                owner.setdefault(id(node), func)
    return owner


def _registration_sites(tree: ast.Module) -> tuple[dict[str, str], list[str]]:
    """Lit les couples (sous-commande, fonction) déclarés par `add(...)`.

    C'est le SEUL endroit où une fonction de module est légitimement employée
    comme valeur : le balayage lit ce site, donc il en répond.
    """
    mapping: dict[str, str] = {}
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "add"
        ):
            continue
        if len(node.args) < 2:
            offenders.append(f"ligne {node.lineno} : add() sans (nom, fonction)")
            continue
        name_node, fn_node = node.args[0], node.args[1]
        if not (isinstance(name_node, ast.Constant) and isinstance(name_node.value, str)):
            offenders.append(
                f"ligne {node.lineno} : nom de sous-commande non littéral "
                f"({ast.unparse(name_node)})"
            )
            continue
        if not isinstance(fn_node, ast.Name):
            offenders.append(
                f"ligne {node.lineno} : fonction de sous-commande non analysable "
                f"({ast.unparse(fn_node)})"
            )
            continue
        mapping[name_node.value] = fn_node.id
    return mapping, offenders


def _registration_value_sites(tree: ast.Module) -> set[int]:
    """Identités des nœuds `Name` employés comme valeur au site `add(...)`."""
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "add"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Name)
        ):
            allowed.add(id(node.args[1]))
    return allowed


def _dispatch_shape_offenders(tree: ast.Module) -> list[str]:
    """Le dispatch final (`a.fn(a)`) est la seule indirection tolérée.

    Elle n'est tolérée que parce que le balayage LIT ses deux extrémités : le
    `set_defaults(fn=<Name>)` d'un côté, le `add(nom, fonction)` de l'autre.
    Si cette forme change, le balayage cesse d'en répondre et le dit.
    """
    offenders: list[str] = []
    set_defaults = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "set_defaults"
    ]
    if len(set_defaults) != 1:
        offenders.append(
            f"{len(set_defaults)} appel(s) set_defaults — la forme du dispatch "
            "n'est plus celle que le balayage sait lire"
        )
        return offenders
    call = set_defaults[0]
    keywords = {kw.arg for kw in call.keywords}
    if keywords != {"fn"} or not any(
        kw.arg == "fn" and isinstance(kw.value, ast.Name) for kw in call.keywords
    ):
        offenders.append(
            f"ligne {call.lineno} : set_defaults n'attache plus un unique "
            "`fn=<nom>` analysable"
        )
    return offenders


# ------------------------------------------------------------------- balayage


def _mentions_osascript(func: ast.AST) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(marker in node.value for marker in _OSASCRIPT_MARKERS):
                return True
        elif isinstance(node, ast.Name) and node.id in _OSASCRIPT_MARKERS:
            return True
    return False


def _call_graph(functions: dict[str, ast.AST]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for name, func in functions.items():
        called: set[str] = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in functions:
                    called.add(node.func.id)
        graph[name] = called
    return graph


def slow_functions(tree: ast.Module) -> set[str]:
    """Fonctions de module dont l'exécution peut solliciter `osascript`."""
    functions = _module_functions(tree)
    seeds = {name for name in _OSA_SEEDS if name in functions}
    seeds |= {name for name, func in functions.items() if _mentions_osascript(func)}
    graph = _call_graph(functions)
    slow = set(seeds)
    changed = True
    while changed:
        changed = False
        for name, called in graph.items():
            if name not in slow and called & slow:
                slow.add(name)
                changed = True
    return slow


def sweep_offenders(path: Path | str | None = None) -> list[str]:
    """Formes dont le balayage ne peut PAS répondre. Résidu exigé nul."""
    tree = _parse(path)
    functions = _module_functions(tree)
    module_names = _module_bindings(tree)
    owner = _enclosing_scopes(tree)
    allowed_value_sites = _registration_value_sites(tree)
    # Le nom porté par `f(...)` est une POSITION D'APPEL, pas un emploi comme
    # valeur : il est jugé par la règle des appels, jamais deux fois.
    call_positions = {
        id(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    local_cache: dict[int, tuple[set[str], set[str]]] = {}

    offenders: list[str] = []
    _, registration_offenders = _registration_sites(tree)
    offenders += registration_offenders
    offenders += _dispatch_shape_offenders(tree)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            roots = (
                [alias.name.split(".")[0] for alias in node.names]
                if isinstance(node, ast.Import)
                else [(node.module or "").split(".")[0]]
            )
            for root in roots:
                if root and root not in _STDLIB_ALLOWLIST:
                    offenders.append(
                        f"ligne {node.lineno} : import hors liste blanche "
                        f"({root}) — surface de sollicitation inconnue"
                    )
            continue

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "getattr":
                # `getattr(a, "task_id", None)` lit une DONNÉE sur l'espace de
                # noms argparse : c'est le motif réel du script, et le refuser
                # en bloc ferait crier la garde sans rien distinguer. Ce qui
                # est refusé est la résolution dynamique d'une FONCTION : nom
                # d'attribut non littéral, résultat appelé sur place (règle des
                # appels dont la cible n'est pas un nom), ou résultat lié à un
                # nom ensuite appelé (`_dynamic_names`).
                attr = node.args[1] if len(node.args) > 1 else None
                if not (isinstance(attr, ast.Constant) and isinstance(attr.value, str)):
                    offenders.append(
                        f"ligne {node.lineno} : getattr dont l'attribut n'est pas "
                        "un littéral — résolution dynamique non analysable"
                    )
                continue
            if isinstance(func, (ast.Subscript, ast.Call, ast.Lambda, ast.IfExp)):
                offenders.append(
                    f"ligne {node.lineno} : appel dont la cible n'est pas un nom "
                    f"({ast.unparse(func)})"
                )
                continue
            if isinstance(func, ast.Name):
                scope = owner.get(id(node))
                dynamic: set[str] = set()
                locals_: set[str] = set()
                if scope is not None:
                    cached = local_cache.get(id(scope))
                    if cached is None:
                        cached = (_local_bindings(scope), _dynamically_bound_names(scope))
                        local_cache[id(scope)] = cached
                    locals_, dynamic = cached
                if func.id in dynamic:
                    offenders.append(
                        f"ligne {node.lineno} : appel d'un nom lié dynamiquement "
                        f"({func.id}) — la fonction appelée n'est pas analysable"
                    )
                    continue
                if func.id in module_names or func.id in _BUILTINS or func.id in locals_:
                    continue
                offenders.append(
                    f"ligne {node.lineno} : appel d'un nom non résolu ({func.id})"
                )
            continue

        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in functions
            and id(node) not in allowed_value_sites
            and id(node) not in call_positions
        ):
            offenders.append(
                f"ligne {node.lineno} : fonction de module employée comme valeur "
                f"({node.id}) — référence indirecte hors du site de déclaration"
            )

    return sorted(set(offenders))


def subcommands_reaching_osa(path: Path | str | None = None) -> set[str]:
    tree = _parse(path)
    mapping, _ = _registration_sites(tree)
    slow = slow_functions(tree)
    return {name for name, fn in mapping.items() if fn in slow}


def declared_subcommands(path: Path | str | None = None) -> set[str]:
    mapping, _ = _registration_sites(_parse(path))
    return set(mapping)


def _fast_path_commands(path: Path | str | None = None) -> frozenset[str]:
    from build.bundle import read_fast_path_commands

    return read_fast_path_commands(path or SCRIPT_PATH)


# ---------------------------------------------------------------------- INV-002-2


def test_the_sweep_vouches_for_every_form_in_the_real_script():
    """Résidu exigé nul : une garde qui ne sait pas lire ne garde rien."""
    assert sweep_offenders() == []


def test_no_fast_path_subcommand_reaches_osa():
    """INV-002-2 — intersection vide, mesurée sur le graphe d'appel réel."""
    intersection = _fast_path_commands() & subcommands_reaching_osa()
    assert intersection == set(), (
        "ces sous-commandes sont déclarées en régime rapide et atteignent "
        f"pourtant osa() : {sorted(intersection)} — l'invite TCC reviendrait"
    )


def test_the_fast_path_list_only_names_real_subcommands():
    """Une entrée fantôme ne protège rien et masque une faute de frappe."""
    unknown = _fast_path_commands() - declared_subcommands()
    assert unknown == set(), f"sous-commandes inexistantes déclarées : {sorted(unknown)}"


def test_the_partition_is_not_degenerate():
    """Les deux régimes existent réellement : sinon la partition ne dit rien."""
    fast = _fast_path_commands()
    slow = subcommands_reaching_osa()
    assert fast, "régime rapide vide — l'option 1 (disclaim inconditionnel) serait due"
    assert slow, "aucune sous-commande ne sollicite osascript — partition sans objet"


def test_url_scheme_writes_stay_on_the_fast_path():
    """`create-project` et `add-task` écrivent par schéma d'URL, sans AppleEvents.

    ADR-002 § Context, correction du périmètre : les partitionner comme
    « écritures » les taxerait sans nécessité. Mesuré ici sur le graphe, pas
    présumé de leur nom.
    """
    assert {"create-project", "add-task"} & subcommands_reaching_osa() == set()
    assert {"create-project", "add-task"} <= _fast_path_commands()


# ------------------------------------- le balayage éprouvé sur ce qu'il doit refuser
#
# Reprise littérale de la leçon de `test_the_sweep_flags_every_form_it_cannot_vouch_for`
# (§ Zones sensibles 3) : une garde ne vaut que si elle DÉTECTE ce qu'elle
# interdit. Chaque forme ci-dessous doit produire un offender, JAMAIS un
# silence — y compris quand le résidu réel serait nul.

_HEADER = (
    "import subprocess\n"
    "OSASCRIPT = '/usr/bin/osascript'\n"
    "def osa(script):\n"
    "    return subprocess.run([OSASCRIPT, '-e', script])\n"
    "def cmd_areas(a):\n"
    "    return 0\n"
)

_MAIN = (
    "def main():\n"
    "    def add(name, fn):\n"
    "        s = sub.add_parser(name)\n"
    "        s.set_defaults(fn=fn)\n"
    "        return s\n"
    "    add('areas', cmd_areas)\n"
)

_TRAPS = {
    "alias de fonction": _HEADER + "def cmd_x(a):\n    f = osa\n    return f('x')\n" + _MAIN,
    "getattr dynamique": _HEADER
    + "import sys\n"
    + "def cmd_x(a):\n    return getattr(sys.modules[__name__], 'osa')('x')\n"
    + _MAIN,
    "getattr lié puis appelé": _HEADER
    + "import sys\n"
    + "def cmd_x(a):\n    f = getattr(sys.modules[__name__], 'osa')\n    return f('x')\n"
    + _MAIN,
    "dispatch par dictionnaire": _HEADER
    + "def cmd_x(a):\n    return {'k': osa}['k']('x')\n"
    + _MAIN,
    "nom de sous-commande non littéral": _HEADER
    + "def main():\n"
    "    def add(name, fn):\n"
    "        s = sub.add_parser(name)\n"
    "        s.set_defaults(fn=fn)\n"
    "        return s\n"
    "    n = 'areas'\n"
    "    add(n, cmd_areas)\n",
    "fonction de sous-commande non analysable": _HEADER
    + "def main():\n"
    "    def add(name, fn):\n"
    "        s = sub.add_parser(name)\n"
    "        s.set_defaults(fn=fn)\n"
    "        return s\n"
    "    add('areas', TABLE['areas'])\n",
    "import hors liste blanche": _HEADER
    + "import appscript\n"
    + "def cmd_x(a):\n    return appscript.app('Things').run()\n"
    + _MAIN,
    "appel d'un nom inconnu": _HEADER
    + "def cmd_x(a):\n    return helper_venu_d_ailleurs('x')\n"
    + _MAIN,
    "lambda appelée en place": _HEADER
    + "def cmd_x(a):\n    return (lambda s: osa(s))('x')\n"
    + _MAIN,
    "dispatch dont la forme a changé": _HEADER
    + "def main():\n"
    "    def add(name, fn):\n"
    "        s = sub.add_parser(name)\n"
    "        s.set_defaults(fn=fn, autre=1)\n"
    "        return s\n"
    "    add('areas', cmd_areas)\n",
}


@pytest.mark.parametrize("trap", sorted(_TRAPS))
def test_the_sweep_flags_every_form_it_cannot_vouch_for(tmp_path, trap):
    script = tmp_path / "thingskit"
    script.write_text(_TRAPS[trap], encoding="utf-8")
    assert sweep_offenders(script), f"forme non détectée : {trap}"


def test_the_sweep_does_not_cry_wolf_on_a_plain_script(tmp_path):
    """Une garde qui refuse tout ne distingue plus rien."""
    script = tmp_path / "thingskit"
    script.write_text(_HEADER + "def cmd_y(a):\n    return osa('x')\n" + _MAIN, encoding="utf-8")
    assert sweep_offenders(script) == []


# ------------------- le balayage éprouvé sur ce qu'il doit VOIR (pas seulement refuser)

_REACHES = {
    "appel direct": "def cmd_x(a):\n    return osa('x')\n",
    "par une fonction intermédiaire": (
        "def helper(s):\n    return osa(s)\n"
        "def cmd_x(a):\n    return helper('x')\n"
    ),
    "par deux niveaux d'indirection": (
        "def deep(s):\n    return osa(s)\n"
        "def helper(s):\n    return deep(s)\n"
        "def cmd_x(a):\n    return helper('x')\n"
    ),
    "sans passer par osa, en nommant l'exécutable": (
        "def cmd_x(a):\n    return subprocess.run([OSASCRIPT, '-e', 'x'])\n"
    ),
    "sans passer par osa, par littéral": (
        "def cmd_x(a):\n    return subprocess.run(['/usr/bin/osascript', '-e', 'x'])\n"
    ),
}


@pytest.mark.parametrize("form", sorted(_REACHES))
def test_the_sweep_sees_every_route_to_osascript(tmp_path, form):
    """Un chemin vers `osascript` que le balayage ne verrait pas rendrait le
    régime rapide dangereux — c'est le seul sens d'oubli qui coûte cher."""
    script = tmp_path / "thingskit"
    script.write_text(
        _HEADER
        + _REACHES[form]
        + "def main():\n"
        "    def add(name, fn):\n"
        "        s = sub.add_parser(name)\n"
        "        s.set_defaults(fn=fn)\n"
        "        return s\n"
        "    add('areas', cmd_areas)\n"
        "    add('x', cmd_x)\n",
        encoding="utf-8",
    )
    assert "x" in subcommands_reaching_osa(script), f"chemin manqué : {form}"
    assert "areas" not in subcommands_reaching_osa(script)
