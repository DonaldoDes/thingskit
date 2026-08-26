"""Zone sensible n° 2 — aucune valeur ne sort d'un littéral de chaîne AppleScript.

BUG-033 : `create-area` interpolait `a.name` — un argument positionnel brut —
entre les guillemets d'un littéral AppleScript remis à `osascript`, sans passer
par `_esc()`. Un nom portant un guillemet double referme le littéral et la
suite de la chaîne devient du CODE, exécuté sous l'identité de processus à
laquelle le consentement TCC est accordé.

Le défaut a survécu parce que 17 sites appliquaient l'échappement et qu'un seul
l'omettait : relire ne le voit pas, compter le voit. Ce module ne teste donc
pas `create-area` — il ferme la CLASSE, sur le modèle de
`test_no_executable_is_invoked_by_bare_name` (`tests/test_absolute_executables.py`) :
un balayage d'AST qui exige un compte résiduel NUL, et une contre-épreuve qui
prouve que le balayage voit le défaut qu'il interdit.

Le prédicat porte sur la POSITION, pas sur le nom de la commande : une
interpolation précédée d'un nombre IMPAIR de guillemets non échappés — comptés
sur le texte statique cumulé depuis le début du littéral — se trouve, par
construction, à l'intérieur d'un littéral de chaîne du script émis. La seule
dispense est une constante de module affectée UNE FOIS à un littéral, jamais
visée par un `global`, et non masquée dans la portée du site.

Le prédicat a d'abord été écrit `before.endswith('"')` sur le seul fragment
précédent : il fermait la forme EXACTE de BUG-033 et quatre formes voisines
passaient (deuxième interpolation dans le même littéral, `%`, `.format`,
paramètre homonyme d'une constante de module). C'est la classe qui se ferme,
pas l'instance — et les formes qui ne produisent aucun `JoinedStr` sont, elles,
interdites par un second balayage plus bas.

CE QUE CES DEUX BALAYAGES NE TIENNENT PAS
-----------------------------------------
Écrit ici plutôt que tu, parce que la phrase « la classe est fermée » a déjà
été portée par ce module alors qu'elle était fausse.

Un banc de 12 formes d'évasion a été soumis aux prédicats réels le 2026-08-26.
Avant ce lot, 2 étaient vues ; après, 5. Les 7 qui passent ont toutes la même
propriété, et c'est elle qui borne honnêtement la garde : **la valeur transite
par une indirection que l'analyse statique ne suit pas** — variable locale,
constante hissée, objet mutable, fonction de la bibliothèque standard.

    gabarit ouvert rangé dans une variable locale, puis f-string    NON VUE
    gabarit `%` hissé dans une constante de module (`TPL % v`)      NON VUE
    `'…"@@"…'.replace('@@', v)`                                     NON VUE
    liste de fragments hissée dans une variable, puis `''.join(l)`  NON VUE
    `io.StringIO()` + `.write()` incrémental                        NON VUE
    `string.Template('…"$n"…').substitute(n=v)`                     NON VUE
    `%` dont l'opérande gauche n'est pas un littéral nu             NON VUE

Trois autres angles morts, hors banc :
  - une valeur injectée par une interpolation ANTÉRIEURE du même littéral —
    sa valeur est inconnue à l'analyse, la parité la traite comme neutre ;
  - une constante dispensée réaffectée par `globals()[…] = …` ou par
    `setattr(sys.modules[__name__], …)` — 0 occurrence dans `bin/thingskit`,
    mesuré le 2026-08-26 par
    `grep -nE "global |globals[(][)]|setattr" bin/thingskit` -> rc=1, vide ;
  - un `format_spec` qui porte lui-même une interpolation : son texte statique
    compte désormais dans la parité, mais l'interpolation qu'il contient est
    examinée avec sa propre parité, repartie de zéro.

Ce que la garde tient donc : la forme DIRECTE, celle où le gabarit et la
valeur se rencontrent dans la même expression. C'est la forme de BUG-033 et
celle de ses 17 voisins sains — pas toutes les manières d'écrire le défaut.
"""
from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "bin" / "thingskit"

ESCAPE_FUNCTION = "_esc"
# Second neutraliseur, et il ne neutralise PAS de la même façon : `_esc`
# échappe, `_requirement_value` REFUSE. Il garde l'unique gabarit du script
# qui n'est pas de l'AppleScript — l'exigence de code composée depuis le
# fichier d'identité scellé (ADR-003) —, où un échappement AppleScript serait
# le mauvais outil : la grammaire de `csreq` n'est pas celle d'`osascript`, et
# échapper y ferait passer une valeur que le dépôt veut voir refusée.
#
# La dispense qu'il ouvre est plus étroite que celle d'`_esc` : elle exige une
# forme, elle ne rend pas une valeur portable. Les deux passent par les MÊMES
# contrôles de portée — un `_requirement_value = lambda v, f: v` posé en tête
# de fonction dispenserait autrement tout ce qu'il couvre.
REQUIREMENT_VALUE_FUNCTION = "_requirement_value"
NEUTRALISING_FUNCTIONS = (ESCAPE_FUNCTION, REQUIREMENT_VALUE_FUNCTION)


def _globally_rebound_names(tree: ast.Module) -> set[str]:
    """Noms visés par un `global` où que ce soit dans le module.

    Un `global X` suivi d'une affectation, depuis n'importe quelle fonction,
    dément la phrase qui justifie la dispense — « sa valeur est écrite dans la
    source ». Le `global` seul suffit à retirer la dispense : distinguer celui
    qui affecte de celui qui lit coûterait une analyse de flot pour un gain
    nul (0 occurrence dans `bin/thingskit`, mesuré).
    """
    return {name
            for node in ast.walk(tree)
            if isinstance(node, ast.Global)
            for name in node.names}


def _module_level_bound_names(tree: ast.Module,
                              owner: dict[int, ast.AST | None]) -> dict[str, int]:
    """Compte des liaisons AU NIVEAU DU MODULE, par nom.

    Compté sur la portée (`owner`) et non sur `tree.body` : un `if`, un `try`
    ou un `for` de module lie tout autant, et l'ancien parcours ne voyait que
    le premier niveau de la liste d'instructions.
    """
    counts: dict[str, int] = {}

    def bump(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    for node in ast.walk(tree):
        if owner.get(id(node)) is not None:
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bump(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bump((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bump(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bump(node.name)
    return counts


def _module_literal_names(tree: ast.Module,
                          owner: dict[int, ast.AST | None]) -> set[str]:
    """Noms dispensés : affectés AU NIVEAU DU MODULE à un littéral, une seule
    fois, et jamais visés par un `global`.

    Volontairement étroit : une constante composée (f-string, concaténation,
    appel) n'y est PAS, parce que sa valeur peut dépendre d'autre chose que de
    la source. Seul un littéral est hors de portée de toute entrée.

    Les trois conditions ne sont pas décoratives — chacune ferme un
    contournement mesuré. La version d'origine AJOUTAIT sur la première
    affectation et ne retirait JAMAIS : `X = 'safe'` suivi, plus bas ou dans
    une branche, de `X = os.environ['X']` restait dispensé, et un `global X`
    depuis n'importe quelle autre fonction du module aussi.
    """
    rebound = _globally_rebound_names(tree)
    counts = _module_level_bound_names(tree, owner)

    names: set[str] = set()
    for stmt in ast.walk(tree):
        if owner.get(id(stmt)) is not None:
            continue
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
        else:
            continue
        if not isinstance(stmt.value, ast.Constant):
            continue
        for target in targets:
            if (isinstance(target, ast.Name)
                    and counts.get(target.id, 0) == 1
                    and target.id not in rebound):
                names.add(target.id)
    return names


def _neutralising_call(node: ast.AST) -> str | None:
    """Nom du neutraliseur appelé par `node`, ou `None`."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in NEUTRALISING_FUNCTIONS):
        return node.func.id
    return None


def _is_escape_call(node: ast.AST) -> bool:
    return _neutralising_call(node) == ESCAPE_FUNCTION


def _escape_definition_is_sound(tree: ast.Module,
                                owner: dict[int, ast.AST | None]) -> bool:
    """`_esc` est défini UNE fois au niveau module, par un `def`, et jamais
    relié.

    Le test `node.func.id == "_esc"` n'a, seul, aucune notion de portée : un
    `_esc = lambda s: s` — au niveau module ou en tête de fonction — dispense
    tout ce qu'il couvre. C'est la classe fermée pour les constantes de
    module, laissée ouverte de l'autre côté de la dispense.

    Un module qui ne lie pas `_esc` du tout n'a rien à dire : la fonction vient
    d'ailleurs, et l'exiger ici refuserait toutes les épreuves partielles.
    """
    return all(_neutraliser_definition_is_sound(tree, owner, name)
               for name in NEUTRALISING_FUNCTIONS)


def _neutraliser_definition_is_sound(tree: ast.Module,
                                     owner: dict[int, ast.AST | None],
                                     name: str) -> bool:
    bound = _module_level_bound_names(tree, owner).get(name, 0)
    if bound == 0:
        return True
    definitions = [node for node in ast.walk(tree)
                   if owner.get(id(node)) is None
                   and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name == name]
    return (bound == 1 and len(definitions) == 1
            and name not in _globally_rebound_names(tree))


def _escape_is_shadowed_at(site: ast.AST,
                           owner: dict[int, ast.AST | None]) -> bool:
    """`_esc` est-il relié dans une des portées qui contiennent le site ?

    Même remontée que pour les constantes de module — la machinerie de portée
    existe, elle ne servait qu'à un côté de la dispense.
    """
    scope = owner.get(id(site))
    while scope is not None:
        bound = _locally_bound_names(scope, owner)
        if any(name in bound for name in NEUTRALISING_FUNCTIONS):
            return True
        scope = owner.get(id(scope))
    return False


# Un antislash neutralise le caractère qui suit DANS le littéral AppleScript :
# `\\"` ne referme pas la chaîne. Le retirer avant de compter est ce qui
# empêche la parité de s'inverser sur un guillemet échappé — sans quoi la
# garde devient aveugle sur tout ce qui suit dans le même littéral.
_APPLESCRIPT_ESCAPED = re.compile(r"\\.", re.S)


def _inside_a_string_literal(static_text: str) -> bool:
    """Parité des guillemets NON échappés du texte statique CUMULÉ.

    Le prédicat d'origine regardait le seul fragment qui précède
    immédiatement l'interpolation (`before.endswith('"')`). Il fermait la
    forme exacte de BUG-033 et rien d'autre : une DEUXIÈME valeur dans le même
    littéral — `f'error "{_esc(M)} sur {a.name}"'` — passait, alors que
    composer un message AppleScript avec deux valeurs est le cas banal.
    """
    return _APPLESCRIPT_ESCAPED.sub("", static_text).count('"') % 2 == 1


def _enclosing_functions(tree: ast.Module) -> dict[int, ast.AST | None]:
    """`id(noeud)` -> fonction englobante la plus proche (ou `None`).

    Sert la portée : un nom de module n'est dispensé que s'il n'est masqué
    dans AUCUNE des fonctions qui contiennent le site.
    """
    owner: dict[int, ast.AST | None] = {id(tree): None}

    def walk(node, current):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                owner[id(child)] = current
                walk(child, child)
            else:
                owner[id(child)] = current
                walk(child, current)

    walk(tree, None)
    return owner


def _parameter_names(fn: ast.AST) -> set[str]:
    args = getattr(fn, "args", None)
    if args is None:
        return set()
    named = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    if args.vararg:
        named.append(args.vararg)
    if args.kwarg:
        named.append(args.kwarg)
    return {a.arg for a in named}


def _locally_bound_names(fn: ast.AST, owner: dict[int, ast.AST | None]) -> set[str]:
    """Noms LIÉS dans le corps propre de `fn` — affectation, `for`, `with`,
    `except`, marcheur, importation, compréhension. Volontairement large :
    tout ce qui peut recevoir une entrée annule la dispense."""
    bound = _parameter_names(fn)
    for node in ast.walk(fn):
        if owner.get(id(node)) is not fn:
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
    return bound


def _is_dispensed_module_constant(name: str, site: ast.AST,
                                  literal_names: set[str],
                                  owner: dict[int, ast.AST | None]) -> bool:
    if name not in literal_names:
        return False
    scope = owner.get(id(site))
    while scope is not None:
        if name in _locally_bound_names(scope, owner):
            return False
        scope = owner.get(id(scope))
    return True


def _unescaped_quoted_interpolations(path: Path | None = None) -> list[str]:
    """Compte résiduel : interpolations posées dans un littéral de chaîne du
    script émis sans passer par `_esc`.

    Ce que ce balayage NE voit PAS est énuméré au § « CE QUE CES DEUX
    BALAYAGES NE TIENNENT PAS » de la docstring du module — sept formes
    mesurées, plus trois angles morts hors banc. En un mot : toute valeur qui
    atteint le gabarit par une INDIRECTION.
    """
    source = (path or SCRIPT_PATH).read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = _enclosing_functions(tree)
    literal_names = _module_literal_names(tree, owner)
    escape_is_sound = _escape_definition_is_sound(tree, owner)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        static = ""
        for part in node.values:
            if isinstance(part, ast.Constant):
                if isinstance(part.value, str):
                    static += part.value
                continue
            if not isinstance(part, ast.FormattedValue):
                continue
            inside = _inside_a_string_literal(static)
            # Le `format_spec` est du texte STATIQUE du littéral émis : s'il
            # porte un guillemet, il déplace la parité de TOUT ce qui suit.
            # Compté après la décision sur CETTE interpolation, avant celle
            # sur les suivantes.
            if isinstance(part.format_spec, ast.JoinedStr):
                for spec_part in part.format_spec.values:
                    if (isinstance(spec_part, ast.Constant)
                            and isinstance(spec_part.value, str)):
                        static += spec_part.value
            if not inside:
                continue
            expression = part.value
            if _neutralising_call(expression) is not None:
                if _escape_is_shadowed_at(node, owner) or not escape_is_sound:
                    offenders.append(
                        f"ligne {node.lineno} : {ast.unparse(expression)} — "
                        f"un neutraliseur est relié dans la portée du site, "
                        f"la dispense ne tient pas"
                    )
                continue
            if isinstance(expression, ast.Constant):
                continue
            if (isinstance(expression, ast.Name)
                    and _is_dispensed_module_constant(
                        expression.id, node, literal_names, owner)):
                continue
            offenders.append(
                f"ligne {node.lineno} : {ast.unparse(expression)} "
                f"interpolé dans un littéral de chaîne sans {ESCAPE_FUNCTION}"
            )
    return sorted(set(offenders))


# ---------------------------------------------------------------------------
# La classe qu'un balayage de `JoinedStr` ne peut PAS voir.
#
# `%`, `.format`, `.join` et la concaténation assemblent un littéral de script
# sans jamais produire de `JoinedStr` : la garde ci-dessus leur est aveugle par
# construction. Il n'en existe aucune occurrence dans `bin/thingskit`
# aujourd'hui — l'interdiction est donc gratuite à poser, et c'est le seul
# moment où elle l'est.
# ---------------------------------------------------------------------------

def _carries_a_quote(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and '"' in node.value)


def _script_strings_built_outside_an_fstring(path: Path | None = None) -> list[str]:
    """Compte résiduel : littéraux portant un guillemet assemblés autrement
    qu'en f-string.

    Deux critères, parce que les formes ne se ressemblent pas. Pour `%`,
    `.format` et `.join`, le gabarit REFERME ses guillemets autour du trou
    (`'name:"%s"'`) : le critère est la présence d'un guillemet dans le
    littéral. Pour `+`, le littéral s'arrête au milieu (`'name:"'`) : le
    critère est la PARITÉ, la même que celle du balayage de f-strings.

    `.join` a DEUX critères depuis le troisième tour de review, et le second
    est le cas banal : le séparateur porte le guillemet (`'", "'.join(…)`,
    rare), OU un fragment de la liste INLINE le porte et le séparateur est
    vide (`''.join(['name:"', v, '"}'])`). Seule la première moitié était
    couverte, et la portée annoncée disait « `.join` ».

    Ce que le balayage ne voit pas, et qui est dit plutôt que tu : un gabarit
    dont le guillemet arrive par un NOM plutôt que par un littéral (`TPL % v`
    où `TPL` est une constante de module), une liste de fragments HISSÉE dans
    une variable avant le `.join`, un `+` ou un `%` dont l'opérande gauche
    n'est pas un littéral nu, et les formes d'assemblage autres que ces
    quatre-là — `string.Template`, `str.replace`, `io.StringIO().write()`,
    toutes vérifiées non employées ici et toutes non vues.
    """
    source = (path or SCRIPT_PATH).read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders: list[str] = []

    def flag(node, form):
        offenders.append(
            f"ligne {node.lineno} : littéral portant un guillemet "
            f"assemblé par {form}"
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            if _carries_a_quote(node.left):
                flag(node, "%")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # Le critère n'est PAS « porte un guillemet » : concaténer deux
            # fragments de script ÉQUILIBRÉS est licite, et `bin/thingskit` le
            # fait deux fois (lignes 1365 et 1392, mesuré). Le défaut est un
            # littéral qui LAISSE une chaîne OUVERTE — ce qui suit atterrit
            # alors dedans, exactement comme dans une f-string.
            if (isinstance(node.left, ast.Constant)
                    and isinstance(node.left.value, str)
                    and _inside_a_string_literal(node.left.value)
                    and not isinstance(node.right, ast.Constant)):
                flag(node, "+")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("format", "join")):
            if _carries_a_quote(node.func.value):
                flag(node, f".{node.func.attr}")
            elif (node.func.attr == "join" and node.args
                    and isinstance(node.args[0], (ast.List, ast.Tuple, ast.Set))
                    and any(_carries_a_quote(element)
                            for element in node.args[0].elts)):
                # Le cas BANAL, et celui que le critère « le séparateur porte
                # un guillemet » manquait : le séparateur est VIDE et ce sont
                # les fragments qui referment le littéral —
                # `''.join(['name:"', v, '"}'])`.
                flag(node, ".join")
    return sorted(set(offenders))


def test_no_value_reaches_a_script_string_literal_unescaped():
    """AC-2 : compte résiduel d'interpolations non échappées = 0.

    Garde de régression : ce test échoue si un site redevient nu — c'est
    exactement ce qui s'était produit sur `create-area` (BUG-033).
    """
    assert _unescaped_quoted_interpolations() == []


def test_the_sweep_actually_sees_an_unescaped_interpolation(tmp_path):
    """La garde ci-dessus ne vaut que si elle DÉTECTE le défaut qu'elle interdit.

    Le corps rejoué est celui de BUG-033, mot pour mot.
    """
    fake = tmp_path / "thingskit"
    fake.write_text(
        "def osa(s):\n"
        "    return 0, ''\n"
        "def cmd_create_area(a):\n"
        "    return osa(f'tell application \"Things3\" to make new area '\n"
        "               f'with properties {{name:\"{a.name}\"}}')\n",
        encoding="utf-8",
    )
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 4 : a.name interpolé dans un littéral de chaîne sans _esc"
    ]


def test_the_sweep_accepts_the_escaped_form_of_the_same_site(tmp_path):
    """Contre-épreuve symétrique : la garde ne refuse pas tout, elle refuse le
    défaut. Sans elle, un balayage cassé rendant `[]` sur tout passerait pour
    vert."""
    fake = tmp_path / "thingskit"
    fake.write_text(
        "def _esc(s):\n"
        "    return s\n"
        "def cmd_create_area(a):\n"
        "    return f'make new area with properties {{name:\"{_esc(a.name)}\"}}'\n",
        encoding="utf-8",
    )
    assert _unescaped_quoted_interpolations(fake) == []


def test_a_module_constant_is_the_only_dispense(tmp_path):
    """La dispense porte sur un littéral de MODULE, jamais sur un nom local :
    une variable locale peut porter une entrée."""
    fake = tmp_path / "thingskit"
    fake.write_text(
        "MARKER = 'THINGSKIT_NO_LABEL'\n"
        "def build(a):\n"
        "    local = a.name\n"
        "    return f'error \"{MARKER}\" & \"{local}\"'\n",
        encoding="utf-8",
    )
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 4 : local interpolé dans un littéral de chaîne sans _esc"
    ]


def test_a_module_name_bound_to_a_computed_value_is_not_dispensed(tmp_path):
    """Une constante de module COMPOSÉE n'est pas un littéral : sa valeur peut
    dépendre d'autre chose que de la source, la dispense ne la couvre pas."""
    fake = tmp_path / "thingskit"
    fake.write_text(
        "import os\n"
        "COMPUTED = os.environ.get('X', '')\n"
        "def build():\n"
        "    return f'error \"{COMPUTED}\"'\n",
        encoding="utf-8",
    )
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 4 : COMPUTED interpolé dans un littéral de chaîne sans _esc"
    ]


def test_the_escape_function_neutralises_the_three_characters_that_break_out():
    """`_esc` est la dispense : ce qu'elle vaut se constate, pas se suppose.

    Guillemet et antislash referment ou décalent le littéral ; le saut de ligne
    termine l'instruction AppleScript et ouvre la suivante.
    """
    from conftest import thingskit_cli as thingskit

    assert thingskit._esc('a"b') == 'a\\"b'
    assert thingskit._esc("a\\b") == "a\\\\b"
    assert thingskit._esc("a\nb") == "a\\nb"
    # L'antislash est traité EN PREMIER : sans cela `\"` deviendrait `\\"`,
    # c'est-à-dire un antislash littéral suivi d'un guillemet qui referme.
    assert thingskit._esc('\\"') == '\\\\\\"'
    # Le RETOUR CHARIOT n'est PAS échappé, et c'est une décision mesurée, pas
    # un oubli — l'épingler ici évite qu'on l'« ajoute par symétrie » avec le
    # LF. Mesuré le 2026-08-26 sur ce poste, par le chemin exact d'`osa()`
    # (`osascript -e <script>`) :
    #     osascript -e $'return "a\rdo shell script \\"echo PWNED\\""'
    #       -> rend la chaîne ENTIÈRE, rc=0 : le CR ne referme pas le littéral
    #     osascript -e $'return "a\rb"' | xxd  ->  610d 620a  (octet pour octet)
    # La constitution tranche le reste : refuser une classe qui passe est un
    # sur-refus, aussi fautif que laisser passer une classe qui casse.
    assert thingskit._esc("a\rb") == "a\rb"


# ---------------------------------------------------------------------------
# Durcissement (review sécurité + fonctionnelle du 2026-08-26).
#
# Le prédicat d'origine reconnaissait la position littérale par le SEUL
# fragment constant qui précède immédiatement l'interpolation
# (`before.endswith('"')`). Il fermait donc la FORME exacte de BUG-033 et rien
# d'autre : quatre formes voisines, toutes banales, passaient. Elles sont
# mesurées ci-dessous, chacune par une manipulation qui doit faire échouer la
# garde — une affirmation ne vaut pas preuve.
# ---------------------------------------------------------------------------

def _fake_module(tmp_path, source: str) -> Path:
    fake = tmp_path / "thingskit"
    fake.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")
    return fake


# --- parité : la position se calcule sur le texte statique CUMULÉ ----------

def test_a_second_interpolation_in_the_same_literal_is_seen(tmp_path):
    """Composer un message AppleScript avec DEUX valeurs est banal.

    Le fragment qui précède `a.name` est ` sur ` — il ne se termine pas par un
    guillemet. Seule la parité des guillemets depuis le DÉBUT du littéral dit
    qu'on est encore dedans.
    """
    fake = _fake_module(tmp_path, r'''
        MARK = 'm'
        def _esc(s):
            return s
        def osa(s):
            return 0, ''
        def build(a):
            return osa(f'error "{_esc(MARK)} sur {a.name}"')
        ''')
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 7 : a.name interpolé dans un littéral de chaîne sans _esc"
    ]


def test_an_interpolation_outside_a_closed_literal_is_not_flagged(tmp_path):
    """Contre-épreuve de la parité : refermé, on est DEHORS, et une garde qui
    refuserait tout serait désactivée dans la semaine."""
    fake = _fake_module(tmp_path, r'''
        def _esc(s):
            return s
        def build(a, n):
            return f'repeat "{_esc(a.name)}" times {n}'
        ''')
    assert _unescaped_quoted_interpolations(fake) == []


def test_an_applescript_escaped_quote_does_not_flip_the_parity(tmp_path):
    """`\\"` à l'intérieur du littéral ne le referme pas : le compter comme un
    guillemet ouvrant inverserait la parité et rendrait la garde aveugle sur
    tout ce qui suit."""
    fake = _fake_module(tmp_path, r'''
        def build(a):
            return f'say "prefixe \\" suite {a.name}"'
        ''')
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 2 : a.name interpolé dans un littéral de chaîne sans _esc"
    ]


def test_the_parity_spans_implicitly_concatenated_fragments(tmp_path):
    """La forme réelle du site de BUG-033 : deux fragments f-string accolés.

    Python les fusionne en UN `JoinedStr` ; la parité doit se calculer sur
    l'ensemble, pas fragment par fragment.
    """
    fake = _fake_module(tmp_path, r'''
        def osa(s):
            return 0, ''
        def build(a):
            return osa(f'tell application "Things3" to make new area '
                       f'with properties {{name:"{a.name}"}}')
        ''')
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 4 : a.name interpolé dans un littéral de chaîne sans _esc"
    ]


# --- dispense : sensible à la portée ---------------------------------------

def test_a_module_constant_shadowed_by_a_parameter_is_not_dispensed(tmp_path):
    """`_module_literal_names` collectait des noms sans aucune notion de
    portée. Un paramètre homonyme porte une ENTRÉE, pas la constante."""
    fake = _fake_module(tmp_path, r'''
        MARKER = 'x'
        def build(MARKER):
            return f'error "{MARKER}"'
        ''')
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 3 : MARKER interpolé dans un littéral de chaîne sans _esc"
    ]


def test_a_module_constant_reassigned_locally_is_not_dispensed(tmp_path):
    """Même défaut par l'autre route : une affectation locale au même nom."""
    fake = _fake_module(tmp_path, r'''
        MARKER = 'x'
        def build(a):
            MARKER = a.name
            return f'error "{MARKER}"'
        ''')
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 4 : MARKER interpolé dans un littéral de chaîne sans _esc"
    ]


def test_a_module_constant_shadowed_in_an_enclosing_scope_is_not_dispensed(tmp_path):
    """La portée se remonte : le paramètre est sur la fonction ENGLOBANTE."""
    fake = _fake_module(tmp_path, r'''
        MARKER = 'x'
        def outer(MARKER):
            def inner():
                return f'error "{MARKER}"'
            return inner
        ''')
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 4 : MARKER interpolé dans un littéral de chaîne sans _esc"
    ]


def test_a_genuine_module_constant_is_still_dispensed(tmp_path):
    """Contre-épreuve : la dispense reste, sinon le durcissement se paie en
    faux positifs et la garde finit désactivée."""
    fake = _fake_module(tmp_path, r'''
        MARKER = 'THINGSKIT_NO_LABEL'
        def build():
            return f'error "{MARKER}"'
        ''')
    assert _unescaped_quoted_interpolations(fake) == []


# --- la classe hors f-string ----------------------------------------------
#
# Un balayage de `JoinedStr` ne voit, par construction, AUCUNE des formes qui
# n'en sont pas. La review fonctionnelle a mesuré qu'il n'en existe aucune
# dans `bin/thingskit` aujourd'hui — ce qui rend l'interdiction gratuite à
# poser. Elle ne rend pas la classe impossible à contourner : elle rend
# impossibles les QUATRE formes nommées, et le § « CE QUE CES DEUX BALAYAGES
# NE TIENNENT PAS » du module dit lesquelles restent — `str.replace`,
# `string.Template`, `io.StringIO`, et tout assemblage passant par une
# variable intermédiaire.

def test_no_script_string_is_built_outside_an_f_string():
    """Compte résiduel = 0 : aucun littéral portant un guillemet n'est
    assemblé par `%`, `.format`, `.join` ou `+`."""
    assert _script_strings_built_outside_an_fstring() == []


@pytest.mark.parametrize("body, expected", [
    ("return osa('x name:\"%s\"' % a.name)", "%"),
    ("return osa('x name:\"{}\"'.format(a.name))", ".format"),
    ("return osa('\", \"'.join(a.names))", ".join"),
    ("return osa('x name:\"' + a.name + '\"')", "+"),
])
def test_the_out_of_fstring_sweep_sees_each_forbidden_form(tmp_path, body, expected):
    fake = _fake_module(tmp_path, "def osa(s):\n    return 0, ''\ndef build(a):\n    " + body + "\n")
    assert _script_strings_built_outside_an_fstring(fake) == [
        f"ligne 4 : littéral portant un guillemet assemblé par {expected}"
    ]


def test_the_out_of_fstring_sweep_leaves_balanced_script_fragments_alone(tmp_path):
    """Contre-épreuve mesurée : `bin/thingskit` concatène deux fois des
    fragments de script ÉQUILIBRÉS (lignes 1365 et 1392). Les refuser aurait
    coûté deux faux positifs, donc la désactivation de la garde."""
    fake = _fake_module(tmp_path, r'''
        def build(title):
            return ('tell application "Things3" to activate\n'
                    + _shown(title)
                    + 'end timeout\n'
                      'return "OK"\n')
        ''')
    assert _script_strings_built_outside_an_fstring(fake) == []


def test_the_out_of_fstring_sweep_leaves_a_quoteless_template_alone(tmp_path):
    """Contre-épreuve : ces opérateurs restent licites hors littéral de script.

    Sans elle, l'interdiction s'étendrait à tout `%` du fichier et serait
    retirée au premier message de log.
    """
    fake = _fake_module(tmp_path, r'''
        def build(a):
            msg = 'trouvé %s' % a.name
            joined = ', '.join(a.names)
            return msg + joined + ' fin'
        ''')
    assert _script_strings_built_outside_an_fstring(fake) == []


# ---------------------------------------------------------------------------
# Troisième tour de review (2026-08-26). Le lot précédent a fermé la parité,
# la portée des constantes et les formes hors f-string — et laissé DEUX trous
# du même côté que ceux qu'il fermait :
#
#   - la dispense `_esc` n'avait, elle, aucune notion de portée : un
#     `_esc = lambda s: s` local dispense tout le corps de la fonction ;
#   - la dispense de constante de module ajoutait sur la PREMIÈRE affectation
#     et ne retirait jamais : un `global` depuis n'importe quelle autre
#     fonction, ou une seconde affectation au niveau module, la conservait.
#
# Résidu nul pour les deux dans `bin/thingskit` (mesuré) : les poser ne crée
# aucune dette, et c'est le seul moment où c'est vrai.
# ---------------------------------------------------------------------------

def test_an_escape_call_shadowed_by_a_local_binding_does_not_dispense(tmp_path):
    """`_esc = lambda s: s` en tête de fonction dispense TOUT son corps.

    C'est exactement la classe fermée pour les constantes de module, laissée
    ouverte de l'autre côté de la dispense.
    """
    fake = _fake_module(tmp_path, r'''
        def _esc(s):
            return s.replace('"', chr(92) + '"')
        def cmd(a):
            _esc = lambda s: s
            return f'make new area with properties {{name:"{_esc(a.name)}"}}'
        ''')
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 5 : _esc(a.name) — un neutraliseur est relié dans la portée du site, "
        "la dispense ne tient pas"
    ]


def test_an_escape_function_rebound_at_module_level_does_not_dispense(tmp_path):
    """Même défaut par l'autre route : la fonction elle-même réaffectée."""
    fake = _fake_module(tmp_path, r'''
        def _esc(s):
            return s.replace('"', chr(92) + '"')
        _esc = lambda s: s
        def cmd(a):
            return f'error "{_esc(a.name)}"'
        ''')
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 5 : _esc(a.name) — un neutraliseur est relié dans la portée du site, "
        "la dispense ne tient pas"
    ]


def test_a_genuine_escape_call_is_still_dispensed(tmp_path):
    """Contre-épreuve : la dispense reste sur la forme saine — une fonction
    `_esc` définie une fois au niveau module et jamais reliée."""
    fake = _fake_module(tmp_path, r'''
        def _esc(s):
            return s.replace('"', chr(92) + '"')
        def cmd(a):
            return f'make new area with properties {{name:"{_esc(a.name)}"}}'
        ''')
    assert _unescaped_quoted_interpolations(fake) == []


def test_a_module_constant_reassigned_by_a_global_elsewhere_is_not_dispensed(tmp_path):
    """La dispense dit « sa valeur est écrite dans la source ». Un `global`
    depuis n'importe quelle AUTRE fonction du module la dément."""
    fake = _fake_module(tmp_path, r'''
        MARKER = 'safe'
        def poison(a):
            global MARKER
            MARKER = a.name
        def cmd():
            return f'error "{MARKER}"'
        ''')
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 6 : MARKER interpolé dans un littéral de chaîne sans _esc"
    ]


def test_a_module_name_assigned_twice_at_module_level_is_not_dispensed(tmp_path):
    """La première affectation était un littéral, la seconde ne l'est pas.

    L'ancien balayage ajoutait sur la première et ne retirait jamais.
    """
    fake = _fake_module(tmp_path, r'''
        import os
        DEFAULT = 'safe'
        DEFAULT = os.environ.get('X', '')
        def cmd():
            return f'error "{DEFAULT}"'
        ''')
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 5 : DEFAULT interpolé dans un littéral de chaîne sans _esc"
    ]


def test_a_module_name_rebound_inside_a_module_level_branch_is_not_dispensed(tmp_path):
    """La seconde affectation n'a pas besoin d'être au premier niveau de la
    liste des instructions : un `if` de module suffit."""
    fake = _fake_module(tmp_path, r'''
        import os
        DEFAULT = 'safe'
        if os.environ.get('X'):
            DEFAULT = os.environ['X']
        def cmd():
            return f'error "{DEFAULT}"'
        ''')
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 6 : DEFAULT interpolé dans un littéral de chaîne sans _esc"
    ]


def test_a_module_constant_assigned_exactly_once_is_still_dispensed(tmp_path):
    """Contre-épreuve du durcissement ci-dessus : la dispense légitime reste,
    sinon `bin/thingskit` se paie des faux positifs et la garde saute."""
    fake = _fake_module(tmp_path, r'''
        MARKER = 'THINGSKIT_NO_LABEL'
        OTHER = 'x'
        def cmd():
            return f'error "{MARKER}" et "{OTHER}"'
        ''')
    assert _unescaped_quoted_interpolations(fake) == []


# --- `.join` : le séparateur n'est pas le seul porteur de guillemet --------

def test_a_join_over_an_inline_list_of_fragments_is_seen(tmp_path):
    """L'interdiction de `.join` ne portait que sur le SÉPARATEUR — le cas
    rare. Le cas banal met le guillemet dans les FRAGMENTS et le séparateur
    est vide : `''.join(['name:"', v, '"}'])` était invisible."""
    fake = _fake_module(tmp_path, r'''
        def osa(s):
            return 0, ''
        def cmd(a):
            return osa(''.join(['make new area with properties {name:"',
                                a.name, '"}']))
        ''')
    assert _script_strings_built_outside_an_fstring(fake) == [
        "ligne 4 : littéral portant un guillemet assemblé par .join"
    ]


def test_a_join_over_a_quoteless_inline_list_is_not_flagged(tmp_path):
    """Contre-épreuve : joindre des fragments sans guillemet reste licite."""
    fake = _fake_module(tmp_path, r'''
        def cmd(a):
            return ', '.join(['un', a.name, 'trois'])
        ''')
    assert _script_strings_built_outside_an_fstring(fake) == []


# --- le `format_spec` fait partie du texte émis ----------------------------

def test_the_format_spec_counts_toward_the_cumulative_static_text(tmp_path):
    """Un `format_spec` est du texte STATIQUE du littéral émis : s'il porte un
    guillemet, il déplace la parité de tout ce qui suit."""
    fake = _fake_module(tmp_path, r"""
        def cmd(a, n):
            return f'say "{n:"<5}" puis {a.name}'
        """)
    assert _unescaped_quoted_interpolations(fake) == [
        "ligne 2 : a.name interpolé dans un littéral de chaîne sans _esc",
        # `n` aussi : il est dedans, avant même que le `format_spec` ne compte.
        "ligne 2 : n interpolé dans un littéral de chaîne sans _esc",
    ]
