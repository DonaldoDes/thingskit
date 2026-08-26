# Spec — Empaquetage en bundle signé (`thingskit.app`)

Décision de référence : ADR-001, vault `products/thingskit/foundation/decisions/`.
US : US-004, milestone Backlog Fixes.

## Overview

`bin/thingskit` a pour shebang `#!/usr/bin/env python3` (ligne 1). Un script ne porte pas d'identité de code : c'est l'interpréteur qui la porte. Le Python Homebrew est signé ad-hoc et son identifiant change à chaque montée de version (mesuré : `python3-5555494470c5…` en 3.14.6, `python3-55554944b6bd…` en 3.14.7). Le consentement TCC `kTCCServiceSystemPolicyAppData`, requis par la lecture du Group Container Things (`bin/thingskit` l. 176, 247-259, ouverture en `mode=ro`), est donc invalidé à chaque `brew upgrade python`.

Objectif : rendre l'identité de code stable en empaquetant `thingskit` dans un `.app` signé embarquant son propre interpréteur.

## Structure de l'artefact

```
thingskit.app/
  Contents/
    Info.plist              CFBundleIdentifier = <configure, cf. ADR-003>
    MacOS/thingskit         interpréteur Python vendu (porte l'identité de code)
    Resources/thingskit     copie octet pour octet de bin/thingskit
    Frameworks/             runtime de l'interpréteur
```

Entrée `PATH` : `~/.local/bin/thingskit` devient un lanceur qui **`exec`** l'exécutable du bundle avec `"$@"`. Interdit : `open -a` (ne transmet ni la sortie ni le code de sortie).

Le lanceur porte deux gardes, l'une et l'autre ajoutées après mesure d'un défaut réel (rework du 2026-08-18) :

- **`-I` avant le script embarqué.** Sans lui, l'interpréteur honore `PYTHONPATH` et exécute `sitecustomize.py` **dans le processus qui porte l'identité du bundle** — l'identité même à laquelle `kTCCServiceSystemPolicyAppData` est accordé. Mesuré : un `sitecustomize.py` posé dans un répertoire de `PYTHONPATH` s'exécute et la commande fonctionne ensuite normalement ; avec `-I`, l'injection disparaît et la sortie est identique. Le hardened runtime bloque `DYLD_INSERT_LIBRARIES`, il n'a jamais couvert les variables propres à Python. `bin/thingskit` n'importe que la stdlib, donc `-I` (soit `-E -s -P`) est sans effet de bord.
- **Le sceau est évalué au lancement.** `Contents/Resources/thingskit` appartient à l'utilisateur : modifiable sans élévation, et le code modifié tournerait avec le grant. Le sceau ne le détectait qu'*a posteriori*, jamais au lancement. Le lanceur exécute donc `/usr/bin/codesign --verify --strict "$APP"` avant l'`exec`, et refuse en nommant la cause (code `126`, distinct de tout code du chemin nominal — `main()` rend 0, 1 ou 2, argparse rend 2 ; `126` reste néanmoins ambigu avec le `126` que rend `sh` quand l'`exec` final échoue, cas d'un `Contents/MacOS/thingskit` absent : ce sont les messages sur `stderr` qui séparent les deux causes, pas le code). Coût mesuré (20 runs par cas, 2026-08-18) : 12,6 ms pour `codesign --verify --strict` seul ; 80,1 ms pour un `thingskit areas` sans le contrôle de sceau, contre 103,9 ms via le lanceur — soit ~24 ms de surcoût, dont ~13 ms de `codesign` lui-même, le reste étant le processus supplémentaire forké par le lanceur.

Le chemin de `codesign` est **absolu**, jamais résolu par `PATH` : un vérificateur détournable par l'environnement ne vérifie rien — c'est exactement la classe de défaut que `-I` referme.

## Contraintes

- **C-1** — Le code de sortie de l'exécutable du bundle remonte intact à l'appelant. Le contrat `0` = effet constaté est l'invariant central du projet.
- **C-2** — `bin/thingskit` reste l'unique source de vérité ; le contenu de `Contents/Resources/thingskit` en est une copie produite par le build, jamais éditée sur place.
- **C-3** — Le build est idempotent et reconstruit le bundle depuis zéro.
- **C-4** — Aucun test automatisé ne dépend du bundle installé, de Things, ni de la vraie base. La suite passe sur un poste où `thingskit.app` n'existe pas.
- **C-5** — Signature : `--options runtime`, sous **une identité de développement de l'équipe nommée par `build/identity.conf`** (ADR-003 ; l'équipe était écrite en dur ici jusqu'au 2026-08-26) — jamais un certificat nommé en dur. L'identité est résolue sur le poste au moment du build (`resolve_signing_identity`), par lecture de l'OU du sujet du certificat feuille, et c'est son empreinte SHA-1 qui est passée à `codesign -s`. Aucune identité de l'équipe sur le poste ⇒ **refus explicite avant toute écriture**, jamais de repli ad-hoc. Plusieurs identités éligibles ⇒ sélection déterministe par ordre de (nom, empreinte), annoncée sur `stderr`. L'exigence de code opposée au bundle (`identifier` + `certificate leaf[subject.OU]`) est **inchangée** : elle n'a jamais porté sur le nom de la feuille (BUG-010).
- **C-6** — Build et signature sont **par poste** (Mac Studio, MacBook Air) ; le grant TCC est local à la machine.
- **C-7** — Aucun entitlement n'est ajouté sans mesure établissant sa nécessité (cf. NC-4 de l'ADR).

## Test IDs

| ID | Type | Description |
|----|------|-------------|
| BNDL-01 | Unit | Le générateur d'`Info.plist` pose le `CFBundleIdentifier` configuré et `CFBundleExecutable = thingskit` |
| BNDL-02 | Unit | La ligne `codesign` composée porte `--options runtime` et l'identité attendue |
| BNDL-03 | Unit | Le lanceur transmet `argv` sans altération, y compris arguments vides et contenant des espaces |
| BNDL-04 | Unit | Le lanceur remplace son image de processus (`exec`), il ne crée pas de sous-processus |
| BNDL-05 | Unit | Le build refuse de produire un bundle si `bin/thingskit` et la copie `Resources/` diffèrent |
| BNDL-06 | Integration | La suite pytest existante passe inchangée, sans `thingskit.app` installé (garde C-4) |
| BNDL-07 | Manuel | `codesign -dv --verbose=4` rend l'`Identifier` et le `TeamIdentifier` de `build/identity.conf` |
| BNDL-08 | Manuel | Après `brew upgrade python`, sans réinstallation : mêmes `Identifier`/`TeamIdentifier` qu'avant |
| BNDL-09 | Manuel | Après `brew upgrade python` : `timeout 20 thingskit areas` rend la main sans dialogue de consentement |
| BNDL-10 | Manuel | Le code de sortie est préservé de bout en bout : une commande en échec via le lanceur rend un code non nul identique à l'invocation directe |
| BNDL-11 | Manuel | Sans entitlement `disable-library-validation`, `Contents/MacOS/thingskit -c "import sqlite3"` réussit (tranche NC-4) |
| BNDL-12 | Unit | Le lanceur pose `-I` **avant** le script embarqué (posé après, il devient un `argv` du script au lieu d'un drapeau) |
| BNDL-13 | Adversité | Un `sitecustomize.py` posé dans `PYTHONPATH` ne s'exécute pas, et la commande rend le même résultat |
| BNDL-14 | Adversité | Le lanceur refuse un bundle au sceau invalide, avant tout `exec`, avec un message nommant la cause |
| BNDL-15 | Adversité | Sur une **copie** du bundle installé, l'altération d'une ressource est refusée par le vrai `codesign` (test sauté si le bundle n'est pas installé — C-4) |
| BNDL-16 | Unit | Le code de refus (`126`) n'entre en collision avec aucun code du chemin nominal (C-1) |
| BNDL-17 | Unit | Le vérificateur de sceau est invoqué par chemin absolu, jamais résolu par `PATH` |
| BNDL-18 | Unit | Un `codesign` en échec sur un Mach-O imbriqué **arrête** le build au lieu d'annoncer « construit et signé » |
| BNDL-19 | Unit | `-add_rpath` déjà posé n'interrompt pas la reconstruction (C-3) |
| BNDL-20 | Adversité | Le build oppose l'exigence de code à l'artefact qu'il vient de produire : un `.app` réellement signé ad-hoc — donc au sceau **valide** — est refusé, et le message nomme l'exigence, l'artefact et l'identité employée (BUG-013) |
| BNDL-21 | Unit | La vérification est câblée **après** `_sign_everything` dans `build()` : opposée avant la signature, elle ne vérifierait rien |

BNDL-07 à BNDL-11 sont **manuels et assumés comme tels**, au même titre que la § Limite de couverture assumée de la constitution. BNDL-08 et BNDL-09 sont **deux conditions distinctes** : la stabilité de `codesign` ne vaut jamais preuve de persistance du grant.

## Migration

1. Ajouter le script de build et l'`Info.plist` au repo.
2. Construire, signer, installer en `/Applications/thingskit.app`.
3. Vérifier BNDL-07 **avant** de toucher au `PATH`.
4. Basculer `~/.local/bin/thingskit` du symlink vers le lanceur. *(Fait le 2026-08-18, au rework : jusque-là l'entrée `PATH` était restée le symlink vers `bin/thingskit`, donc le bundle n'était pas sur le chemin réellement emprunté.)*
5. Accorder le consentement TCC une fois, sur le nouveau bundle.
6. Amender `constitution.md` : clause d'exposition (§ Raison d'être) + § Zones sensibles n° 3 (identité de code et consentement TCC).

**Rollback**, en un pas : restaurer `~/.local/bin/thingskit` en symlink vers `bin/thingskit`. Le source n'est jamais modifié par l'opération.

## Points ouverts

NC-1 à NC-6 de l'ADR-001. NC-4 est tranché par la mesure (aucun entitlement requis) ; NC-3 (expiration du certificat) reste le risque à instrumenter.

- **NC-3 s'est durci au rework.** Le lanceur conditionne désormais l'exécution à `codesign --verify --strict`. Si l'expiration du certificat (**2027-07-16**) fait échouer cette vérification, l'outil ne se dégrade pas : il **refuse de démarrer**. `--timestamp` est mesuré comme fonctionnel sur ce poste (contre-signature Apple réelle obtenue, `Timestamp=` présent), au prix de **30 s de build au lieu de 13 s** et d'une **dépendance réseau** au service Apple. Ce qui n'est **pas** établi, et ne peut pas l'être avant l'événement déclencheur — même forme que BNDL-08/09 : que `--verify` échoue effectivement une fois le certificat expiré. `--timestamp=none` est donc **laissé en l'état**, et le point est consigné plutôt que tranché à l'aveugle.
- **Le grant reste atteignable hors du lanceur — résidu mesuré, non fermé.** `Contents/MacOS/thingskit` est un interpréteur Python généraliste : tout processus tournant sous l'utilisateur peut l'invoquer directement, sans `-I` et sans contrôle de sceau, et hériter du grant TCC. Reproduit le 2026-08-18 : `/Applications/thingskit.app/Contents/MacOS/thingskit -c "<lecture de la base Things>" "$DB"` rend `LECTURE ARBITRAIRE: (892,)`, `rc=0`. Ce n'est pas une régression de l'empaquetage — c'est inhérent à l'embarquement d'un interpréteur stock, et le chemin antérieur (script + Python Homebrew) ne valait pas mieux. Conséquence retenue : l'invariant de `constitution.md` § Zones sensibles n° 3 est restreint à sa portée réelle (« sur le chemin du lanceur ») plutôt que d'affirmer une fermeture qui n'existe pas. Fermer le trou supposerait un `Contents/MacOS/thingskit` Mach-O ignorant `argv` et chargeant le script scellé en dur : décision d'architecture distincte, non ouverte par ce rework.
- **`PATH` était le `PYTHONPATH` restant — fermé.** `-I` isole l'interpréteur de ses variables `PYTHON*`, jamais de `PATH`. Les commandes invoquées par nom nu depuis `bin/thingskit` (`osascript`, `open`, `pgrep`) étaient donc détournables par un homonyme déposé dans un répertoire du `PATH`, exécutant du code arbitraire sous l'identité porteuse du grant (reproduit le 2026-08-18 avec un stub `osascript`, sceau valide et `-I` posé). Elles passent désormais par des constantes de chemin absolu (`OSASCRIPT`, `OPEN`, `PGREP`), gardées par un balayage d'AST à compte résiduel nul et par un test qui rejoue l'exploit.
- **NC-3 — l'expiration du certificat n'empêchera pas le lanceur de démarrer.** `man codesign`, option `expires` : l'expiration n'est prise en compte à la vérification que si ce drapeau a été posé **à la signature**. Le sceau de l'artefact porte `flags=0x10000(runtime)`, sans `expires` — `--verify --strict` ne devrait donc pas échouer le 2027-07-16. Cette conclusion est **documentaire et structurelle, pas empirique** : aucun spécimen naturellement périmé et non horodaté n'a été trouvé sur le poste pour la confirmer, et elle ne dispense pas de vérifier le jour venu.
- **Importer le script embarqué comme module invalide le sceau — constaté le 2026-08-18.** Charger `Contents/Resources/thingskit` par `import` depuis un autre processus fait écrire un `Contents/Resources/__pycache__/*.pyc` DANS le bundle ; `codesign --verify --strict` rend alors `a sealed resource is missing or invalid`, et le lanceur refuse de démarrer jusqu'à suppression du dossier. Le chemin nominal n'est pas concerné (le script est exécuté comme `__main__`, jamais importé, et Python n'écrit pas de bytecode pour `__main__`), mais toute instrumentation ou adversité doit charger une **copie hors du bundle**, ou poser `sys.dont_write_bytecode`. Le mode d'échec est une indisponibilité, pas une brèche — mais il est silencieux jusqu'au refus suivant.
- **Le résidu de `build/bundle.py` est un chemin vers un artefact *validement signé* et corrompu — pas vers un artefact non signé.** Le build invoque encore `codesign`, `install_name_tool` et `otool` par nom nu, donc résolus par `PATH`. La lecture rassurante serait « au pire la signature échoue et le build s'arrête » ; la mesure du raisonnement dit autre chose : un stub `codesign` placé en tête de `PATH` pendant un build peut modifier le bundle **puis** déléguer à `/usr/bin/codesign`. L'artefact sort alors **valide et signé sous l'équipe configurée** — soit l'identité même à laquelle le grant TCC est accordé, et celle que l'exigence de code du lanceur exige — et il est installé dans `/Applications`. Le contrôle de sceau du lanceur ne peut rien y voir : il est satisfait, à juste titre. Gravité **P2** malgré tout, et pour deux raisons qui tiennent au prérequis, jamais à l'impact : il faut déjà exécuter du code sous l'utilisateur, et le build est manuel et rare. Consigné ici pour que la sous-priorisation soit un choix et non un malentendu de lecture. **Fermé le 2026-08-23** : les 8 sites (1 `otool`, 6 `install_name_tool`, `codesign_command`) sont passés en chemin absolu, et la règle est enforcée par un test qui balaie **tout argv du module** — le balayage en a rendu 7 là où le rapport en nommait 1, et un huitième qu'une première règle trop étroite (arguments d'appel seulement) laissait passer. `otool` a disparu avec le sous-processus : le contrôle d'autonomie lit désormais la table des commandes de chargement en direct.
- **Fraîcheur des dylibs tierces embarquées.** `libcrypto`, `libssl`, `libsqlite3`, `liblzma`, `libzstd`, `libmpdec` sont copiées dans `Contents/Frameworks/` au moment du build, donc **gelées à la version présente sur le poste ce jour-là**. Un `brew upgrade openssl` corrigeant une faille ne les met plus à jour : c'est la contrepartie assumée de l'autonomie recherchée (se détacher de l'état de Homebrew coupe aussi ses correctifs). Aucun mécanisme ne signale aujourd'hui qu'une dylib embarquée est en retard sur celle du système. Chantier distinct, non ouvert par ce rework.
- **Le pilotage de la reconstruction reste manuel.** Rien ne déclenche un rebuild après un `brew upgrade python`, et le lanceur, qui refuse désormais un sceau invalide, rendra ce défaut visible sous forme de refus plutôt que de dialogue TCC. C'est un progrès de diagnostic, pas une automatisation.
