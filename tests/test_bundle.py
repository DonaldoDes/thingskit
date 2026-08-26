"""BNDL-01..05 — logique pure du build du bundle signé (spec specs/bundle-signe/spec.md).

Aucun test ici ne dépend de `/Applications/thingskit.app`, de Things, ni de la
vraie base (contrainte C-4 / INV-001-3). Le lanceur est éprouvé en l'exécutant
réellement, mais contre une cible jetable construite dans `tmp_path`.
"""

import ast
import hashlib
import os
import plistlib
import re
import subprocess
from pathlib import Path

import pytest

from build import bundle
from conftest import requires_conforming_bundle

# ---------------------------------------------------------------- BNDL-01


def test_info_plist_carries_the_fixed_bundle_identifier():
    parsed = plistlib.loads(bundle.info_plist_xml().encode("utf-8"))
    assert parsed["CFBundleIdentifier"] == "app.sowell.thingskit"
    assert parsed["CFBundleExecutable"] == "thingskit"


def test_info_plist_is_valid_plist_and_names_the_bundle():
    parsed = plistlib.loads(bundle.info_plist_xml().encode("utf-8"))
    assert parsed["CFBundleName"] == "thingskit"
    assert parsed["CFBundlePackageType"] == "APPL"


# ---------------------------------------------------------------- BNDL-02


def test_codesign_command_omits_entitlements_when_none_are_required():
    """NC-4 se tranche par la mesure : aucun entitlement n'est posé par défaut."""
    assert "--entitlements" not in bundle.codesign_command("/tmp/x.app", "DEADBEEF")


def test_codesign_command_passes_entitlements_only_when_given():
    cmd = bundle.codesign_command("/tmp/x.app", "DEADBEEF", entitlements="/tmp/ent.plist")
    assert cmd[cmd.index("--entitlements") + 1] == "/tmp/ent.plist"


# ---------------------------------------------------------------- BNDL-03/04


ARGV_CASES = [
    ["areas"],
    ["add-task", "--title", "deux mots"],
    ["set-notes", "--task", ""],
    ["x", "  ", "a\tb", "accentué é", "--flag=vaut quelque chose"],
    [],
]


def _fake_bundle(tmp_path, body, shim_commands=("areas",), codesign=None):
    """Faux `.app` dont l'executable est un script jetable, PLUS son shim.

    Depuis ADR-002, le lanceur `sh` n'execute plus l'interpreteur directement :
    il passe par le shim scelle, qui porte le controle de sceau, `-I` et la
    decision de responsabilite. Le montage de test suit la vraie chaine, sans
    quoi il eprouverait un lanceur qui n'existe plus.
    """
    app = tmp_path / "thingskit.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir(parents=True)
    (app / "Contents" / "Resources" / "thingskit").write_text("# copie\n")
    exe = app / "Contents" / "MacOS" / "thingskit"
    exe.write_text(body)
    exe.chmod(0o755)
    bundle.compile_shim(
        bundle.shim_source(
            shim_commands,
            app_path=str(app),
            codesign=str(codesign or _stub_codesign(tmp_path)),
        ),
        app / "Contents" / "MacOS" / bundle.SHIM_NAME,
    )
    return app


def _stub_codesign(tmp_path, rc=0, name="codesign-stub"):
    """Faux verificateur de sceau : la vraie signature exige un certificat.

    Le chemin du verificateur est un parametre de la GENERATION du shim, pas
    une variable d'environnement : le shim de production cable
    `/usr/bin/codesign` en dur (cf. `test_the_shim_verifies_the_seal_through_an
    _absolute_path`). Le seam est donc a la generation, jamais a l'execution.
    """
    stub = tmp_path / name
    stub.write_text(f"#!/bin/sh\nexit {rc}\n")
    stub.chmod(0o755)
    return stub


def _write_launcher(tmp_path, app):
    launcher = tmp_path / "launcher"
    launcher.write_text(bundle.launcher_script(str(app)))
    launcher.chmod(0o755)
    return launcher


def test_launcher_never_uses_open(tmp_path):
    """`open -a` ne transmet ni la sortie ni le code de sortie (ADR-001 § 2)."""
    script = bundle.launcher_script("/Applications/thingskit.app")
    assert "open -a" not in script
    assert "open " not in script


def test_the_whole_launch_chain_is_made_of_execs(tmp_path):
    """BNDL-04 — mesure du PID de bout en bout : `sh` -> shim -> interpreteur.

    C'est la mesure qui repond d'INV-002-4, pas la lecture du mot-cle `exec` ni
    celle de `POSIX_SPAWN_SETEXEC` : les deux maillons pourraient etre corrects
    separement et un processus intermediaire apparaitre entre eux.
    """
    app = _fake_bundle(tmp_path, '#!/bin/sh\nprintf "%s" "$$"\n')
    launcher = _write_launcher(tmp_path, app)
    proc = subprocess.Popen([str(launcher), "areas"], stdout=subprocess.PIPE)
    reported = int(proc.communicate()[0])
    assert reported == proc.pid


@pytest.mark.parametrize("argv", ARGV_CASES, ids=lambda a: repr(a))
def test_the_launch_chain_transmits_argv_without_alteration(tmp_path, argv):
    # L'executable jetable rejette ses arguments separes par NUL : aucun
    # regroupement ni decoupage ne peut passer inapercu. `shift 2` ecarte les
    # deux arguments poses par le shim lui-meme : `-I` et le script embarque.
    app = _fake_bundle(
        tmp_path,
        '#!/bin/sh\nshift 2\nfor a in "$@"; do printf "%s\\0" "$a"; done\n',
    )
    launcher = _write_launcher(tmp_path, app)
    out = subprocess.run(
        [str(launcher), *argv], capture_output=True, check=True
    ).stdout
    received = out.split(b"\0")[:-1] if out else []
    assert received == [a.encode() for a in argv]


@pytest.mark.parametrize("code", [0, 1, 3, 42])
def test_the_launch_chain_preserves_the_exit_code(tmp_path, code):
    app = _fake_bundle(tmp_path, f"#!/bin/sh\nexit {code}\n")
    launcher = _write_launcher(tmp_path, app)
    assert subprocess.run([str(launcher), "areas"]).returncode == code


# ------------------------------------------- isolation de l'interpreteur


def test_the_launch_chain_neutralises_pythonpath_injection(tmp_path):
    """Adversite : un `sitecustomize.py` pose dans PYTHONPATH ne doit rien executer.

    Le porteur de `-I` a change (ADR-002 : `sh` -> shim), l'invariant non. Le
    test eprouve la semantique d'un VRAI interpreteur, pas une chaine.
    """
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "sitecustomize.py").write_text(
        "import sys; sys.stderr.write('INJECTED')\n"
    )
    app = _fake_bundle(tmp_path, '#!/bin/sh\nexec /usr/bin/env python3 "$@"\n')
    (app / "Contents" / "Resources" / "thingskit").write_text("print('ok')\n")
    launcher = _write_launcher(tmp_path, app)
    env = {**os.environ, "PYTHONPATH": str(evil)}
    proc = subprocess.run(
        [str(launcher), "areas"], capture_output=True, env=env, text=True
    )
    assert "INJECTED" not in proc.stderr, proc.stderr
    assert proc.stdout.strip() == "ok"
    assert proc.returncode == 0


# ------------------------------------------- validation du sceau au lancement


def test_the_launch_chain_refuses_a_bundle_whose_seal_is_invalid(tmp_path):
    """`Contents/Resources/thingskit` appartient a l'utilisateur : modifiable
    sans elevation, et le code modifie tournerait avec le grant TCC. Le sceau
    doit etre evalue AU LANCEMENT, pas constate a posteriori."""
    ran = tmp_path / "ran"
    app = _fake_bundle(
        tmp_path, f'#!/bin/sh\ntouch "{ran}"\n',
        codesign=_stub_codesign(tmp_path, rc=1, name="cs-ko"),
    )
    launcher = _write_launcher(tmp_path, app)
    proc = subprocess.run([str(launcher), "areas"], capture_output=True, text=True)
    assert proc.returncode == bundle.SEAL_REFUSAL_CODE
    assert "sceau" in proc.stderr.lower()
    assert not ran.exists(), "l'executable a tourne malgre un sceau invalide"


def test_the_refusal_survives_the_change_of_porter(tmp_path):
    """ADR-002 § Consequences : le message change de porteur (`sh` -> Mach-O) et
    doit rester au moins aussi nomme, sans quoi le diagnostic d'un bundle
    altere regresse."""
    app = _fake_bundle(
        tmp_path, "#!/bin/sh\nexit 0\n",
        codesign=_stub_codesign(tmp_path, rc=1, name="cs-ko"),
    )
    launcher = _write_launcher(tmp_path, app)
    proc = subprocess.run([str(launcher), "areas"], capture_output=True, text=True)
    assert proc.stderr.strip(), "refus silencieux"
    assert "invalide" in proc.stderr.lower()
    assert str(app) in proc.stderr
    assert proc.returncode not in (0, 1, 2)


# ---------------------------------------------------------------- BNDL-05


def test_build_refuses_when_the_embedded_copy_diverges_from_the_source(tmp_path):
    src = tmp_path / "thingskit"
    src.write_bytes(b"#!/usr/bin/env python3\nprint(1)\n")
    copy = tmp_path / "Resources_thingskit"
    copy.write_bytes(b"#!/usr/bin/env python3\nprint(2)\n")
    with pytest.raises(bundle.BundleError):
        bundle.verify_embedded_copy(src, copy)


def test_build_accepts_a_byte_for_byte_copy(tmp_path):
    src = tmp_path / "thingskit"
    src.write_bytes(b"\xc3\xa9\x00binaire\n")
    copy = tmp_path / "copy"
    copy.write_bytes(src.read_bytes())
    bundle.verify_embedded_copy(src, copy)  # ne lève pas


def test_build_refuses_when_the_embedded_copy_is_missing(tmp_path):
    src = tmp_path / "thingskit"
    src.write_bytes(b"x")
    with pytest.raises(bundle.BundleError):
        bundle.verify_embedded_copy(src, tmp_path / "absent")


# ---------------------------------------------------------------- C-4


# Ce qui, dans un test, fait REELLEMENT dependre la suite d'un bundle installe :
# atteindre le systeme de fichiers a ce chemin. Nommer le chemin pour en
# engendrer une chaine (`launcher_script`, `shim_source`) n'en depend pas — la
# fonction rend du texte, sur un poste nu comme sur un poste equipe.
INSTALL_PATH = bundle.INSTALL_PATH
INSTALL_PATH_NAME = "INSTALL_PATH"  # la meme cible, nommee par la constante
CANONICAL_PREDICATE = "conforming_bundle_missing"
CANONICAL_MARKER = "requires_conforming_bundle"

_FILESYSTEM_REACH = frozenset({
    "copytree", "copy", "copy2", "isdir", "isfile", "exists", "listdir",
    "open", "Path", "rmtree", "stat", "walk", "glob", "run", "Popen",
})


def _installed_bundle_reaches(source: str) -> list[str]:
    """Occurrences du bundle installe qui atteignent le systeme de fichiers
    sans que le saut soit decide par le predicat canonique."""
    import ast as _ast

    tree = _ast.parse(source)
    exempt = set()
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            # (a) le predicat canonique lui-meme : il DOIT atteindre le disque,
            #     c'est sa raison d'etre. Exemption structurelle — attachee au
            #     mecanisme, pas a un nom de fichier.
            # (b) tout test dont le saut est decide par ce predicat.
            if node.name == CANONICAL_PREDICATE or any(
                CANONICAL_MARKER in _ast.unparse(dec) for dec in node.decorator_list
            ):
                exempt.update(id(sub) for sub in _ast.walk(node))
    found = []
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call) or id(node) in exempt:
            continue
        callee = node.func
        callee_name = (
            callee.attr if isinstance(callee, _ast.Attribute)
            else callee.id if isinstance(callee, _ast.Name)
            else None
        )
        if callee_name not in _FILESYSTEM_REACH:
            continue
        for sub in _ast.walk(node):
            names_the_bundle = (
                isinstance(sub, _ast.Constant)
                and isinstance(sub.value, str)
                and INSTALL_PATH in sub.value
            ) or (
                isinstance(sub, _ast.Attribute) and sub.attr == INSTALL_PATH_NAME
            ) or (
                isinstance(sub, _ast.Name) and sub.id == INSTALL_PATH_NAME
            )
            if names_the_bundle:
                found.append(f"ligne {sub.lineno} ({callee_name})")
                break
    return found


def test_the_c4_guard_actually_sees_an_unguarded_dependency():
    """La garde ci-dessous ne vaut que si elle DETECTE ce qu'elle interdit."""
    assert _installed_bundle_reaches(
        "import shutil\n"
        "def test_x(tmp_path):\n"
        "    shutil.copytree('/Applications/thingskit.app', tmp_path / 'c')\n"
    )


def test_the_c4_guard_refuses_a_skipif_that_does_not_decide_on_the_bundle():
    """Un `skipif` quelconque ne vaut pas garde — c'est le defaut du 2026-08-21.

    La garde s'est longtemps contentee de constater la PRESENCE d'un `skipif`,
    sans rien dire de ce qu'il decide. `test_the_root_seal_does_not_vouch_for_the_shim`
    sautait sur `not isdir(...)` — la seule PRESENCE du bundle — alors que son
    corps exige un bundle CONFORME (post-ADR-002, shim present). Sur un poste au
    bundle anterieur, le test ne sautait pas et echouait avant d'atteindre la
    moindre assertion sur le sceau : la garde regardait a cote de sa propre
    classe. L'adequation du saut se decide desormais en UN endroit, le predicat
    canonique, et la garde exige qu'on passe par lui.
    """
    assert _installed_bundle_reaches(
        "import pytest, shutil\n"
        "@pytest.mark.skipif(not os.path.isdir('/Applications/thingskit.app'), reason='r')\n"
        "def test_x(tmp_path):\n"
        "    shutil.copytree('/Applications/thingskit.app', tmp_path / 'c')\n"
    )


def test_the_c4_guard_sees_a_reach_named_by_the_module_constant():
    """Nommer la cible par `bundle.INSTALL_PATH` la rend identique, pas absente.

    Le balayage ne lisait que les litteraux : `tests/test_code_identity.py`
    atteignait le bundle installe par la constante et lui echappait entierement.
    """
    assert _installed_bundle_reaches(
        "import shutil\n"
        "from build import bundle\n"
        "def test_x(tmp_path):\n"
        "    shutil.copytree(bundle.INSTALL_PATH, tmp_path / 'c')\n"
    )


def test_the_c4_guard_does_not_cry_wolf_on_a_guarded_one():
    """Un test dont le saut est decide par le predicat canonique, le predicat
    lui-meme, et une simple generation de chaine sont tous trois legitimes —
    une garde qui refuse tout ne distingue plus rien."""
    assert _installed_bundle_reaches(
        "import shutil\n"
        "from conftest import requires_conforming_bundle\n"
        "@requires_conforming_bundle\n"
        "def test_x(tmp_path):\n"
        "    shutil.copytree('/Applications/thingskit.app', tmp_path / 'c')\n"
    ) == []
    assert _installed_bundle_reaches(
        "import os\n"
        "def conforming_bundle_missing():\n"
        "    return not os.path.isdir('/Applications/thingskit.app')\n"
    ) == []
    assert _installed_bundle_reaches(
        "def test_x():\n"
        "    assert launcher_script('/Applications/thingskit.app')\n"
    ) == []


def test_the_suite_does_not_depend_on_an_installed_bundle():
    """Garde C-4 / INV-001-3 : la suite passe sur un poste sans `thingskit.app`.

    La garde exigeait auparavant qu'aucun fichier de test hormis celui-ci ne
    NOMME le chemin d'installation, et s'exemptait elle-meme par son nom. Une
    liste d'exemptions par nom de fichier grandit a chaque nouveau fichier de
    test qui a une raison legitime de toucher au vrai bundle — c'est la forme
    meme d'une deviance qui se normalise, et ADR-002 en aurait ete la premiere
    occasion.

    L'invariant reel n'a jamais ete « ne pas nommer le chemin » mais « ne pas en
    DEPENDRE ». La garde porte donc sur ce qui le rend vrai : toute occurrence
    du chemin qui ATTEINT le systeme de fichiers vit dans un test protege par un
    `skipif`. Rien a exempter, et la garde vaut pour les fichiers qui n'existent
    pas encore.

    Sa limite, enoncee sans reserve : elle lit une liste d'appels
    (`_FILESYSTEM_REACH`), donc une dependance passant par un nom qui n'y figure
    pas lui echapperait. C'est un proxy, pas une preuve — la preuve reste
    l'execution de la suite sur un poste nu, que ce test ne peut pas faire.
    """
    here = os.path.dirname(__file__)
    offenders = []
    for name in sorted(os.listdir(here)):
        if not name.endswith(".py"):
            continue
        source = open(os.path.join(here, name), encoding="utf-8").read()
        offenders += [f"{name}:{hit}" for hit in _installed_bundle_reaches(source)]
    assert offenders == [], (
        "ces occurrences atteignent le bundle installe sans etre protegees "
        f"par un skipif : {offenders}"
    )


# ---------------------------------------------------------------- autonomie


def test_build_refuses_a_bundle_that_still_references_the_package_manager(tmp_path):
    """Le build ne rend pas un artefact dont l'autonomie n'est pas constatée.

    Mesuré le 2026-08-18 : `otool -L` de l'exécutable peut être propre alors
    que le stub interne du framework recharge la dylib du Cellar. Le contrôle
    porte donc sur TOUS les Mach-O du bundle, pas sur l'exécutable seul.
    """
    app = tmp_path / "thingskit.app"
    app.mkdir()
    with pytest.raises(bundle.BundleError) as exc:
        bundle.assert_no_package_manager_refs(
            app, _refs=lambda p: ["/opt/homebrew/opt/sqlite/lib/libsqlite3.dylib"]
        )
    assert "/opt/homebrew" in str(exc.value)


def test_build_accepts_a_bundle_free_of_package_manager_references(tmp_path):
    app = tmp_path / "thingskit.app"
    app.mkdir()
    bundle.assert_no_package_manager_refs(app, _refs=lambda p: [])


# ------------------------------------------- integrite du build (signature)


def test_a_failing_codesign_aborts_the_build(tmp_path, monkeypatch):
    """« bundle construit et signé » ne doit jamais couvrir une signature ratée.

    Un Mach-O laissé non signé après `install_name_tool` est tué par le noyau
    (SIGKILL, rc=137) : un build qui rend 0 dans cet état ment sur son artefact.
    """
    dest = tmp_path / "thingskit.app"
    (dest / "Contents" / "Frameworks").mkdir(parents=True)
    dylib = dest / "Contents" / "Frameworks" / "libx.dylib"
    dylib.write_bytes(b"\xcf\xfa\xed\xfe")

    # Seule la signature de la dylib IMBRIQUÉE échoue : celle de la racine
    # passait déjà par `_run(check=True)`. C'est la boucle par Mach-O qui
    # avalait l'échec, et c'est elle seule que ce test éprouve.
    # Le stub reproduit fidèlement `subprocess.run` : il ne lève QUE si
    # `check=True` lui est passé. C'est exactement ce que le test doit
    # discriminer — un stub qui lèverait inconditionnellement passerait aussi
    # sur le code défectueux, et ne prouverait rien.
    def fail_codesign(cmd, *a, check=False, **kw):
        rc = 1 if (cmd[0] == bundle.CODESIGN and cmd[-1] == str(dylib)) else 0
        if rc and check:
            raise subprocess.CalledProcessError(rc, cmd, stderr="codesign: échec")
        return subprocess.CompletedProcess(cmd, rc, "", "")

    monkeypatch.setattr(bundle.subprocess, "run", fail_codesign)
    with pytest.raises(subprocess.CalledProcessError):
        bundle._sign_everything(dest, "IDENTITY", None)


def test_build_reports_a_signing_failure_instead_of_succeeding(tmp_path, monkeypatch):
    """`main()` doit rendre non nul, pas imprimer « construit et signé »."""

    def boom(*a, **kw):
        raise subprocess.CalledProcessError(1, ["codesign"], stderr="échec")

    monkeypatch.setattr(bundle, "build", boom)
    assert bundle.main(["bundle.py", str(tmp_path / "x.app")]) == 1


# ------------------------------------------- relocalisation (sondes otool)


def test_each_vendored_dylib_is_probed_once(tmp_path, monkeypatch):
    """`otool` était sondé deux fois par dylib et chaque réécriture rejouée à
    l'identique — accident d'édition, pas une double passe voulue."""
    (tmp_path / "libx.dylib").write_bytes(b"\xcf\xfa\xed\xfe")
    probes, rewrites = [], []
    monkeypatch.setattr(
        bundle,
        "_homebrew_refs",
        lambda p: (probes.append(p), ["/opt/homebrew/lib/libz.dylib"])[1],
    )
    monkeypatch.setattr(bundle, "_run", lambda cmd, **kw: rewrites.append(cmd))
    monkeypatch.setattr(bundle.subprocess, "run", lambda *a, **kw: None)

    bundle._rewrite_vendored_dylibs(tmp_path)

    assert len(probes) == 1, f"otool sondé {len(probes)} fois pour une dylib"
    changes = [c for c in rewrites if "-change" in c]
    assert len(changes) == 1, changes
    assert changes[0][2] == "/opt/homebrew/lib/libz.dylib"
    assert changes[0][3] == "@rpath/libz.dylib"


def test_vendored_dylib_rewrite_tolerates_an_already_present_rpath(tmp_path, monkeypatch):
    """C-3 : `-add_rpath` échoue légitimement à la reconstruction (rpath déjà
    posé). Cet échec-là ne doit jamais interrompre le build."""
    (tmp_path / "libx.dylib").write_bytes(b"\xcf\xfa\xed\xfe")
    monkeypatch.setattr(bundle, "_homebrew_refs", lambda p: [])

    def refuse_rpath(cmd, *a, **kw):
        if "-add_rpath" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "already contains")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bundle.subprocess, "run", refuse_rpath)
    bundle._rewrite_vendored_dylibs(tmp_path)  # ne lève pas


def test_relocation_helpers_are_extracted_and_named():
    """`_relocate` orchestrait 65 lignes à trois niveaux de boucles."""
    for name in (
        "_vendor_third_party_dylibs",
        "_rewrite_vendored_dylibs",
        "_rewrite_framework_machos",
    ):
        assert hasattr(bundle, name), name


# ------------------------------------------------- exigence de code (passe 3)


def test_the_seal_check_pins_the_signing_identity_not_just_any_valid_signature():
    """Sans `-R`, `--verify --strict` accepte N'IMPORTE QUELLE signature valide.

    Mesure du 2026-08-18, sur une copie du bundle installe : alterer
    `Contents/Resources/thingskit` puis re-signer ad-hoc (`codesign --force
    -s -`) fait repasser `--verify --strict` a rc=0, alors que le bundle porte
    alors `Signature=adhoc` et `TeamIdentifier=not set`. Le controle constatait
    donc « une signature », jamais « LA signature ».

    Le porteur du controle a change (ADR-002 : `sh` -> shim Mach-O), donc le
    quoting `sh` n'a plus lieu d'etre garde — l'exigence traverse desormais une
    chaine C. Ce qui est garde reste ce qui compte : que `-R` soit oppose, et
    qu'il porte les trois clauses de l'exigence du depot.
    """
    source = bundle.shim_source(["areas"])
    assert '"-R="' in source
    assert "anchor apple generic" in source
    assert f'identifier \\"{bundle.BUNDLE_IDENTIFIER}\\"' in source
    assert f'certificate leaf[subject.OU]=\\"{bundle.TEAM_IDENTIFIER}\\"' in source


@requires_conforming_bundle
def test_the_real_bundle_satisfies_the_requirement_the_launcher_demands():
    """Une exigence que l'artefact reel ne satisfait pas serait une panne."""
    rc = subprocess.run(
        [
            "/usr/bin/codesign", "--verify", "--strict",
            f"-R={bundle.CODE_REQUIREMENT}", "/Applications/thingskit.app",
        ],
        capture_output=True, text=True,
    )
    assert rc.returncode == 0, rc.stderr


@requires_conforming_bundle
def test_an_adhoc_resigned_copy_is_refused_by_the_shim(tmp_path):
    """Adversite : le contournement mesure est rejoue, sur une COPIE.

    Le bundle installe n'est JAMAIS touche : l'alteration et la re-signature
    portent sur une copie en tmpdir.
    """
    import shutil as _sh

    copy = tmp_path / "thingskit.app"
    _sh.copytree("/Applications/thingskit.app", copy, symlinks=True)
    shim = bundle.compile_shim(
        bundle.shim_source(["areas"], app_path=str(copy)), tmp_path / "shim"
    )
    ok = subprocess.run([str(shim), "areas"], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr

    with open(copy / "Contents" / "Resources" / "thingskit", "a") as fh:
        fh.write("\n# altere\n")
    resign = subprocess.run(
        ["/usr/bin/codesign", "--force", "-s", "-", str(copy)],
        capture_output=True, text=True,
    )
    assert resign.returncode == 0, resign.stderr
    # Le sceau ad-hoc est VALIDE — c'est tout le point du defaut corrige.
    plain = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(copy)],
        capture_output=True, text=True,
    )
    assert plain.returncode == 0, "le sceau ad-hoc devait etre valide"

    ko = subprocess.run([str(shim), "areas"], capture_output=True, text=True)
    assert ko.returncode == bundle.SEAL_REFUSAL_CODE, (ko.returncode, ko.stderr)
    assert "sceau" in ko.stderr.lower()


# ------------------------------------------------------- BUG-010 : identité
# de signature résolue sur le poste, jamais nommée en dur.
#
# L'exigence de code opposée au bundle porte sur l'identifiant et sur l'ÉQUIPE
# (`certificate leaf[subject.OU]`), jamais sur le nom du certificat feuille.
# Toute identité de développement de l'équipe la satisfait donc ; nommer un
# certificat précis liait le build à un seul poste sans rien garder de plus.
#
# Les seams sont injectés (`listing`, `team_of`) : aucun de ces tests ne dépend
# du trousseau du poste (C-4), sauf ceux explicitement marqués.

_LISTING_STUDIO = (
    '  1) 1A2B3C4D5E6F708192A3B4C5D6E7F80912345678 '
    '"Apple Development: Prenom NOM (XXXXXXXXXX)"\n'
    "     1 valid identities found\n"
)


def _team_of(mapping):
    """Sonde d'équipe injectée : rend l'OU du certificat feuille, ou None."""
    return lambda sha1, name: mapping.get(sha1)


# ------------------------------------------------ US-010 : fuite d'identité
#
# La garde d'origine interdisait UNE valeur, et seulement dans `build/`. Elle
# était juste et sans portée : les fixtures « mesurées » de CE fichier
# publiaient la même identité — nom légal, identifiant de développeur, UID et
# sujet DN entier — quand les fixtures voisines employaient déjà des noms
# fictifs. Le dépôt devenant public, l'écart cesse d'être théorique.
#
# La garde ne peut PAS être écrite en nommant la valeur interdite : l'y écrire
# la réintroduirait dans le dépôt. Elle porte donc sur la FORME — toute
# identité de signature citée dans `build/` ou `tests/` doit figurer dans les
# jeux fictifs ci-dessous — et rend un compte résiduel nul, sur le modèle de
# `test_no_executable_is_invoked_by_bare_name`. Ajouter une fixture impose
# d'étendre l'allowlist : c'est le point, la décision redevient consciente.
# Le dépôt est PUBLIC : la portée du balayage est ce qu'il publie, donc
# l'arbre SUIVI par git — pas deux répertoires de `.py`. La portée d'origine
# (`build/` + `tests/`, extension `.py`) laissait dehors `bin/thingskit`,
# `constitution.md` et `pyproject.toml`, pendant que le nom du test disait
# « anywhere in the repository ».
def _tracked_paths() -> list[Path]:
    listing = subprocess.run(
        ["git", "-C", str(bundle.REPO_ROOT), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [bundle.REPO_ROOT / rel for rel in listing.split("\0") if rel]


def _undecodable_tracked_files() -> list[str]:
    """Fichiers suivis que le balayage ne sait pas lire.

    Rendu VIDE aujourd'hui, et épinglé comme tel : ajouter un binaire au dépôt
    fait échouer la garde de portée plutôt que de rétrécir le balayage en
    silence. Un fichier sauté sans le dire est le mode d'échec exact que ce
    lot corrige.
    """
    unreadable = []
    for path in _tracked_paths():
        try:
            path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            unreadable.append(str(path.relative_to(bundle.REPO_ROOT)))
    return sorted(unreadable)


# Un nom d'identité de signature, suivi de son identifiant de développeur
# entre parenthèses quand il y en a un. Les deux sont extraits ENSEMBLE : une
# parenthèse isolée, dans un dépôt de code Python, est du code — la chercher
# seule rendait 1 109 faux positifs (mesuré).
_IDENTITY_RE = re.compile(
    r"(?:Apple Development|Apple Distribution|Developer ID Application"
    r"|Mac Developer): ([^\"()\\,\n]+?)\s*(?:\(([^)\"\n]{1,20})\))?(?=[\"\\,\n]|$)"
)
# Identifiant porté par un sujet DN : équipe (`OU=`) ou compte (`UID=`).
_DN_TOKEN_RE = re.compile(r"(?:OU|UID)=([A-Za-z0-9]{1,20})")
_FINGERPRINT_RE = re.compile(r"\b[0-9A-F]{40}\b")

# Un nom légal cité en clair : un mot capitalisé suivi d'un à trois mots en
# CAPITALES. La classe n'est balayée que dans les LITTÉRAUX de chaîne hors
# docstring des fichiers Python — c'est-à-dire dans les FIXTURES, ce dont
# parle la clause de la constitution. Mesuré : 3 formes distinctes à cette
# portée, contre 38 en incluant commentaires et docstrings (`Le CLI`,
# `Chemin ABSOLU`, `Ne JAMAIS`…), et 12 en incluant la prose des `.md`. Une
# allowlist de prose grossit à chaque paragraphe : la garde serait désactivée.
# Les majuscules sont ACCENTUÉES des deux côtés : `[A-Z]` est ASCII strict, et
# `Émilie DUPONT` passait donc à travers sur un dépôt francophone. Gratuit —
# mesuré 0 occurrence nouvelle sur l'arbre suivi.
_UPPER = "A-ZÀ-ÖØ-Þ"
_LOWER = "a-zà-öø-ÿ"
_PERSONAL_NAME_RE = re.compile(
    rf"\b[{_UPPER}][{_LOWER}']+(?: [{_UPPER}]{{2,}}){{1,3}}\b")
_ALLOWED_PERSONAL_NAMES = {
    "Prenom NOM",     # gabarit neutre, déjà dans `build/bundle.py`
    "Developer ID",   # classe de certificat Apple, pas une personne
}

# Un identifiant d'équipe ou de développeur Apple ÉCRIT SEUL : dix caractères
# alphanumériques majuscules mêlant lettres et chiffres. C'est la forme que
# l'assertion nominative retirée couvrait et que le balayage par la forme
# complète manque — il ne le voit que serti dans `Apple Development: … (…)`
# ou dans un sujet DN. La valeur ne peut PAS être citée ici, pas même en
# commentaire : ce balayage la détecterait, et il a effectivement fait échouer
# sa propre écriture quand elle y figurait.
_BARE_IDENTIFIER_RE = re.compile(
    r"\b(?=[A-Z0-9]{10}\b)(?=[A-Z0-9]*[0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{10}\b"
)

# Noms fictifs. Aucun n'est celui d'une personne réelle.
_FICTIONAL_NAMES = {
    "Prenom NOM", "Quelqu un", "Autre equipe", "Zoe", "Adele", "X", "Mallory",
}

# Les SEULS identifiants réels admis, nommés un par un avec ce qui les rend
# publics. Un ajout muet est impossible : il faut écrire le motif.
_REAL_IDENTIFIERS_ADMITTED = {
    bundle.TEAM_IDENTIFIER:
        "équipe du projet — publique par construction : elle est dans "
        "`bundle.CODE_REQUIREMENT`, donc dans le sceau de tout artefact "
        "distribué. Une garde qui la refuserait serait ininstallable.",
    "JLMPQHK86H":
        "équipe de Cultured Code — publique : c'est le préfixe du Group "
        "Container de Things 3, lisible dans le chemin de la base "
        "(`bin/thingskit`, `_GC`). Elle ne désigne personne ici.",
}

# Identifiants de fixture. Chacun doit être MANIFESTEMENT synthétique — la
# règle de forme ci-dessous rend impossible d'y glisser une valeur réelle.
_SYNTHETIC_IDENTIFIERS = {
    "XXXXXXXXXX", "YYYYYYYYYY", "XXXX", "X", "1", "2",
    "EVILTEAM1", "REALTEAM01", "FAKE", "OLDTEAM001", "NEWTEAM002", "TEAM000001",
}
_ALLOWED_IDENTIFIERS = set(_REAL_IDENTIFIERS_ADMITTED) | _SYNTHETIC_IDENTIFIERS

_ALLOWED_FINGERPRINTS = {
    "F" * 40, "0" * 40, "A" * 40,
    "1A2B3C4D5E6F708192A3B4C5D6E7F80912345678",
}

# Marques d'une valeur inventée : un mot déclaré, une répétition d'au moins
# trois caractères, ou une séquence croissante. Là où l'empreinte ci-dessous
# rend un ajout seulement VISIBLE, cette règle-ci le rend TRÈS IMPROBABLE —
# pas impossible, et le mot « IMPOSSIBLE » a figuré ici à tort. Mesuré le
# 2026-08-26 par Monte-Carlo sur 200 000 identifiants de 10 caractères
# [A-Z0-9] tirés au hasard (graine 20260826) : 1 345 les satisfont, soit
# 0,67 %. Les marqueurs `OLD`, `NEW`, `NOM`, `REAL`, `TEAM` sont des
# sous-chaînes courtes, et un identifiant réel peut les contenir par accident.
# Rejeu : cf. la commande dans `constitution.md` § Tests.
_SYNTHETIC_MARKERS = ("FAKE", "EVIL", "TEAM", "TEST", "OLD", "NEW", "REAL",
                      "DUMMY", "PRENOM", "NOM")
_REPEATED_RUN_RE = re.compile(r"(.)\1{2,}")


def _is_manifestly_synthetic(token: str) -> bool:
    if len(token) <= 4:
        return True
    if _REPEATED_RUN_RE.search(token):
        return True
    if "12345678" in token:
        return True
    return any(marker in token.upper() for marker in _SYNTHETIC_MARKERS)


# Marques d'un NOM inventé. La règle de forme du jeu d'identifiants ne
# transpose pas aux noms : `Adele` ne porte ni répétition, ni séquence, ni mot
# déclaré, et c'est pourtant un nom de fixture depuis toujours. La règle est
# donc l'inverse — un nom n'est admis que si CHACUN de ses mots vient d'un
# vocabulaire fermé : un mot très court, un mot de gabarit, ou une persona
# canonique de la littérature de test.
_PLACEHOLDER_WORDS = {
    "PRENOM", "NOM", "QUELQU", "UN", "UNE", "AUTRE", "EQUIPE", "TEAM",
    "DEVELOPER", "ID", "FAKE", "TEST", "DUMMY", "INCONNU", "ANONYME",
}
# Le rôlier de la littérature cryptographique, plus les deux prénoms déjà
# employés par les fixtures voisines. Aucun ne désigne quelqu'un ici.
_CANONICAL_TEST_PERSONAS = {
    "ALICE", "BOB", "CAROL", "DAVE", "EVE", "MALLORY", "TRENT", "OSCAR",
    "ZOE", "ADELE",
}


def _is_manifestly_fictional_name(name: str) -> bool:
    """Chaque mot du nom vient d'un vocabulaire fermé.

    Écrire `Jean DUPONT` dans l'un des deux jeux de noms fait donc échouer la
    garde : `JEAN` n'est ni assez court, ni un mot de gabarit, ni une persona
    canonique. C'est ce qui rend l'ajout d'une valeur réelle IMPOSSIBLE dans
    ces deux jeux-là — au sens strict, ici, parce que le vocabulaire est
    ÉNUMÉRÉ et non deviné à la forme.
    """
    words = name.split()
    if not words:
        return False
    return all(len(word) <= 3
               or word.upper() in _PLACEHOLDER_WORDS
               or word.upper() in _CANONICAL_TEST_PERSONAS
               for word in words)


def _allowlist_digest() -> str:
    """Empreinte des SEPT jeux. Modifier l'un d'eux impose de toucher la
    constante épinglée : le changement passe par une ligne dont la raison
    d'être est de le signaler en revue.

    Trois de ces jeux ont été ajoutés au troisième tour de review : les MOTIFS
    des identifiants réels admis (seules les clés y entraient — réécrire ou
    vider un motif ne se signalait nulle part), et les deux vocabulaires qui
    ouvrent la règle de forme sur les noms (y ajouter un mot autorise un nom
    de plus).
    """
    payload = "\n".join(
        "|".join(sorted(group)) for group in (
            _FICTIONAL_NAMES, _ALLOWED_IDENTIFIERS,
            _ALLOWED_FINGERPRINTS, _ALLOWED_PERSONAL_NAMES,
            _PLACEHOLDER_WORDS, _CANONICAL_TEST_PERSONAS,
            {f"{token}={reason}"
             for token, reason in _REAL_IDENTIFIERS_ADMITTED.items()},
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Épinglé le 2026-08-26. Le régénérer :
#     .venv/bin/python -c "import sys;sys.path.insert(0,'tests');\
# import test_bundle as t;print(t._allowlist_digest())"
_ALLOWLIST_DIGEST = "9273db2c853685e6ba4ce3d7bee62a4127471e2816f488a1abafea8dff8e8432"


def _swept_sources():
    for path in _tracked_paths():
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def _docstring_free_string_literals(source: str) -> list[str] | None:
    """Littéraux de chaîne hors docstring. `None` si la source n'est pas du
    Python analysable — la classe « valeur personnelle » ne s'y applique pas."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def _leaked_personal_names(sources=None) -> list[str]:
    """Compte résiduel : noms légaux cités en clair dans une fixture."""
    offenders = []
    for path, source in (sources if sources is not None else _swept_sources()):
        where = path.name if hasattr(path, "name") else str(path)
        literals = _docstring_free_string_literals(source)
        if literals is None:
            continue
        for literal in literals:
            for match in set(_PERSONAL_NAME_RE.findall(literal)):
                if match not in _ALLOWED_PERSONAL_NAMES:
                    offenders.append(f"{where} : valeur personnelle {match!r}")
    return sorted(set(offenders))


def _leaked_bare_identifiers(sources=None) -> list[str]:
    """Compte résiduel : identifiants Apple écrits SEULS, hors des jeux
    déclarés."""
    offenders = []
    for path, source in (sources if sources is not None else _swept_sources()):
        where = path.name if hasattr(path, "name") else str(path)
        for token in set(_BARE_IDENTIFIER_RE.findall(source)):
            if token not in _ALLOWED_IDENTIFIERS:
                offenders.append(f"{where} : identifiant nu {token!r}")
    return sorted(set(offenders))


def _leaked_identities(sources=None) -> list[str]:
    """Compte résiduel : identités de signature non fictives citées en clair."""
    offenders = []
    for path, source in (sources if sources is not None else _swept_sources()):
        where = path.name if hasattr(path, "name") else str(path)
        for name, identifier in set(_IDENTITY_RE.findall(source)):
            if name.strip() not in _FICTIONAL_NAMES:
                offenders.append(f"{where} : nom d'identité {name.strip()!r}")
            if identifier and identifier not in _ALLOWED_IDENTIFIERS:
                offenders.append(f"{where} : identifiant {identifier!r}")
        for token in set(_DN_TOKEN_RE.findall(source)):
            if token not in _ALLOWED_IDENTIFIERS:
                offenders.append(f"{where} : identifiant {token!r}")
        for fingerprint in set(_FINGERPRINT_RE.findall(source)):
            if fingerprint not in _ALLOWED_FINGERPRINTS:
                offenders.append(f"{where} : empreinte {fingerprint!r}")
    return sorted(set(offenders))


def test_no_real_signing_identity_is_written_anywhere_in_the_repository():
    """AC-6 : compte résiduel d'identités réelles = 0, sur `build/` ET `tests/`.

    L'ancienne garde ne couvrait que `build/` — c'est par `tests/` que la
    valeur est entrée, et elle y est restée sans que rien ne le dise.
    """
    assert _leaked_identities() == []


def test_the_leak_sweep_actually_sees_a_real_identity():
    """La garde ne vaut que si elle DÉTECTE ce qu'elle interdit.

    Les valeurs éprouvées sont SYNTHÉTIQUES — écrire la vraie ici la remettrait
    dans le dépôt, ce qui est le défaut même qu'on corrige. Elles sont de plus
    COMPOSÉES à l'exécution : écrites d'un seul tenant, elles feraient échouer
    la garde sur ce fichier-ci, et la seule issue serait de les allowlister,
    c'est-à-dire de rendre la contre-épreuve inopérante.
    """
    prefix = "Apple Deve" + "lopment: "
    name = "Je" + "an DUPONT"
    developer = "7Q2WE" + "9081T"
    fingerprint = "9F8E7D6C5B4A3928" + "1706F5E4D3C2B1A098765432"
    team = "56XY9" + "9ZZ01"
    account = "8N4KK" + "SYNTZ"
    source = (
        f'  1) {fingerprint} "{prefix}{name} ({developer})"\n'
        f'DN = "subject=OU={team},UID={account}"\n'
    )
    assert _leaked_identities([(Path("fixture.py"), source)]) == sorted([
        f"fixture.py : empreinte {fingerprint!r}",
        f"fixture.py : identifiant {developer!r}",
        f"fixture.py : identifiant {team!r}",
        f"fixture.py : identifiant {account!r}",
        f"fixture.py : nom d'identité {name!r}",
    ])


def test_the_leak_sweep_does_not_flag_the_projects_own_team_identifier():
    """Contre-épreuve : le Team ID du projet est PUBLIC par construction — il
    est dans le sceau de tout artefact distribué. Le refuser rendrait la garde
    ininstallable, donc désactivée."""
    source = f'DN = "subject=OU={bundle.TEAM_IDENTIFIER},CN=Apple Development: Zoe (1)"\n'
    assert _leaked_identities([(Path("fixture.py"), source)]) == []


def test_no_certificate_name_is_hardcoded_in_the_build():
    """AC-1 : plus aucune identité nommée en dur dans le module."""
    assert not hasattr(bundle, "SIGNING_IDENTITY")


def test_identity_listing_is_parsed_into_hash_and_name_pairs():
    assert bundle.parse_identity_listing(_LISTING_STUDIO) == [
        (
            "1A2B3C4D5E6F708192A3B4C5D6E7F80912345678",
            "Apple Development: Prenom NOM (XXXXXXXXXX)",
        )
    ]


def test_identity_listing_of_a_bare_machine_parses_to_nothing():
    """Le MacBook Air rend littéralement ceci (mesuré le 2026-08-19)."""
    assert bundle.parse_identity_listing("     0 valid identities found\n") == []
    assert bundle.parse_identity_listing("") == []


def test_the_team_is_read_from_the_certificate_never_from_the_identity_name():
    """Le nom porte `(XXXXXXXXXX)`, l'équipe est `56AP2NSB54` : deux valeurs
    distinctes sur le certificat réel. Déduire l'équipe du nom serait faux."""
    assert (
        bundle.subject_ou(
            "subject=C=US,O=SoWell,OU=56AP2NSB54,"
            "CN=Apple Development: Prenom NOM (XXXXXXXXXX),UID=YYYYYYYYYY"
        )
        == "56AP2NSB54"
    )


def test_an_identity_of_another_team_is_not_eligible():
    listing = '  1) AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "Apple Development: Quelqu un (XXXX)"\n'
    assert (
        bundle.eligible_signing_identities(
            listing=listing, team_of=_team_of({"A" * 40: "ZZZZZZZZZZ"})
        )
        == []
    )


def test_the_studio_identity_is_still_the_one_retained():
    """AC-5 / BUG-010-04 : comportement inchangé sur ce poste."""
    assert bundle.resolve_signing_identity(
        listing=_LISTING_STUDIO,
        team_of=_team_of(
            {"1A2B3C4D5E6F708192A3B4C5D6E7F80912345678": bundle.TEAM_IDENTIFIER}
        ),
    ) == "1A2B3C4D5E6F708192A3B4C5D6E7F80912345678"


def test_no_eligible_identity_is_a_clean_refusal_naming_the_team():
    """BUG-010-02 : échec net, jamais un bundle non signé."""
    with pytest.raises(bundle.BundleError) as exc:
        bundle.resolve_signing_identity(
            listing='  1) AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "Apple Development: Autre equipe (XXXX)"\n',
            team_of=_team_of({"A" * 40: "ZZZZZZZZZZ"}),
        )
    assert bundle.TEAM_IDENTIFIER in str(exc.value)


def test_an_empty_keychain_is_a_clean_refusal_too():
    with pytest.raises(bundle.BundleError):
        bundle.resolve_signing_identity(
            listing="     0 valid identities found\n", team_of=_team_of({})
        )


def test_several_eligible_identities_resolve_deterministically():
    """BUG-010-03 : la règle de choix est le nom, puis l'empreinte — et elle ne
    dépend pas de l'ordre dans lequel `security` a listé les identités."""
    a = '  1) FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF "Apple Development: Zoe (1)"\n'
    b = '  2) 0000000000000000000000000000000000000000 "Apple Development: Adele (2)"\n'
    teams = _team_of({"F" * 40: bundle.TEAM_IDENTIFIER, "0" * 40: bundle.TEAM_IDENTIFIER})
    assert bundle.resolve_signing_identity(listing=a + b, team_of=teams) == "0" * 40
    assert bundle.resolve_signing_identity(listing=b + a, team_of=teams) == "0" * 40


def test_ties_on_the_name_are_broken_by_the_fingerprint():
    same = '  1) FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF "Apple Development: X (1)"\n  2) 0000000000000000000000000000000000000000 "Apple Development: X (1)"\n'
    teams = _team_of({"F" * 40: bundle.TEAM_IDENTIFIER, "0" * 40: bundle.TEAM_IDENTIFIER})
    assert bundle.resolve_signing_identity(listing=same, team_of=teams) == "0" * 40


def test_several_eligible_identities_are_announced_not_chosen_in_silence(capsys):
    a = '  1) FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF "Apple Development: Zoe (1)"\n  2) 0000000000000000000000000000000000000000 "Apple Development: Adele (2)"\n'
    teams = _team_of({"F" * 40: bundle.TEAM_IDENTIFIER, "0" * 40: bundle.TEAM_IDENTIFIER})
    bundle.resolve_signing_identity(listing=a, team_of=teams)
    err = capsys.readouterr().err
    assert "Zoe" in err and "Adele" in err


def test_a_single_eligible_identity_says_nothing(capsys):
    bundle.resolve_signing_identity(
        listing=_LISTING_STUDIO,
        team_of=_team_of(
            {"1A2B3C4D5E6F708192A3B4C5D6E7F80912345678": bundle.TEAM_IDENTIFIER}
        ),
    )
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("identity", ["-", "", "  "])
def test_the_build_refuses_to_sign_ad_hoc(identity):
    """La dégradation la plus dangereuse : un bundle « signé » sans identité de
    code satisfait `--verify --strict` mais pas l'exigence, et perd le grant
    TCC. `codesign -s -` ne doit jamais pouvoir être composé ici."""
    with pytest.raises(bundle.BundleError):
        bundle.codesign_command("/tmp/x.app", identity)


def test_codesign_command_enables_hardened_runtime_with_the_given_identity():
    cmd = bundle.codesign_command("/Applications/thingskit.app", "DEADBEEF")
    # Chemin absolu, jamais résolu par PATH : voir INSTALL_NAME_TOOL.
    assert cmd[0] == bundle.CODESIGN == "/usr/bin/codesign"
    assert "--options" in cmd and cmd[cmd.index("--options") + 1] == "runtime"
    assert cmd[cmd.index("-s") + 1] == "DEADBEEF"
    assert cmd[-1] == "/Applications/thingskit.app"


def test_the_build_resolves_the_identity_before_touching_the_destination(tmp_path):
    """BUG-010-02, versant destructeur : sur un poste sans identité, le build
    doit refuser AVANT d'avoir détruit le bundle installé — sinon il laisse le
    poste sans bundle du tout, ce que le lanceur traduit en refus d'exécution."""
    dest = tmp_path / "thingskit.app"
    dest.mkdir()
    (dest / "temoin").write_text("intact")
    with pytest.raises(bundle.BundleError):
        bundle.build(
            dest,
            listing="     0 valid identities found\n",
            team_of=_team_of({}),
        )
    assert (dest / "temoin").read_text() == "intact"


@pytest.mark.skipif(
    not os.path.exists("/usr/bin/security"),
    reason="poste sans `security` (C-4)",
)
def test_the_host_probes_agree_with_this_machines_keychain():
    """Les sondes réelles (non injectées) doivent au moins être exécutables et
    concorder entre elles : chaque identité listée porte une équipe lisible ou
    explicitement inconnue, sans exception."""
    for sha1, name in bundle.host_identities():
        team = bundle.certificate_team(sha1, name)
        assert team is None or (isinstance(team, str) and team)


# ------------------------------------------------- BUG-013 point 3 : le build
# oppose lui-même l'exigence de code à l'artefact qu'il vient de produire.
#
# Ces tests n'injectent AUCUNE sonde à la place de `codesign` : ils opposent la
# fonction réelle à des artefacts réellement signés, seule façon qu'un défaut
# de la fonction les fasse rougir.


def _minimal_app(tmp_path, name="thingskit.app"):
    """Un .app minimal mais RÉEL : plist + un Mach-O emprunté au système."""
    import shutil as _sh

    app = tmp_path / name
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text(
        bundle.info_plist_xml(), encoding="utf-8"
    )
    _sh.copyfile("/bin/echo", macos / "thingskit")
    (macos / "thingskit").chmod(0o755)
    return app


@pytest.mark.skipif(
    not os.path.exists("/usr/bin/codesign"), reason="poste sans `codesign` (C-4)"
)
def test_an_adhoc_signed_artifact_is_refused_by_the_build_verification(tmp_path):
    """Le défaut visé : un sceau VALIDE qui ne satisfait pas l'exigence.

    `codesign --verify --strict` seul rend 0 sur cet artefact — c'est
    l'opposition de l'exigence qui le fait échouer.
    """
    app = _minimal_app(tmp_path)
    signed = subprocess.run(
        ["/usr/bin/codesign", "--force", "--options", "runtime", "-s", "-", str(app)],
        capture_output=True, text=True,
    )
    assert signed.returncode == 0, signed.stderr
    plain = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(app)],
        capture_output=True, text=True,
    )
    assert plain.returncode == 0, "le sceau ad-hoc devait être valide"

    with pytest.raises(bundle.BundleError) as exc:
        bundle.assert_bundle_satisfies_requirement(app, "ADHOC-FINGERPRINT")
    message = str(exc.value)
    assert bundle.CODE_REQUIREMENT in message
    assert str(app) in message
    assert "ADHOC-FINGERPRINT" in message


@pytest.mark.skipif(
    not os.path.exists("/usr/bin/codesign"), reason="poste sans `codesign` (C-4)"
)
def test_an_unsigned_artifact_is_refused_too(tmp_path):
    app = _minimal_app(tmp_path)
    with pytest.raises(bundle.BundleError) as exc:
        bundle.assert_bundle_satisfies_requirement(app, "DEADBEEF")
    assert str(app) in str(exc.value)


@requires_conforming_bundle
def test_the_installed_bundle_passes_the_very_check_the_build_runs():
    """Cas nominal : la vérification ne rougit pas sur un artefact conforme."""
    bundle.assert_bundle_satisfies_requirement(
        "/Applications/thingskit.app", "identité du poste"
    )


@pytest.mark.skipif(
    not os.path.exists("/usr/bin/codesign"), reason="poste sans `codesign` (C-4)"
)
def test_a_requirement_the_artifact_cannot_satisfy_is_named_in_the_refusal(tmp_path):
    """Le message doit nommer l'exigence RÉELLEMENT opposée, pas une constante."""
    app = _minimal_app(tmp_path)
    subprocess.run(
        ["/usr/bin/codesign", "--force", "-s", "-", str(app)],
        capture_output=True, text=True, check=True,
    )
    bogus = 'identifier "app.sowell.absent"'
    with pytest.raises(bundle.BundleError) as exc:
        bundle.assert_bundle_satisfies_requirement(app, "X1", requirement=bogus)
    assert bogus in str(exc.value)


def test_the_build_verifies_the_artifact_it_produced_before_returning():
    """AC-1, versant statique : la vérification est câblée en fin de `build()`,
    APRÈS la signature — vérifier avant signer ne vérifierait rien."""
    import inspect

    src = inspect.getsource(bundle.build)
    assert "assert_bundle_satisfies_requirement" in src
    assert src.index("_sign_everything(") < src.index(
        "assert_bundle_satisfies_requirement("
    )


# ------------------------------------------------- BUG-013 point 1 : une
# virgule ÉCHAPPÉE (RFC2253) dans une valeur n'est pas un séparateur de RDN.


def test_an_escaped_comma_cannot_forge_the_team():
    """Le sujet ci-dessous n'a qu'UN seul OU : `EVILTEAM1`.

    `CN=…\\,OU=56AP2NSB54` est une valeur CN contenant une virgule LITTÉRALE,
    pas deux RDN. Un analyseur qui coupe sur toutes les virgules y lit un OU
    qui n'existe pas, et le premier trouvé gagne — il rendrait `56AP2NSB54`,
    soit exactement l'équipe attendue par le build, pour un certificat d'une
    tout autre équipe.
    """
    assert (
        bundle.subject_ou(
            "subject=CN=Apple Development: Mallory (X)\\,OU=56AP2NSB54,"
            "OU=EVILTEAM1,O=Evil,C=US"
        )
        == "EVILTEAM1"
    )


def test_an_escaped_comma_before_the_real_ou_is_not_a_separator_either():
    assert (
        bundle.subject_ou("subject=O=Some\\, Inc.,OU=REALTEAM01,C=US")
        == "REALTEAM01"
    )


def test_a_subject_without_ou_stays_unreadable():
    assert bundle.subject_ou("subject=CN=X\\,OU=FAKE,O=Y,C=US") is None
    assert bundle.subject_ou("") is None


def test_an_escaped_backslash_does_not_escape_the_next_comma():
    """`\\\\` est un antislash littéral : la virgule qui suit sépare bien."""
    assert bundle.subject_ou("subject=O=Back\\\\,OU=REALTEAM01,C=US") == "REALTEAM01"


# ------------------------------------------------- BUG-013 point 2 : le cœur
# du correctif (boucle PEM, correspondance d'empreinte, analyse du sujet) est
# exercé POUR DE VRAI, sur des certificats réels et un `openssl` réel. Seule
# la sortie de `security find-certificate` est fournie en fixture : c'est la
# donnée du poste, pas la logique.


def _synthetic_certificate(tmp_path, stem, subject):
    """Un certificat X.509 réel, et son empreinte SHA-1, mesurée par openssl."""
    cert = tmp_path / f"{stem}.pem"
    key = tmp_path / f"{stem}.key"
    subprocess.run(
        ["/usr/bin/openssl", "req", "-x509", "-newkey", "ec",
         "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-subj", subject],
        capture_output=True, text=True, check=True,
    )
    line = subprocess.run(
        ["/usr/bin/openssl", "x509", "-noout", "-fingerprint", "-sha1",
         "-in", str(cert)],
        capture_output=True, text=True, check=True,
    ).stdout
    sha1 = line.split("=", 1)[1].strip().replace(":", "").upper()
    return sha1, cert.read_text(encoding="utf-8")


def _find_certificate_dump(*entries):
    """Reproduit la sortie de `security find-certificate -a -Z -p`."""
    return "".join(
        f"SHA-1 hash: {sha1}\n"
        f"SHA-256 hash: {'0' * 64}\n"
        f"{pem}"
        for sha1, pem in entries
    )


@pytest.mark.skipif(
    not os.path.exists("/usr/bin/openssl"), reason="poste sans `openssl` (C-4)"
)
def test_certificate_team_reads_the_certificate_of_the_asked_fingerprint(tmp_path):
    """Le cas que le docstring de la fonction invoque : deux certificats de
    MÊME nom (renouvellement) et d'empreintes différentes. C'est l'empreinte
    qui tranche, jamais l'ordre du dump ni le nom."""
    name = "Apple Development: Prenom NOM (XXXXXXXXXX)"
    old = _synthetic_certificate(tmp_path, "old", f"/OU=OLDTEAM001/CN={name}")
    new = _synthetic_certificate(tmp_path, "new", f"/OU=NEWTEAM002/CN={name}")
    assert old[0] != new[0]
    dump = _find_certificate_dump(old, new)

    assert bundle.certificate_team(new[0], name, dump=dump) == "NEWTEAM002"
    assert bundle.certificate_team(old[0], name, dump=dump) == "OLDTEAM001"
    # L'ordre du dump ne décide de rien.
    reversed_dump = _find_certificate_dump(new, old)
    assert bundle.certificate_team(old[0], name, dump=reversed_dump) == "OLDTEAM001"
    # Casse de l'empreinte indifférente (le trousseau rend des majuscules).
    assert bundle.certificate_team(new[0].lower(), name, dump=dump) == "NEWTEAM002"


@pytest.mark.skipif(
    not os.path.exists("/usr/bin/openssl"), reason="poste sans `openssl` (C-4)"
)
def test_certificate_team_is_none_when_no_certificate_matches(tmp_path):
    name = "Apple Development: X (1)"
    one = _synthetic_certificate(tmp_path, "one", f"/OU=TEAM000001/CN={name}")
    dump = _find_certificate_dump(one)
    assert bundle.certificate_team("F" * 40, name, dump=dump) is None
    assert bundle.certificate_team(one[0], name, dump="") is None


@pytest.mark.skipif(
    not os.path.exists("/usr/bin/openssl"), reason="poste sans `openssl` (C-4)"
)
def test_a_certificate_forging_an_ou_in_its_cn_is_read_for_what_it_is(tmp_path):
    """Points 1 et 2 réunis, de bout en bout : un certificat RÉEL dont le CN
    contient `,OU=56AP2NSB54` ne doit jamais passer pour l'équipe du build."""
    name = "Apple Development: Mallory (X),OU=" + bundle.TEAM_IDENTIFIER
    evil = _synthetic_certificate(tmp_path, "evil", f"/CN={name}/OU=EVILTEAM1/O=Evil")
    dump = _find_certificate_dump(evil)
    assert bundle.certificate_team(evil[0], name, dump=dump) == "EVILTEAM1"
    assert (
        bundle.eligible_signing_identities(
            listing=f'  1) {evil[0]} "{name}"\n',
            team_of=lambda sha1, n: bundle.certificate_team(sha1, n, dump=dump),
        )
        == []
    )


# ------------------------------------------- ADR-002 § Decision 5 : le TYPE
# de certificat
#
# Le `csreq` que TCC a enregistre pour `app.sowell.thingskit` pinne l'identifier,
# l'ancre Apple, le CN du certificat FEUILLE et le marqueur de l'intermediaire —
# jamais le Team ID (mesure du 2026-08-21). L'exigence du depot, elle, ne portait
# que l'equipe. Les deux ne coincidaient donc pas, et l'ecart avait une
# consequence precise et UNIDIRECTIONNELLE : un bundle pouvait satisfaire
# l'exigence du depot et avoir perdu le grant.
#
# Le cas concret est un changement de TYPE de certificat (Apple Development ->
# Developer ID Application, la voie de repli d'ADR-001 § NC-3) : le Team ID reste
# `56AP2NSB54`, le sceau tient, le build se declare reussi — et l'utilisateur
# recolte une invite TCC au premier lancement, sans que rien ne l'ait signale.


def test_the_requirement_carries_a_certificate_type_discriminant():
    """L'exigence du depot doit etre au moins aussi stricte que celle de TCC."""
    assert "1.2.840.113635.100.6.1.2" in bundle.CODE_REQUIREMENT


def test_the_requirement_does_not_pin_the_individual_certificate():
    """Le CN change a chaque renouvellement : le pinner casserait le lanceur a
    echeance fixe sans rien proteger de plus. C'est l'ecart assume avec le
    `csreq` de TCC, qui le pinne, lui."""
    assert "subject.CN" not in bundle.CODE_REQUIREMENT
    # Aucun nom de certificat, quel qu'il soit : la garde porte sur la
    # CLASSE, pas sur le nom du mainteneur du jour (US-010).
    assert "Apple Development" not in bundle.CODE_REQUIREMENT
    assert "Developer ID" not in bundle.CODE_REQUIREMENT


def test_the_requirement_still_carries_what_it_carried_before():
    """L'amendement AJOUTE une clause, il n'en retire aucune (BUG-010, BUG-013)."""
    assert "anchor apple generic" in bundle.CODE_REQUIREMENT
    assert f'identifier "{bundle.BUNDLE_IDENTIFIER}"' in bundle.CODE_REQUIREMENT
    assert (
        f'certificate leaf[subject.OU]="{bundle.TEAM_IDENTIFIER}"'
        in bundle.CODE_REQUIREMENT
    )


@pytest.mark.skipif(
    not os.path.exists("/usr/bin/codesign"), reason="poste sans `codesign` (C-4)"
)
def test_the_type_discriminant_alone_refuses_an_artifact_that_lacks_it(tmp_path):
    """La clause ajoutee DISCRIMINE, elle n'est pas decorative.

    Opposee seule a un artefact signe ad-hoc — donc sans aucune extension Apple
    sur sa feuille —, elle doit refuser. Sans cette contre-epreuve, une clause
    inerte (OID mal orthographie, syntaxe toleree et sans effet) passerait
    inapercue : l'exigence complete continuerait de rendre rc=0 sur le vrai
    bundle grace a ses AUTRES clauses, et rc!=0 sur l'ad-hoc pour la meme
    raison. Le test complet ne dit donc rien de la clause elle-meme.
    """
    app = _minimal_app(tmp_path)
    signed = subprocess.run(
        ["/usr/bin/codesign", "--force", "--options", "runtime", "-s", "-", str(app)],
        capture_output=True, text=True,
    )
    assert signed.returncode == 0, signed.stderr
    only_the_clause = (
        "certificate leaf[field.1.2.840.113635.100.6.1.2] exists"
    )
    proc = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", f"-R={only_the_clause}", str(app)],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0, (
        "la clause de type ne discrimine rien — elle serait decorative"
    )


@requires_conforming_bundle
def test_the_type_discriminant_alone_accepts_the_real_bundle():
    """L'autre moitie de la contre-epreuve : la clause ne refuse pas TOUT.

    Opposee seule au bundle reel, elle doit passer — sans quoi le build
    deviendrait impossible sur ce poste pour une clause qui ne veut rien dire.
    """
    proc = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict",
         "-R=certificate leaf[field.1.2.840.113635.100.6.1.2] exists",
         "/Applications/thingskit.app"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


@requires_conforming_bundle
def test_the_installed_bundle_satisfies_the_amended_requirement():
    """Un amendement qui rendrait le bundle en place irrecevable serait une
    panne, pas un durcissement."""
    bundle.assert_bundle_satisfies_requirement(
        "/Applications/thingskit.app", "identité du poste"
    )


@requires_conforming_bundle
def test_the_root_seal_does_not_vouch_for_the_shim(tmp_path):
    """INV-002-6 — le sceau de la RACINE ne couvre pas le shim. Grave la mesure.

    ┌──────────────────────────────────────────────────────────────────────┐
    │ SI CE TEST ÉCHOUE, CE N'EST PROBABLEMENT PAS UNE RÉGRESSION.         │
    │ Son assertion centrale est que la vérification de la racine du bundle │
    │ NE DÉTECTE RIEN quand le shim est altéré (rc == 0). C'est délibéré :  │
    │ il fige une NON-COUVERTURE mesurée le 2026-08-21, pas une garantie.   │
    │ Le voir échouer signifie que le shim est désormais COUVERT par le     │
    │ sceau racine — une bonne nouvelle, obtenue par un changement          │
    │ d'agencement du bundle (shim promu exécutable principal, déplacé sous │
    │ `Contents/Resources/`, sceau de ressources élargi).                   │
    │ À FAIRE ALORS : relire INV-002-6 dans `constitution.md` et ADR-002    │
    │ § Decision 5bis, qui affirment tous deux cette non-couverture, et les │
    │ corriger — puis seulement adapter ce test. NE PAS le « réparer » en   │
    │ relâchant l'assertion : c'est exactement ce qu'il existe pour         │
    │ empêcher, à savoir qu'une non-couverture devienne silencieusement une │
    │ couverture sans que personne ne relise l'invariant.                   │
    └──────────────────────────────────────────────────────────────────────┘

    Mesure gravée : un octet altéré dans `Contents/MacOS/thingskit-launch`
    laisse `codesign --verify --strict -R=<exigence>` sur la RACINE en `rc=0`
    (trois offsets distincts) ; la même altération dans
    `Contents/Resources/thingskit` rend `rc=1`, « a sealed resource is missing
    or invalid ». Le shim est un Mach-O imbriqué dans `Contents/MacOS/` sans
    être l'exécutable principal : il est hors du sceau de ressources.

    Aucune sonde ne remplace `codesign` — c'est le vrai vérificateur qui est
    opposé aux vrais artefacts. Le bundle installé n'est JAMAIS touché : tout
    se passe sur une copie en `tmp_path`.
    """
    import shutil as _sh

    def verify(target: Path) -> int:
        return subprocess.run(
            [
                "/usr/bin/codesign", "--verify", "--strict",
                f"-R={bundle.CODE_REQUIREMENT}", str(target),
            ],
            capture_output=True, text=True,
        ).returncode

    def flip_one_byte(target: Path, offset: int) -> None:
        with open(target, "r+b") as fh:
            fh.seek(offset)
            byte = fh.read(1)
            fh.seek(offset)
            fh.write(bytes([byte[0] ^ 0xFF]))

    copy = tmp_path / "thingskit.app"
    _sh.copytree("/Applications/thingskit.app", copy, symlinks=True)
    shim = copy / "Contents" / "MacOS" / bundle.SHIM_NAME
    assert shim.is_file(), f"shim absent du bundle : {shim}"

    # (0) Point de départ : l'artefact intact satisfait l'exigence, racine ET
    #     shim. Sans quoi les assertions suivantes ne diraient rien.
    assert verify(copy) == 0, "la copie intacte devait satisfaire l'exigence"
    assert verify(shim) == 0, "le shim intact devait satisfaire l'exigence (INV-002-6)"

    # (1)+(2) Shim altéré → la RACINE ne détecte rien. C'est la raison d'être
    #         du test (voir l'encadré ci-dessus).
    for offset in (17000, 20000, 33000):
        altered = tmp_path / f"altered-{offset}.app"
        _sh.copytree(copy, altered, symlinks=True)
        flip_one_byte(altered / "Contents" / "MacOS" / bundle.SHIM_NAME, offset)
        assert verify(altered) == 0, (
            f"offset {offset} : la vérification de la RACINE a détecté "
            "l'altération du shim. Ce n'est pas une régression — relire "
            "l'encadré du docstring AVANT de toucher à ce test."
        )
        # (3) Opposée DIRECTEMENT au shim, l'exigence, elle, refuse.
        assert verify(altered / "Contents" / "MacOS" / bundle.SHIM_NAME) != 0, (
            f"offset {offset} : le shim altéré satisfaisait encore l'exigence "
            "— alors plus rien ne le vérifie, ni la racine ni lui-même"
        )

    # (4) Contre-épreuve : la racine détecte bien ce qu'elle SCELLE vraiment.
    #     Sans elle, l'assertion (2) passerait aussi si `verify` était inerte.
    witness = tmp_path / "witness.app"
    _sh.copytree(copy, witness, symlinks=True)
    with open(witness / "Contents" / "Resources" / "thingskit", "a") as fh:
        fh.write("\n# altere\n")
    assert verify(witness) != 0, (
        "la racine n'a pas détecté l'altération d'une ressource SCELLÉE — "
        "`codesign` ou l'exigence est inerte, et l'assertion (2) ne prouve rien"
    )


# ------------------------------------- autonomie : lire le binaire, pas otool
#
# Mesuré le 2026-08-23 sur `/Applications/thingskit.app` : 92 Mach-O, **83
# portant un `LC_RPATH` vers /opt/homebrew**, `/opt/homebrew/lib` **premier
# dans l'ordre de recherche sur les 83**, et `otool -L` en voyait **zéro**.
# `assert_no_package_manager_refs` était donc vert sur un bundle qui viole
# INV-001-1, depuis toujours — parce que `_homebrew_refs` interrogeait
# `otool -L`, qui n'imprime que LC_ID_DYLIB et LC_LOAD_DYLIB.
#
# Le correctif du 2026-08-18 avait fermé la moitié « exécutable seul » (le
# contrôle balaie tous les Mach-O) et laissé l'autre : chaque Mach-O était
# toujours regardé à travers un outil qui n'en voit qu'une partie.


def _tiny_macho(dest: Path, arch: str = "arm64") -> Path:
    src = dest.with_suffix(".c")
    src.write_text("int main(void){return 0;}\n")
    # -headerpad_max_install_names : sans lui, `install_name_tool -add_rpath`
    # refuse ("changes to the load commands do not fit") sur un binaire aussi
    # petit. C'est une contrainte de la FIXTURE, pas du code sous test.
    subprocess.run(["/usr/bin/cc", "-arch", arch, "-headerpad_max_install_names",
                    "-o", str(dest), str(src)], check=True, capture_output=True)
    return dest


def _has_cc() -> bool:
    return Path("/usr/bin/cc").exists() and Path("/usr/bin/install_name_tool").exists()


requires_cc = pytest.mark.skipif(not _has_cc(), reason="pas de /usr/bin/cc sur ce poste")


def _rpaths(path: Path) -> list[str]:
    """Oracle indépendant : les LC_RPATH tels que `otool -l` les imprime."""
    out = subprocess.run(["/usr/bin/otool", "-l", str(path)],
                         capture_output=True, text=True).stdout
    lines, found, seen_rpath = out.splitlines(), [], False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("cmd "):
            seen_rpath = stripped.split()[1] == "LC_RPATH"
        elif seen_rpath and stripped.startswith("path "):
            found.append(stripped[5:].rsplit(" (offset", 1)[0])
    return found


@requires_cc
def test_the_autonomy_probe_sees_an_rpath_that_otool_L_cannot(tmp_path):
    """Le défaut, reproduit sur un vrai binaire construit pour l'occasion."""
    exe = _tiny_macho(tmp_path / "probe")
    subprocess.run(["/usr/bin/install_name_tool", "-add_rpath",
                    "/opt/homebrew/lib", str(exe)], check=True, capture_output=True)

    otool_L = subprocess.run(["/usr/bin/otool", "-L", str(exe)],
                             capture_output=True, text=True).stdout
    assert "/opt/homebrew" not in otool_L, "c'est le défaut : otool -L est aveugle"
    assert "/opt/homebrew/lib" in _rpaths(exe), "l'oracle, lui, la voit"

    assert bundle._homebrew_load_commands(exe) == [("LC_RPATH", "/opt/homebrew/lib")]
    # ...et la sonde des dylibs, elle, reste vide : un rpath ne se réécrit pas
    # par `-change`, il se supprime. Les deux surfaces sont distinctes.
    assert bundle._homebrew_refs(exe) == []


@requires_cc
def test_the_build_gate_refuses_a_bundle_carrying_such_an_rpath(tmp_path):
    """Bout en bout : INV-001-1 opposé à un arbre réel, pas à une sonde injectée."""
    app = tmp_path / "thingskit.app"
    (app / "Contents" / "Frameworks").mkdir(parents=True)
    exe = _tiny_macho(app / "Contents" / "Frameworks" / "libx")
    subprocess.run(["/usr/bin/install_name_tool", "-add_rpath",
                    "/opt/homebrew/lib", str(exe)], check=True, capture_output=True)

    with pytest.raises(bundle.BundleError) as exc:
        bundle.assert_no_package_manager_refs(app)
    assert "/opt/homebrew/lib" in str(exc.value)
    assert "LC_RPATH" in str(exc.value), "le motif nomme la commande, pas juste le chemin"


@requires_cc
def test_the_rpath_sweep_removes_them_and_leaves_the_others(tmp_path):
    """Ce que le build doit faire du résidu : le supprimer, pas l'accepter."""
    exe = _tiny_macho(tmp_path / "libx")
    for rpath in ("/opt/homebrew/lib", "@loader_path", "@executable_path/../Frameworks"):
        subprocess.run(["/usr/bin/install_name_tool", "-add_rpath", rpath, str(exe)],
                       check=True, capture_output=True)
    assert "/opt/homebrew/lib" in _rpaths(exe)

    removed = bundle._strip_package_manager_rpaths(tmp_path)

    assert removed == [(exe, "/opt/homebrew/lib")], removed
    assert _rpaths(exe) == ["@loader_path", "@executable_path/../Frameworks"], (
        "les rpaths légitimes du bundle doivent survivre à la passe")
    assert bundle._homebrew_load_commands(exe) == []


@requires_cc
def test_the_sweep_is_idempotent_on_an_already_clean_binary(tmp_path):
    """Le build est idempotent (C-3) : une seconde passe ne doit pas échouer."""
    exe = _tiny_macho(tmp_path / "libx")
    subprocess.run(["/usr/bin/install_name_tool", "-add_rpath", "@loader_path", str(exe)],
                   check=True, capture_output=True)
    assert bundle._strip_package_manager_rpaths(tmp_path) == []
    assert _rpaths(exe) == ["@loader_path"]


@requires_cc
def test_a_duplicated_rpath_is_removed_down_to_the_last_one(tmp_path):
    """`-delete_rpath` n'en retire qu'une occurrence par appel.

    Un Mach-O peut porter deux fois le même rpath (mesuré comme régime normal
    d'une reconstruction, cf. `_try_add_rpath`). Retirer « le » rpath une fois
    laisserait le second en place, et le contrôle qui suit serait rouge sur un
    bundle que la passe croit avoir nettoyé.
    """
    exe = _tiny_macho(tmp_path / "libx")
    for _ in range(2):
        subprocess.run(["/usr/bin/install_name_tool", "-add_rpath",
                        "/opt/homebrew/lib", str(exe)], capture_output=True)
    if _rpaths(exe).count("/opt/homebrew/lib") < 2:
        pytest.skip("ce poste refuse le doublon de rpath ; le cas n'est pas atteignable")
    bundle._strip_package_manager_rpaths(tmp_path)
    assert "/opt/homebrew/lib" not in _rpaths(exe)


def test_the_sweep_runs_before_the_gate_and_the_gate_before_the_signature():
    """L'ordre est le fond du correctif, pas un détail : `install_name_tool`
    invalide la signature, donc nettoyer APRÈS avoir signé rendrait un bundle
    tué par le noyau (SIGKILL, rc=137)."""
    import ast, inspect

    body = ast.parse(inspect.getsource(bundle.build))
    calls = [n.func.id for n in ast.walk(body)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    for name in ("_strip_package_manager_rpaths", "assert_no_package_manager_refs",
                 "_sign_everything"):
        assert name in calls, f"{name} n'est pas appelé par build()"
    assert (calls.index("_strip_package_manager_rpaths")
            < calls.index("assert_no_package_manager_refs")
            < calls.index("_sign_everything")), calls


def test_the_macho_reader_is_the_same_file_on_both_sides():
    """Une copie vendue qui dérive est pire que pas de copie du tout.

    `.claude/scripts/macho_loadcmds.py` est la copie que la garde de session
    charge sur un poste où ce dépôt n'est pas cloné. Même idiome que
    `verify_embedded_copy` pour `bin/thingskit` -> bundle.
    """
    source = Path(bundle.__file__).resolve().parent / "macho.py"
    vendored = Path.home() / "basic-memory" / ".claude" / "scripts" / "macho_loadcmds.py"
    if not vendored.exists():
        pytest.skip("vault absent de ce poste")
    assert source.read_bytes() == vendored.read_bytes(), (
        f"{vendored} diverge de {source} — recopier, ne pas éditer sur place")


def test_no_argv_in_the_build_starts_with_a_bare_binary_name():
    """`otool` était invoqué par nom nu : troisième instance du même défaut,
    après IOREG dans la sonde et OTOOL dans la garde. Un `otool` Homebrew en
    tête de PATH aurait mesuré la dépendance même qu'il devait détecter.

    La liste des noms à vérifier est DÉRIVÉE du module, jamais tenue à la main.
    """
    import ast

    tree = ast.parse(Path(bundle.__file__).read_text())
    consts = {n.targets[0].id: ast.unparse(n.value)
              for n in tree.body
              if isinstance(n, ast.Assign) and len(n.targets) == 1
              and isinstance(n.targets[0], ast.Name)}
    params = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        args = fn.args
        positional = args.posonlyargs + args.args
        for arg, default in zip(positional[len(positional) - len(args.defaults):],
                                args.defaults):
            params[arg.arg] = ast.unparse(default)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is not None:
                params[arg.arg] = ast.unparse(default)

    # Un argv n'est pas toujours l'argument direct d'un appel : `codesign_command`
    # construit sa liste, l'affecte, et la rend. Balayer les seuls arguments
    # d'appel laissait ce site — le plus sensible, c'est la SIGNATURE — hors du
    # contrôle. La règle porte donc sur toute liste littérale dont un élément
    # ressemble à un drapeau.
    bare, inspected = [], []
    candidates = [n for n in ast.walk(tree) if isinstance(n, ast.List) and n.elts]
    for arg in candidates:
        if not any(isinstance(e, ast.Constant) and isinstance(e.value, str)
                   and e.value.startswith("-") for e in arg.elts):
            continue
        head = arg.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            # `cmd += ["-s", identity, target]` prolonge un argv, il n'en
            # commence pas un : sa tête EST un drapeau.
            if head.value.startswith("-"):
                continue
            inspected.append(head.value)
            if not head.value.startswith("/"):
                bare.append(f"ligne {arg.lineno}: {head.value}")
        elif isinstance(head, ast.Name):
            value = consts.get(head.id) or params.get(head.id)
            value = consts.get(value, value)
            inspected.append(f"{head.id}={value}")
            if value is None or not value.startswith(("'/", '"/')):
                bare.append(f"ligne {arg.lineno}: {head.id}={value}")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in node.args:
            if not (isinstance(arg, ast.List) and arg.elts):
                continue
            head = arg.elts[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                inspected.append(head.value)
                if not head.value.startswith("/"):
                    bare.append(f"ligne {node.lineno}: {head.value}")
            elif isinstance(head, ast.Name):
                # Un nom peut être une constante de module OU un paramètre dont
                # le défaut est cette constante (`codesign: str = CODESIGN`).
                # Ne résoudre que la première forme rendait 2 faux positifs.
                value = consts.get(head.id) or params.get(head.id)
                value = consts.get(value, value)
                inspected.append(f"{head.id}={value}")
                if value is None or not value.startswith(("'/", '"/')):
                    bare.append(f"ligne {node.lineno}: {head.id}={value}")
    assert bare == [], bare
    assert inspected, "aucun argv inspecté — un balayage vide ne prouve rien"


# ------------------------- autonomie, deuxième route : le démarrage de Python
#
# Le nettoyage des rpaths rend le bundle autonome au niveau de dyld. Il ne dit
# rien de la couche au-dessus. Mesuré le 2026-08-23 sur le bundle
# FRAÎCHEMENT RECONSTRUIT, par le chemin EXACT (le shim pose `-I`) :
#
#   sys.path hors bundle : ['/opt/homebrew/lib/python3.14/site-packages',
#                           '<un chemin de projet du poste>']
#   modules chargés depuis /opt/homebrew : ['_distutils_hack']
#
# La cause est `lib/python3.14/sitecustomize.py`, écrit par Homebrew et copié
# tel quel dans le bundle. Sa fin s'exécute SANS condition et fait
# `site.addsitedir('/opt/homebrew/lib/python3.14/site-packages')` ; les trois
# fichiers `.pth` qui s'y trouvent sont alors exécutés à chaque démarrage,
# **dans le processus qui porte le consentement TCC**. `-I` n'y change rien :
# il implique `-s` (pas de site utilisateur) mais pas `-S`, donc `site.py`
# tourne et importe toujours `sitecustomize`.
#
# C'est la même intention que `_prune_dangling_symlinks`, dont le motif écrit
# est déjà « le lien réintroduirait dans le sys.path du bundle les paquets
# d'une installation Homebrew dont on cherche précisément à se détacher » : la
# route par le lien était fermée, celle par le hook de démarrage restait
# ouverte.


def test_the_package_manager_startup_hook_is_removed_from_the_bundle(tmp_path):
    lib = tmp_path / "Contents/Frameworks/Python.framework/Versions/3.14/lib/python3.14"
    lib.mkdir(parents=True)
    hook = lib / "sitecustomize.py"
    hook.write_text("import site\nsite.addsitedir('/opt/homebrew/lib/python3.14/site-packages')\n")
    keeper = lib / "sitecustomize_unrelated.py"
    keeper.write_text("# rien à voir\n")

    removed = bundle._strip_package_manager_startup_hooks(tmp_path)

    assert removed == [hook], removed
    assert not hook.exists()
    assert keeper.exists(), "seuls les hooks de DÉMARRAGE sont visés"


def test_a_startup_hook_that_does_not_name_the_package_manager_is_kept(tmp_path):
    """La règle porte sur le gestionnaire de paquets, pas sur le nom du fichier."""
    lib = tmp_path / "lib/python3.14"
    lib.mkdir(parents=True)
    hook = lib / "sitecustomize.py"
    hook.write_text("# hook local, sans dépendance externe\n")
    assert bundle._strip_package_manager_startup_hooks(tmp_path) == []
    assert hook.exists()


def test_the_startup_hook_sweep_covers_usercustomize_and_pth_too():
    """Les trois routes que `site.py` exécute au démarrage, pas la seule vue."""
    names = bundle.PACKAGE_MANAGER_STARTUP_HOOK_NAMES
    assert "sitecustomize.py" in names
    assert "usercustomize.py" in names
    assert any(n.endswith(".pth") or n == "*.pth" for n in names), names


def test_the_startup_hook_sweep_runs_before_the_signature():
    """Retirer un fichier après signature invaliderait le sceau."""
    import ast, inspect

    calls = [n.func.id for n in ast.walk(ast.parse(inspect.getsource(bundle.build)))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "_strip_package_manager_startup_hooks" in calls
    assert (calls.index("_strip_package_manager_startup_hooks")
            < calls.index("_sign_everything")), calls


@requires_conforming_bundle
def test_the_installed_interpreter_loads_nothing_from_outside_the_bundle():
    """La mesure de bout en bout, par le chemin EXACT que le shim emprunte.

    Une garde qui lirait la seule table des commandes de chargement resterait
    verte pendant que le processus importe du code de /opt/homebrew : c'est
    précisément le motif « l'autonomie ne se déduit pas de la seule inspection
    de l'exécutable » de la constitution, appliqué une couche plus haut.
    """
    app = Path(bundle.INSTALL_PATH)
    probe = (
        "import sys\n"
        "outside = [p for p in sys.path if p and not p.startswith(sys.prefix)"
        " and not p.startswith(str(sys.base_prefix))]\n"
        "foreign = sorted({m for m, v in sys.modules.items()"
        " if getattr(v, '__file__', None) and not v.__file__.startswith(sys.prefix)"
        " and '/Applications/thingskit.app' not in v.__file__})\n"
        "print(repr((outside, foreign)))\n"
    )
    out = subprocess.run(
        [str(app / "Contents" / "MacOS" / "thingskit"), "-I", "-c", probe],
        capture_output=True, text=True, check=True).stdout
    outside, foreign = eval(out)
    assert outside == [], f"sys.path sort du bundle : {outside}"
    assert foreign == [], f"modules chargés hors du bundle : {foreign}"


def test_no_external_call_in_the_build_is_unbounded():
    """Un outil bloqué fige le build sans diagnostic, indéfiniment.

    Le dépôt avait déjà borné une invocation — `SHIM_CODESIGN_TIMEOUT_SECONDS`,
    posée sur la vérification de sceau du shim — et laissé les autres. Balayé le
    2026-08-23 : **7 `subprocess.run` sans échéance**, dont `codesign`,
    `security find-identity`, `cc` et `install_name_tool`. Le plus lent d'entre
    eux coûte 0,33 s sur ce poste.

    L'un des sept avait été introduit dans cette passe même, par le contrôle
    d'autonomie de l'interpréteur : borner ce qu'on ajoute est le premier
    endroit où la règle s'applique.
    """
    import ast

    tree = ast.parse(Path(bundle.__file__).read_text())
    unbounded = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and "subprocess.run" in ast.unparse(node.func)):
            continue
        kwargs = {k.arg for k in node.keywords}
        # `**kw` peut porter l'échéance : c'est le cas de `_run`, qui la pose
        # lui-même par défaut. Ne pas l'exclure rendrait la règle inapplicable.
        if "timeout" in kwargs or None in kwargs:
            continue
        unbounded.append(f"ligne {node.lineno}: {ast.unparse(node.func)}")
    assert unbounded == [], unbounded

    # Et l'échéance par défaut de `_run` n'est pas None : rien en production ne
    # lui passe `timeout=`, donc un défaut à None laisserait le chemin réel
    # non borné tout en satisfaisant la règle ci-dessus.
    import inspect
    signature = inspect.signature(bundle._run)
    assert signature.parameters["timeout"].default is not None
    assert isinstance(signature.parameters["timeout"].default, (int, float))


# ------------------------------------- US-010 (suite) : portée et allowlists
#
# Trois défauts relevés en review, chacun mesuré :
#
# 1. PORTÉE. Le nom du test disait `anywhere_in_the_repository` ; le balayage
#    ne lisait que les `.py` de `build/` et `tests/` — ni `bin/thingskit`, ni
#    `constitution.md`, ni `pyproject.toml`. Une affirmation de couverture plus
#    large que la garde.
# 2. CLASSE. Le balayage voit `Apple Development: … (…)` et NE VOIT PAS un nom
#    légal nu (`AUTHOR = "Prenom NOM"`), qui est pourtant ce que le commit
#    précédent venait de retirer de quatre fichiers. Rien n'empêchait la
#    régression sous une suite verte.
# 3. IDENTIFIANT NU. L'assertion retirée couvrait un identifiant écrit SEUL ;
#    le remplacement ne le voit que serti dans la forme complète.
#
# Les allowlists, enfin, vivent dans un fichier que le balayage lit : y
# inscrire une valeur réelle la rendait simultanément présente et autorisée.

def test_the_sweep_covers_the_whole_tracked_tree():
    """La portée est ce que le dépôt PUBLIE, pas deux répertoires de `.py`."""
    swept = {str(path.relative_to(bundle.REPO_ROOT)) for path, _ in _swept_sources()}
    assert {"bin/thingskit", "constitution.md", "pyproject.toml",
            "build/bundle.py", "tests/test_bundle.py"} <= swept
    assert len(swept) >= 30
    assert _undecodable_tracked_files() == []


def test_no_personal_value_is_written_in_a_fixture():
    """Compte résiduel de noms légaux cités en clair = 0."""
    assert _leaked_personal_names() == []


def test_the_personal_name_sweep_actually_sees_a_legal_name():
    """Valeur SYNTHÉTIQUE et COMPOSÉE à l'exécution — l'écrire d'un seul tenant
    la ferait détecter dans ce fichier-ci, et la seule issue serait de
    l'allowlister, c'est-à-dire de rendre la contre-épreuve inopérante."""
    name = "Je" + "an DUPONT"
    source = f'AUTHOR = "{name}"\n'
    assert _leaked_personal_names([(Path("fixture.py"), source)]) == [
        f"fixture.py : valeur personnelle {name!r}"
    ]


def test_the_personal_name_sweep_leaves_prose_and_docstrings_alone():
    """La classe est la FIXTURE, pas la prose.

    Ce dépôt capitalise pour appuyer (`Le CLI`, `Chemin ABSOLU`, `Ne JAMAIS`).
    Mesuré : 38 formes distinctes dans les commentaires et docstrings de
    `build/`, `tests/` et `bin/thingskit`, contre 3 dans les seuls littéraux de
    données. Les refuser aurait imposé une allowlist de prose, qui grossit à
    chaque paragraphe — donc une garde désactivée dans le mois.

    L'épreuve porte sur un fichier RÉEL du dépôt plutôt que sur une prose
    fabriquée : l'écrire ici la ferait détecter dans ce fichier-ci.
    """
    source = (bundle.REPO_ROOT / "tests" / "test_write_wait.py").read_text(
        encoding="utf-8")
    assert _PERSONAL_NAME_RE.findall(source), "la prose de ce fichier en porte"
    assert _leaked_personal_names([(Path("test_write_wait.py"), source)]) == []


def test_no_bare_apple_identifier_is_written_in_the_repository():
    """Compte résiduel d'identifiants Apple nus non déclarés = 0.

    C'est ce que couvrait l'assertion nominative retirée au commit précédent :
    le balayage par la forme complète est plus large en général et strictement
    plus étroit sur ce cas — il ne voit l'identifiant que serti dans
    `Apple Development: … (…)` ou dans un sujet DN.
    """
    assert _leaked_bare_identifiers() == []


def test_the_bare_identifier_sweep_sees_an_identifier_written_alone():
    """La forme exacte que l'assertion retirée couvrait, et que le balayage
    par la forme complète manque."""
    identifier = "7Q2WE" + "9081T"
    source = f'SIGNING_IDENTITY = "{identifier}"\n'
    assert _leaked_bare_identifiers([(Path("fixture.py"), source)]) == [
        f"fixture.py : identifiant nu {identifier!r}"
    ]


def test_the_bare_identifier_sweep_does_not_flag_a_fingerprint_or_a_uuid():
    """Contre-épreuve : ni une empreinte de 40 caractères, ni un UUID Things de
    22, ne sont des identifiants d'équipe."""
    source = f'A = "{"F" * 40}"\nB = "{"R" * 22}"\nC = "XXXXXXXXXX"\n'
    assert _leaked_bare_identifiers([(Path("fixture.py"), source)]) == []


# --- les allowlists ne se protègent pas elles-mêmes ------------------------

def test_every_synthetic_identifier_is_manifestly_synthetic():
    """Chaque entrée porte une marque — répétition, séquence, ou mot déclaré.

    Ce que cela vaut, chiffré : 0,67 % des identifiants de 10 caractères
    tirés au hasard passeraient (200 000 tirages, graine 20260826, mesuré le
    2026-08-26). La garde est donc forte, pas absolue — elle refuse 99,33 %
    d'une valeur réelle choisie sans intention de tromper.
    """
    assert [t for t in _SYNTHETIC_IDENTIFIERS if not _is_manifestly_synthetic(t)] == []
    assert [t for t in _ALLOWED_FINGERPRINTS if not _is_manifestly_synthetic(t)] == []


def test_the_synthetic_shape_rule_refuses_a_real_looking_identifier():
    """Contre-épreuve : sans elle, la règle de forme pourrait tout accepter."""
    assert not _is_manifestly_synthetic("7Q2WE" + "9081T")
    assert not _is_manifestly_synthetic("Je" + "an DUPONT")


def test_every_admitted_real_identifier_carries_its_rationale():
    """Les seuls identifiants RÉELS admis sont nommés un par un, avec le motif
    qui les rend publics. Un ajout muet est donc impossible : il faut écrire
    pourquoi."""
    assert set(_REAL_IDENTIFIERS_ADMITTED) <= _ALLOWED_IDENTIFIERS
    for token, reason in _REAL_IDENTIFIERS_ADMITTED.items():
        assert len(reason) >= 20, token


def test_the_allowlists_cannot_grow_unnoticed():
    """Rend VISIBLE tout ajout : les quatre jeux sont épinglés par empreinte.

    Sans cela, inscrire une valeur réelle dans une allowlist la rendait
    simultanément présente dans le dépôt et autorisée par la garde qui balaie
    ce fichier — invisible par construction. L'empreinte force le diff à
    passer par une ligne dont la raison d'être est de le signaler.
    """
    assert _allowlist_digest() == _ALLOWLIST_DIGEST


_ENVIRONMENT_ARTEFACTS = (".venv/", ".code-review-graph/", ".ruff_cache/",
                          "dist/", ".pytest_cache/", "__pycache__/")


def test_no_environment_artefact_is_tracked():
    """Le dépôt devient public : un `git add -A` ne doit pas pouvoir y verser
    l'environnement virtuel du poste ni la base du graphe de code.

    La cause se ferme dans `.gitignore` ; ce test est ce qui empêche la
    correction de se défaire en silence — l'ignore d'un répertoire ne dit rien
    de ce qui est DÉJÀ suivi.
    """
    tracked = [str(p.relative_to(bundle.REPO_ROOT)) for p in _tracked_paths()]
    assert [t for t in tracked
            if any(t == a.rstrip("/") or t.startswith(a) or f"/{a}" in t
                   for a in _ENVIRONMENT_ARTEFACTS)] == []
    assert [t for t in tracked if t.endswith(".egg-info")
            or ".egg-info/" in t] == []


# --- troisième tour de review (2026-08-26) ---------------------------------
#
# Deux jeux sur quatre n'avaient AUCUNE règle de forme — précisément les deux
# qui gouvernent la classe « valeur personnelle » ajoutée au lot précédent —
# alors que la constitution écrivait « les allowlists sont elles-mêmes
# gardées ». Et `[A-Z]` étant ASCII strict, un prénom accentué passait à
# travers, sur un dépôt francophone.

def test_the_personal_name_sweep_sees_an_accented_first_name():
    """`Émilie DUPONT` : `[A-Z]` est ASCII strict, l'accent passait à travers.

    Gratuit à fermer — mesuré 0 occurrence nouvelle sur l'arbre suivi.
    """
    name = "Émi" + "lie DUPONT"
    source = f'AUTHOR = "{name}"\n'
    assert _leaked_personal_names([(Path("fixture.py"), source)]) == [
        f"fixture.py : valeur personnelle {name!r}"
    ]


def test_every_fictional_name_is_manifestly_fictional():
    """Les deux jeux de NOMS sont gardés comme le jeu d'identifiants l'est.

    Sans cette règle, inscrire un nom réel dans `_FICTIONAL_NAMES` ou dans
    `_ALLOWED_PERSONAL_NAMES` le rendait simultanément présent dans le dépôt et
    autorisé par la garde qui balaie ce fichier.
    """
    assert [n for n in _FICTIONAL_NAMES
            if not _is_manifestly_fictional_name(n)] == []
    assert [n for n in _ALLOWED_PERSONAL_NAMES
            if not _is_manifestly_fictional_name(n)] == []


def test_the_fictional_name_rule_refuses_a_real_looking_name():
    """Contre-épreuve : sans elle, la règle pourrait tout accepter.

    Valeurs COMPOSÉES à l'exécution, même raison qu'ailleurs dans ce fichier.
    """
    assert not _is_manifestly_fictional_name("Je" + "an DUPONT")
    assert not _is_manifestly_fictional_name("Émi" + "lie DUPONT")
    assert not _is_manifestly_fictional_name("Du" + "pont")


def test_the_digest_covers_the_rationales_of_the_admitted_real_identifiers():
    """Les MOTIFS entraient dans la garde de contenu mais pas dans l'empreinte.

    Seules les clés y étaient : réécrire le motif qui justifie un identifiant
    réel — ou le vider — ne touchait pas l'empreinte, donc ne se signalait
    nulle part.
    """
    before = _allowlist_digest()
    original = dict(_REAL_IDENTIFIERS_ADMITTED)
    key = next(iter(_REAL_IDENTIFIERS_ADMITTED))
    try:
        _REAL_IDENTIFIERS_ADMITTED[key] = "motif réécrit " + "x" * 20
        assert _allowlist_digest() != before
    finally:
        _REAL_IDENTIFIERS_ADMITTED.clear()
        _REAL_IDENTIFIERS_ADMITTED.update(original)
    assert _allowlist_digest() == before


def test_the_digest_covers_the_two_vocabularies_that_open_the_name_rule():
    """`_PLACEHOLDER_WORDS` et `_CANONICAL_TEST_PERSONAS` sont la porte de la
    règle de forme sur les noms : y ajouter un mot autorise un nom de plus.

    Ils entrent donc dans l'empreinte, au même titre que les jeux qu'ils
    gouvernent.
    """
    before = _allowlist_digest()
    _CANONICAL_TEST_PERSONAS.add("JEANNOT")
    try:
        assert _allowlist_digest() != before
    finally:
        _CANONICAL_TEST_PERSONAS.discard("JEANNOT")
    _PLACEHOLDER_WORDS.add("MACHIN")
    try:
        assert _allowlist_digest() != before
    finally:
        _PLACEHOLDER_WORDS.discard("MACHIN")
    assert _allowlist_digest() == before
