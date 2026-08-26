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
      et suivre la valeur sans suivre son rang confond les deux.

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
à tort dans ce dépôt.
  - Les indirections qu'une analyse statique ne suit pas : `%`, `.format()`,
    `string.Template`, `.replace()` sur un gabarit, `io.StringIO`. Elles ne
    sont pas suivies — elles sont INTERDITES, par
    `test_no_output_is_composed_by_a_form_the_sweep_cannot_follow`.
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
  - `json.dumps` compte comme conversion : elle échappe la classe Cc, dont ESC,
    CR et LF — la seule dont le dommage soit mesuré. Elle n'échappe PAS Cf sous
    `ensure_ascii=False` : un U+202E traverse une sortie `--json`. Résidu
    nommé, non fermé par ce lot.
  - `!r` et `_rendered` bornent la classe de `str.isprintable()`. Ce qui est
    imprimable ET trompeur — homoglyphe, espace cadratin, titre imitant mot
    pour mot un message du programme — leur échappe. Le quoting en limite la
    portée, il ne la ferme pas.
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
            return rec(expr.value)
        if isinstance(expr, ast.Call):
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
    """La borne est celle de `str.isprintable()` — Cc, Cf, Zl, Zp, Zs hors
    espace, Cs, Co, Cn — jamais une liste de caractères énumérés : une liste
    ne couvre que ce qu'on a pensé à y inscrire. Balayage du plan multilingue
    de base, pas d'un échantillon choisi."""
    escaped = 0
    for cp in range(0x0000, 0x10000):
        ch = chr(cp)
        if ch.isprintable():
            continue
        if unicodedata.category(ch) == "Cs":     # surrogate : pas de str légal
            continue
        rendered = thingskit_module()._rendered(f"a{ch}b")
        assert rendered.isprintable(), f"U+{cp:04X} traverse : {rendered!r}"
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
