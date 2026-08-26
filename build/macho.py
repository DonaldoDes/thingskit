"""macho — lire la table des commandes de chargement d'un Mach-O, sans processus.

Pourquoi ce module existe. Le contrôle d'autonomie du bundle (INV-001-1,
constitution § Zones sensibles 3 : « le build échoue si une référence
`/opt/homebrew` subsiste dans un Mach-O du bundle ») a été écrit contre
`otool -L`. Or `otool -L` n'imprime QUE `LC_ID_DYLIB` et `LC_LOAD_DYLIB` : il
est structurellement aveugle à `LC_RPATH`, qui est pourtant la première entrée
du chemin de recherche de dyld. Mesuré le 2026-08-23 sur
`/Applications/thingskit.app` : 92 Mach-O, **83 portant un `LC_RPATH` vers
/opt/homebrew**, `/opt/homebrew/lib` **premier dans l'ordre de recherche sur
les 83**, et `otool -L` en voyait **zéro**. Sept de ces binaires — dont `_ssl`
et `_hashlib` — chargent par `@rpath` une dylib qui existe réellement dans
`/opt/homebrew/lib` : seule la *library validation* implicite du hardened
runtime empêchait le chargement. Le contrôle était vert, et son docstring
affirmait « No Mach-O file anywhere inside the bundle references /opt/homebrew ».

Ce que ce module lit : la table des commandes de chargement, telle que dyld la
lit. Toutes les commandes porteuses de chemin, sur **toutes les tranches** d'un
binaire universel. Aucun sous-processus (le contrôle tournait 92 `otool -L` par
appel).

Ce qu'il ne lit **pas**, délibérément : les octets bruts du fichier. Mesuré sur
le même bundle, `Contents/Frameworks/libcrypto.3.dylib` porte
`/opt/homebrew/etc/openssl@3` (OPENSSLDIR), `ENGINESDIR` et `MODULESDIR` en
**données compilées**, avec un `LC_RPATH` propre (`@loader_path`). dyld ne les
lit pas, `install_name_tool` ne peut pas les réécrire, et aucune reconstruction
de CE projet ne peut les retirer — elles viennent du paquet openssl de
Homebrew. Un balayage d'octets rendrait donc le contrôle rouge pour toujours,
sur un défaut que personne ne peut corriger : c'est ainsi qu'un lecteur apprend
à sauter la garde. La frontière est nommée ici pour qu'elle soit un choix, pas
un oubli.

⚠️ Ce fichier a une **copie vendue** dans le vault
(`.claude/scripts/macho_loadcmds.py`), parce que la garde de session doit
tourner sur un poste où ce dépôt n'est pas cloné. Les deux copies sont
**identiques à l'octet près**, épinglées par test des deux côtés — même idiome
que `verify_embedded_copy` pour `bin/thingskit`. Toute modification se fait
ici, puis se recopie.
"""

from __future__ import annotations

import os
import stat
import struct
from pathlib import Path

FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
MH_MAGIC = 0xFEEDFACE
MH_MAGIC_64 = 0xFEEDFACF

LC_REQ_DYLD = 0x80000000

# Toute commande de cette table range son chemin dans une `lc_str`, dont
# l'offset (relatif au début de la commande) tient dans le u32 à l'octet 8 —
# `dylib_command`, `rpath_command`, `dylinker_command` et `sub_*_command`
# partagent cette disposition. C'est ce qui permet un décodage uniforme.
PATH_BEARING_LOAD_COMMANDS: dict[int, str] = {
    0x0C: "LC_LOAD_DYLIB",
    0x0D: "LC_ID_DYLIB",
    0x0E: "LC_LOAD_DYLINKER",
    0x0F: "LC_ID_DYLINKER",
    0x12: "LC_SUB_FRAMEWORK",
    0x13: "LC_SUB_UMBRELLA",
    0x14: "LC_SUB_CLIENT",
    0x15: "LC_SUB_LIBRARY",
    0x18 | LC_REQ_DYLD: "LC_LOAD_WEAK_DYLIB",
    0x1C | LC_REQ_DYLD: "LC_RPATH",
    0x1F | LC_REQ_DYLD: "LC_REEXPORT_DYLIB",
    0x20: "LC_LAZY_LOAD_DYLIB",
    0x23 | LC_REQ_DYLD: "LC_LOAD_UPWARD_DYLIB",
    0x27: "LC_DYLD_ENVIRONMENT",
}

# Un en-tête sain en annonce quelques dizaines. Le plafond n'est pas une
# élégance : sans lui, quatre octets de bruit relus comme `ncmds` font boucler
# le décodeur sur un fichier corrompu, dans une garde qui tourne à chaque
# démarrage de session.
MAX_PLAUSIBLE_NCMDS = 4096


class MachOParseError(Exception):
    """Le fichier n'a pas pu être lu comme un Mach-O.

    Levée, jamais avalée en liste vide : « je n'ai pas pu lire » et « aucune
    référence » sont deux verdicts opposés, et les confondre est exactement le
    fail-open que ce module remplace.
    """


def _u32(blob: bytes, offset: int, little: bool) -> int:
    if offset + 4 > len(blob):
        raise MachOParseError(f"lecture u32 hors limites à l'offset {offset}")
    return struct.unpack_from("<I" if little else ">I", blob, offset)[0]


def _u64(blob: bytes, offset: int, little: bool) -> int:
    if offset + 8 > len(blob):
        raise MachOParseError(f"lecture u64 hors limites à l'offset {offset}")
    return struct.unpack_from("<Q" if little else ">Q", blob, offset)[0]


def _slice_load_command_paths(blob: bytes, base: int) -> list[tuple[str, str]]:
    """Commandes porteuses de chemin d'UNE tranche, commençant à `base`."""
    if base + 4 > len(blob):
        raise MachOParseError(f"tranche tronquée à l'offset {base}")
    magic_be = struct.unpack_from(">I", blob, base)[0]
    magic_le = struct.unpack_from("<I", blob, base)[0]
    if magic_be in (MH_MAGIC, MH_MAGIC_64):
        little, sixty_four = False, magic_be == MH_MAGIC_64
    elif magic_le in (MH_MAGIC, MH_MAGIC_64):
        little, sixty_four = True, magic_le == MH_MAGIC_64
    else:
        raise MachOParseError(
            f"magie Mach-O inconnue à l'offset {base}: {blob[base:base + 4]!r}")

    header_size = 32 if sixty_four else 28
    ncmds = _u32(blob, base + 16, little)
    if ncmds > MAX_PLAUSIBLE_NCMDS:
        raise MachOParseError(f"ncmds invraisemblable ({ncmds}) à l'offset {base}")

    found: list[tuple[str, str]] = []
    cursor = base + header_size
    for _ in range(ncmds):
        cmd = _u32(blob, cursor, little)
        cmdsize = _u32(blob, cursor + 4, little)
        # cmdsize nul ferait boucler à l'infini ; débordant, sortirait du fichier.
        if cmdsize < 8 or cursor + cmdsize > len(blob):
            raise MachOParseError(
                f"cmdsize invalide ({cmdsize}) pour la commande à l'offset {cursor}")
        name = PATH_BEARING_LOAD_COMMANDS.get(cmd)
        if name is not None:
            str_offset = _u32(blob, cursor + 8, little)
            if not 8 < str_offset < cmdsize:
                raise MachOParseError(
                    f"lc_str hors de sa commande ({str_offset} pour cmdsize "
                    f"{cmdsize}) à l'offset {cursor}")
            raw = blob[cursor + str_offset:cursor + cmdsize]
            found.append((name, raw.split(b"\x00", 1)[0].decode("utf-8", "replace")))
        cursor += cmdsize
    return found


def load_command_paths(path: str | Path) -> list[tuple[str, str]]:
    """`[(nom de commande, chemin)]` pour tout le fichier, toutes tranches.

    Lève `MachOParseError` si le fichier n'est pas lisible comme un Mach-O —
    y compris `OSError` traduite : un fichier illisible ne doit jamais se
    présenter comme un fichier sans référence.
    """
    try:
        blob = Path(path).read_bytes()
    except OSError as exc:
        raise MachOParseError(f"{path}: {exc}") from exc
    if len(blob) < 8:
        raise MachOParseError(f"{path}: {len(blob)} octets, trop court pour un Mach-O")

    magic = struct.unpack_from(">I", blob, 0)[0]
    if magic in (FAT_MAGIC, FAT_MAGIC_64):
        nfat = struct.unpack_from(">I", blob, 4)[0]
        if not 1 <= nfat < MAX_PLAUSIBLE_FAT_ARCH_COUNT:
            raise MachOParseError(f"{path}: nfat_arch invraisemblable ({nfat})")
        wide = magic == FAT_MAGIC_64
        entry_size = 32 if wide else 20
        out: list[tuple[str, str]] = []
        for index in range(nfat):
            entry = 8 + index * entry_size
            offset = (_u64(blob, entry + 8, False) if wide
                      else _u32(blob, entry + 8, False))
            if offset >= len(blob):
                raise MachOParseError(
                    f"{path}: tranche {index} annoncée à l'offset {offset}, "
                    f"hors d'un fichier de {len(blob)} octets")
            out.extend(_slice_load_command_paths(blob, offset))
        return out

    try:
        return _slice_load_command_paths(blob, 0)
    except MachOParseError as exc:
        raise MachOParseError(f"{path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Trouver les Mach-O d'un bundle
# ---------------------------------------------------------------------------
#
# Ce classifieur vivait dans la garde du vault. Il est remonté ici le
# 2026-08-23 parce que le build en avait un SECOND, différent : `_macho_files`
# retenait un fichier sur son suffixe (`.so`/`.dylib`) ou son bit d'exécution,
# et **écartait les liens symboliques**. Mesuré sur le vrai bundle : la garde
# voyait 92 Mach-O, le contrôle du build 142 — dont 55 qui n'étaient pas des
# Mach-O du tout (scripts shell de `bin/`), et **5 Mach-O que le build ne
# regardait jamais**, tous des liens, dont `Python.framework/Python` et
# `libpython3.14.dylib`, c'est-à-dire précisément les fichiers porteurs de la
# dépendance Homebrew. Deux énumérateurs pour un même invariant, c'est le
# contrôle de construction et la garde de session qui peuvent se contredire.

HOMEBREW_PREFIX_MARKER = "/opt/homebrew"

# Mach-O magics, as the 4 first bytes appear ON DISK. `file` recognises
# exactly these; anything else it names something other than "Mach-O".
MACHO_THIN_MAGICS = frozenset(
    {
        b"\xfe\xed\xfa\xce",  # MH_MAGIC     32-bit, big-endian on disk
        b"\xce\xfa\xed\xfe",  # MH_CIGAM     32-bit, byte-swapped (i386)
        b"\xfe\xed\xfa\xcf",  # MH_MAGIC_64  64-bit, big-endian on disk
        b"\xcf\xfa\xed\xfe",  # MH_CIGAM_64  64-bit, byte-swapped (arm64/x86_64)
    }
)
# Fat/universal archive headers. Per the Mach-O spec these are ALWAYS stored
# big-endian, which is why the byte-swapped forms (0xbebafeca / 0xbfbafeca)
# are deliberately absent: `file` does not call them Mach-O either, and
# accepting them here would make this classifier disagree with the reference
# implementation it must reproduce exactly.
MACHO_FAT_MAGICS = frozenset({b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"})
# 0xCAFEBABE is shared with the Java .class format. `file` tells them apart on
# the u32 at offset 4: nfat_arch for a fat archive (a handful of slices),
# major class-file version for Java (>= 45 since Java 1.1). Same cut-off here.
MAX_PLAUSIBLE_FAT_ARCH_COUNT = 20


class BundleScanError(Exception):
    """L'arbre n'a pas pu être énuméré.

    `os.walk` avale toute `OSError` par défaut : un `Contents/` devenu
    illisible rend zéro fichier, donc zéro Mach-O, donc zéro anomalie — le même
    vert que 92 fichiers propres. Levée pour que le balayage se lise
    « non établi » au lieu de « rien trouvé ».
    """


def is_macho_magic(head: bytes | None) -> bool:
    """True when `head` (the first bytes of a file) starts a Mach-O image.

    Reproduces the classification `file -b` performs, including its
    disambiguation of 0xCAFEBABE between a fat Mach-O archive and a Java
    .class file. Tolerates None and short reads: an empty or truncated file
    is simply not a Mach-O, never an exception.
    """
    if not head or len(head) < 4:
        return False
    magic = head[:4]
    if magic in MACHO_THIN_MAGICS:
        return True
    if magic in MACHO_FAT_MAGICS:
        if len(head) < 8:
            return False
        nfat_arch = int.from_bytes(head[4:8], "big")
        return 1 <= nfat_arch < MAX_PLAUSIBLE_FAT_ARCH_COUNT
    return False


def _read_magic(path: str, size: int = 8) -> bytes | None:
    """First `size` bytes of `path`, or None if it is not readable as a file.

    Two guards, both there to reproduce `file`'s behaviour rather than to be
    defensive for its own sake:

    * `os.stat` FOLLOWS symlinks -- verified 2026-08-22 on the real bundle,
      where macOS `file -b` also dereferences by default and reported 5
      symlinked Mach-O paths (Python.framework/Python and friends) as
      Mach-O. Skipping symlinks here would silently drop those 5 files from
      the /opt/homebrew autonomy scan.
    * S_ISREG excludes FIFOs, sockets and device nodes. `file` names them and
      moves on; opening a FIFO for reading BLOCKS until a writer appears,
      which would hang this guard -- and it runs at every session start.

    Anything unreadable (broken symlink, mode 000, EIO) yields None, matching
    `file`'s "cannot open" / "broken symbolic link", which are not Mach-O.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read(size)
    except OSError:
        return None


def find_macho_files(bundle_path: str, walk_fn=os.walk, read_magic_fn=None) -> list[str]:
    """Every Mach-O under `bundle_path`, classified WITHOUT spawning anything.

    This used to run `file -b <path>` once per file. On the real bundle that
    is 3614 subprocesses for 92 hits: measured 2026-08-22 at 10.3 s of the
    guard's 11.1 s total, nearly all of it spent in select.poll() waiting on
    processes rather than doing work. The classification only ever needed the
    file's first 4 bytes, which Python reads directly. Same result set
    (verified path-by-path against the `file` implementation on the real
    bundle), ~150x less wall-clock.
    """
    read_magic = read_magic_fn or _read_magic
    macho_files: list[str] = []
    walk_errors: list[OSError] = []
    for root, _dirs, files in walk_fn(bundle_path, onerror=walk_errors.append):
        for name in files:
            path = os.path.join(root, name)
            if is_macho_magic(read_magic(path)):
                macho_files.append(path)
    # os.walk's default is to SWALLOW every OSError. A Contents/ that turned
    # unreadable then yields zero files, zero Mach-O and zero findings -- the
    # same green as 92 clean ones. Measured 2026-08-23. The only honest reading
    # of a partial walk is that the scan established nothing.
    if walk_errors:
        first = walk_errors[0]
        extra = (f" (and {len(walk_errors) - 1} other error(s))"
                 if len(walk_errors) > 1 else "")
        raise BundleScanError(
            f"{getattr(first, 'filename', bundle_path)}: {first}{extra}")
    return macho_files
