"""Zone sensible n° 3 — `-I` ferme les variables `PYTHON*`, jamais `PATH`.

Le lanceur isole l'interpreteur de son environnement Python, et verifie le
sceau par un chemin absolu. Mais le processus qui porte le grant TCC continue
d'heriter du `PATH` de son appelant : toute commande invoquee par NOM NU y est
resolue, donc detournable par un homonyme depose dans un repertoire du `PATH`.

C'est la meme classe de defaut que `PYTHONPATH` et que `codesign` resolu par
`PATH`, avec une consequence plus lourde : ce n'est pas une garde qu'on
neutralise, c'est du code arbitraire qu'on execute sous l'identite de code
porteuse de `kTCCServiceSystemPolicyAppData`.

Reproduit le 2026-08-18 : un stub `osascript` depose dans un repertoire du
`PATH` s'executait, sceau valide et `-I` pose.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "bin" / "thingskit"

_SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}
# `os.system`/`os.popen` passent par un shell, et les variantes `*p*`
# (`execvp`, `spawnlp`...) resolvent explicitement par `PATH` : dans les deux
# cas argv[0] est detournable par construction, quel que soit son contenu.
_OS_SHELL_CALLS = {"system", "popen"}


def _is_subprocess_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _SUBPROCESS_CALLS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    )


def _os_call_name(node: ast.AST) -> str | None:
    """Rend le nom de la fonction si `node` est un `os.<exec>` d'un executable."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
    ):
        return None
    name = node.func.attr
    if name in _OS_SHELL_CALLS or name.startswith(("exec", "spawn", "posix_spawn")):
        return name
    return None


def _leftmost_list(node: ast.AST) -> ast.AST | None:
    """Rend le premier element de `[...]`, `[...] + x`, `[...] + x + y`."""
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        node = node.left
    if isinstance(node, ast.List) and node.elts:
        return node.elts[0]
    return None


def _assignments_to(name: str, scope: ast.AST) -> list[ast.Assign]:
    return [
        n
        for n in ast.walk(scope)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
    ]


def _argv0_candidates(arg: ast.AST, scope: ast.AST, tree: ast.AST):
    """Rend TOUS les noeuds `argv[0]` possibles, ou `None` si non analysable.

    Toutes les affectations d'un nom sont suivies, pas la premiere trouvee :
    une reaffectation conditionnelle rendait la premiere valeur (absolue) et
    masquait la seconde (nue).
    """
    found = _leftmost_list(arg)
    if found is not None:
        return [found]
    if isinstance(arg, ast.Name):
        assigns = _assignments_to(arg.id, scope) or _assignments_to(arg.id, tree)
        if not assigns:
            return None
        out = []
        for assign in assigns:
            nested = _leftmost_list(assign.value)
            if nested is None:
                return None
            out.append(nested)
        return out
    return None


def _module_constant(name: str, tree: ast.AST):
    """Rend la valeur d'une constante de module, ou `None` si non litterale."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                return node.value.value
    return None


def _judge_argv0(argv0: ast.AST, tree: ast.AST) -> str | None:
    """Rend la raison du rejet, ou `None` si l'invocation est absolue."""
    if isinstance(argv0, ast.Constant) and isinstance(argv0.value, str):
        return None if argv0.value.startswith("/") else repr(argv0.value)
    if isinstance(argv0, ast.Name):
        value = _module_constant(argv0.id, tree)
        if isinstance(value, str) and value.startswith("/"):
            return None
        return f"{argv0.id} = {value!r}"
    return f"argv[0] non analysable ({type(argv0).__name__})"


def _bare_invocations(path=None) -> list[str]:
    """Recense les invocations d'executable non prouvees absolues.

    Ce que le balayage ne sait pas lire est un OFFENDER, jamais un silence :
    « je ne sais pas resoudre argv[0] » n'est pas « argv[0] est absolu ». Sans
    cette regle, la garde exigeait un compte residuel nul sur les seules deux
    formes qu'elle savait lire (sonde du 2026-08-18 : 2 detectees, 8 manquees).
    """
    tree = ast.parse(Path(path or SCRIPT_PATH).read_text(encoding="utf-8"))
    scopes = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ] + [tree]
    offenders: list[str] = []
    seen: set[int] = set()
    for scope in scopes:
        for node in ast.walk(scope):
            if id(node) in seen:
                continue
            os_name = _os_call_name(node)
            if os_name is not None:
                seen.add(id(node))
                if os_name in _OS_SHELL_CALLS or "p" in os_name.split("exec")[-1].split("spawn")[-1]:
                    offenders.append(
                        f"ligne {node.lineno} : os.{os_name} resout par shell ou PATH"
                    )
                    continue
                argv0 = node.args[0] if node.args else None
                reason = (
                    "argv[0] absent"
                    if argv0 is None
                    else _judge_argv0(argv0, tree)
                )
                if reason:
                    offenders.append(f"ligne {node.lineno} : {reason}")
                continue
            if not _is_subprocess_call(node):
                continue
            seen.add(id(node))
            if any(
                kw.arg == "shell"
                and not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
                for kw in node.keywords
            ):
                offenders.append(
                    f"ligne {node.lineno} : shell=True — argv[0] resolu par PATH"
                )
                continue
            arg = node.args[0] if node.args else None
            if arg is None:
                for kw in node.keywords:
                    if kw.arg == "args":
                        arg = kw.value
            if arg is None:
                offenders.append(f"ligne {node.lineno} : argv absent")
                continue
            candidates = _argv0_candidates(arg, scope, tree)
            if candidates is None:
                offenders.append(
                    f"ligne {node.lineno} : argv[0] non analysable "
                    f"({ast.unparse(arg)})"
                )
                continue
            for argv0 in candidates:
                reason = _judge_argv0(argv0, tree)
                if reason:
                    offenders.append(f"ligne {node.lineno} : {reason}")
    return sorted(set(offenders))


def test_no_executable_is_invoked_by_bare_name():
    """AC-1 : compte residuel d'invocations par nom nu = 0.

    Garde de regression : ce test echoue si un chemin absolu redevient nu.
    """
    assert _bare_invocations() == []


def test_the_sweep_actually_sees_a_bare_invocation(tmp_path):
    """La garde ci-dessus ne vaut que si elle DETECTE le defaut qu'elle interdit."""
    fake = tmp_path / "thingskit"
    fake.write_text(
        "import subprocess\n"
        "def f():\n"
        "    subprocess.run(['osascript', '-e', 'x'])\n",
        encoding="utf-8",
    )
    global SCRIPT_PATH
    old, SCRIPT_PATH = SCRIPT_PATH, fake
    try:
        assert _bare_invocations() == ["ligne 3 : 'osascript'"]
    finally:
        SCRIPT_PATH = old


@pytest.mark.parametrize(
    "name,expected",
    [("OSASCRIPT", "/usr/bin/osascript"), ("OPEN", "/usr/bin/open"), ("PGREP", "/usr/bin/pgrep")],
)
def test_the_absolute_paths_exist_on_this_machine(thingskit, name, expected):
    """AC-4 : un chemin absolu inexistant remplacerait un detournement par une panne."""
    value = getattr(thingskit, name)
    assert value == expected
    assert os.path.isfile(value) and os.access(value, os.X_OK), value


def test_osa_ignores_a_homonym_stub_placed_in_path(tmp_path, thingskit):
    """Adversite : l'exploit reproduit — un faux `osascript` en tete de `PATH`.

    Le stub laisse une trace sur disque ; son absence prouve qu'il n'a pas
    tourne. On eprouve l'appel REEL, pas une chaine de caracteres.
    """
    marker = tmp_path / "PWNED"
    stub = tmp_path / "osascript"
    stub.write_text(f'#!/bin/sh\ntouch "{marker}"\necho PWNED\n', encoding="utf-8")
    stub.chmod(0o755)
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{tmp_path}:{old}"
    try:
        rc, out = thingskit.osa('return "pong"')
    finally:
        os.environ["PATH"] = old
    assert not marker.exists(), "le stub de PATH a ete execute"
    assert "PWNED" not in out
    assert (rc, out) == (0, "pong")


def test_url_open_and_ensure_running_use_absolute_paths(monkeypatch, thingskit):
    """Les autres invocations traversent le meme `PATH` : meme exigence."""
    calls: list[list[str]] = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        thingskit.subprocess, "run", lambda argv, **kw: (calls.append(argv), _R())[1]
    )
    thingskit.url_open([{"type": "to-do"}])
    thingskit.ensure_running()
    assert calls, "aucune invocation observee"
    for argv in calls:
        assert argv[0].startswith("/"), argv


# --------------------------------------------------- portee du balayage (passe 3)
#
# Sonde du 2026-08-18 : sur dix formes d'invocation, le balayage d'origine en
# detectait deux et en manquait huit — `argv0 is None` etait un `continue`,
# donc « je ne sais pas lire » se lisait « rien a signaler ». Le residu reel
# etait nul, mais la garde promettait plus qu'elle ne tenait.

_FORMS = {
    "f-string": (
        "import subprocess\n"
        "def f(x):\n"
        "    subprocess.run([f'{x}osascript', '-e', 'y'])\n"
    ),
    "concatenation": (
        "import subprocess\n"
        "def f(p):\n"
        "    subprocess.run([p + 'osascript'])\n"
    ),
    "shell=True": (
        "import subprocess\n"
        "def f():\n"
        "    subprocess.run('/usr/bin/osascript -e y', shell=True)\n"
    ),
    "kwarg args=": (
        "import subprocess\n"
        "def f():\n"
        "    subprocess.run(args=['osascript', '-e', 'y'])\n"
    ),
    "fonction helper": (
        "import subprocess\n"
        "def sh(cmd):\n"
        "    return subprocess.run(cmd)\n"
        "def f():\n"
        "    sh(['osascript', '-e', 'y'])\n"
    ),
    "os.system": (
        "import os\n"
        "def f():\n"
        "    os.system('osascript -e y')\n"
    ),
    "variable reaffectee": (
        "import subprocess\n"
        "def f(cond):\n"
        "    cmd = ['/usr/bin/osascript']\n"
        "    if cond:\n"
        "        cmd = ['osascript']\n"
        "    subprocess.run(cmd)\n"
    ),
    "list.append": (
        "import subprocess\n"
        "def f():\n"
        "    cmd = []\n"
        "    cmd.append('osascript')\n"
        "    subprocess.run(cmd)\n"
    ),
    "constante nue": (
        "import subprocess\n"
        "OSASCRIPT = 'osascript'\n"
        "def f():\n"
        "    subprocess.run([OSASCRIPT, '-e', 'y'])\n"
    ),
    "variable globale nue": (
        "import subprocess\n"
        "TOOL = 'osascript'\n"
        "def f():\n"
        "    argv = [TOOL]\n"
        "    subprocess.run(argv)\n"
    ),
}


@pytest.mark.parametrize("form", sorted(_FORMS))
def test_the_sweep_flags_every_form_it_cannot_vouch_for(tmp_path, form):
    """Ce que la garde ne sait pas lire est un offender, jamais un silence."""
    script = tmp_path / "thingskit"
    script.write_text(_FORMS[form], encoding="utf-8")
    offenders = _bare_invocations(script)
    assert offenders, f"forme non detectee : {form}"


def test_the_sweep_does_not_cry_wolf_on_an_absolute_invocation(tmp_path):
    """Une garde qui refuse tout ne distingue plus rien."""
    script = tmp_path / "thingskit"
    script.write_text(
        "import subprocess\n"
        "OSASCRIPT = '/usr/bin/osascript'\n"
        "def f(bg):\n"
        "    argv = [OSASCRIPT] + (['-g'] if bg else []) + ['x']\n"
        "    subprocess.run(argv, check=False)\n"
        "    subprocess.run(['/usr/bin/open', '-g'], check=False)\n",
        encoding="utf-8",
    )
    assert _bare_invocations(script) == []
