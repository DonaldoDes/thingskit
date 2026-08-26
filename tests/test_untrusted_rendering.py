"""Zone sensible n° 1 — une valeur d'origine non contrôlée n'atteint pas la
sortie sans conversion (BUG-026).

Le dommage n'est pas une corruption d'octets : les octets émis sont corrects,
et c'est ce qui rend le défaut invisible en relecture. C'est le TERMINAL qui
exécute ce qu'il reçoit. Un titre portant `\x1b[2K\r` efface la ligne et fait
lire une confirmation qu'aucune partie du programme n'a écrite, avec un code
retour 0 légitime ; un titre portant U+202E inverse le sens de lecture.

LE PRÉDICAT
===========
Cinq mesures ont précédé ce module et ont rendu cinq résultats — 24 sites plus
13 faibles, 237 champs sur 326, 82 sites, 117, 37. Aucune n'était un comptage
fautif : chacune présumait une définition de « valeur d'origine non contrôlée »
au lieu de l'écrire. Celle-ci est écrite, elle est exécutable, et elle est
ci-dessous. C'est elle qu'il faut contester pour contester le chiffre.

Racines — une expression est d'origine non contrôlée si elle est :
  R1. le namespace argparse d'une commande (le 1er paramètre d'un `cmd_*`),
      donc tout attribut qu'on en tire : les arguments de la ligne de commande ;
  R2. le résultat de `q(...)`, seule lecture de la base Things — titres,
      headings, areas, notes, identifiants ;
  R3. le résultat de `osa(...)`, la sortie d'`osascript`. C'est l'inclusion que
      le balayage à 37 sites EXCLUAIT, et c'est ce qui l'a établi faux ;
  R4. un paramètre d'une fonction du module qui reçoit, sur au moins un site
      d'appel, une valeur d'origine non contrôlée ;
  R5. le retour — par SLOT de tuple — d'une fonction dont au moins un `return`
      l'est. Le slot n'est pas un détail : `_resolve_*` rend `(uuid, message)`,
      et suivre la valeur sans suivre son rang confond les deux ;
  R6. `sys.argv`. R1 ne voit la ligne de commande qu'une fois PARSÉE ; une
      lecture directe d'argv la précède. Sans cette racine le prédicat a un
      trou en amont de lui-même — c'est par là que le chemin `unrecognized
      arguments` d'`argparse` a émis ESC et CR bruts jusqu'au 2026-08-26 ;
  R7. le contenu d'un FICHIER lu par le CLI — `<x>.read()`, `<x>.read_text()`
      ou `<x>.readlines()`. La racine porte sur la LECTURE, pas sur une de ses
      orthographes : n'en reconnaître qu'une était le défaut du balayage de
      `sleep`, qui ne voyait `time.sleep` que sous une forme sur trois.
      **Ce qu'elle ne voit pas, dit plutôt que tu** : une lecture atteinte par
      un alias (`lire = handle.read` puis `lire()`), par `os.read`, par
      `json.load(handle)`, ou par un module tiers. Aucune n'existe dans
      `bin/thingskit` — mesuré — et l'ajout d'une seule impose de relire cette
      racine.
      Ajoutée le 2026-08-26 par ADR-003, qui fait entrer une source externe
      que les six racines précédentes n'atteignaient pas : le fichier
      d'identité scellé dans le bundle. Il est scellé, donc il devrait être
      digne de confiance — mais le cas où la garde REFUSE est précisément
      celui où il ne l'est pas, et c'est là que son contenu atteint un
      message. Une racine qui ne vaudrait que « quand tout va bien » ne vaut
      rien.

Trajet — la valeur reste non contrôlée à travers : attribut, indice, tranche,
`+`, `.join`, ternaire, `or`/`and`, compréhension, cible de boucle sur un
itérable non contrôlé, mutation de conteneur (`append`, `extend`, `update`,
`insert`, `setdefault`, `d[k] = v`), interpolation dans une f-string, appel de
méthode sur un receveur ou un argument non contrôlé. **Quel que soit le nombre
de compositions intermédiaires** : c'est la clause de la constitution, et c'est
ce qui distingue ce balayage d'une garde posée sur les sites d'affichage.

Conversions — ce qui purge : `!r`, `!a`, `repr()`, `ascii()`, `_rendered()`,
`json.dumps()`, et un format_spec de PRÉSENTATION NUMÉRIQUE (`b d o x X n e E
f F g G %`). Ce dernier n'est pas une commodité : `f"{v:04d}"` lève sur une
chaîne, donc la valeur y est un nombre et ne peut porter aucun caractère de
contrôle. `s` et un simple alignement (`:<38`) n'en sont PAS — ils acceptent
une chaîne et la laissent intacte —, ni `c`, qui fabrique un caractère à
partir d'un entier et peut donc produire un ESC. Épinglé par
`test_an_alignment_spec_is_not_a_conversion`.

Neutres — ce qui ne transporte pas : `len`, `int`, `float`, `bool`, `abs`,
`round`, `ord`, `isinstance`, comparaisons, opérateurs booléens.

Sorties — les arguments positionnels de `print`, de `sys.stdout.write` et de
`sys.stderr.write` ; et, par R5, le retour de toute fonction dont la valeur
atteint l'un d'eux.

CE QUE LE PRÉDICAT EXCLUT, ET POURQUOI
--------------------------------------
Refuser une classe qui passe est un sur-refus, aussi fautif que laisser passer
une classe qui casse (constitution § Zones sensibles).
  - Les littéraux, constantes de module et compteurs : `len(rows)` n'est pas
    une valeur d'origine non contrôlée, et `27 tâche(s)` ne doit pas devenir
    `'27' tâche(s)`.
  - Ce que le programme écrit lui-même — le texte statique des f-strings, le
    `\n` de mise en page. Un filtre posé sur le site d'affichage les prendrait
    aussi ; c'est la seconde raison pour laquelle la propriété porte sur la
    valeur.
  - Une valeur composée brute mais émise CONVERTIE plus loin. C'est le faux
    positif vérifié du balayage à 237 : `where = a.list + f" › {a.heading}"`
    compose brut, mais `where` n'est émis que par `{where!r}`. Ce balayage-ci
    ne le compte pas — épinglé par
    `test_the_sweep_does_not_cry_wolf_on_a_value_converted_further_along`.

CE QU'IL NE COUVRE PAS
----------------------
Écrit ici plutôt que tu, parce que « la classe est fermée » a déjà été affirmé
à tort dans ce dépôt. Cette liste a DOUBLÉ le 2026-08-26 : le lot initial
affirmait « le script entier tient cet invariant » alors qu'une valeur sortait
brute par `argparse`, et annonçait une couverture de `json.dumps` deux fois
plus large que la mesure. Les deux défauts étaient invisibles au balayage —
c'est ce que cette liste existe pour dire.
  - Les indirections qu'une analyse statique ne suit pas : `%`, `.format()`,
    `string.Template`, `.replace()` sur un gabarit, `io.StringIO`. Elles ne
    sont pas suivies — elles sont INTERDITES, par
    `test_no_output_is_composed_by_a_form_the_sweep_cannot_follow`.
  - Les portées et les puits que le balayage ne modélise pas : conteneur ou
    global de module, méthode de classe, alias de `print` ou d'un flux
    standard, `sys.exit`/`SystemExit` à message composé, défaut de paramètre
    calculé, paramètre variadique, walrus en argument de puits, exception
    interpolée, `writelines`, `os.write`, `subprocess` à stdio hérité dont
    l'argv est interpolé. Quinze détecteurs, quatorze formes, ZÉRO occurrence :
    `test_no_output_escapes_the_sweep_by_its_scope_or_by_its_sink`. Chacune est
    épinglée dans les deux sens — la garde la refuse
    (`test_the_scope_and_sink_guard_refuses_each_form`) ET le balayage seul la
    manquerait (`test_the_sweep_alone_would_have_missed_each_form`).
    Ce que cette garde-là ne voit pas, à son tour : un `subprocess` dont l'argv
    est passé par un NOM (`subprocess.run(argv)`, ligne 506) — elle n'inspecte
    qu'une liste littérale.
  - Le code de MODULE. Le balayage ne collecte que les fonctions de premier
    niveau : le bloc `if __name__ == "__main__"` — dont le `print(_refusal)`
    du contrôle d'identité de code — n'est analysé par rien.
  - Ce qu'`argparse` émet. Il compose hors du module, à partir d'argv, avant
    que la valeur n'existe comme namespace : aucune racine ne l'atteint. La
    borne y est posée au PUITS, dans `_BoundedParser.error`/`.exit` — c'est la
    seule frontière où le message repasse par nous.
  - Le partage « `!r` en prose, `_rendered` en position » n'est mécanisé que
    pour sa forme cumulée (`_rendered(x)!r`,
    `test_no_prose_message_uses_the_conditional_rendering`). Distinguer la
    prose d'une position délimitée n'est pas décidable à l'AST : les trois
    écarts du 2026-08-26 (lignes 882, 890, 1045) ont été trouvés à la
    RELECTURE, et un quatrième le serait de la même façon.
  - La sensibilité au flot : un nom est non contrôlé si UNE de ses affectations
    l'est, où qu'elle soit. Sur-approximation assumée, jamais l'inverse.
  - Un indice remonte à son conteneur sans que la clé soit modélisée :
    `seen["gap"]` et `seen["probe"]` sont confondus s'ils coexistent.
  - Les portées imbriquées sont fusionnées avec leur fonction englobante — les
    closures de `wait_for_effect` lisent bien les locales de leur englobante.
  - Les types ne sont pas modélisés : `STATUS_LABELS.get(status, status)` est
    compté non contrôlé bien que `status` soit un entier de la base. Le coût de
    cette sur-approximation est nul, `_rendered` laissant inchangée toute
    valeur imprimable.
  - `json.dumps` compte comme conversion, et sa portée est PLUS ÉTROITE que
    ce que ce module a d'abord annoncé. Mesuré le 2026-08-26, `ensure_ascii=
    False` : elle échappe C0 — 32 caractères sur les 65 de Cc, U+0000..U+001F,
    dont ESC, CR et LF. DEL et TOUT C1 traversent (33 caractères), dont
    U+0085 NEL, que `str.splitlines()` traite comme un saut de ligne : un
    titre peut donc couper une ligne de sortie `--json`. Cf traverse aussi.
    Résidu nommé : DEL, C1 et Cf traversent une sortie `--json`. Rejouable :

        .venv/bin/python -c 'import json,unicodedata as u;
        cc=[chr(c) for c in range(0x110000) if u.category(chr(c))=="Cc"];
        print(len(cc), sum(c not in json.dumps("a"+c+"b",ensure_ascii=False)
        for c in cc))'
        -> 65 32

  - `_rendered` borne la classe refusée — Cc, Cf, Zl, Zp, Cs, Co, Cn — et NON
    celle de `str.isprintable()`, qui y ajoute les séparateurs d'espace. `!r`,
    lui, est `repr` : il échappe U+00A0. C'est le seul écart entre les deux
    conversions, il va dans le sens du sur-refus, et il subsiste en prose.
  - Ce qui est imprimable ET trompeur — homoglyphe, espace cadratin, titre
    imitant mot pour mot un message du programme — échappe aux deux. Le
    quoting en limite la portée, il ne la ferme pas. Sortir Zs de la classe
    refusée élargit ce résidu-là, et rien d'autre : les 17 `Zs` sont des
    espaces visibles de largeur non nulle et n'exécutent rien.
"""
from __future__ import annotations

import argparse
import ast
import sqlite3
import sys
import unicodedata
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "bin" / "thingskit"

ROOT_CALLS = {"q", "osa"}
CONVERSIONS = {114, 97}                      # !r, !a
CONVERTING_CALLS = {"repr", "ascii", "_rendered"}
DROPPING_CALLS = {"len", "int", "float", "bool", "abs", "round", "ord",
                  "isinstance"}
MUTATORS = {"append": 0, "add": 0, "extend": 0, "update": 0,
            "insert": 1, "setdefault": 1}
NUMERIC_PRESENTATION = set("bdoxXneEfFgG%")


def _is_sys_argv(expr) -> bool:
    """R6 — `sys.argv` est une racine, en amont du namespace argparse.

    R1 ne voit la ligne de commande qu'une fois parsée. Une lecture directe
    d'`sys.argv` la précède : sans cette racine, le prédicat a un trou en
    amont de lui-même — et c'est par là que le chemin `unrecognized
    arguments` d'`argparse` a émis ESC et CR bruts jusqu'au 2026-08-26.
    """
    return (isinstance(expr, ast.Attribute) and expr.attr == "argv"
            and isinstance(expr.value, ast.Name) and expr.value.id == "sys")


READING_METHODS = {"read", "read_text", "readlines"}


def _is_file_read(expr) -> bool:
    """R7 — le contenu d'un fichier lu par le CLI.

    Un seul site dans `bin/thingskit` (la lecture du fichier d'identité
    scellé), et la règle porte sur la FORME plutôt que sur ce site : une
    seconde lecture de fichier serait autrement une racine de plus que rien
    n'atteindrait. Les trois orthographes sont reconnues — `read`, `read_text`
    et `readlines` —, et ce que la racine ne voit pas est énuméré dans le
    prédicat, en tête de module.
    """
    return (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
            and expr.func.attr in READING_METHODS)


def _is_numeric_spec(spec) -> bool:
    """`{v:04d}` coerce en nombre — donc convertit. `{v:<38}` non."""
    if spec is None:
        return False
    if not (isinstance(spec, ast.JoinedStr) and len(spec.values) == 1
            and isinstance(spec.values[0], ast.Constant)):
        return False
    text = spec.values[0].value
    return bool(text) and text[-1] in NUMERIC_PRESENTATION


# ---------------------------------------------------------------------------
# Le balayage
# ---------------------------------------------------------------------------
class Sweep:
    """Analyse de teinte intra-module sur le prédicat du docstring."""

    def __init__(self, source: str):
        self.source = source
        self.tree = ast.parse(source)
        self.funcs = {n.name: n for n in self.tree.body
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assigns: dict[str, dict[str, list]] = {}
        self.returns: dict[str, list] = {}
        self.sinks: dict[str, list] = {}
        self.loops: dict[str, list] = {}
        for name, fn in self.funcs.items():
            self._collect(name, fn)
        self.tainted_names = {f: set() for f in self.funcs}
        self.tainted_ret: set[str] = set()
        self.emitting: dict[str, set] = {}
        self._fixpoint_taint()
        self._fixpoint_emission()

    # -- collecte ----------------------------------------------------------
    def _collect(self, fname, fn):
        A = self.assigns.setdefault(fname, {})
        R = self.returns.setdefault(fname, [])
        S = self.sinks.setdefault(fname, [])
        L = self.loops.setdefault(fname, [])
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    self._bind(A, t, node.value)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value:
                self._bind(A, node.target, node.value)
            elif isinstance(node, ast.NamedExpr):
                self._bind(A, node.target, node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                L.append((node.target, node.iter))
            elif isinstance(node, ast.Return) and node.value is not None:
                R.append(node.value)
            elif isinstance(node, ast.Call):
                S.extend(self._sink_args(node))
                self._bind_mutation(A, node)

    @staticmethod
    def _bind(A, target, value, slot=None):
        if isinstance(target, ast.Name):
            A.setdefault(target.id, []).append((value, slot))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for i, elt in enumerate(target.elts):
                Sweep._bind(A, elt, value, i)
        elif isinstance(target, ast.Starred):
            Sweep._bind(A, target.value, value, slot)
        elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            A.setdefault(target.value.id, []).append((value, slot))

    @staticmethod
    def _bind_mutation(A, call):
        """`out.append(v)` lie `v` à `out`. Sans cette règle, une valeur ne
        circule que par affectation, et la table d'`agenda` — peuplée par
        `append` — sortait du balayage : 60 sites mesurés sans, 80 avec."""
        f = call.func
        if not (isinstance(f, ast.Attribute) and f.attr in MUTATORS):
            return
        i = MUTATORS[f.attr]
        if isinstance(f.value, ast.Name) and len(call.args) > i:
            A.setdefault(f.value.id, []).append((call.args[i], None))

    @staticmethod
    def _sink_args(call):
        f = call.func
        if isinstance(f, ast.Name) and f.id == "print":
            return list(call.args)
        if (isinstance(f, ast.Attribute) and f.attr == "write"
                and isinstance(f.value, ast.Attribute)
                and f.value.attr in ("stdout", "stderr")):
            return list(call.args)
        return []

    # -- teinte ------------------------------------------------------------
    def tainted(self, expr, fname, seen=None) -> bool:
        seen = seen or set()
        if id(expr) in seen:
            return False
        seen = seen | {id(expr)}
        T = self.tainted_names[fname]
        rec = lambda e: self.tainted(e, fname, seen)
        if isinstance(expr, ast.Name):
            return expr.id in T
        if isinstance(expr, (ast.Attribute, ast.Subscript, ast.Starred)):
            if _is_sys_argv(expr):
                return True                                          # R6
            return rec(expr.value)
        if isinstance(expr, ast.Call):
            if _is_file_read(expr):
                return True                                          # R7
            f = expr.func
            if isinstance(f, ast.Name):
                if f.id in CONVERTING_CALLS or f.id in DROPPING_CALLS:
                    return False
                if f.id in ROOT_CALLS:
                    return True
                if f.id in self.funcs and f.id in self.tainted_ret:
                    return True
            if isinstance(f, ast.Attribute):
                if f.attr == "dumps":
                    return False
                if rec(f.value):
                    return True
            return (any(rec(a) for a in expr.args)
                    or any(rec(k.value) for k in expr.keywords))
        if isinstance(expr, ast.JoinedStr):
            return any(rec(v) for v in expr.values
                       if isinstance(v, ast.FormattedValue))
        if isinstance(expr, ast.FormattedValue):
            if expr.conversion in CONVERSIONS or _is_numeric_spec(expr.format_spec):
                return False
            return rec(expr.value)
        if isinstance(expr, ast.BinOp):
            return rec(expr.left) or rec(expr.right)
        if isinstance(expr, ast.IfExp):
            return rec(expr.body) or rec(expr.orelse)
        if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
            return any(rec(e) for e in expr.elts)
        if isinstance(expr, ast.Dict):
            return any(rec(v) for v in expr.values if v is not None)
        if isinstance(expr, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return rec(expr.elt)
        if isinstance(expr, ast.DictComp):
            return rec(expr.key) or rec(expr.value)
        return False

    def _fixpoint_taint(self):
        for fname, fn in self.funcs.items():
            if fname.startswith("cmd_") and fn.args.args:
                self.tainted_names[fname].add(fn.args.args[0].arg)   # R1
        changed = True
        while changed:
            changed = False
            for fname in self.funcs:
                T = self.tainted_names[fname]
                for var, binds in self.assigns[fname].items():
                    if var not in T and any(self.tainted(e, fname) for e, _ in binds):
                        T.add(var)
                        changed = True
                for target, it in self.loops[fname]:
                    if self.tainted(it, fname):
                        for n in ast.walk(target):
                            if isinstance(n, ast.Name) and n.id not in T:
                                T.add(n.id)
                                changed = True
                if fname not in self.tainted_ret and any(
                        self.tainted(r, fname) for r in self.returns[fname]):
                    self.tainted_ret.add(fname)                      # R5
                    changed = True
            changed |= self._propagate_arguments()                   # R4

    def _propagate_arguments(self) -> bool:
        changed = False
        for caller, fn in self.funcs.items():
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in self.funcs):
                    continue
                callee = node.func.id
                names = [a.arg for a in self.funcs[callee].args.args]
                for i, arg in enumerate(node.args):
                    if (i < len(names) and names[i] not in self.tainted_names[callee]
                            and self.tainted(arg, caller)):
                        self.tainted_names[callee].add(names[i])
                        changed = True
                for kw in node.keywords:
                    if (kw.arg and kw.arg not in self.tainted_names[callee]
                            and self.tainted(kw.value, caller)):
                        self.tainted_names[callee].add(kw.arg)
                        changed = True
        return changed

    # -- émission ----------------------------------------------------------
    def _fixpoint_emission(self):
        changed = True
        while changed:
            changed = False
            for fname in self.funcs:
                for root, slot in self.emitted_roots(fname):
                    for leaf, conv, lslot in self.leaves(root, fname, slot):
                        if conv:
                            continue
                        if (isinstance(leaf, ast.Call)
                                and isinstance(leaf.func, ast.Name)
                                and leaf.func.id in self.funcs
                                and leaf.func.id not in ROOT_CALLS):
                            got = self.emitting.setdefault(leaf.func.id, set())
                            if lslot not in got:
                                got.add(lslot)
                                changed = True

    def emitted_roots(self, fname):
        roots = [(e, None) for e in self.sinks[fname]]
        for slot in self.emitting.get(fname, ()):
            roots += [(r, slot) for r in self.returns[fname]]
        return roots

    def leaves(self, expr, fname, slot=None, seen=None):
        """Feuilles ÉMISES : `(noeud, convertie?, slot)`.

        Descend dans les compositions ET REMONTE le trajet des noms locaux
        jusqu'à leurs affectations — c'est cette remontée qui fait porter le
        prédicat sur la valeur, et non sur le site d'affichage.
        """
        seen = set() if seen is None else seen
        key = (id(expr), slot)
        if key in seen:
            return []
        seen = seen | {key}
        rec = lambda e, sl=None: self.leaves(e, fname, sl, seen)

        if isinstance(expr, ast.Constant):
            return []
        if isinstance(expr, ast.IfExp):
            return rec(expr.body, slot) + rec(expr.orelse, slot)
        if isinstance(expr, ast.BoolOp):
            return [x for v in expr.values for x in rec(v, slot)]
        if isinstance(expr, ast.Tuple) and slot is not None:
            return rec(expr.elts[slot]) if slot < len(expr.elts) else []
        if isinstance(expr, ast.Name) or (
                isinstance(expr, ast.Subscript) and isinstance(expr.value, ast.Name)):
            # Un indice sur un nom remonte comme le nom : `seen["gap"] = …`
            # puis `print(f"{seen['gap']}")` est LA forme « composé ailleurs,
            # interpolé plus loin » que la constitution nomme. La clé n'est
            # pas modélisée — sur-approximation, jamais l'inverse.
            root = expr if isinstance(expr, ast.Name) else expr.value
            binds = self.assigns[fname].get(root.id)
            if not binds:
                return [(expr, False, slot)]
            return [x for value, bslot in binds
                    for x in rec(value, bslot if slot is None else slot)]
        if slot is not None:
            return [(expr, False, slot)]
        if isinstance(expr, ast.JoinedStr):
            out = []
            for v in expr.values:
                if not isinstance(v, ast.FormattedValue):
                    continue
                if v.conversion in CONVERSIONS or _is_numeric_spec(v.format_spec):
                    out.append((v.value, True, None))
                else:
                    out += rec(v.value)
                if v.format_spec is not None:
                    out += rec(v.format_spec)
            return out
        if isinstance(expr, ast.BinOp):
            return rec(expr.left) + rec(expr.right)
        if isinstance(expr, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return rec(expr.elt)
        if isinstance(expr, ast.Call):
            f = expr.func
            if isinstance(f, ast.Name) and f.id in CONVERTING_CALLS:
                return [(expr, True, None)]
            if isinstance(f, ast.Attribute) and f.attr == "dumps":
                return [(expr, True, None)]
            if isinstance(f, ast.Attribute) and f.attr in ("join", "format"):
                out = rec(f.value) if f.attr == "join" else []
                return out + [x for a in expr.args for x in rec(a)]
        return [(expr, False, slot)]

    # -- verdict -----------------------------------------------------------
    def violations(self) -> list[tuple[str, int, str]]:
        found = set()
        for fname in self.funcs:
            for root, slot in self.emitted_roots(fname):
                for leaf, conv, _ in self.leaves(root, fname, slot):
                    if conv:
                        continue
                    if (isinstance(leaf, ast.Call)
                            and isinstance(leaf.func, ast.Name)
                            and leaf.func.id in self.funcs
                            and leaf.func.id not in ROOT_CALLS):
                        # Suivi par les `return` de la fonction, pas ici — SAUF
                        # pour une racine : `osa(...)` EST l'origine, et la
                        # suivre par son corps la ferait disparaître. C'est
                        # l'exclusion qui a rendu faux le balayage à 37 sites.
                        continue
                    if self.tainted(leaf, fname):
                        found.add((fname, leaf.lineno,
                                   ast.get_source_segment(self.source, leaf) or ""))
        return sorted(found, key=lambda v: (v[1], v[2]))


def _script_source() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _report(viol) -> str:
    return "\n".join(f"  {ln:5d}  {fn:26s}  {src}" for fn, ln, src in viol)


# ---------------------------------------------------------------------------
# Le résidu, et le compte
# ---------------------------------------------------------------------------
def test_no_untrusted_value_reaches_the_output_unconverted():
    """Compte résiduel NUL. C'est la mesure de BUG-026 rendue permanente : le
    prochain site brut est refusé, pas signalé après coup."""
    viol = Sweep(_script_source()).violations()
    assert viol == [], (
        f"{len(viol)} valeur(s) d'origine non contrôlée atteignent la sortie "
        f"sans conversion :\n{_report(viol)}")


def test_no_output_is_composed_by_a_form_the_sweep_cannot_follow():
    """Les indirections que l'analyse ne suit pas sont INTERDITES.

    `%`, `.format()`, `string.Template`, `.replace()` sur un gabarit et
    `io.StringIO` composent du texte sans produire de `JoinedStr` : le
    balayage ci-dessus les traverserait sans rien voir. Les interdire est ce
    qui empêche que « 0 site » devienne vrai par contournement plutôt que par
    correction. Mesuré : 0 occurrence dans `bin/thingskit`.
    """
    tree = ast.parse(_script_source())
    forbidden = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and (
                isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)):
            forbidden.append((node.lineno, "formatage `%`"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "format" and not (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id in ("dt", "datetime")):
                forbidden.append((node.lineno, ".format()"))
            if node.func.attr == "substitute":
                forbidden.append((node.lineno, "string.Template"))
        if isinstance(node, ast.Attribute) and node.attr == "StringIO":
            forbidden.append((node.lineno, "io.StringIO"))
    assert forbidden == [], (
        "composition de texte par une forme que le balayage ne suit pas : "
        f"{forbidden}")


# ---------------------------------------------------------------------------
# Contre-épreuves : le balayage voit-il ce qu'il interdit ?
# ---------------------------------------------------------------------------
def test_the_sweep_sees_a_raw_site_reintroduced_into_the_real_script():
    """Manipulation RÉELLE : on retire une conversion du script du dépôt et le
    balayage doit passer de 0 à au moins 1. Sans cette épreuve, « 0 site »
    pourrait aussi bien signifier « le balayage ne voit rien »."""
    source = _script_source()
    needle = "print(f\"tâche ajoutée : {a.title!r} → {where!r}\")"
    assert needle in source, "site témoin déplacé — mettre l'épreuve à jour"
    mutated = source.replace(needle,
                             "print(f\"tâche ajoutée : {a.title} → {where!r}\")")
    viol = Sweep(mutated).violations()
    assert any(fn == "cmd_add_task" for fn, _, _ in viol), (
        f"le balayage n'a pas vu le site brut réintroduit : {_report(viol)}")


SYNTHETIC = {
    "direct": '''
def q(sql, args=()): return []
def cmd_x(a):
    print(f"fait : {a.title}")
''',
    # Le cas que la constitution nomme : composé dans un helper, interpolé plus
    # loin. Une garde posée sur les sites d'affichage ne le voit pas.
    "compose_ailleurs": '''
def q(sql, args=()): return []
def _label(a):
    return f"{a.list} › {a.heading}"
def cmd_x(a):
    print(f"fait : {_label(a)}")
''',
    # Conteneur peuplé par mutation, puis parcouru : la forme de `cmd_agenda`.
    "mutation_de_conteneur": '''
def q(sql, args=()): return []
def cmd_x(a):
    rows = q("select title from TMTask")
    acc = []
    for (t,) in rows:
        acc.append({"title": t})
    for o in acc:
        print(f"{o['title']}")
''',
    # La sortie d'osascript — l'origine que le balayage à 37 sites excluait.
    "sortie_osascript": '''
def osa(script): return (0, "")
def cmd_x(a):
    rc, out = osa("tell application \\"Things3\\" to quit")
    print(f"résultat : {out}")
''',
    # Deuxième slot d'un tuple de retour : suivre la valeur sans suivre son
    # rang confondrait le message converti et l'identifiant brut.
    "slot_de_retour": '''
def q(sql, args=()): return []
def _resolve(title):
    rows = q("select uuid, title from TMTask where title=?", (title,))
    return rows[0][0], rows[0][1]
def cmd_x(a):
    uuid, label = _resolve(a.title)
    print(f"trouvé : {label!r} ({uuid})")
''',
    # R7 — le contenu d'un fichier lu, composé puis émis brut. Aucune des six
    # racines précédentes ne l'atteignait : c'est ADR-003 qui fait entrer
    # cette source dans le script.
    "contenu_de_fichier": '''
def q(sql, args=()): return []
def cmd_x(a):
    with open("/tmp/x") as handle:
        text = handle.read()
    print(f"lu : {text}")
''',
    # Paramètre d'un helper qui imprime lui-même.
    "parametre_de_helper": '''
def q(sql, args=()): return []
def _say(title):
    print(f"note posée sur {title}")
def cmd_x(a):
    _say(a.title)
''',
}


@pytest.mark.parametrize("form", sorted(SYNTHETIC))
def test_the_sweep_sees_each_form_of_the_class(form):
    viol = Sweep(SYNTHETIC[form]).violations()
    assert viol, f"forme `{form}` non vue par le balayage"


def test_the_sweep_does_not_cry_wolf_on_a_value_converted_further_along():
    """Le faux positif VÉRIFIÉ du balayage à 237 champs : `where` compose
    `a.heading` brut, mais n'est émis que par `{where!r}`. La valeur est
    convertie — plus loin. Ce n'est pas un défaut, et le compter en serait un.
    """
    source = '''
def cmd_x(a):
    where = a.list + (f" › {a.heading}" if a.heading else "")
    print(f"trouvé : {a.title!r} dans {where!r}")
'''
    assert Sweep(source).violations() == []


def test_an_alignment_spec_is_not_a_conversion():
    """Contre-épreuve de la règle ci-dessus : `:<38` aligne une chaîne et la
    laisse intacte. La confondre avec `:04d` rendrait muet tout le rendu
    tabulaire — c'est-à-dire les commandes de lecture, les plus utilisées."""
    aligned = """
def q(sql, args=()): return []
def cmd_x(a):
    rows = q("select title from TMTask")
    for (t,) in rows:
        print(f"{t:<38}")
"""
    numeric = """
def q(sql, args=()): return []
def cmd_x(a):
    rows = q("select n from TMTask")
    for (n,) in rows:
        print(f"{n:04d}")
"""
    assert Sweep(aligned).violations(), "`:<38` compté à tort comme conversion"
    assert Sweep(numeric).violations() == [], "`:04d` ne coerce pas ?"


def test_the_sweep_does_not_cry_wolf_on_a_counter_or_a_literal():
    """`27 tâche(s)` ne doit pas devenir `'27' tâche(s)`."""
    source = '''
def q(sql, args=()): return []
import sys
def cmd_x(a):
    rows = q("select uuid from TMTask")
    print(f"\\n{len(rows)} tâche(s)", file=sys.stderr)
    print("terminé")
'''
    assert Sweep(source).violations() == []


# ---------------------------------------------------------------------------
# La conversion elle-même
# ---------------------------------------------------------------------------
HOSTILE = [
    pytest.param("\x1b[2K\rtâche ajoutée : AUTRE", id="esc-erase-line"),
    pytest.param("Compte rendu\nrésumé", id="newline"),
    pytest.param("Titre\rfaux", id="carriage-return"),
    pytest.param("Titre‮gnitsil", id="bidi-override"),
    pytest.param("Titre suite", id="line-separator"),
    pytest.param("Titre​caché", id="zero-width-space"),
]

READABLE = [
    pytest.param("Rédiger la synthèse — été 2026", id="accents-tiret-cadratin"),
    pytest.param("Migration cmux → Ghostty", id="fleche"),
    pytest.param("Projet › Initiative", id="chevron"),
    pytest.param("Livrer 🚀 la v2", id="emoji"),
    pytest.param("27 tâche(s)", id="compteur"),
    pytest.param("A1B2C3D4E5F6G7H8", id="uuid"),
]


@pytest.mark.parametrize("hostile", HOSTILE)
def test_rendered_bounds_every_non_printable_form(thingskit, hostile):
    out = thingskit._rendered(hostile)
    assert out.isprintable(), f"rendu non imprimable : {out!r}"
    assert "\x1b" not in out and "\n" not in out and "\r" not in out


@pytest.mark.parametrize("benign", READABLE)
def test_rendered_keeps_a_printable_value_verbatim(thingskit, benign):
    """Contre-épreuve du sur-refus : la conversion ne coûte pas la lisibilité
    qui la ferait abandonner. Une valeur imprimable sort telle quelle — c'est
    ce qui permet de la poser sur une colonne alignée ou dans un pipe."""
    assert thingskit._rendered(benign) == benign


def test_rendered_bounds_the_class_not_a_list_of_characters():
    """La borne est une CLASSE — Cc, Cf, Zl, Zp, Cs, Co, Cn —, jamais une
    liste de caractères énumérés : une liste ne couvre que ce qu'on a pensé à
    y inscrire. Balayage du plan multilingue de base, pas d'un échantillon
    choisi. Les séparateurs d'espace (Zs) en sont SORTIS le 2026-08-26 ; c'est
    `test_the_refused_class_still_holds_every_dangerous_category` qui tient
    les deux directions.
    """
    escaped = 0
    for cp in range(0x0000, 0x10000):
        ch = chr(cp)
        cat = unicodedata.category(ch)
        if cat not in {"Cc", "Cf", "Zl", "Zp", "Co", "Cn"}:
            continue
        rendered = thingskit_module()._rendered(f"a{ch}b")
        assert ch not in rendered, f"U+{cp:04X} traverse : {rendered!r}"
        escaped += 1
    assert escaped > 1000, f"balayage trop étroit : {escaped} points de code"


def thingskit_module():
    import thingskit_cli
    return thingskit_cli


# ---------------------------------------------------------------------------
# Adversité de bout en bout — trois natures de sortie
#
# Le balayage ci-dessus est statique : il prouve qu'aucune valeur n'atteint la
# sortie sans conversion, jamais que la sortie reste lisible. Ces épreuves-ci
# font traverser une VRAIE valeur hostile à trois commandes de nature
# différente — une liste, une confirmation, un échec — et leur jumelle bénigne
# vérifie qu'on n'a pas payé la borne en illisibilité. Refuser une classe qui
# passe est un défaut au même titre que laisser passer une classe qui casse.
# ---------------------------------------------------------------------------
SPOOF = "\x1b[2K\rarea créée : AUTRE"
BIDI = "Titre‮gnitsil"

SCHEMA = """
CREATE TABLE TMArea (uuid TEXT PRIMARY KEY, title TEXT);
CREATE TABLE TMTask (
    uuid TEXT PRIMARY KEY, title TEXT, type INTEGER, trashed INTEGER,
    project TEXT, heading TEXT, area TEXT, status INTEGER, notes TEXT,
    start INTEGER, startDate INTEGER, reminderTime INTEGER, deadline INTEGER
);
"""


@pytest.fixture
def db(thingskit, monkeypatch, tmp_path):
    """Base jetable — jamais celle de Things."""
    def _build(areas=(), projects=()):
        path = tmp_path / "main.sqlite"
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        for uuid, title in areas:
            con.execute("insert into TMArea (uuid, title) values (?,?)", (uuid, title))
        for uuid, title, area in projects:
            con.execute(
                "insert into TMTask (uuid, title, type, trashed, area) "
                "values (?,?,?,0,?)", (uuid, title, thingskit.TYPE_PROJECT, area))
        con.commit()
        con.close()
        monkeypatch.setattr(thingskit, "db_path", lambda: path)
        return path
    return _build


def _lines(captured):
    return captured.out.splitlines()


# -- une LISTE : `projects` -------------------------------------------------
def test_a_listing_never_emits_a_control_sequence_from_a_stored_title(
        thingskit, db, capsys):
    db(areas=[("A" * 22, "Pro")], projects=[("P" * 22, SPOOF, "A" * 22)])

    rc = thingskit.cmd_projects(argparse.Namespace(area=None, json=False))

    out = capsys.readouterr().out
    assert rc == 0
    assert "\x1b" not in out and "\r" not in out, repr(out)
    assert len(out.splitlines()) == 1, "la ligne a été effacée ou dédoublée"


def test_a_listing_stays_readable_and_aligned_on_ordinary_titles(
        thingskit, db, capsys):
    """Contre-épreuve du sur-refus, sur la commande de LECTURE — celle qu'on
    lance le plus. Accents, tiret cadratin et flèche traversent intacts, et la
    colonne reste alignée : la borne ne coûte pas la mise en page."""
    db(areas=[("A" * 22, "Chantiers")],
       projects=[("P" * 22, "Migration cmux → Ghostty", "A" * 22),
                 ("Q" * 22, "Rédiger la synthèse — été 2026", "A" * 22)])

    thingskit.cmd_projects(argparse.Namespace(area=None, json=False))

    lines = _lines(capsys.readouterr())
    assert "Migration cmux → Ghostty" in lines[0], lines
    assert "Rédiger la synthèse — été 2026" in lines[1], lines
    assert "'" not in lines[0], f"citation inutile sur une colonne : {lines[0]!r}"
    starts = {line.index("Chantiers") for line in lines}
    assert len(starts) == 1, f"colonnes désalignées : {lines}"
    assert len({len(line.split("  ")[1]) for line in lines}) == 1


# -- une CONFIRMATION : `create-area` --------------------------------------
@pytest.fixture
def created(thingskit, monkeypatch):
    """`create-area` dont l'effet est constaté — sans toucher l'application."""
    def _rig(osa_result=(0, "")):
        monkeypatch.setattr(thingskit, "ensure_running", lambda: None)
        monkeypatch.setattr(thingskit, "osa", lambda script: osa_result)
        monkeypatch.setattr(thingskit, "wait_for_effect",
                            lambda probe, **kw: osa_result[0] == 0)
        monkeypatch.setattr(thingskit, "time",
                            type("T", (), {"sleep": staticmethod(lambda s: None)}))
    return _rig


def test_a_confirmation_never_emits_a_control_sequence_from_an_argument(
        thingskit, db, created, capsys):
    """Le vecteur exact du ticket : un nom qui efface la ligne fait LIRE une
    confirmation qu'aucune partie du programme n'a écrite, avec un code retour
    0 parfaitement légitime."""
    db()
    created()

    rc = thingskit.cmd_create_area(argparse.Namespace(name=SPOOF))

    out = capsys.readouterr().out
    assert rc == 0
    assert "\x1b" not in out and "\r" not in out, repr(out)
    assert len(out.splitlines()) == 1, f"ligne dédoublée : {out!r}"
    # Le nom hostile ressort ÉCHAPPÉ et cité : `\x1b` y est quatre caractères
    # de texte, plus une séquence. Ce que la citation ne ferme pas, et qu'il
    # faut dire : la charge contient les mots « area créée », qui restent
    # lisibles dans les guillemets. Le quoting montre où la valeur commence,
    # il n'empêche pas une valeur d'IMITER un message — résidu déjà nommé.
    assert "\\x1b" in out, out


def test_a_confirmation_never_reverses_the_reading_order(
        thingskit, db, created, capsys):
    """U+202E n'émet aucun caractère de contrôle au sens C0 et inverse
    pourtant le sens de lecture de tout ce qui suit — d'où une classe, et non
    une liste de caractères."""
    db()
    created()
    thingskit.cmd_create_area(argparse.Namespace(name=BIDI))
    assert "‮" not in capsys.readouterr().out


def test_a_confirmation_stays_readable_on_an_ordinary_name(
        thingskit, db, created, capsys):
    db()
    created()
    thingskit.cmd_create_area(argparse.Namespace(name="Chantiers — été 2026"))
    assert "area créée : 'Chantiers — été 2026'" in capsys.readouterr().out


# -- un ÉCHEC : la sortie d'`osascript` ------------------------------------
def test_a_failure_never_emits_a_control_sequence_from_the_osascript_output(
        thingskit, db, created, capsys):
    """L'origine que le balayage à 37 sites EXCLUAIT. `out` n'est ni un
    argument ni un champ de la base : c'est ce qu'un processus tiers a écrit
    sur sa sortie d'erreur, et le CLI ne le maîtrise pas davantage."""
    db()
    created(osa_result=(1, SPOOF))

    rc = thingskit.cmd_create_area(argparse.Namespace(name="Cible"))

    err = capsys.readouterr().err
    assert rc == 1
    assert "\x1b" not in err and "\r" not in err, repr(err)
    assert "ÉCHEC création area" in err


def test_a_failure_still_names_the_problem_it_observed(
        thingskit, db, created, capsys):
    """Contre-épreuve du sur-refus sur la branche d'échec : borner le rendu ne
    doit pas effacer le diagnostic — un message d'échec qui ne nomme plus
    l'écart constaté ne vaut pas mieux qu'un faux succès."""
    db()
    created(osa_result=(1, "Things got an error: AppleEvent timed out"))

    thingskit.cmd_create_area(argparse.Namespace(name="Cible"))

    err = capsys.readouterr().err
    assert "AppleEvent timed out" in err, err
    assert "'Cible'" in err, err


# ===========================================================================
# BUG-026 — rework du 2026-08-26
#
# Ce qui suit ferme cinq défauts que le lot initial laissait ouverts, et que
# deux revues ont établis. Chacun porte ici son épreuve ; les chiffres qu'ils
# corrigent portent, dans la constitution, la commande qui les produit.
# ===========================================================================

# ---------------------------------------------------------------------------
# 1. Le chemin argparse — la sortie que le balayage ne peut PAS voir
#
# `argparse.ArgumentParser.error` compose son message HORS du module : aucun
# balayage intra-module ne l'atteint, et la racine (`sys.argv`) n'existe même
# pas encore comme namespace au moment où la valeur sort. Enumération des
# gabarits de message d'argparse 3.12.9 qui portent une valeur d'argv :
#
#   `unrecognized arguments: %s`          BRUT et ATTEIGNABLE  <- le défaut
#   `ambiguous option: %(option)s …`      brut, mais la valeur doit être un
#                                          préfixe d'une option déclarée : elle
#                                          ne peut porter aucun caractère de la
#                                          classe refusée
#   `unexpected option string: %s`        idem, et marqué « shouldn't ever get
#                                          here » dans argparse
#   `ignored explicit argument %r`        converti par `%r`
#   `invalid %(type)s value: %(value)r`   converti par `%r`
#   `invalid choice: %(value)r …`         converti par `%r`
#   `the following arguments are required: %s`  noms de `dest`, texte du
#                                          programme
#
# Reproduit le 2026-08-26 : `thingskit projects "- x\x1b[2K\rtâche ajoutée…"`
# émettait ESC et CR bruts sur stderr, le terminal effaçait la ligne d'erreur
# et l'utilisateur lisait une confirmation de succès.
# ---------------------------------------------------------------------------
ARGV_SPOOF = "- revoir le budget\x1b[2K\rtâche ajoutée : 'Revoir le budget'"


def test_an_unrecognised_argument_never_reaches_stderr_raw(
        thingskit, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["thingskit", "projects", ARGV_SPOOF])

    with pytest.raises(SystemExit) as exit_info:
        thingskit.main()

    err = capsys.readouterr().err
    assert exit_info.value.code == 2
    assert "\x1b" not in err and "\r" not in err, repr(err)
    assert "\\x1b" in err, err
    assert "unrecognized arguments" in err, err


def test_an_argument_error_never_smuggles_a_line_break(
        thingskit, monkeypatch, capsys):
    """Un LF dans argv fabriquerait une ligne entière que le programme n'a pas
    écrite — le même dommage que l'effacement, par un autre caractère."""
    monkeypatch.setattr(sys, "argv",
                        ["thingskit", "projects", "x\ntâche ajoutée : 'X'"])

    with pytest.raises(SystemExit):
        thingskit.main()

    err = capsys.readouterr().err
    lignes = [l for l in err.splitlines() if l.startswith("thingskit:")]
    assert len(lignes) == 1, err
    assert "tâche ajoutée" in lignes[0], err


def test_the_bound_leaves_the_parser_own_help_untouched(
        thingskit, monkeypatch, capsys):
    """Contre-épreuve du sur-refus : l'aide et l'usage sont du texte du
    programme, imprimables. La borne ne doit rien y échapper."""
    monkeypatch.setattr(sys, "argv", ["thingskit", "--help"])

    with pytest.raises(SystemExit):
        thingskit.main()

    out = capsys.readouterr().out
    assert "add-task" in out and "usage: thingskit" in out
    assert "\\x" not in out and "\\n" not in out, out


def test_the_bound_escapes_in_place_instead_of_quoting_the_whole_message(
        thingskit):
    """`_bounded` n'est pas `repr` : il échappe caractère par caractère et
    laisse le reste du message lisible — un message d'erreur intégralement
    cité ne serait plus un message d'erreur."""
    assert thingskit._bounded("erreur : a\x1b[2Kb") == "erreur : a\\x1b[2Kb"
    assert thingskit._bounded("erreur : ordinaire") == "erreur : ordinaire"


# ---------------------------------------------------------------------------
# 2. `sys.argv` est une racine du prédicat (R6)
#
# Les racines R1-R5 supposent que la valeur existe déjà comme namespace
# argparse. Une lecture directe d'`sys.argv` la précède : elle DOIT être une
# racine, sans quoi le prédicat a un trou en amont de lui-même.
# ---------------------------------------------------------------------------
def test_sys_argv_is_a_root_of_the_predicate():
    source = '''
import sys
def cmd_x(a):
    print(f"reçu : {sys.argv[1]}")
'''
    assert Sweep(source).violations(), "`sys.argv` n'est pas une racine"


def test_sys_argv_stays_a_root_through_a_local_name():
    source = '''
import sys
def cmd_x(a):
    argv = sys.argv[1:]
    joint = " ".join(argv)
    print(f"reçu : {joint}")
'''
    assert Sweep(source).violations(), "le trajet depuis `sys.argv` est perdu"


def test_the_sweep_sees_sys_argv_reintroduced_into_the_real_script():
    """Manipulation RÉELLE : on injecte une lecture d'argv brute dans le
    script du dépôt, le balayage doit la refuser."""
    source = _script_source()
    needle = "def cmd_uuid(a) -> int:\n"
    assert needle in source, "site témoin déplacé — mettre l'épreuve à jour"
    mutated = source.replace(
        needle, needle + '    print(f"argv : {sys.argv[1]}")\n', 1)
    viol = Sweep(mutated).violations()
    assert any(fn == "cmd_uuid" for fn, _, _ in viol), _report(viol)


# ---------------------------------------------------------------------------
# 3. La classe refusée EXCLUT les séparateurs d'espace (Zs)
#
# Mesuré le 2026-08-26 sur la base Things réelle : 902 titres, 2 cités par la
# garde, et le seul caractère en cause est U+00A0 — la typographie française
# devant `?` et `!`. Zéro caractère de classe dangereuse. Les 17 `Zs` sont des
# espaces VISIBLES de largeur non nulle et n'exécutent rien ; U+200B (largeur
# nulle) est Cf, U+2028 est Zl, U+202E est Cf : tous restent refusés.
#
#   .venv/bin/python - <<'EOF'
#   import sqlite3, glob, unicodedata
#   p = glob.glob("/Users/donaldo/Library/Group Containers/*/ThingsData-*/"
#                 "Things Database.thingsdatabase/main.sqlite")[0]
#   con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
#   t  = [x for (x,) in con.execute("select title from TMTask "
#                                   "where trashed=0 and title is not null")]
#   t += [x for (x,) in con.execute("select title from TMArea "
#                                   "where title is not null")]
#   R = {"Cc","Cf","Zl","Zp","Cs","Co","Cn"}
#   print(len(t), sum(not x.isprintable() for x in t),
#         sum(any(unicodedata.category(c) in R for c in x) for x in t))
#   EOF
#   -> 902 2 0
# ---------------------------------------------------------------------------
ZS = [chr(cp) for cp in range(0x110000)
      if unicodedata.category(chr(cp)) == "Zs"]


def test_a_space_separator_is_no_longer_refused(thingskit):
    """Les 17 `Zs`, un par un. L'insécable est la seule forme réellement
    rencontrée ; les seize autres relèvent de la même classe et ne peuvent
    pas être refusées sans que le motif écrit devienne faux."""
    assert len(ZS) == 17, ZS
    for ch in ZS:
        titre = f"Revoir le budget{ch}?"
        assert thingskit._rendered(titre) == titre, (
            f"U+{ord(ch):04X} ({unicodedata.name(ch)}) cité à tort")


def test_the_refused_class_still_holds_every_dangerous_category(thingskit):
    """Contre-épreuve, dans l'autre direction : tout ce qui relève de Cc, Cf,
    Zl, Zp, Co ou Cn reste refusé. Balayage du plan multilingue de base, pas
    d'un échantillon choisi — une liste ne couvre que ce qu'on y inscrit."""
    dangereux = {"Cc", "Cf", "Zl", "Zp", "Co", "Cn"}
    refuses = 0
    for cp in range(0x0000, 0x10000):
        ch = chr(cp)
        cat = unicodedata.category(ch)
        if cat == "Cs":                      # surrogate : pas de str légal
            continue
        rendu = thingskit._rendered(f"a{ch}b")
        if cat in dangereux:
            assert rendu != f"a{ch}b", f"U+{cp:04X} ({cat}) traverse : {rendu!r}"
            refuses += 1
        else:
            assert rendu == f"a{ch}b", f"U+{cp:04X} ({cat}) cité à tort : {rendu!r}"
    assert refuses > 1000, f"balayage trop étroit : {refuses} points de code"


def test_the_refused_class_is_named_by_category_not_by_isprintable(thingskit):
    """`str.isprintable()` ne sait pas exprimer cette classe : il refuse Zs.
    Le prédicat doit donc être explicite — épinglé ici pour qu'un retour à
    `isprintable()` échoue."""
    assert thingskit._REFUSED_CATEGORIES == frozenset(
        {"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"})
    insecable = "a b"
    assert not insecable.isprintable()
    assert thingskit._rendered(insecable) == insecable


# ---------------------------------------------------------------------------
# 4. Le chiffre du balayage se rejoue — sur l'ÉTAT D'AVANT
#
# La constitution annonçait « 87 valeurs dans 32 fonctions » avec une commande
# qui lit le fichier CORRIGÉ, laquelle rend donc 0. Le chiffre juste est celui
# du balayage appliqué à `bin/thingskit` AVANT le correctif — et il vaut 90/34.
# Cette épreuve rend le chiffre exécutable au lieu de le laisser attesté.
# ---------------------------------------------------------------------------
BEFORE_FIX = "cc4ff9d"          # parent du correctif BUG-026


def test_the_figure_of_the_sweep_is_the_one_written_in_the_constitution():
    import subprocess
    got = subprocess.run(["git", "show", f"{BEFORE_FIX}:bin/thingskit"],
                         cwd=REPO_ROOT, capture_output=True, text=True)
    if got.returncode != 0:
        pytest.skip(f"objet git {BEFORE_FIX} indisponible dans ce checkout")
    viol = Sweep(got.stdout).violations()
    assert (len(viol), len({fn for fn, _, _ in viol})) == (90, 34), (
        f"{len(viol)} valeurs / {len({fn for fn, _, _ in viol})} fonctions")


# ---------------------------------------------------------------------------
# 5. La garde de PORTÉE et de PUITS
#
# La garde de composition (ci-dessus) interdit les formes que le balayage ne
# suit pas quand il COMPOSE du texte. Il n'en existait aucune sur l'autre axe :
# les portées que le balayage ne modélise pas (module, classe, variadique) et
# les puits qu'il ne connaît pas (`sys.exit`, `writelines`, `os.write`, un
# alias de `print`, un `subprocess` à stdio hérité). Sur cet axe, une valeur
# non contrôlée sort sans qu'aucune épreuve ne s'en aperçoive.
#
# Les quinze détecteurs ci-dessous couvrent les quatorze formes établies par la
# revue du 2026-08-26 (les deux formes « conteneur ou global de module » et
# « alias de print ou de sys.std* » sont chacune scindées en deux détecteurs,
# et `sys.exit` est séparé de `raise SystemExit`). Toutes sont à ZÉRO
# occurrence : la garde est gratuite maintenant, et impossible à poser plus
# tard.
# ---------------------------------------------------------------------------
MUTATING_METHODS = {"append", "add", "extend", "update", "insert", "setdefault"}


def _module_level_names(tree) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _is_stream_attr(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr in ("stdout", "stderr")
            and isinstance(node.value, ast.Name) and node.value.id == "sys")


def _is_sink_call(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name) and f.id == "print":
        return True
    return isinstance(f, ast.Attribute) and f.attr == "write" and _is_stream_attr(f.value)


def _emits(fn) -> bool:
    return any(_is_sink_call(n) for n in ast.walk(fn))


def _is_literal_exit_arg(arg, module_names) -> bool:
    """Un code de sortie, pas un message composé."""
    if arg is None or isinstance(arg, ast.Constant):
        return True
    if isinstance(arg, ast.Name):
        return arg.id in module_names
    if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
        return arg.func.id in module_names
    return False


def scope_and_sink_findings(source: str) -> list[tuple[int, str]]:
    """Formes que le balayage ne voit pas — par la PORTÉE ou par le PUITS."""
    tree = ast.parse(source)
    module_names = _module_level_names(tree)
    found: list[tuple[int, str]] = []
    top_funcs = [n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    for fn in top_funcs:
        for node in ast.walk(fn):
            # F1 — un `global` fait sortir la valeur de la portée analysée.
            if isinstance(node, ast.Global):
                found.append((node.lineno, "global de module"))
            # F2 — un conteneur de module peuplé dans une fonction est lu
            #      dans une autre : le balayage est intra-fonction.
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in MUTATING_METHODS
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in module_names):
                found.append((node.lineno, "conteneur de module muté"))
        # F9/F10 — la teinte n'est propagée qu'aux paramètres NOMMÉS.
        if _emits(fn):
            if fn.args.vararg:
                found.append((fn.lineno, "paramètre variadique positionnel"))
            if fn.args.kwarg:
                found.append((fn.lineno, "paramètre variadique nommé"))
        # F8 — un défaut calculé n'est jamais évalué par le balayage.
        for d in list(fn.args.defaults) + [k for k in fn.args.kw_defaults if k]:
            if isinstance(d, (ast.Constant, ast.UnaryOp)):
                continue
            if isinstance(d, (ast.Tuple, ast.List, ast.Set)) and not d.elts:
                continue
            if isinstance(d, ast.Dict) and not d.keys:
                continue
            if isinstance(d, ast.Name) and d.id in module_names:
                continue
            found.append((fn.lineno, "défaut de paramètre calculé"))

    for node in ast.walk(tree):
        # F3 — le balayage ne collecte que les fonctions de MODULE.
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if _emits(sub) or any(isinstance(n, ast.JoinedStr)
                                      for n in ast.walk(sub)):
                    found.append((sub.lineno, "méthode de classe qui émet"))
        # F4/F5 — un alias rend le puits méconnaissable.
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Name) and value.id == "print":
                found.append((node.lineno, "alias de print"))
            if _is_stream_attr(value) or (isinstance(value, ast.Attribute)
                                          and value.attr == "write"
                                          and _is_stream_attr(value.value)):
                found.append((node.lineno, "alias de flux standard"))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name.split(".")[-1] == "print" and alias.asname:
                    found.append((node.lineno, "alias de print"))
        # F6/F7 — `sys.exit("…")` écrit sur stderr ; ce n'est pas un puits connu.
        if isinstance(node, ast.Call):
            f = node.func
            exit_call = ((isinstance(f, ast.Attribute) and f.attr == "exit"
                          and isinstance(f.value, ast.Name) and f.value.id == "sys"),
                         (isinstance(f, ast.Name) and f.id == "SystemExit"))
            arg = node.args[0] if node.args else None
            if exit_call[0] and not _is_literal_exit_arg(arg, module_names):
                found.append((node.lineno, "sys.exit à message composé"))
            if exit_call[1] and not _is_literal_exit_arg(arg, module_names):
                found.append((node.lineno, "SystemExit à message composé"))
            # F13/F14 — deux puits que `_sink_args` ne connaît pas.
            if isinstance(f, ast.Attribute) and f.attr == "writelines":
                found.append((node.lineno, "writelines"))
            if (isinstance(f, ast.Attribute) and f.attr == "write"
                    and isinstance(f.value, ast.Name) and f.value.id == "os"):
                found.append((node.lineno, "os.write"))
            # F11 — un walrus lie un nom que le balayage n'a pas collecté.
            if _is_sink_call(node):
                for a in node.args:
                    if any(isinstance(n, ast.NamedExpr) for n in ast.walk(a)):
                        found.append((node.lineno, "walrus en argument de puits"))
            # F15 — stdio HÉRITÉ : ce que le fils écrit sort sur notre terminal.
            if (isinstance(f, ast.Attribute)
                    and f.attr in ("run", "call", "check_call", "Popen")
                    and isinstance(f.value, ast.Name) and f.value.id == "subprocess"):
                kw = {k.arg for k in node.keywords}
                herite = not ({"capture_output", "stdout", "stderr"} & kw)
                argv = node.args[0] if node.args else None
                if herite and isinstance(argv, (ast.List, ast.Tuple)) and any(
                        isinstance(e, ast.JoinedStr) for e in argv.elts):
                    found.append((node.lineno,
                                  "subprocess à stdio hérité, argv interpolé"))
        # F12 — `except … as exc` puis `{exc}` : le nom n'est lié par aucune
        #       affectation, donc le balayage le tient pour non teinté.
        if isinstance(node, ast.ExceptHandler) and node.name:
            for sub in ast.walk(node):
                if not isinstance(sub, ast.FormattedValue):
                    continue
                if sub.conversion in CONVERSIONS or _is_numeric_spec(sub.format_spec):
                    continue
                inner = sub.value
                if isinstance(inner, ast.Name) and inner.id == node.name:
                    found.append((sub.lineno, "exception interpolée sans conversion"))
    return sorted(set(found))


def test_no_output_escapes_the_sweep_by_its_scope_or_by_its_sink():
    findings = scope_and_sink_findings(_script_source())
    assert findings == [], (
        "forme que le balayage ne voit pas (portée ou puits) : "
        + "\n".join(f"  {ln:5d}  {label}" for ln, label in findings))


SCOPE_AND_SINK_FORMS = {
    "global_de_module": '''
import sys
BUF = []
def cmd_x(a):
    global BUF
    BUF = [a.title]
def cmd_y(a):
    print(BUF[0])
''',
    "conteneur_de_module_mute": '''
BUF = []
def cmd_x(a):
    BUF.append(a.title)
def cmd_y(a):
    print(BUF[0])
''',
    "methode_de_classe_qui_emet": '''
class Reporter:
    def say(self, title):
        print(f"fait : {title}")
def cmd_x(a):
    Reporter().say(a.title)
''',
    "alias_de_print": '''
say = print
def cmd_x(a):
    say(a.title)
''',
    "alias_de_flux_standard": '''
import sys
out = sys.stdout
def cmd_x(a):
    out.write(a.title)
''',
    "sys_exit_a_message_compose": '''
import sys
def cmd_x(a):
    sys.exit(f"introuvable : {a.title}")
''',
    "SystemExit_a_message_compose": '''
def cmd_x(a):
    raise SystemExit(f"introuvable : {a.title}")
''',
    "defaut_de_parametre_calcule": '''
import sys
def _say(title=sys.argv[-1]):
    print(title)
''',
    "parametre_variadique_positionnel": '''
def _say(*parts):
    print(" ".join(parts))
def cmd_x(a):
    _say(a.title)
''',
    "parametre_variadique_nomme": '''
def _say(**fields):
    print(fields["title"])
def cmd_x(a):
    _say(title=a.title)
''',
    "walrus_en_argument_de_puits": '''
def q(sql, args=()): return []
def cmd_x(a):
    print(label := q("select title from TMTask")[0][0])
''',
    "exception_interpolee_sans_conversion": '''
def cmd_x(a):
    try:
        int(a.title)
    except ValueError as exc:
        print(f"non reconnu : {exc}")
''',
    "writelines": '''
import sys
def cmd_x(a):
    sys.stdout.writelines([a.title])
''',
    "os_write": '''
import os
def cmd_x(a):
    os.write(1, a.title.encode())
''',
    "subprocess_a_stdio_herite_argv_interpole": '''
import subprocess
def cmd_x(a):
    subprocess.run(["/usr/bin/open", f"things:///show?id={a.title}"], check=False)
''',
}


@pytest.mark.parametrize("form", sorted(SCOPE_AND_SINK_FORMS))
def test_the_scope_and_sink_guard_refuses_each_form(form):
    findings = scope_and_sink_findings(SCOPE_AND_SINK_FORMS[form])
    assert findings, f"forme `{form}` non refusée"


@pytest.mark.parametrize("form", sorted(SCOPE_AND_SINK_FORMS))
def test_the_sweep_alone_would_have_missed_each_form(form):
    """Le point de la garde : ces formes passent le balayage. Sans elle, le
    « compte résiduel nul » serait vrai ET la valeur sortirait quand même."""
    assert Sweep(SCOPE_AND_SINK_FORMS[form]).violations() == [], (
        f"forme `{form}` déjà vue par le balayage — la garde est inutile ici")


def test_the_scope_and_sink_guard_does_not_cry_wolf_on_ordinary_code():
    """Contre-épreuve du sur-refus : un code de sortie entier, un défaut
    constant, un `subprocess` dont l'argv ne porte aucune interpolation."""
    source = '''
import subprocess
import sys
CODE = 125
TIMEOUT = 5.0
def db_path():
    sys.exit("base introuvable")
def wait(timeout=TIMEOUT, poll=0.05, args=(), opts=None):
    return timeout
def run():
    subprocess.run(["/usr/bin/open", "-g", "-a", "Things3"], check=False)
    raise SystemExit(CODE)
'''
    assert scope_and_sink_findings(source) == []


# ---------------------------------------------------------------------------
# 6. La troncature s'applique à la VALEUR, jamais à son rendu
#
# `_rendered(v)[:60]` coupe la séquence d'échappement que le rendu vient de
# produire : `\\x1b` amputé en `\\x1` ou `\\`, c'est-à-dire un rendu qui ne
# décrit plus la valeur — et, sur `!r`, un guillemet fermant perdu.
# ---------------------------------------------------------------------------
def test_no_truncation_applies_to_the_result_of_a_conversion():
    tree = ast.parse(_script_source())
    coupes = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)):
            continue
        haut = node.slice.upper
        # `repr(x)[1:-1]` retire les guillemets — il ne TRONQUE pas. Ce qui
        # ampute un rendu, c'est une borne haute à un rang positif.
        if not (isinstance(haut, ast.Constant) and isinstance(haut.value, int)
                and haut.value >= 0):
            continue
        inner = node.value
        if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                and inner.func.id in CONVERTING_CALLS):
            coupes.append((node.lineno, ast.get_source_segment(_script_source(), node)))
    assert coupes == [], f"troncature appliquée à un rendu : {coupes}"


def test_a_truncated_cell_never_ends_inside_an_escape(thingskit, db, capsys):
    """Épreuve de bout en bout sur `agenda` : un titre long portant un ESC au
    rang de coupe doit sortir tronqué ET complet — jamais `\\x1` ni `\\`."""
    long_hostile = "a" * 58 + "\x1b" + "b" * 40
    rendu = thingskit._rendered(long_hostile[:60])
    assert rendu.endswith("'"), rendu
    assert "\\x1b" in rendu, rendu
    tronque_apres = thingskit._rendered(long_hostile)[:60]
    assert not tronque_apres.endswith("'"), (
        "l'épreuve ne distingue plus les deux ordres")


# ---------------------------------------------------------------------------
# 7. Le partage écrit est tenu : `!r` en prose, `_rendered` en position
# ---------------------------------------------------------------------------
def test_no_prose_message_uses_the_conditional_rendering():
    """`_rendered` en prose contredit le partage que le lot vient d'écrire, et
    `_rendered(x)!r` cumule deux conversions. Sites relevés le 2026-08-26 :
    lignes 882, 890 et 1045."""
    source = _script_source()
    tree = ast.parse(source)
    fautes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FormattedValue):
            continue
        inner = node.value
        if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                and inner.func.id == "_rendered" and node.conversion in CONVERSIONS):
            fautes.append((node.lineno, "conversion cumulée `_rendered(...)!r`"))
    assert fautes == [], fautes


def test_the_seventh_root_covers_the_three_reading_forms():
    """R7 ne lisait que `<x>.read()` : `read_text()` et `readlines()`
    passaient. La racine porte sur la LECTURE, pas sur une de ses orthographes
    — c'est la meme discipline que le balayage des formes de `sleep`, qui ne
    voyait qu'une des trois manieres d'atteindre `time.sleep`.
    """
    for form in ("handle.read()", "path.read_text()", "handle.readlines()"):
        source = (
            "def q(sql, args=()): return []\n"
            "def cmd_x(a):\n"
            f"    text = {form}\n"
            '    print(f"lu : {text}")\n'
        )
        assert Sweep(source).violations(), form


def test_a_call_that_merely_looks_like_a_read_is_not_a_root():
    """Contre-epreuve : la racine ne doit pas teindre n'importe quel appel."""
    source = (
        "def q(sql, args=()): return []\n"
        "def cmd_x(a):\n"
        "    text = handle.ready()\n"
        '    print(f"lu : {text}")\n'
    )
    assert Sweep(source).violations() == []
