"""ADR-003 — l'identite de code attendue vient d'une origine de configuration.

Ce que ce fichier tient, invariant par invariant :

  INV-003-1  aucune configuration absente, vide, illisible ou malformee ne
             produit un build ni une execution ;
  INV-003-2  aucune configuration ne retire une clause du plancher de forme ;
  INV-003-3  le fichier d'identite est ecrit AVANT la signature ;
  INV-003-5  les deux porteurs — exigence compilee dans le shim et fichier
             d'identite scelle — sortent de la MEME lecture ;
  INV-003-7  aucun chemin d'execution du CLI n'ouvre la configuration de
             construction ;
  INV-003-8  aucun chemin d'installation n'est ecrit en dur dans une source
             publiee.

Le versant CLI de INV-003-1 (les six formes degenerees opposees au chemin de
lecture) vit dans `tests/test_code_identity.py`, aupres de la garde qu'il
eprouve. Aucun test d'ici ne depend du bundle installe ni de Things (C-4).
"""

import ast
import inspect
import subprocess
import textwrap
from pathlib import Path

import pytest

from build import bundle
from conftest import thingskit_cli as cli

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "thingskit"

# Valeurs de fixture. Les equipes sont celles des jeux synthetiques deja
# declares dans `tests/test_bundle.py` : le balayage anti-fuite couvre CE
# fichier aussi, et une valeur inventee au fil de la plume y echouerait.
FAKE_ID = "app.example.thingskit"
FAKE_TEAM = "TEAM000001"
FAKE_PATH = "/tmp/thingskit-fixture/thingskit.app"


def _config(tmp_path, identifier=FAKE_ID, team=FAKE_TEAM, path=FAKE_PATH,
            name="identity.conf"):
    """Ecrit une configuration de construction et rend son chemin."""
    dest = tmp_path / name
    lines = []
    if identifier is not None:
        lines.append(f"bundle_identifier = {identifier}")
    if team is not None:
        lines.append(f"team_identifier = {team}")
    if path is not None:
        lines.append(f"install_path = {path}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


# --------------------------------------------------- origine unique, sous build/


def test_the_configuration_origin_lives_under_build_and_is_not_versioned():
    """Une seule origine, sous `build/`, et le depot public ne la porte pas.

    « Pas de valeur par defaut versionnee » (ADR-003) n'est pas une intention :
    un fichier suivi la reintroduirait dans un depot public, ce que la garde
    anti-fuite d'US-010 interdit par ailleurs.
    """
    origin = Path(bundle.BUILD_IDENTITY_CONFIG)
    assert origin.parent == REPO_ROOT / "build"
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch",
         str(origin.relative_to(REPO_ROOT))],
        capture_output=True, text=True,
    )
    assert tracked.returncode != 0, "la configuration ne doit pas etre suivie"


def test_the_local_configuration_is_ignored_by_git():
    """Ignoree explicitement, pas seulement absente : un `git add -A` du
    mainteneur publierait autrement son identifiant d'equipe."""
    origin = Path(bundle.BUILD_IDENTITY_CONFIG)
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-v",
         str(origin.relative_to(REPO_ROOT))],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ------------------------------------------------------ lecture de la configuration


def test_a_complete_configuration_is_read(tmp_path):
    fields = bundle.read_build_identity(_config(tmp_path))
    assert fields == {
        "bundle_identifier": FAKE_ID,
        "team_identifier": FAKE_TEAM,
        "install_path": FAKE_PATH,
    }


def test_comments_and_blank_lines_are_ignored(tmp_path):
    dest = tmp_path / "identity.conf"
    dest.write_text(
        "# une configuration locale\n"
        "\n"
        f"bundle_identifier   =   {FAKE_ID}\n"
        f"   team_identifier={FAKE_TEAM}\n"
        "\n"
        f"install_path = {FAKE_PATH}\n"
        "# fin\n",
        encoding="utf-8",
    )
    fields = bundle.read_build_identity(dest)
    assert fields["team_identifier"] == FAKE_TEAM


def test_an_absent_configuration_is_a_named_refusal(tmp_path):
    """INV-003-1, versant build : le mode d'echec « configuration manquante »
    doit se diagnostiquer sans lire le code."""
    with pytest.raises(bundle.BundleError) as exc:
        bundle.read_build_identity(tmp_path / "absente.conf")
    message = str(exc.value)
    assert "absente.conf" in message
    for field in ("bundle_identifier", "team_identifier", "install_path"):
        assert field in message, "le refus doit nommer les champs attendus"


def test_an_empty_configuration_is_a_named_refusal(tmp_path):
    dest = tmp_path / "identity.conf"
    dest.write_text("", encoding="utf-8")
    with pytest.raises(bundle.BundleError):
        bundle.read_build_identity(dest)


def test_a_configuration_of_comments_only_is_a_refusal(tmp_path):
    dest = tmp_path / "identity.conf"
    dest.write_text("# rien que des commentaires\n#\n", encoding="utf-8")
    with pytest.raises(bundle.BundleError):
        bundle.read_build_identity(dest)


@pytest.mark.parametrize(
    "missing", ["bundle_identifier", "team_identifier", "install_path"]
)
def test_a_missing_field_is_named_in_the_refusal(tmp_path, missing):
    kwargs = {"identifier": FAKE_ID, "team": FAKE_TEAM, "path": FAKE_PATH}
    kwargs[{"bundle_identifier": "identifier", "team_identifier": "team",
            "install_path": "path"}[missing]] = None
    with pytest.raises(bundle.BundleError) as exc:
        bundle.read_build_identity(_config(tmp_path, **kwargs))
    assert missing in str(exc.value)


def test_a_configuration_that_is_a_directory_is_a_refusal(tmp_path):
    """Illisible n'est pas absent, et les deux valent refus."""
    (tmp_path / "identity.conf").mkdir()
    with pytest.raises(bundle.BundleError):
        bundle.read_build_identity(tmp_path / "identity.conf")


def test_an_undecodable_configuration_is_a_refusal(tmp_path):
    dest = tmp_path / "identity.conf"
    dest.write_bytes(b"bundle_identifier = \xff\xfe\n")
    with pytest.raises(bundle.BundleError):
        bundle.read_build_identity(dest)


# ------------------------------------------------- configurations hostiles


@pytest.mark.parametrize("line", [
    "bundle_identifier",                      # aucun separateur
    "= app.example.thingskit",                # aucune cle
    "bundle identifier = app.example",        # cle hors forme
    "BUNDLE_IDENTIFIER = app.example",        # casse : cle inconnue
    "signing_identity = app.example",         # cle inconnue
])
def test_a_malformed_or_unknown_line_is_refused(tmp_path, line):
    dest = tmp_path / "identity.conf"
    dest.write_text(
        f"{line}\n"
        f"bundle_identifier = {FAKE_ID}\n"
        f"team_identifier = {FAKE_TEAM}\n"
        f"install_path = {FAKE_PATH}\n",
        encoding="utf-8",
    )
    with pytest.raises(bundle.BundleError):
        bundle.read_build_identity(dest)


def test_a_duplicated_field_is_refused(tmp_path):
    """Deux valeurs pour un champ : « la derniere gagne » est un choix
    implicite, et ce depot n'en fait aucun."""
    dest = tmp_path / "identity.conf"
    dest.write_text(
        f"bundle_identifier = {FAKE_ID}\n"
        "bundle_identifier = app.autre.thingskit\n"
        f"team_identifier = {FAKE_TEAM}\n"
        f"install_path = {FAKE_PATH}\n",
        encoding="utf-8",
    )
    with pytest.raises(bundle.BundleError):
        bundle.read_build_identity(dest)


HOSTILE_IDENTIFIERS = [
    "",                                        # vide
    "   ",                                     # blanc
    'app.evil" or true',                       # clause injectee dans l'exigence
    'app.evil"',                               # guillemet seul
    "app evil",                                # espace
    "app.evil\x1b[2K",                         # sequence de controle
    "app.evil\x00",                            # octet nul
    "app.evil\u202e",                         # inversion du sens de lecture
    "-app.evil",                               # ne commence pas par un alphanum
    "app." + "e" * 200,                        # longueur excessive
    "app/evil",                                # separateur de chemin
]

HOSTILE_TEAMS = [
    "",
    "   ",
    "team000001",                              # minuscules
    "TEAM00001",                               # neuf caracteres
    "TEAM0000012",                             # onze caracteres
    'X" or true',
    "TEAM 00001",
    "TEAM00000\x1b",
]

HOSTILE_PATHS = [
    "",
    "   ",
    "thingskit.app",                           # relatif
    "/tmp/x.app\"; rm -rf /",                  # injection dans le lanceur sh
    "/tmp/$(id).app",                          # substitution de commande
    "/tmp/`id`.app",
    "/tmp/x.app\x1b[2K",
    "/tmp/x",                                  # pas un bundle
    "/tmp/x.app\x00",
]


@pytest.mark.parametrize("value", HOSTILE_IDENTIFIERS)
def test_a_hostile_bundle_identifier_is_refused(tmp_path, value):
    with pytest.raises(bundle.BundleError):
        bundle.read_build_identity(_config(tmp_path, identifier=value))


@pytest.mark.parametrize("value", HOSTILE_TEAMS)
def test_a_hostile_team_identifier_is_refused(tmp_path, value):
    with pytest.raises(bundle.BundleError):
        bundle.read_build_identity(_config(tmp_path, team=value))


@pytest.mark.parametrize("value", HOSTILE_PATHS)
def test_a_hostile_install_path_is_refused(tmp_path, value):
    with pytest.raises(bundle.BundleError):
        bundle.read_build_identity(_config(tmp_path, path=value))


def test_a_value_carrying_a_newline_cannot_smuggle_a_second_field(tmp_path):
    """Le champ suivant ne doit pas pouvoir etre pose depuis une valeur."""
    dest = tmp_path / "identity.conf"
    dest.write_text(
        f"bundle_identifier = {FAKE_ID}\\nteam_identifier = OLDTEAM001\n"
        f"team_identifier = {FAKE_TEAM}\n"
        f"install_path = {FAKE_PATH}\n",
        encoding="utf-8",
    )
    with pytest.raises(bundle.BundleError):
        bundle.read_build_identity(dest)


def test_the_refusal_never_relays_a_control_sequence_from_the_configuration(tmp_path):
    """Une valeur d'origine non controlee n'atteint pas le terminal telle
    quelle : le refus la CONVERTIT, il ne la recopie pas (constitution,
    § Zones sensibles 1)."""
    with pytest.raises(bundle.BundleError) as exc:
        bundle.read_build_identity(
            _config(tmp_path, identifier="app.evil\x1b[2K\rautre"))
    assert "\x1b" not in str(exc.value)
    assert "\r" not in str(exc.value)


# ------------------------------------------- INV-003-2 : le plancher de forme


ACCEPTED = [
    (FAKE_ID, FAKE_TEAM),
    ("thingskit", "OLDTEAM001"),
    ("app.example.tools.thingskit-2", "NEWTEAM002"),
]


@pytest.mark.parametrize("identifier,team", ACCEPTED)
def test_the_requirement_always_carries_the_three_clauses_of_the_floor(
        identifier, team):
    requirement = bundle.code_requirement(identifier, team)
    assert requirement.startswith("anchor apple generic")
    assert f'identifier "{identifier}"' in requirement
    assert (f"certificate leaf[field.{bundle.CERTIFICATE_TYPE_OID}] exists"
            in requirement)
    assert f'certificate leaf[subject.OU]="{team}"' in requirement


def test_the_floor_is_not_parameterisable():
    """Contre-epreuve : le plancher cesserait d'etre un plancher si la
    composition acceptait d'en recevoir les clauses."""
    parameters = list(inspect.signature(bundle.code_requirement).parameters)
    assert parameters == ["bundle_identifier", "team_identifier"], (
        "toute entree supplementaire ouvrirait le plancher a la configuration")
    source = inspect.getsource(bundle.code_requirement)
    assert "anchor apple generic" in source
    # Le marqueur de type vient d'une constante du module, jamais d'une
    # valeur lue : c'est ce qui le rend non configurable.
    assert "CERTIFICATE_TYPE_OID" in source
    assert "CERTIFICATE_TYPE_OID" not in parameters


def test_the_configuration_cannot_name_a_clause_of_the_floor(tmp_path):
    """Le vocabulaire de la configuration est CLOS : trois champs, pas un de
    plus. Un champ `anchor` ou `requirement` serait la voie par laquelle une
    configuration elargirait l'exigence au lieu de la restreindre."""
    for key in ("anchor", "requirement", "certificate_type_oid", "code_requirement"):
        dest = tmp_path / f"identity-{key}.conf"
        dest.write_text(
            f"bundle_identifier = {FAKE_ID}\n"
            f"team_identifier = {FAKE_TEAM}\n"
            f"install_path = {FAKE_PATH}\n"
            f"{key} = n importe quoi\n",
            encoding="utf-8",
        )
        with pytest.raises(bundle.BundleError):
            bundle.read_build_identity(dest)


# ---------------------------- INV-003-5 : les deux porteurs, une seule lecture


def test_the_identity_file_is_read_back_by_the_cli_parser():
    """Le fichier ecrit par le build est exactement ce que le CLI sait lire —
    en litteral, pas « les deux nomment le meme identifiant »."""
    text = bundle.code_identity_file_text(FAKE_ID, FAKE_TEAM)
    assert cli.parse_code_identity(text) == (FAKE_ID, FAKE_TEAM)


def test_the_shim_and_the_identity_file_carry_the_same_requirement():
    """INV-003-5 : egalite LITTERALE des deux porteurs."""
    requirement = bundle.code_requirement(FAKE_ID, FAKE_TEAM)
    source = bundle.shim_source(
        ["areas"], app_path=FAKE_PATH, requirement=requirement)
    identifier, team = cli.parse_code_identity(
        bundle.code_identity_file_text(FAKE_ID, FAKE_TEAM))
    assert cli.compose_code_requirement(identifier, team) == requirement
    assert requirement.replace('"', '\\"') in source


def test_the_build_derives_both_carriers_from_a_single_read():
    """Deux lectures, ce serait deux verites possibles dans un meme build."""
    tree = ast.parse(inspect.getsource(bundle.build))
    reads = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "read_build_identity"]
    assert len(reads) == 1, "la configuration se lit une fois par build"


def test_the_identity_file_is_written_before_the_signature():
    """INV-003-3 : ecrire apres la signature invaliderait le sceau des
    ressources — et un fichier hors sceau ne prouve rien.

    L'ordre se lit sur les NUMEROS DE LIGNE, pas sur l'ordre de parcours de
    `ast.walk`, qui est un parcours en LARGEUR : indexer sa sortie ne prouvait
    l'ordre de la source que tant que les trois appels restaient au premier
    niveau du corps, et serait devenu faux — silencieusement — le jour ou l'un
    d'eux passerait dans une condition ou une boucle (releve en review).
    """
    tree = ast.parse(inspect.getsource(bundle.build))
    lines = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            lines.setdefault(node.func.id, node.lineno)
    for name in ("write_code_identity", "_sign_everything",
                 "assert_bundle_satisfies_requirement"):
        assert name in lines, f"{name} n'est pas appele par build()"
    assert (lines["write_code_identity"]
            < lines["_sign_everything"]
            < lines["assert_bundle_satisfies_requirement"]), lines


def test_the_ordering_check_reads_the_source_not_the_traversal():
    """Contre-epreuve du correctif : sur une source ou l'ordre d'ecriture et
    l'ordre de parcours en largeur DIVERGENT, la lecture par numero de ligne
    tranche juste. C'est le cas qu'un `ast.walk` indexe rendait faux.
    """
    source = textwrap.dedent("""
        def build(flag):
            if flag:
                premier()
            second()
    """)
    tree = ast.parse(source)
    walked = [n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    lines = {n.func.id: n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert walked == ["second", "premier"], walked
    assert lines["premier"] < lines["second"]


HOSTILE_COMPOSITIONS = (
    [(value, FAKE_TEAM) for value in HOSTILE_IDENTIFIERS]
    + [(FAKE_ID, value) for value in HOSTILE_TEAMS]
)


@pytest.mark.parametrize("identifier,team", HOSTILE_COMPOSITIONS)
def test_the_build_composition_refuses_a_hostile_value_at_the_site(
        identifier, team):
    """Symetrie avec `bin/thingskit` (releve en review) : le script reaffirme
    la forme au SITE d'interpolation, le build ne le faisait pas.

    Non atteignable — `build()` est le seul appelant, apres validation — mais
    le motif vaut des deux cotes : c'est la meme chaine, et la meme injection
    de clause si la valeur passe.
    """
    with pytest.raises(bundle.BundleError):
        bundle.code_requirement(identifier, team)


def test_the_build_composition_accepts_a_wellformed_pair():
    """Contre-epreuve : refuser TOUT ne prouverait rien."""
    assert bundle.code_requirement(FAKE_ID, FAKE_TEAM).startswith(
        "anchor apple generic")


@pytest.mark.parametrize("destination", [
    "thingskit.app",                    # relatif
    "/tmp/x.app\"; rm -rf /",           # injection dans le lanceur sh
    "/tmp/$(id).app",
    "/tmp/x",                           # pas un bundle
])
def test_a_destination_out_of_form_is_refused_by_the_build_entry_point(
        tmp_path, destination, capsys):
    """La destination de la ligne de commande subit la MEME forme que celle de
    la configuration (releve en review : `argv[1]` y echappait, alors que la
    meme donnee passee par `--config` etait validee — un ecart de traitement
    est une invitation)."""
    assert bundle.main(["bundle.py", destination,
                        "--config", str(_config(tmp_path))]) == 1
    assert "install_path" in capsys.readouterr().err


def test_the_identity_file_lands_where_the_cli_looks_for_it(tmp_path):
    """L'accord des deux cotes est MESURE : le build ecrit a l'endroit que la
    derivation du CLI designe depuis l'interpreteur scelle."""
    contents = tmp_path / "thingskit.app" / "Contents"
    (contents / "Resources").mkdir(parents=True)
    (contents / "MacOS").mkdir(parents=True)
    written = bundle.write_code_identity(contents, FAKE_ID, FAKE_TEAM)
    executable = contents / "MacOS" / "thingskit"
    executable.write_text("", encoding="utf-8")
    assert Path(cli.code_identity_path(str(executable))) == written
    assert cli.parse_code_identity(
        written.read_text(encoding="utf-8")) == (FAKE_ID, FAKE_TEAM)


# ------------------------------- INV-003-7 : le CLI n'ouvre pas la configuration


def _cli_string_constants(source: str) -> list[str]:
    return [n.value for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_no_execution_path_of_the_cli_reads_the_build_configuration():
    """Balayage a compte residuel nul, jamais une relecture.

    **Ce qu'il couvre, exactement** : le nom de l'origine de configuration et
    le segment `build/` ecrits dans un LITTERAL de chaine du script. C'est la
    forme directe, celle ou le chemin et le code se rencontrent dans la meme
    expression.

    **Ce qu'il ne couvre PAS**, dit plutot que tu (releve en review) : un
    chemin RECONSTRUIT — par concatenation de fragments, par `os.environ`, ou
    par remontee depuis l'emplacement du script. La derniere de ces trois
    routes est la seule qui menerait au depot sans le nommer, et elle est
    fermee separement, par une propriete du script entier plutot que par ce
    balayage : `test_the_cli_does_not_derive_a_path_from_its_own_location`
    (`tests/test_code_identity.py`) exige que `__file__` n'y figure pas du
    tout. Les deux autres restent des invariants NON gardes.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    origin = Path(bundle.BUILD_IDENTITY_CONFIG).name
    offenders = [literal for literal in _cli_string_constants(source)
                 if origin in literal or "build/" in literal]
    assert offenders == [], offenders


def test_the_configuration_sweep_sees_a_cli_that_would_read_it():
    """Contre-epreuve : un balayage qui ne voit rien ne prouve rien."""
    origin = Path(bundle.BUILD_IDENTITY_CONFIG).name
    offenders = [literal for literal in _cli_string_constants(
        f"CONF = 'build/{origin}'\n") if origin in literal or "build/" in literal]
    assert offenders != []


def test_the_cli_never_imports_the_identity_file():
    """Le fichier scelle est lu comme une DONNEE. `-I` n'implique pas `-S` et
    ne ferme pas l'ombrage par un module homonyme (build/bundle.py:66-72) :
    un `import` sur le chemin de demarrage de la garde serait une surface
    d'execution, pas une lecture."""
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"exec", "eval", "compile", "__import__", "import_module"}
    reached = {n.func.id for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id in forbidden}
    assert reached == set(), reached
    assert "importlib" not in source
    assert "runpy" not in source


# ------------------------------- INV-003-8 : le chemin d'installation configure


def test_the_carriers_take_the_install_path_from_the_configuration():
    other = "/tmp/ailleurs/thingskit.app"
    requirement = bundle.code_requirement(FAKE_ID, FAKE_TEAM)
    shim = bundle.shim_source(["areas"], app_path=other, requirement=requirement)
    launcher = bundle.launcher_script(other)
    assert other in shim and other in launcher
    assert "/Applications/thingskit.app" not in shim
    assert "/Applications/thingskit.app" not in launcher


def _code_string_literals(path: Path) -> list[str]:
    """Litteraux de chaine hors docstring d'un `.py`, ou lignes de CODE d'un
    gabarit C — la narration d'une mesure n'est pas une decision gravee.

    Meme distinction que la classe « valeur personnelle » de
    `tests/test_bundle.py`, et pour le meme motif : une garde qui balaie la
    prose impose une allowlist qui grossit a chaque paragraphe, donc une garde
    desactivee dans le mois.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".in":
        return [line for line in text.splitlines()
                if not line.lstrip().startswith(("//", "*", "/*"))]
    tree = ast.parse(text)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def test_no_installation_path_is_written_in_the_published_sources():
    """Le chemin d'installation cesse d'etre une decision gravee dans la
    source publiee — balaye, pas relu."""
    offenders = []
    for path in (SCRIPT, REPO_ROOT / "build" / "bundle.py",
                 REPO_ROOT / "build" / "thingskit-launch.c.in",
                 REPO_ROOT / "build" / "macho.py"):
        for literal in _code_string_literals(path):
            if "/Applications/thingskit" in literal:
                offenders.append(f"{path.name} : {literal!r}")
    assert offenders == [], offenders


def test_the_installation_path_sweep_sees_a_path_written_in_code(tmp_path):
    """Contre-epreuve : un balayage qui ne voit rien ne prouve rien."""
    source = tmp_path / "grave.py"
    source.write_text('INSTALL = "/Applications/thingskit.app"\n', encoding="utf-8")
    assert any("/Applications/thingskit" in literal
               for literal in _code_string_literals(source))
    narration = tmp_path / "narration.py"
    narration.write_text('"""mesure sur /Applications/thingskit.app."""\n',
                         encoding="utf-8")
    assert not any("/Applications/thingskit" in literal
                   for literal in _code_string_literals(narration))


# ------------------------ le build refuse sans configuration (INV-003-1, AC-6)


def test_the_build_refuses_without_a_configuration_before_touching_the_destination(
        tmp_path):
    """Meme exigence que pour l'identite de signature : refuser APRES avoir
    detruit le bundle installe laisserait le poste sans outil."""
    dest = tmp_path / "thingskit.app"
    dest.mkdir()
    (dest / "temoin").write_text("intact", encoding="utf-8")
    with pytest.raises(bundle.BundleError):
        bundle.build(dest, config=tmp_path / "absente.conf")
    assert (dest / "temoin").read_text() == "intact"


def test_the_build_entry_point_reports_the_missing_configuration(tmp_path, capsys):
    """`main()` rend non nul et NOMME la cause : un mode d'echec de plus doit
    se diagnostiquer sans lire le code."""
    assert bundle.main(["bundle.py", str(tmp_path / "x.app"),
                        "--config", str(tmp_path / "absente.conf")]) == 1
    message = capsys.readouterr().err
    assert "absente.conf" in message
    assert "bundle_identifier" in message


# ------------------- les deux cotes s'accordent sur ce qu'est une valeur valide


AGREEMENT_CASES = (
    [(identifier, team, True) for identifier, team in ACCEPTED]
    + [(value, FAKE_TEAM, False) for value in HOSTILE_IDENTIFIERS]
    + [(FAKE_ID, value, False) for value in HOSTILE_TEAMS]
)


@pytest.mark.parametrize("identifier,team,accepted", AGREEMENT_CASES)
def test_the_two_sides_agree_on_what_a_wellformed_identity_is(
        tmp_path, identifier, team, accepted):
    """Les deux regles de forme sont ECRITES deux fois — le script source ne
    peut rien importer de `build/` (INV-003-7) — donc leur accord se mesure
    plutot qu'il ne se relit. Une divergence rendrait un bundle que le build
    accepte et que le CLI refuse au premier lancement.
    """
    text = f"bundle_identifier = {identifier}\nteam_identifier = {team}\n"
    try:
        cli.parse_code_identity(text)
        cli_ok = True
    except ValueError:
        cli_ok = False
    try:
        bundle.read_build_identity(
            _config(tmp_path, identifier=identifier, team=team))
        build_ok = True
    except bundle.BundleError:
        build_ok = False
    assert cli_ok is accepted and build_ok is accepted


# ---------------------------------------- BUG-013 point 1, residu : ambiguite


def test_a_subject_carrying_two_organisational_units_is_refused():
    """« Le premier gagne » est un choix implicite sur la valeur qui decide de
    l'identite du build. Un sujet ambigu ne se tranche pas : il se refuse."""
    assert bundle.subject_ou(
        "subject=CN=Apple Development: Mallory (X),OU=OLDTEAM001,"
        "OU=NEWTEAM002,O=Autre,C=US"
    ) is None


def test_a_subject_carrying_a_single_organisational_unit_is_still_read():
    """Contre-epreuve : le refus d'ambiguite ne doit pas rendre la selection
    inoperante sur le cas nominal."""
    assert bundle.subject_ou(
        "subject=UID=X,CN=Apple Development: Mallory (X),OU=NEWTEAM002,O=Autre,C=US"
    ) == "NEWTEAM002"
