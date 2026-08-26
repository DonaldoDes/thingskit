"""Construction du bundle signé `thingskit.app` (spec specs/bundle-signe/spec.md, ADR-001).

Pourquoi ce module existe
-------------------------
`bin/thingskit` a un shebang : c'est l'interpréteur qui porte l'identité de
code, pas le script. Le Python Homebrew est signé ad-hoc et son identifiant
change à chaque montée de version, ce qui invalide le consentement TCC
`kTCCServiceSystemPolicyAppData` requis pour lire le Group Container Things.
Le bundle embarque son propre interpréteur, signé sous une identité nommée et
figée : l'identité cesse de bouger.

Ce qui est mesuré, et ce qui ne l'est pas
-----------------------------------------
Constaté sur pièce le 2026-08-18, en construisant un prototype, pas déduit :

  - `install_name_tool` invalide la signature ad-hoc d'un Mach-O ; le binaire
    est alors tué par le noyau (SIGKILL, rc=137) tant qu'il n'est pas resigné.
    Toute réécriture de chemin de chargement impose donc une re-signature.
  - Le framework Python de Homebrew embarque un *stub* d'application
    (`Versions/3.14/Resources/Python.app/Contents/MacOS/Python`) qui référence
    la dylib par un chemin ABSOLU vers le Cellar. Sans réécriture de ce stub,
    le processus recharge la dylib Homebrew et `sys.prefix` repart vers
    `/opt/homebrew` — le bundle paraît autonome (`otool -L` de l'exécutable est
    propre) alors qu'il ne l'est pas. C'est le mode d'échec le plus trompeur
    ici, et il n'est visible que sous `DYLD_PRINT_LIBRARIES`.
  - Six modules d'extension de la stdlib se lient à des dylibs Homebrew tierces
    (`_sqlite3` -> libsqlite3, `_ssl`/`_hashlib` -> openssl, `_decimal`,
    `_lzma`, `_zstd`). Elles sont vendues dans `Contents/Frameworks/` et leurs
    références réécrites en `@rpath`, sans quoi `import sqlite3` dépendrait
    encore de l'état de Homebrew — soit exactement ce qu'on cherche à quitter.
  - NC-4 (entitlement `disable-library-validation`) : `codesign_command` n'en
    pose AUCUN par défaut. L'entitlement ne s'ajoute que si une mesure établit
    sa nécessité (BNDL-11), jamais par précaution.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import macho

BUNDLE_NAME = "thingskit"
BUNDLE_VERSION = "1.0"
# Chemin ABSOLU, jamais résolu par PATH : un vérificateur de sceau que
# l'environnement peut détourner ne vérifie rien (même classe de défaut que
# `PYTHONPATH`, cf. `launcher_script`).
CODESIGN = "/usr/bin/codesign"
# `install_name_tool` et `otool` étaient invoqués par NOM NU, sur 7 sites.
# Même défaut que IOREG dans la sonde et OTOOL dans la garde, déjà corrigé
# deux fois ailleurs — et ici le plus exposé des trois : `brew install
# binutils` pose un `install_name_tool` de /opt/homebrew en tête de PATH, et
# c'est lui qui RÉÉCRIT les Mach-O du bundle avant signature. Le balayage qui
# les a trouvés est rejoué en test sur tout argv du module, jamais sur une
# liste de noms tenue à la main.
INSTALL_NAME_TOOL = "/usr/bin/install_name_tool"

# Les fichiers que `site.py` exécute au démarrage de l'interpréteur, et par
# lesquels un gestionnaire de paquets peut rouvrir le `sys.path` du bundle sur
# ses propres paquets. `-I` ne protège de rien ici : il implique `-s` (pas de
# site utilisateur) mais pas `-S`, donc `site.py` tourne et importe toujours
# `sitecustomize`. Les `.pth` sont inclus parce qu'une ligne commençant par
# `import` y est EXÉCUTÉE — pas seulement ajoutée au chemin.
PACKAGE_MANAGER_STARTUP_HOOK_NAMES = ("sitecustomize.py", "usercustomize.py", "*.pth")
# Mêmes motifs de chemin absolu que `CODESIGN` : ces deux outils décident
# quelle identité de code sera apposée au bundle.
SECURITY = "/usr/bin/security"
OPENSSL = "/usr/bin/openssl"
# ADR-003 : quelle identité de code est attendue n'est plus écrite ici. Elle
# vient d'une ORIGINE UNIQUE de configuration, locale et non versionnée, lue
# par le seul build — jamais par un chemin d'exécution du CLI. Le dépôt est
# public : y écrire l'identifiant d'équipe du mainteneur le publierait, et
# rendrait le dépôt inconstructible par un tiers, qui devrait éditer le code
# pour signer sous sa propre identité.
#
# Aucune valeur par défaut versionnée : le fichier est absent d'un clone, et
# le build refuse en le NOMMANT. Coût assumé, écrit plutôt qu'escamoté : le
# mainteneur le recrée après un `git clean`. Format et exemple : CONTRIBUTING.
BUILD_IDENTITY_CONFIG = Path(__file__).resolve().parent / "identity.conf"
BUILD_IDENTITY_FIELDS = ("bundle_identifier", "team_identifier", "install_path")
# Nom du fichier d'identité scellé dans `Contents/Resources/`. Il DOIT être
# celui que `bin/thingskit` dérive de `sys.executable` : l'accord des deux
# côtés est mesuré (`tests/test_build_identity.py`), pas relu.
CODE_IDENTITY_FILE = "code-identity"
# Formes STRICTES, pas seulement échappables — mêmes classes que celles de
# `bin/thingskit`, et leur accord est mesuré plutôt que relu (le script source
# ne peut rien importer d'ici : ce serait une surface d'exécution sur le
# chemin de démarrage de la garde, et `verify_embedded_copy` l'interdit par
# ailleurs). Un guillemet ou un espace admis ici injecterait une clause dans
# l'exigence de code ; un caractère de contrôle, dans la source C du shim.
_BUNDLE_IDENTIFIER_FORM = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,127}\Z")
_TEAM_IDENTIFIER_FORM = re.compile(r"[A-Z0-9]{10}\Z")
# Le chemin d'installation traverse une chaîne C (le shim) ET un `sh` (le
# lanceur) : la classe exclut tout ce qui a un sens dans l'un ou l'autre.
_INSTALL_PATH_FORM = re.compile(r"/[A-Za-z0-9 ._/-]{0,255}\.app\Z")
# Exigence de code opposée au bundle AVANT l'exec. Sans elle, `--verify
# --strict` constate « une signature valide », jamais « LA signature » :
# mesuré le 2026-08-18, altérer `Contents/Resources/thingskit` sur une copie
# puis la re-signer ad-hoc (`codesign --force -s -`) fait repasser le contrôle
# à rc=0, alors que la copie porte `Signature=adhoc` et `TeamIdentifier=not
# set`. Avec l'exigence, la même copie rend rc=3 (`code failed to satisfy
# specified code requirement(s)`) et le bundle réel rc=0.
# Aucune apostrophe : la chaîne est entourée de guillemets SIMPLES dans le
# lanceur `sh`, ses guillemets doubles y traversent sans échappement.
# Marqueur d'extension X.509 du TYPE de certificat (ADR-002 § Decision 5).
#
# Le `csreq` que TCC a enregistre pour le bundle pinne l'identifier, l'ancre
# Apple, le CN du certificat FEUILLE et le marqueur de l'intermediaire — jamais le Team ID (mesure du 2026-08-21). L'exigence du
# depot, elle, ne portait que l'equipe : les deux ne coincidaient donc pas, et
# l'ecart avait une consequence precise et UNIDIRECTIONNELLE — un bundle
# pouvait satisfaire l'exigence du depot ET avoir perdu le grant. Le cas
# concret est un changement de TYPE de certificat (Apple Development ->
# Developer ID Application, la voie de repli d'ADR-001 § NC-3) : Team ID
# inchange, sceau valide, build « reussi », et une invite TCC au premier
# lancement que rien n'aurait signalee. Meme mode d'echec que BUG-013, reouvert
# par un autre chemin.
#
# Le CN individuel n'est PAS pinne, a la difference du `csreq` de TCC : il
# change a chaque renouvellement, et le pinner casserait le lanceur a echeance
# fixe sans rien proteger de plus. L'ecart avec TCC subsiste donc dans ce sens —
# assume, et documente en § Zones sensibles 3.
#
# CE QUI EST MESURE, le 2026-08-21, sur ce poste :
#   - l'OID est porte, critique, par la feuille *Apple Development* de ce poste
#     (`security find-certificate -c … | openssl x509 -noout -text`) ;
#   - l'exigence amendee est satisfaite par le bundle installe (rc=0) ;
#   - opposee SEULE a un artefact ad-hoc, la clause refuse (rc!=0) — elle
#     discrimine, elle n'est pas decorative.
#
# CE QUI N'EST PAS ETABLI, et ne doit pas etre presente comme tranche dans un
# sens ni dans l'autre : que cet OID discrimine effectivement d'un *Developer
# ID Application*. Le trousseau de ce poste ne contient qu'UNE identite de
# signature (`security find-identity -v -p codesigning` -> 1), la contre-epreuve
# n'y est donc pas faisable. Ce qui trancherait : opposer cette clause a un
# artefact reellement signe *Developer ID Application*.
CERTIFICATE_TYPE_OID = "1.2.840.113635.100.6.1.2"
# Aucune apostrophe : historiquement parce que la chaine etait entouree de
# guillemets SIMPLES dans le lanceur `sh`. Le porteur est desormais une chaine C
# (ADR-002), mais la contrainte est conservee — elle ne coute rien et l'exigence
# reste copiable telle quelle dans un `sh` de diagnostic.


def _requirement_value(value: str, form: "re.Pattern[str]") -> str:
    """Rend `value` si elle satisfait `form`, REFUSE sinon.

    Symétrique du neutraliseur de `bin/thingskit` : la forme est réaffirmée là
    où la valeur rencontre le gabarit, et pas seulement à la lecture de la
    configuration. `build()` est aujourd'hui le seul appelant de la fonction
    ci-dessous, et il valide en amont — mais c'est la même chaîne et la même
    injection de clause si une valeur passe, et un site non gardé se met à
    compter le jour où un second appelant apparaît.
    """
    if not form.match(value):
        raise BundleError(
            f"valeur hors forme pour une exigence de code : {value!r} "
            f"(attendu {form.pattern!r})")
    return value


def code_requirement(bundle_identifier: str, team_identifier: str) -> str:
    """Compose l'exigence de code opposee au bundle et au shim.

    Le PLANCHER de forme n'est pas configurable : l'ancrage Apple generique et
    le marqueur d'extension de TYPE de certificat sont ecrits ICI, et aucune
    configuration ne peut les retirer. Une configuration ne peut que
    RESTREINDRE l'exigence, jamais l'elargir — sans quoi une configuration
    degradee affaiblirait la garde en silence, seule degradation invisible
    puisque le build reussirait et que le CLI demarrerait.

    `bin/thingskit` compose la meme chaine a partir du fichier scelle ; leur
    egalite est LITTERALE et mesuree (INV-003-5).
    """
    identifier = _requirement_value(bundle_identifier, _BUNDLE_IDENTIFIER_FORM)
    team = _requirement_value(team_identifier, _TEAM_IDENTIFIER_FORM)
    return (
        'anchor apple generic'
        f' and identifier "{identifier}"'
        f' and certificate leaf[field.{CERTIFICATE_TYPE_OID}] exists'
        f' and certificate leaf[subject.OU]="{team}"'
    )


# Code de refus du lanceur. Distinct de tout code du chemin nominal
# (`main()` rend 0/1, argparse rend 2) : C-1 exige que le contrat
# « 0 = effet constaté » remonte intact.
SEAL_REFUSAL_CODE = 126

# --- ADR-002 : le shim Mach-O qui decide de la responsabilite de processus ---
#
# Nom du second maillon de la chaine de lancement. Les deux maillons sont des
# `exec` : `sh` -> shim -> interpreteur scelle. Aucun processus intermediaire.
SHIM_NAME = "thingskit-launch"
# Chemin ABSOLU, memes motifs que `CODESIGN` : le compilateur decide de ce qui
# sera signe sous l'identite porteuse du grant.
CC = "/usr/bin/cc"
# Borne de temps du controle de sceau DANS le shim. Un verificateur bloque
# ferait autrement pendre toute invocation du CLI sans message (BUG-011).
SHIM_CODESIGN_TIMEOUT_SECONDS = 10

# Échéance de TOUT outil externe que le build lance. Le dépôt avait déjà borné
# une invocation — celle du sceau du shim, ci-dessus — et laissé les six autres.
# Balayé le 2026-08-23 : 7 `subprocess.run` sans échéance, dont `codesign`,
# `security find-identity`, `cc` et `install_name_tool`. Un outil bloqué fige le
# build indéfiniment et sans diagnostic. Le plus lent mesuré ici,
# `codesign --verify --strict --deep` sur le bundle entier, coûte 0,33 s : la
# marge est de deux ordres de grandeur. Un des sept avait été introduit par la
# passe qui a écrit ce commentaire — borner ce qu'on ajoute est le premier
# endroit où la règle s'applique.
EXTERNAL_CALL_TIMEOUT_SECONDS = 60
# Nom de la constante lue dans `bin/thingskit`. Source de verite UNIQUE de la
# partition (INV-002-3) : le shim n'en porte aucune copie ecrite a la main.
FAST_PATH_CONSTANT = "FAST_PATH_COMMANDS"

USAGE = f"""usage: python3 -m build.bundle [destination] [--config <fichier>]

Construit, relocalise et signe le bundle `{BUNDLE_NAME}.app`.

  destination      chemin du bundle a produire. Par defaut, `install_path` de
                   la configuration. Meme forme que ce champ.
  --config <f>     configuration de construction a lire, au lieu de
                   `{BUILD_IDENTITY_CONFIG}`.

Ce depot ne porte AUCUNE valeur d'identite par defaut (ADR-003) : la
configuration est locale, jamais versionnee, et le build refuse en la nommant
si elle manque. Elle porte trois champs, tous obligatoires :

  {", ".join(BUILD_IDENTITY_FIELDS)}

Format et exemple : CONTRIBUTING.md."""

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_SCRIPT = REPO_ROOT / "bin" / "thingskit"
SHIM_TEMPLATE = REPO_ROOT / "build" / "thingskit-launch.c.in"


class BundleError(RuntimeError):
    """Le build refuse de produire un artefact dont il ne peut pas répondre."""


# ------------------------------------------------- ADR-003 : la configuration


def parse_build_identity(text: str, origin: Path | str) -> dict[str, str]:
    """Analyse la configuration de construction, ou REFUSE.

    Aucune tolérance : ligne sans séparateur, champ inconnu, dupliqué,
    manquant, vide ou hors forme valent refus. Ce fichier décide de l'identité
    de code que l'artefact exigera de lui-même — « la dernière valeur gagne »
    y serait un choix implicite, et ce dépôt n'en fait aucun.

    Les valeurs fautives sont CONVERTIES (`!r`) dans le message : elles sont
    d'origine non contrôlée, et une séquence de contrôle recopiée telle quelle
    ferait lire au mainteneur autre chose que ce que le build a écrit.
    """
    fields: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise BundleError(
                f"configuration de construction illisible ({origin}) : ligne "
                f"sans separateur {line!r}. Champs attendus : "
                + ", ".join(BUILD_IDENTITY_FIELDS))
        key, value = key.strip(), value.strip()
        if key not in BUILD_IDENTITY_FIELDS:
            raise BundleError(
                f"configuration de construction ({origin}) : champ inconnu "
                f"{key!r}. Champs attendus : " + ", ".join(BUILD_IDENTITY_FIELDS))
        if key in fields:
            raise BundleError(
                f"configuration de construction ({origin}) : champ duplique "
                f"{key!r} — aucune valeur ne l'emporte sur l'autre.")
        fields[key] = value
    for name in BUILD_IDENTITY_FIELDS:
        if name not in fields:
            raise BundleError(
                f"configuration de construction incomplete ({origin}) : champ "
                f"{name} absent. Champs attendus : "
                + ", ".join(BUILD_IDENTITY_FIELDS))
    forms = {
        "bundle_identifier": _BUNDLE_IDENTIFIER_FORM,
        "team_identifier": _TEAM_IDENTIFIER_FORM,
        "install_path": _INSTALL_PATH_FORM,
    }
    for name, form in forms.items():
        if not form.match(fields[name]):
            raise BundleError(
                f"configuration de construction ({origin}) : {name} hors "
                f"forme {fields[name]!r} — attendu {form.pattern!r}.")
    return fields


def read_build_identity(path: Path | str = BUILD_IDENTITY_CONFIG) -> dict[str, str]:
    """Lit l'origine unique de configuration, ou REFUSE en la nommant.

    Le build gagne ici un mode d'échec de plus — configuration manquante — et
    il doit se diagnostiquer sans lire le code : le message nomme le fichier
    attendu et les trois champs.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise BundleError(
            f"configuration de construction absente ou illisible : {path} "
            f"({exc.__class__.__name__}). Ce depot ne porte AUCUNE valeur "
            "d'identite par defaut : creer ce fichier avec les champs "
            + ", ".join(BUILD_IDENTITY_FIELDS)
            + " (cf. CONTRIBUTING.md)."
        ) from exc
    return parse_build_identity(text, path)


def code_identity_file_text(bundle_identifier: str, team_identifier: str) -> str:
    """Contenu du fichier d'identite scelle dans `Contents/Resources/`.

    C'est de la DONNEE, pas du Python : `bin/thingskit` la parse, il ne
    l'importe jamais (ADR-003, option 5 ecartee).
    """
    return (
        "# thingskit — identite de code attendue par le CLI (ADR-003).\n"
        "# Ecrit par build/bundle.py AVANT la signature, donc couvert par le\n"
        "# sceau des ressources. Ne pas editer : toute retouche invalide le\n"
        "# sceau, et le lanceur refuse alors le bundle.\n"
        f"bundle_identifier = {bundle_identifier}\n"
        f"team_identifier = {team_identifier}\n"
    )


def write_code_identity(contents: Path | str, bundle_identifier: str,
                        team_identifier: str) -> Path:
    """Ecrit le fichier d'identite dans `Contents/Resources/` et rend son chemin."""
    dest = Path(contents) / "Resources" / CODE_IDENTITY_FILE
    dest.write_text(
        code_identity_file_text(bundle_identifier, team_identifier),
        encoding="utf-8")
    return dest


# ------------------------------------------------- BNDL-02b (BUG-010)


def parse_identity_listing(output: str) -> list[tuple[str, str]]:
    """Extrait les couples (empreinte SHA-1, nom) de `security find-identity`.

    Format réel, mesuré le 2026-08-19 :

        1) 1A2B3C4D...5678 "Apple Development: Prenom NOM (XXXXXXXXXX)"
             1 valid identities found

    Un poste sans certificat rend la seule ligne `0 valid identities found`,
    qui doit se lire comme une liste vide, pas comme une erreur d'analyse.
    """
    return [
        (m.group(1), m.group(2))
        for m in re.finditer(r'^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"(.*)"\s*$',
                             output, re.MULTILINE)
    ]


def split_rdns(subject_line: str) -> list[str]:
    """Découpe un sujet RFC2253 sur ses virgules NON échappées.

    RFC2253 §2.4 échappe par antislash une virgule appartenant à une valeur.
    Couper sur toutes les virgules fabrique donc des RDN qui n'existent pas :
    mesuré le 2026-08-19, le sujet
    `CN=Apple Development: Mallory (X)\\,OU=<equipe attendue>,OU=EVILTEAM1,O=Evil`
    n'a qu'UN seul OU (`EVILTEAM1`), et un découpage naïf y lit d'abord
    l'équipe même que le build exige.
    """
    line = subject_line
    if line.startswith("subject="):
        line = line[len("subject="):]
    rdns, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            current.append(char)
            escaped = True
        elif char == ",":
            rdns.append("".join(current))
            current = []
        else:
            current.append(char)
    rdns.append("".join(current))
    return [rdn.strip() for rdn in rdns if rdn.strip()]


def unescape_rfc2253(value: str) -> str:
    """Rend la valeur littérale d'un RDN : `\\,` redevient `,`."""
    out, escaped = [], False
    for char in value:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            out.append(char)
    return "".join(out)


def subject_ou(subject_line: str) -> str | None:
    """Rend l'OU d'un sujet X.509 rendu par `openssl -nameopt RFC2253`.

    L'OU est l'identifiant d'équipe. Il ne se déduit PAS du nom de l'identité :
    mesuré le 2026-08-19 sur le certificat réel de ce poste, la valeur entre
    parenthèses du nom et l'OU du sujet sont deux chaînes DIFFÉRENTES. Lire
    l'une pour l'autre donnerait un build qui croit vérifier l'équipe et ne
    vérifie rien.
    """
    units = [unescape_rfc2253(value)
             for key, sep, value in (rdn.partition("=")
                                     for rdn in split_rdns(subject_line))
             if sep and key.strip().upper() == "OU"]
    # Deux unités d'organisation : « la première gagne » serait un choix
    # implicite sur la valeur qui décide de l'identité du build. Ce dépôt
    # refuse sur ambiguïté plutôt que de trancher (même règle que les
    # résolutions par titre du CLI). Fail-closed : le certificat devient
    # inéligible, il ne devient jamais éligible par erreur.
    if len(units) != 1:
        return None
    return units[0]


def host_identities() -> list[tuple[str, str]]:
    """Identités de signature de code du trousseau du poste courant."""
    out = subprocess.run(
        [SECURITY, "find-identity", "-v", "-p", "codesigning"],
        capture_output=True, timeout=EXTERNAL_CALL_TIMEOUT_SECONDS, text=True,
    ).stdout
    return parse_identity_listing(out)


def certificate_team(sha1: str, name: str, *, dump: str | None = None) -> str | None:
    """Équipe (OU du sujet) du certificat feuille d'empreinte `sha1`.

    Les certificats sont demandés par nom puis filtrés sur l'empreinte : un
    même nom peut couvrir plusieurs certificats (renouvellement, expiration),
    et c'est l'empreinte qui identifie celui que `codesign` emploiera.

    `dump` n'est qu'une couture de DONNÉE : il substitue la sortie de
    `security find-certificate -a -Z -p`, jamais la logique de la fonction —
    boucle PEM, correspondance d'empreinte et analyse du sujet par `openssl`
    restent exercées telles quelles par les tests.
    """
    if dump is None:
        dump = subprocess.run(
            [SECURITY, "find-certificate", "-a", "-Z", "-p", "-c", name],
            capture_output=True, timeout=EXTERNAL_CALL_TIMEOUT_SECONDS, text=True,
        ).stdout
    current, pem, wanted = None, [], sha1.upper()
    for line in dump.splitlines():
        if line.startswith("SHA-1 hash:"):
            current = line.split(":", 1)[1].strip().upper()
            pem = []
        elif line.startswith("-----BEGIN CERTIFICATE"):
            pem = [line]
        elif pem:
            pem.append(line)
            if line.startswith("-----END CERTIFICATE") and current == wanted:
                subject = subprocess.run(
                    [OPENSSL, "x509", "-noout", "-subject", "-nameopt", "RFC2253"],
                    input="\n".join(pem) + "\n", capture_output=True, timeout=EXTERNAL_CALL_TIMEOUT_SECONDS, text=True,
                ).stdout
                return subject_ou(subject)
    return None


def eligible_signing_identities(
    team: str, *, listing: str | None = None, team_of=None
) -> list[tuple[str, str]]:
    """Identités du poste appartenant à `team`, triées par (nom, empreinte).

    Le tri est la règle de sélection : il ne dépend pas de l'ordre dans lequel
    `security` a listé le trousseau, lequel n'est ni documenté ni stable.
    """
    identities = (
        parse_identity_listing(listing) if listing is not None else host_identities()
    )
    probe = team_of if team_of is not None else certificate_team
    eligible = [(sha1, name) for sha1, name in identities if probe(sha1, name) == team]
    return sorted(eligible, key=lambda pair: (pair[1], pair[0]))


def resolve_signing_identity(
    team: str, *, listing: str | None = None, team_of=None
) -> str:
    """Empreinte de l'identité de signature à employer sur ce poste.

    L'empreinte SHA-1 est rendue plutôt que le nom : `codesign -s` l'accepte et
    elle désigne UN certificat, là où un nom peut en couvrir plusieurs.

    Aucune identité de l'équipe -> refus explicite. Il n'y a pas de repli
    possible : signer ad-hoc produirait un bundle qui perd le consentement TCC
    tout en paraissant signé (ADR-001).
    """
    eligible = eligible_signing_identities(team, listing=listing, team_of=team_of)
    if not eligible:
        raise BundleError(
            f"aucune identité de signature de l'équipe {team} sur ce poste — "
            "impossible de construire un bundle satisfaisant l'exigence de "
            "code composée depuis la configuration de construction. Émettre "
            "un certificat Apple Development pour cette équipe et le vérifier "
            "avec "
            f"`{SECURITY} find-identity -v -p codesigning`."
        )
    if len(eligible) > 1:
        print(
            "build: plusieurs identités de l'équipe "
            f"{team} sur ce poste, retenue par ordre de nom : "
            + ", ".join(f"{name} [{sha1[:8]}]" for sha1, name in eligible)
            + f" -> {eligible[0][1]}",
            file=sys.stderr,
        )
    return eligible[0][0]


# --------------------------------------------------------------- BNDL-01


def info_plist_xml(
    identifier: str,
    *,
    executable: str = BUNDLE_NAME,
    name: str = BUNDLE_NAME,
    version: str = BUNDLE_VERSION,
) -> str:
    """Rend l'`Info.plist` du bundle, calqué sur le précédent interne calkit."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>CFBundleIdentifier</key>
\t<string>{identifier}</string>
\t<key>CFBundleExecutable</key>
\t<string>{executable}</string>
\t<key>CFBundleName</key>
\t<string>{name}</string>
\t<key>CFBundlePackageType</key>
\t<string>APPL</string>
\t<key>CFBundleVersion</key>
\t<string>{version}</string>
\t<key>CFBundleShortVersionString</key>
\t<string>{version}</string>
\t<key>LSBackgroundOnly</key>
\t<true/>
</dict>
</plist>
"""


# --------------------------------------------------------------- BNDL-02


def codesign_command(
    target: str, identity: str, entitlements: str | None = None,
    identifier: str | None = None,
) -> list[str]:
    """Compose la ligne `codesign`.

    `--options runtime` aligne l'artefact sur calkit (`flags=0x10000(runtime)`).
    Aucun entitlement n'est posé sans mesure (NC-4).

    L'identité n'a PAS de défaut : elle se résout sur le poste
    (`resolve_signing_identity`). Une identité vide ou `-` est refusée ici même
    — `codesign -s -` produit un sceau ad-hoc qui passe `--verify --strict`
    mais échoue l'exigence de code, donc perd le grant TCC : c'est la seule
    dégradation silencieuse possible de ce module.
    """
    if not identity or not identity.strip() or identity.strip() == "-":
        raise BundleError(
            "signature ad-hoc ou identité vide refusée : le bundle doit porter "
            "une identité de l'équipe nommée par la configuration de "
            f"construction (identité reçue : {identity!r})"
        )
    cmd = [CODESIGN, "--force", "--timestamp=none", "--options", "runtime"]
    if entitlements is not None:
        cmd += ["--entitlements", entitlements]
    if identifier is not None:
        # `-i` n'est pose que sur le shim : sans lui, `codesign` derive
        # l'identifiant d'un Mach-O IMBRIQUE de son nom de fichier, et
        # l'exigence unique du build — qui nomme l'identifiant configure — ne
        # pourrait pas lui etre opposee. Le depot n'aurait alors qu'une
        # exigence pour le bundle et aucune pour le premier code qui s'execute.
        cmd += ["-i", identifier]
    cmd += ["-s", identity, target]
    return cmd


# ----------------------------------------------------------- ADR-002 : le shim


def read_fast_path_commands(source: Path | str = SOURCE_SCRIPT) -> frozenset[str]:
    """Lit `FAST_PATH_COMMANDS` dans `bin/thingskit` (INV-002-3).

    Le build ECHOUE si la constante est absente, vide, non litterale, ou
    porteuse d'autre chose que des chaines non vides. Il ne devine jamais : une
    liste devinee ferait revenir l'invite TCC sur une sous-commande, et une
    liste vide ferait basculer tout le CLI en regime lent — deux erreurs
    silencieuses, dans deux sens opposes.
    """
    source = Path(source)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise BundleError(
            f"{FAST_PATH_CONSTANT} illisible : {source} ({exc})"
        ) from exc
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise BundleError(
            f"{FAST_PATH_CONSTANT} illisible : {source} n'analyse pas ({exc})"
        ) from exc

    found = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == FAST_PATH_CONSTANT
            for t in node.targets
        ):
            found = node.value
    if found is None:
        raise BundleError(
            f"{FAST_PATH_CONSTANT} introuvable dans {source} — la partition "
            "rapide/lent n'a plus de source de verite"
        )

    literal = found
    if (
        isinstance(found, ast.Call)
        and isinstance(found.func, ast.Name)
        and found.func.id in {"frozenset", "set", "tuple"}
    ):
        literal = found.args[0] if found.args else ast.Set(elts=[])
    try:
        value = ast.literal_eval(literal)
    except (ValueError, SyntaxError) as exc:
        raise BundleError(
            f"{FAST_PATH_CONSTANT} n'est pas une valeur litterale dans "
            f"{source} — le build ne peut pas en repondre ({exc})"
        ) from exc

    if isinstance(value, dict):
        value = set(value)
    if not isinstance(value, (set, frozenset, list, tuple)):
        raise BundleError(
            f"{FAST_PATH_CONSTANT} n'est pas un ensemble de sous-commandes "
            f"dans {source}"
        )
    commands = tuple(value)
    if not commands:
        raise BundleError(
            f"{FAST_PATH_CONSTANT} est vide dans {source} — tout le CLI "
            "basculerait en regime lent sans que rien ne le dise"
        )
    for name in commands:
        if not isinstance(name, str) or not name:
            raise BundleError(
                f"{FAST_PATH_CONSTANT} porte une entree qui n'est pas une "
                f"sous-commande ({name!r}) dans {source}"
            )
    return frozenset(commands)


def _c_string(value: str) -> str:
    """Rend le corps d'une chaine C, ou refuse ce qu'il ne sait pas echapper.

    Le refus n'est pas de la prudence de style : une chaine mal echappee ici
    produirait une source C qui compile en disant autre chose que ce qu'on
    croit — sur l'exigence de code, ce serait un controle affaibli en silence.
    """
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise BundleError(
            f"valeur non representable dans une chaine C : {value!r}"
        )
    return value.replace("\\", "\\\\").replace('"', '\\"')


def shim_source(
    commands,
    *,
    app_path: str,
    requirement: str,
    bundle_name: str = BUNDLE_NAME,
    codesign: str = CODESIGN,
    template: Path | str = SHIM_TEMPLATE,
    timeout: int = SHIM_CODESIGN_TIMEOUT_SECONDS,
) -> str:
    """Rend la source C du shim, table du regime rapide injectee.

    Le gabarit ne porte aucune sous-commande : c'est ici, et seulement ici, que
    la liste lue dans `bin/thingskit` entre dans le shim.
    """
    names = sorted(commands)
    if not names:
        raise BundleError(
            "table du regime rapide vide — le shim disclaimerait tout, soit "
            "l'option 1 d'ADR-002 sans l'avoir decidee"
        )
    for name in names:
        # Forme STRICTE, pas seulement echappable : une sous-commande d'argparse
        # est un mot de `[a-z0-9-]`. Tout le reste est le signe que la liste ne
        # dit pas ce qu'on croit — l'echappement correct rendrait le defaut
        # silencieux au lieu de le nommer.
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise BundleError(
                f"sous-commande de forme inattendue : {name!r} — la table du "
                "regime rapide ne peut porter que des noms de sous-commande"
            )
    table = "\n".join(f'    "{_c_string(name)}",' for name in names)
    text = Path(template).read_text(encoding="utf-8")
    substitutions = {
        "@APP_PATH@": _c_string(app_path),
        "@BUNDLE_NAME@": _c_string(bundle_name),
        "@CODESIGN@": _c_string(codesign),
        "@CODE_REQUIREMENT@": _c_string(requirement),
        "@SEAL_REFUSAL_CODE@": str(SEAL_REFUSAL_CODE),
        "@CODESIGN_TIMEOUT_SECONDS@": str(int(timeout)),
        "@FAST_PATH_COMMANDS@": table,
    }
    for marker, value in substitutions.items():
        if marker not in text:
            raise BundleError(f"gabarit du shim sans marqueur {marker} : {template}")
        text = text.replace(marker, value)
    return text


def compile_shim(source_text: str, dest: Path | str, *, cc: str = CC) -> Path:
    """Compile la source rendue en un Mach-O a `dest`.

    La compilation est un echec DUR : un shim absent laisserait le lanceur
    pointer sur rien, donc un poste sans outil — panne franche, jamais silence.
    """
    dest = Path(dest)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"{SHIM_NAME}.c"
        src.write_text(source_text, encoding="utf-8")
        proc = subprocess.run(
            [cc, "-Wall", "-Werror", "-O2", "-o", str(dest), str(src)],
            capture_output=True, timeout=EXTERNAL_CALL_TIMEOUT_SECONDS, text=True,
        )
    if proc.returncode != 0:
        raise BundleError(
            f"compilation du shim impossible ({cc}) : "
            f"rc={proc.returncode} {(proc.stderr or proc.stdout).strip()}"
        )
    dest.chmod(0o755)
    return dest


# --------------------------------------------------------------- BNDL-03/04


def launcher_script(
    app_path: str,
    shim: str = SHIM_NAME,
) -> str:
    """Rend le lanceur installe en `~/.local/bin/thingskit`.

    Depuis ADR-002, le lanceur `sh` n'est plus qu'un `exec` vers le shim :
    controle de sceau, isolation `-I` et decision de responsabilite ont migre
    dans le Mach-O, qui est le seul endroit ou elles peuvent etre prises.

    - `exec` — pas un sous-processus : la transmission du code de sortie devient
      structurelle au lieu de dependre d'un `$?` recopie (INV-001-2). `open -a`
      est proscrit : il rend la main sans transmettre ni sortie ni code.
    - **le controle de sceau n'est pas duplique ici.** Le shim le fait AVANT de
      remplacer son image, donc avant que le script scelle ne s'execute — meme
      instant qu'auparavant, autre porteur. Le refaire dans `sh` paierait un
      second `codesign` sur le chemin chaud sans rien garder de plus.
    - le shim lui-meme est SCELLE dans le bundle : il n'est pas modifiable sans
      invalider la signature que le premier chemin d'execution verifie.

    Ce que ce deplacement ne leve pas : l'ambiguite deja documentee du 126 de
    `sh` en cas d'echec d'`exec`. Elle n'est ni aggravee ni fermee ici.
    """
    return f"""#!/bin/sh
# Lanceur de thingskit — genere par build/bundle.py, ne pas editer a la main.
# `exec` remplace l'image du processus : argv, stdin/stdout/stderr et le code
# de sortie traversent sans intermediaire (ADR-001 INV-001-2).
#
# Le sceau, l'isolation `-I` et la responsabilite de processus sont decides par
# le shim scelle ci-dessous (ADR-002) : `sh` ne sait pas appeler
# `responsibility_spawnattrs_setdisclaim`, et la responsabilite se decide avant
# le chargement de l'image.
APP="{app_path}"
exec "$APP/Contents/MacOS/{shim}" "$@"
"""


# --------------------------------------------------------------- BNDL-05


def verify_embedded_copy(source: Path | str, embedded: Path | str) -> None:
    """Refuse un bundle dont la copie embarquée diverge du source (INV-001-5).

    `bin/thingskit` est l'unique source de vérité : une copie éditée sur place
    ferait diverger silencieusement ce qui s'exécute de ce qui est testé.
    """
    source, embedded = Path(source), Path(embedded)
    if not embedded.exists():
        raise BundleError(f"copie embarquée absente : {embedded}")
    if source.read_bytes() != embedded.read_bytes():
        raise BundleError(
            f"la copie embarquée diverge du source : {embedded} != {source}"
        )


def assert_no_package_manager_refs(bundle_root: Path | str, _refs=None) -> None:
    """Refuse un bundle dont un Mach-O référence encore le gestionnaire de paquets.

    Le contrôle porte sur TOUS les Mach-O, pas sur le seul exécutable : mesuré
    le 2026-08-18, `otool -L` de l'exécutable peut être propre alors que le stub
    `Versions/*/Resources/Python.app` recharge la dylib du Cellar. C'est le
    contrôle qui matérialise INV-001-1 — sans lui, l'autonomie du bundle ne
    serait qu'une intention.
    """
    refs = _refs if _refs is not None else _homebrew_load_commands
    guilty = []
    for path in _macho_files(Path(bundle_root)):
        found = refs(path)
        if found:
            # `_refs` injecté par les tests rend des chaînes ; la sonde réelle
            # rend des couples (commande, chemin). Le motif nomme la COMMANDE
            # quand elle est connue : « LC_RPATH /opt/homebrew/lib » dit au
            # lecteur quoi supprimer, « /opt/homebrew/lib » ne le dit pas.
            guilty.append(f"{path}: " + ", ".join(
                entry if isinstance(entry, str) else f"{entry[0]} {entry[1]}"
                for entry in found))
    if _refs is not None and not guilty:
        # chemin de test : la sonde est interrogée même sans Mach-O sur disque
        found = refs(Path(bundle_root))
        if found:
            guilty.append(f"{bundle_root}: {', '.join(found)}")
    if guilty:
        raise BundleError(
            "le bundle référence encore /opt/homebrew — identité de code non "
            "autonome :\n  " + "\n  ".join(guilty)
        )


def assert_interpreter_is_self_contained(bundle_path: Path | str) -> None:
    """Lance l'interpréteur vendu et refuse tout chemin ou module hors du bundle.

    L'inspection statique — table des commandes de chargement, contenu des
    fichiers — ne peut pas répondre à cette question : `sys.path` se construit à
    l'exécution, et un hook de démarrage l'ouvre sur un dossier tiers sans qu'un
    seul octet du bundle ne le nomme après coup. C'est la version mesurée du
    constat déjà écrit dans la constitution le 2026-08-18 : « l'autonomie ne se
    déduit pas de la seule inspection de l'exécutable ».

    `-I` reproduit exactement ce que le shim pose (ADR-001) : mesurer sans lui
    répondrait à une question voisine, pas à celle du bundle installé.
    """
    bundle_path = Path(bundle_path)
    exe = bundle_path / "Contents" / "MacOS" / BUNDLE_NAME
    probe = (
        "import sys\n"
        "outside = [p for p in sys.path if p and not p.startswith(sys.prefix)]\n"
        "foreign = sorted({m for m, v in sys.modules.items()"
        " if getattr(v, '__file__', None) and not v.__file__.startswith(sys.prefix)})\n"
        "print(repr((outside, foreign)))\n"
    )
    proc = subprocess.run([str(exe), "-I", "-c", probe],
                          capture_output=True, timeout=EXTERNAL_CALL_TIMEOUT_SECONDS, text=True)
    if proc.returncode != 0:
        raise BundleError(
            "l'interpréteur du bundle n'a pas pu être interrogé sur son "
            f"autonomie — non établie, jamais « bonne » par défaut.\n"
            f"  rc={proc.returncode} {(proc.stderr or proc.stdout).strip()[:300]}")
    try:
        outside, foreign = ast.literal_eval(proc.stdout.strip())
    except (ValueError, SyntaxError) as exc:
        raise BundleError(
            f"sonde d'autonomie illisible ({exc}) : {proc.stdout[:200]!r}") from exc
    if outside or foreign:
        raise BundleError(
            "l'interpréteur du bundle charge du code hors du bundle — identité "
            "de code non autonome :\n"
            f"  sys.path hors bundle : {outside}\n"
            f"  modules hors bundle  : {foreign}")


def assert_bundle_satisfies_requirement(
    bundle_path: Path | str,
    identity: str,
    requirement: str,
    codesign: str = CODESIGN,
) -> None:
    """Oppose l'exigence de code à l'artefact RÉELLEMENT produit (BUG-013).

    Jusqu'ici, le seul `-R=` du dépôt vivait dans le lanceur : l'exigence
    n'était opposée qu'à l'exécution, chez l'utilisateur, sous la forme d'un
    refus de sceau opaque. Tolérable tant que l'identité de signature était
    nommée en dur ; depuis BUG-010 elle se résout sur le poste, donc toute
    erreur de sélection ne se manifestait qu'au premier lancement — sur un
    poste fraîchement équipé, un build « réussi » puis un outil inutilisable.

    Le contrôle porte sur `bundle_path` tel qu'il est sur disque, jamais sur
    une reconstitution de ce qu'on croit avoir signé : c'est ce qui lui permet
    de contredire le reste du build.
    """
    bundle_path = Path(bundle_path)
    proc = subprocess.run(
        [codesign, "--verify", "--strict", f"-R={requirement}", str(bundle_path)],
        capture_output=True, timeout=EXTERNAL_CALL_TIMEOUT_SECONDS,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise BundleError(
            "l'artefact produit ne satisfait pas l'exigence de code du "
            f"lanceur — il serait refusé au premier lancement.\n"
            f"  artefact  : {bundle_path}\n"
            f"  exigence  : {requirement}\n"
            f"  identité de signature employée : {identity}\n"
            f"  codesign  : rc={proc.returncode} {detail}"
        )


# --------------------------------------------------------------- build

def _run(cmd: list[str], timeout: float = EXTERNAL_CALL_TIMEOUT_SECONDS,
         **kw) -> subprocess.CompletedProcess:
    """Tout outil que le build lance passe par ici, avec une échéance.

    Le défaut n'est pas None : rien en production ne passe `timeout=`, donc un
    défaut à None laisserait le chemin réel non borné tout en satisfaisant une
    règle qui ne regarderait que les appels.
    """
    return subprocess.run(cmd, check=True, capture_output=True, text=True,
                          timeout=timeout, **kw)


def _homebrew_load_commands(path: Path) -> list[tuple[str, str]]:
    """Toute commande de chargement de `path` qui nomme /opt/homebrew.

    Lit la table des commandes en direct (`build/macho.py`). Le contrôle
    passait par `otool -L`, qui n'imprime QUE `LC_ID_DYLIB` et `LC_LOAD_DYLIB`
    et ne montre **jamais** `LC_RPATH` — alors que le rpath est la PREMIÈRE
    chose que dyld consulte. Mesuré le 2026-08-23 sur le bundle installé : 83
    Mach-O sur 92 portaient un `LC_RPATH` vers /opt/homebrew, `/opt/homebrew/lib`
    premier dans l'ordre de recherche sur les 83, et ce contrôle était vert.

    Une lecture impossible **lève** au lieu de rendre une liste vide : « je n'ai
    pas pu lire » et « aucune référence » sont deux verdicts opposés, et les
    confondre est exactement le fail-open que cette fonction remplace (l'ancienne
    avalait `OSError` en `[]`).
    """
    return [(kind, value) for kind, value in macho.load_command_paths(path)
            if value.startswith(macho.HOMEBREW_PREFIX_MARKER)]


def _homebrew_refs(path: Path) -> list[str]:
    """Les seules références que `-change` sait réécrire : les dylibs chargées.

    Volontairement distincte de `_homebrew_load_commands` : passer un `LC_RPATH`
    à `install_name_tool -change` ne réécrirait rien. Les rpaths se **suppriment**
    (`_strip_package_manager_rpaths`), ils ne se réécrivent pas.
    """
    return [value for kind, value in _homebrew_load_commands(path)
            if kind in ("LC_LOAD_DYLIB", "LC_ID_DYLIB", "LC_LOAD_WEAK_DYLIB",
                        "LC_REEXPORT_DYLIB", "LC_LAZY_LOAD_DYLIB",
                        "LC_LOAD_UPWARD_DYLIB")]


def _macho_files(root: Path):
    """Les Mach-O de `root`, classés sur les 4 octets de magie.

    Un seul énumérateur pour le build et pour la garde de session
    (`build/macho.py`). Celui-ci retenait un fichier sur son suffixe ou son bit
    d'exécution et **écartait les liens symboliques** : mesuré le 2026-08-23, il
    balayait 142 fichiers là où la garde en voyait 92 — dont 55 qui n'étaient pas
    des Mach-O, et **5 Mach-O qu'il ne regardait jamais**, tous des liens, dont
    `Python.framework/Python` et `libpython3.14.dylib`.
    """
    for path in macho.find_macho_files(str(root)):
        yield Path(path)


def _python_framework() -> Path:
    real = Path(os.path.realpath(sys.executable))
    for parent in real.parents:
        if parent.name == "Python.framework":
            return parent
    raise BundleError(
        f"{sys.executable} n'est pas un build framework — impossible de vendre "
        "l'interpréteur (relancer le build avec le python Homebrew)"
    )


def _version_dir_name(framework: Path) -> str:
    real = Path(os.path.realpath(sys.executable))
    for parent in real.parents:
        if parent.parent == framework / "Versions":
            return parent.name
    raise BundleError("version du framework Python introuvable")


def build(dest: Path | None = None, source: Path = SOURCE_SCRIPT,
          identity: str | None = None,
          entitlements: str | None = None, *, listing: str | None = None,
          team_of=None, config: Path | str = BUILD_IDENTITY_CONFIG) -> Path:
    """Construit, relocalise et signe le bundle. Idempotent : reconstruit à neuf.

    La configuration et l'identité sont lues AVANT toute écriture : `dest` est
    détruit puis reconstruit, donc échouer plus tard laisserait le poste sans
    bundle du tout — ce que le lanceur traduit en refus d'exécution (BUG-009),
    soit une panne franche là où le poste marchait avant le build.

    Les DEUX porteurs de l'exigence — la chaîne compilée dans le shim et le
    fichier d'identité scellé dans `Contents/Resources/` — sortent de CETTE
    lecture, la seule du build (INV-003-5). Deux lectures, ce seraient deux
    vérités possibles dans un même artefact.
    """
    fields = read_build_identity(config)
    bundle_identifier = fields["bundle_identifier"]
    requirement = code_requirement(bundle_identifier, fields["team_identifier"])
    if dest is None:
        dest = Path(fields["install_path"])
    if identity is None:
        identity = resolve_signing_identity(
            fields["team_identifier"], listing=listing, team_of=team_of)
    framework = _python_framework()
    version = _version_dir_name(framework)

    if dest.exists():
        shutil.rmtree(dest)
    contents = dest / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir(parents=True)
    (contents / "Frameworks").mkdir(parents=True)

    (contents / "Info.plist").write_text(
        info_plist_xml(bundle_identifier), encoding="utf-8")

    embedded = contents / "Resources" / BUNDLE_NAME
    shutil.copy2(source, embedded)
    verify_embedded_copy(source, embedded)

    # Second porteur de l'exigence, et le seul que le CLI lise : écrit ICI,
    # donc AVANT `_sign_everything` — un fichier ajouté après la signature
    # invaliderait le sceau des ressources au lieu d'être couvert par lui
    # (INV-003-3, mesuré dans ADR-002 § R-2).
    write_code_identity(contents, bundle_identifier, fields["team_identifier"])

    # Le shim est compile AVANT la copie du framework : la lecture de
    # `FAST_PATH_COMMANDS` et la compilation sont les deux etapes qui peuvent
    # echouer pour une raison de SOURCE, et il vaut mieux qu'elles echouent
    # avant les minutes de copie et de re-signature.
    compile_shim(
        shim_source(read_fast_path_commands(source), app_path=str(dest),
                    requirement=requirement),
        contents / "MacOS" / SHIM_NAME,
    )

    shutil.copytree(
        framework, contents / "Frameworks" / "Python.framework", symlinks=True
    )
    vdir = contents / "Frameworks" / "Python.framework" / "Versions" / version
    exe = contents / "MacOS" / BUNDLE_NAME
    shutil.copy2(Path(os.path.realpath(sys.executable)), exe)
    exe.chmod(0o755)

    _prune_dangling_symlinks(contents / "Frameworks" / "Python.framework")

    dylib_id = f"@rpath/Python.framework/Versions/{version}/Python"
    _relocate(contents, vdir, exe, dylib_id)
    # La relocalisation POSE les rpaths du bundle ; elle ne retire pas ceux que
    # le framework Homebrew portait déjà. Sans cette passe, 83 des 92 Mach-O du
    # bundle gardent `/opt/homebrew/lib` EN TÊTE de leur ordre de recherche.
    _strip_package_manager_rpaths(dest)
    # dyld n'est pas la seule route vers /opt/homebrew : `sitecustomize.py`,
    # écrit par Homebrew et copié avec le framework, rouvre `sys.path` sur les
    # paquets du Cellar à CHAQUE démarrage, `-I` compris.
    _strip_package_manager_startup_hooks(dest)
    assert_no_package_manager_refs(dest)
    _sign_everything(dest, identity, entitlements, framework_version=vdir,
                     bundle_identifier=bundle_identifier)
    # Dernier geste avant de se déclarer réussi : l'artefact réel est opposé
    # à l'exigence que le lanceur lui opposera (BUG-013).
    assert_bundle_satisfies_requirement(dest, identity, requirement)
    # Le shim est le PREMIER code du bundle a s'executer, et le seul a decider
    # de la responsabilite de processus : l'exigence lui est opposee pour
    # lui-meme, pas seulement par ricochet du sceau de la racine (ADR-002,
    # INV-002-6).
    assert_bundle_satisfies_requirement(
        dest / "Contents" / "MacOS" / SHIM_NAME, identity, requirement
    )
    # Dernier contrôle, et le seul qui MESURE l'autonomie au lieu de l'inférer
    # d'une inspection statique. Il vient après la signature parce qu'un Mach-O
    # réécrit par `install_name_tool` et non re-signé est tué par le noyau.
    assert_interpreter_is_self_contained(dest)
    return dest


def _strip_package_manager_rpaths(root: Path) -> list[tuple[Path, str]]:
    """Supprime de chaque Mach-O tout `LC_RPATH` nommant /opt/homebrew.

    Le résidu n'était pas une trace inerte : mesuré le 2026-08-23 sur le bundle
    installé, `/opt/homebrew/lib` était **premier dans l'ordre de recherche** des
    83 Mach-O concernés, donc `@rpath/libssl.3.dylib` s'y résolvait AVANT la
    copie vendue. Sept binaires — `_ssl`, `_hashlib`, `_sqlite3`, `_decimal`,
    `_lzma`, `_zstd`, `libsqlite3.dylib` — chargent par `@rpath` une dylib qui
    existe réellement dans `/opt/homebrew/lib` ; seule la *library validation*
    implicite du hardened runtime empêchait le chargement. Une défense unique et
    implicite, pour un invariant que la constitution déclare tenu.

    D'où ils viennent : la relocalisation POSE les bons rpaths
    (`@loader_path`, `@executable_path/...`) sans jamais retirer ceux que le
    framework Homebrew portait déjà. Ajouter ne remplace pas.

    Trois précautions, chacune mesurée plutôt que supposée :
    * `-delete_rpath` ne retire **qu'une occurrence** par appel, et un Mach-O
      peut porter le même rpath deux fois (régime normal d'une reconstruction,
      cf. `_try_add_rpath`). La boucle relit et recommence jusqu'à épuisement.
    * Les fichiers sont dédoublonnés par `realpath` : l'énumérateur suit les
      liens, et `install_name_tool` sur un lien écrit dans sa cible.
    * L'appel passe par `_run` (`check=True`) : un échec de réécriture laisse
      un Mach-O que le contrôle suivant refusera, et il vaut mieux qu'il échoue
      ici, nommément, qu'à la garde de session sur un poste installé.

    Se place AVANT `assert_no_package_manager_refs` et donc avant
    `_sign_everything` : `install_name_tool` invalide la signature, et un Mach-O
    laissé non signé est tué par le noyau (SIGKILL, rc=137).
    """
    removed: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path in _macho_files(root):
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        while True:
            offending = [value for kind, value in macho.load_command_paths(path)
                         if kind == "LC_RPATH"
                         and value.startswith(macho.HOMEBREW_PREFIX_MARKER)]
            if not offending:
                break
            _run([INSTALL_NAME_TOOL, "-delete_rpath", offending[0], str(path)])
            removed.append((path, offending[0]))
    return removed


def _strip_package_manager_startup_hooks(root: Path) -> list[Path]:
    """Retire les hooks de démarrage Python qui nomment le gestionnaire de paquets.

    Mesuré le 2026-08-23, sur le bundle fraîchement reconstruit et par le chemin
    EXACT que le shim emprunte (`-I`) : `sys.path` portait encore
    `/opt/homebrew/lib/python3.14/site-packages`, et `_distutils_hack` — un
    module dont le fichier est sous `/opt/homebrew` — était **importé à chaque
    démarrage**, dans le processus qui porte le consentement TCC. Les trois
    `.pth` de ce dossier s'exécutent à chaque lancement, et `brew`/`pip` les
    réécrivent sans prévenir.

    La cause : `lib/python3.14/sitecustomize.py`, écrit par Homebrew et copié
    tel quel avec le framework. Sa fin s'exécute sans condition —
    `site.addsitedir('/opt/homebrew/lib/python3.14/site-packages')` — que
    l'interpréteur soit celui du Cellar ou celui du bundle.

    C'est la route jumelle de celle que `_prune_dangling_symlinks` ferme déjà,
    avec le même motif écrit : « le lien réintroduirait dans le `sys.path` du
    bundle les paquets d'une installation Homebrew dont on cherche précisément à
    se détacher ». Une des deux était fermée.

    Le critère est le gestionnaire de paquets NOMMÉ dans le fichier, pas le nom
    du fichier : un hook local sans dépendance externe n'a pas à disparaître.
    """
    removed: list[Path] = []
    for pattern in PACKAGE_MANAGER_STARTUP_HOOK_NAMES:
        for path in sorted(root.rglob(pattern)):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if macho.HOMEBREW_PREFIX_MARKER not in text:
                continue
            path.unlink()
            removed.append(path)
    return removed


def _prune_dangling_symlinks(root: Path) -> None:
    """Remplace tout lien symbolique pointant hors du bundle par un dossier vide.

    Mesuré : le framework Homebrew porte `lib/python3.14/site-packages` ->
    `/opt/homebrew/lib/python3.14/site-packages`. Ce lien pend une fois le
    framework copié, et `codesign --verify --strict --deep` échoue alors sur un
    « No such file or directory » qui ne nomme PAS le lien fautif — l'erreur
    remonte au bundle entier. C'est aussi une fuite : le lien réintroduirait
    dans le `sys.path` du bundle les paquets d'une installation Homebrew dont on
    cherche précisément à se détacher.
    """
    for path in list(root.rglob("*")):
        if path.is_symlink() and not path.exists():
            path.unlink()
            path.mkdir()


def _chmod_w(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o200)


def _try_add_rpath(macho: Path, rpath: str) -> None:
    """Pose un rpath en tolérant qu'il soit DÉJÀ présent.

    C-3 : le build est idempotent, donc `-add_rpath` échoue légitimement à
    chaque reconstruction sur un Mach-O déjà relocalisé. C'est le seul échec
    d'`install_name_tool` toléré ici — les `-change`, eux, passent par `_run`
    et arrêtent le build.
    """
    subprocess.run(
        [INSTALL_NAME_TOOL, "-add_rpath", rpath, str(macho)],
        capture_output=True, timeout=EXTERNAL_CALL_TIMEOUT_SECONDS,
        text=True,
    )


def _vendor_third_party_dylibs(vdir: Path, lib_root: Path) -> None:
    """Copie à plat les dylibs tierces (sqlite3, openssl, xz, zstd, mpdecimal).

    Sans ce passage, `import sqlite3` dépendrait encore de l'état de Homebrew —
    exactement ce dont on cherche à se détacher. La fermeture est transitive :
    une dylib vendue peut elle-même référencer le Cellar.
    """
    pending = set()
    for macho in _macho_files(vdir):
        for ref in _homebrew_refs(macho):
            if "Python.framework" not in ref:
                pending.add(ref)
    seen: set[str] = set()
    while pending:
        ref = pending.pop()
        base = os.path.basename(ref)
        if base in seen:
            continue
        seen.add(base)
        target = lib_root / base
        shutil.copy2(os.path.realpath(ref), target)
        _chmod_w(target)
        _run([INSTALL_NAME_TOOL, "-id", f"@rpath/{base}", str(target)])
        pending.update(_homebrew_refs(target))


def _rewrite_vendored_dylibs(lib_root: Path) -> None:
    """Réécrit en `@rpath` les références des dylibs vendues, et les rend
    résolvables depuis leur propre répertoire (`@loader_path`)."""
    for lib in lib_root.glob("*.dylib"):
        for ref in _homebrew_refs(lib):
            _run([INSTALL_NAME_TOOL, "-change", ref,
                  f"@rpath/{os.path.basename(ref)}", str(lib)])
        _try_add_rpath(lib, "@loader_path")


def _rewrite_framework_machos(vdir: Path, lib_root: Path, dylib_id: str) -> None:
    """Réécrit tout le reste du framework : stub `Python.app`, `bin/`,
    `lib-dynload/*.so`.

    Le stub est le cas trompeur mesuré le 2026-08-18 : `otool -L` de
    l'exécutable peut être propre alors que c'est lui qui recharge la dylib du
    Cellar. La boucle est rejouée jusqu'à ce que la sonde ne trouve plus rien —
    `install_name_tool` ne réécrit pas toujours toutes les références en une
    passe.
    """
    for macho in _macho_files(vdir):
        refs = _homebrew_refs(macho)
        if not refs:
            continue
        _chmod_w(macho)
        while refs:
            for ref in refs:
                new = dylib_id if "Python.framework" in ref else \
                    f"@rpath/{os.path.basename(ref)}"
                _run([INSTALL_NAME_TOOL, "-change", ref, new, str(macho)])
            refs = _homebrew_refs(macho)
        up = os.path.relpath(lib_root, macho.parent)
        _try_add_rpath(macho, f"@loader_path/{up}")
        _try_add_rpath(macho, f"@executable_path/{up}")


def _relocate(contents: Path, vdir: Path, exe: Path, dylib_id: str) -> None:
    """Réécrit tout chemin de chargement absolu vers Homebrew.

    Mesuré : sans ce passage, le bundle recharge la dylib du Cellar via le stub
    `Resources/Python.app` et `sys.prefix` repointe vers /opt/homebrew.
    """
    lib_root = contents / "Frameworks"

    fw_dylib = vdir / "Python"
    _chmod_w(fw_dylib)
    _run([INSTALL_NAME_TOOL, "-id", dylib_id, str(fw_dylib)])

    _vendor_third_party_dylibs(vdir, lib_root)
    _rewrite_vendored_dylibs(lib_root)

    for ref in _homebrew_refs(exe):
        _run([INSTALL_NAME_TOOL, "-change", ref, dylib_id, str(exe)])
    _try_add_rpath(exe, "@executable_path/../Frameworks")

    _rewrite_framework_machos(vdir, lib_root, dylib_id)


def _sign_everything(dest: Path, identity: str, entitlements: str | None,
                     framework_version: Path | None = None,
                     bundle_identifier: str | None = None) -> None:
    """Signe du plus profond vers la racine.

    `install_name_tool` a invalidé les signatures ad-hoc d'origine ; un Mach-O
    laissé dans cet état est tué par le noyau (mesuré : SIGKILL, rc=137).
    """
    for sig in dest.rglob("_CodeSignature"):
        shutil.rmtree(sig, ignore_errors=True)
    machos = sorted(_macho_files(dest), key=lambda p: len(p.parts), reverse=True)
    for macho in machos:
        if macho == dest / "Contents" / "MacOS" / BUNDLE_NAME:
            continue
        # `_run` (check=True) : un `codesign` en échec sur un Mach-O imbriqué
        # laisserait un binaire tué par le noyau (SIGKILL, rc=137) dans un build
        # annoncé « construit et signé ».
        shim = macho == dest / "Contents" / "MacOS" / SHIM_NAME
        _run(codesign_command(
            str(macho), identity, entitlements,
            identifier=bundle_identifier if shim else None,
        ))
    if framework_version is not None:
        # Le framework est un bundle versionné : il se scelle lui-même, sans quoi
        # `--deep --strict` bute sur `Versions/Current/.`.
        _run(codesign_command(str(framework_version), identity, entitlements))
    _run(codesign_command(str(dest), identity, entitlements))


def main(argv: list[str]) -> int:
    """`python3 -m build.bundle [destination] [--config <fichier>]`.

    Sans destination, celle de la configuration : le chemin d'installation
    n'est plus une décision gravée dans la source publiée (INV-003-8).
    """
    args, config = list(argv[1:]), BUILD_IDENTITY_CONFIG
    if "--help" in args or "-h" in args:
        print(USAGE)
        return 0
    if "--config" in args:
        index = args.index("--config")
        if index + 1 >= len(args):
            print("build: --config attend un chemin", file=sys.stderr)
            return 1
        config = Path(args[index + 1])
        del args[index:index + 2]
    try:
        # La destination de la ligne de commande subit la MÊME forme que celle
        # de la configuration. Elle y échappait, alors que la même donnée
        # passée par `--config` était validée : un écart de traitement sur la
        # valeur qui atterrit dans une chaîne C et dans un `sh` est une
        # invitation, même quand il n'est pas exploitable.
        destination = None
        if args:
            try:
                _requirement_value(args[0], _INSTALL_PATH_FORM)
            except BundleError as exc:
                raise BundleError(
                    f"destination hors forme : {args[0]!r} — elle subit la "
                    "forme du champ install_path de la configuration "
                    f"({_INSTALL_PATH_FORM.pattern!r})") from exc
            destination = Path(args[0])
        dest = build(destination, config=config)
    except (BundleError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or ""
        print(f"build: {exc}\n{detail}", file=sys.stderr)
        return 1
    print(f"bundle construit et signé : {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
