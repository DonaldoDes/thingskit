# Constitution — thingskit

## Raison d'être

`thingskit` est le CLI d'accès à Things 3 (Cultured Code) pour le harness
agentique. Il existe parce que la skill `things` (côté vault) ne porte que
l'usage — aucun code, et surtout aucun code régénéré à la volée par un agent
à chaque appel. Un script réécrit à la demande n'est jamais deux fois le
même, donc jamais testé et jamais fiable. Tout ce que le harness sait faire
avec Things passe par ce dépôt, où c'est versionné, lisible et testé.

Le CLI est un unique script (`bin/thingskit`). Pas de package : c'est un choix
délibéré, pas une étape transitoire — la taille du projet ne justifie pas la
charge d'un `src/` layout.

**L'exposition dans le `PATH` passe par un lanceur, plus par un symlink**
(amendement du 2026-08-18, ADR-001). `~/.local/bin/thingskit` est un script
`sh` qui **`exec`** un shim Mach-O scellé dans `/Applications/thingskit.app`,
lequel **`exec`** à son tour l'interpréteur du bundle (amendement du
2026-08-21, ADR-002 — le shim existe parce que `sh` ne sait pas décider de la
responsabilité de processus, cf. § Zones sensibles 3). Les deux maillons sont
des remplacements d'image : la chaîne n'introduit aucun processus
intermédiaire. Le motif est
mesuré, pas esthétique : un symlink vers un script à shebang délègue l'identité
de code à l'interpréteur du système, dont la version bouge — c'est précisément
ce qui invalidait le consentement TCC (voir § Zones sensibles 3). La clause
d'exposition ne pouvait pas être tenue en même temps que l'objectif ; elle est
amendée, pas contournée. L'invariant « unique script, pas de package », lui,
est intact : `bin/thingskit` reste la source de vérité unique, le bundle en est
un artefact de build (`build/bundle.py`), jamais une seconde implémentation.

## Stratégie git

Toute branche part de `master` — jamais de travail direct dessus. Nommage :
`feat/`, `fix/` ou `docs/` suivi de l'identifiant du ticket quand il y en a un
(`fix/bug-016-…`, `feat/us-006-…`). Commits conventionnels, scope = identifiant
du ticket (`fix(bug-016): …`, `feat(move-project): …`, `docs(bug-005): …`).
Pas de pull request : la fusion est locale, par `git merge --no-ff` sur
`master` (`Merge <branche> (<résumé>)`), suivie d'un `git push` sur `origin`.
La branche fusionnée est ensuite supprimée (`git branch -d`, jamais `-D`) —
elles étaient conservées jusqu'ici, elles ne le seront plus.

Invariant qui prime sur tout ce qui précède : rien n'entre dans `master` sans
que les reviewers aient rendu PASS, et un builder ne fusionne jamais de sa
propre initiative — il rend la main. Seule exception, bornée par un critère
vérifiable et non par une appréciation de taille : quand `git diff --name-only`
ne montre aucun fichier de code ni de test — de la documentation seule, `.md` —
le coordinator vérifie et fusionne lui-même, sans reviewer. Dès qu'un seul
fichier exécutable est touché, fût-ce d'une ligne, le PASS des reviewers
redevient obligatoire.

## Ce que le projet fait, et pourquoi c'est découpé ainsi

- **Lecture = SQLite en lecture seule.** Toutes les sous-commandes de
  lecture (`areas`, `projects`, `headings`, `tasks`, `agenda`, `uuid`,
  `deeplink`) interrogent directement la base Things (`db_path()`, ouverte
  en `mode=ro`). C'est plus rapide, scriptable, et indépendant de l'état de
  l'application (pas besoin que Things soit au premier plan, ni même lancé).
- **Écriture = la surface la moins invasive qui fonctionne**, par ordre de
  préférence : schéma d'URL JSON (`create-project`, `add-task`) >
  AppleScript ciblé (`create-area`, `set-notes`, `append-notes`, `delete-task`,
  `complete-task`, `reschedule-task`) > automatisation d'interface (`create-heading`, en dernier
  recours, quand aucune des deux premières ne couvre le besoin). Le choix se
  fait sur ce que la surface expose réellement, constaté sur pièce :
  `complete-task` reste en AppleScript ciblé parce que `sdef
  /Applications/Things3.app` montre (2026-08-12) `status` comme propriété
  **rw** de la classe `to do`, d'énumération `open` / `completed` /
  `canceled` — contrairement au heading, l'automatisation d'interface est
  ici inutile, donc interdite.
- **Toute affirmation sur ce que Things permet ou ne permet pas est
  constatée par test, jamais déduite de la documentation.** Le docstring en
  tête de `bin/thingskit` recense les constats de comportement (dates
  bit-packées, `heading` ignoré à la création si absent, `project` vide pour
  une tâche sous heading, etc.) — chacun daté et vérifié sur pièce. Toute
  nouvelle sous-commande qui touche à une zone non documentée doit
  documenter ce qu'elle a constaté, à cet endroit, de la même façon.

- **Une lecture doit restituer ce qu'une écriture a posé.** C'est le pendant
  de la garantie « une écriture vérifie son effet avant de rendre la main » :
  cette garantie n'est vérifiable par personne si la façade de lecture tait le
  champ écrit. Constaté le 2026-08-12 — `tasks --json` n'exposait pas `notes`,
  alors que 375 des 659 tâches ouvertes en portent ; `set-notes` réussissait,
  et l'utilisateur, lisant du vide, a réécrit des notes en croyant à un échec.
  Toute sous-commande d'écriture d'un champ impose donc une sous-commande de
  lecture qui restitue ce champ, sous test.
- **Un champ dérivé se documente par une table de vérité mesurée, pas par une
  lecture de colonne.** `start` seul ne dit pas où une tâche apparaît :
  `start=2` est « Un jour » sans date et « À venir » avec une date. La table
  complète (croisement AppleScript `to dos of list` × base, 606 tâches) vit
  dans le docstring de `bin/thingskit` et dans `scheduling_list()`, exposée
  sous le champ `list` de `tasks --json`. Exposer la valeur brute d'une
  colonne dont la sémantique est conditionnelle invite au contresens : c'est
  ce qui a fait diagnostiquer un défaut d'écriture inexistant le 2026-08-12.

## Conventions réelles constatées dans le code

- **Une sous-commande = une fonction `cmd_<nom>(a) -> int`.** Le code
  retour est contractuel : `0` = succès constaté, non nul = échec. Jamais de
  levée d'exception non attrapée jusqu'à `main()` — chaque fonction `cmd_*`
  gère ses propres erreurs et imprime sur `stderr`.
- **Écriture = vérification obligatoire après action, jamais avant.** Toute
  sous-commande d'écriture (`create-area`, `create-project`, `add-task`,
  `set-notes`, `append-notes`, `delete-task`, `create-heading`,
  `complete-task`, `cancel-task`, `reopen-task`, `rename-task`,
  `reschedule-task`, `move-task`, `move-project`) relit la
  base après avoir
  déclenché l'action, et ne retourne `0` que si l'effet est **constaté**.
  Cette énumération se tenait à la main et avait dérivé : quatre commandes
  d'écriture y manquaient au 2026-08-26 (`append-notes`, `cancel-task`,
  `rename-task`, `move-task`), avant même l'ajout de `move-project` et de
  `reopen-task`. Ce que
  la liste omet reste couvert par la garde qui, elle, se DÉRIVE de l'AST
  (`tests/test_write_wait.py`) — mais une liste fausse dans la constitution
  décrit un dépôt qui n'existe pas. Elle est recomposée ici depuis le
  balayage, pas depuis la mémoire :

      python3 -c "import importlib.util,sys; sys.path.insert(0,'.'); \
        s=importlib.util.spec_from_file_location('t','tests/test_write_wait.py'); \
        m=importlib.util.module_from_spec(s); s.loader.exec_module(m); \
        print(sorted(m._reaching_and_waiting()[0]))"
  Un code retour `0` signifie « constaté fait », jamais « commande
  envoyée ». C'est l'invariant central du projet — voir § Zones sensibles.
- **Une attente est une condition observée, jamais une durée devinée.**
  L'écriture est vérifiée après action (ci-dessus) ; ce qui sépare l'action de
  sa vérification est une **boucle de relecture bornée** — `wait_for_effect`,
  qui sonde la base toutes les 25 ms jusqu'à constat de l'effet, avec un
  plafond de 15 s. Les durées fixes qu'elle remplace (1500-2500 ms selon la
  commande, 6000 ms pour `ensure_running`) étaient fautives **dans les deux
  sens**, mesuré le 2026-08-25 sur 10 tâches jetables : ~130× trop longues
  dans le cas courant (11-23 ms de médiane réelle), et **trop courtes** sur la
  queue — une suppression sur dix a mis 5026 ms, donc au-delà du plafond de
  1500 ms, et la commande sortait en **échec sur une écriture réussie**. C'est
  le mode d'échec le plus coûteux du projet : l'appelant qui réessaie sur
  échec produit un doublon dans la base réelle de l'utilisateur. La boucle ne
  décide rien — la vérification post-action et le code retour restent au site
  d'appel, et un plafond atteint reste un échec (BUG-016). Le choix des deux
  constantes est justifié au regard de la mesure dans `bin/thingskit`, et
  **épinglé par test** pour qu'il ne redescende pas en silence. Toute nouvelle
  commande d'écriture passe par cette boucle : c'est vérifié par balayage de
  l'AST, pas par relecture (`tests/test_write_wait.py`).

  **Cinq corollaires, chacun tiré d'une classe de défaut balayée, pas d'une
  instance** (2026-08-25). Le compte a d'abord été écrit « trois » devant
  quatre puces, et le premier corollaire a d'abord été énoncé sur une classe
  qui n'était balayée qu'à moitié : les deux sont corrigés ci-dessous, et le
  balayage manquant est désormais mécanique.

  - **Le retour de la boucle se lit toujours.** Une attente dont on jette le
    verdict ne fait que temporiser, et le site qui le faisait recomposait
    ensuite le même prédicat à côté — deux copies qui divergent, celle qui
    attend cessant de décrire celle qui juge. Balayé sur les 14 appels : 1
    site en défaut (`cmd_move_task`), aligné sur le motif de
    `cmd_reschedule_task`, et la forme est désormais interdite par test.
  - **La branche d'échec ne redemande jamais à la base ce que la sonde vient
    d'observer.** Le message se compose depuis la valeur CAPTURÉE par
    fermeture. Une seconde lecture court après l'effet : s'il atterrit entre
    le dernier sondage et le `print`, elle rend la valeur *attendue* et le
    message affirme un échec en montrant deux valeurs identiques — « titre
    constaté en base = 'Nouveau', attendu 'Nouveau' ». Il fait alors douter du
    code retour, qui lui est juste, et c'est pire qu'un message vague. Pire
    encore quand la base est illisible : la relecture n'a AUCUN filet (celui
    de `_probe_once` ne couvre que la sonde) et la commande meurt sur une
    trace Python au lieu d'un message. Le premier balayage de cette classe
    n'avait vu que le *retour jeté* et laissé les six sites de la *seconde
    lecture* : 6 sur 14 branches d'échec en défaut (`cmd_move_task`,
    `cmd_reschedule_task`, puis `_write_task_notes`, `cmd_complete_task`,
    `cmd_rename_task`, `cmd_cancel_task`). La forme est maintenant interdite
    par balayage d'AST, et la branche de repli « aucun écart observé, base
    restée illisible » est couverte sur les six. Ce que ce balayage couvre est
    MESURÉ, pas déclaré : rejoué sur `dfa7387` — l'état du script où les six
    sites étaient simultanément en défaut — il en trouve 6, sur 14 branches
    d'échec inspectées. Il lui faut pour cela deux propriétés, chacune tirée
    d'une forme réellement rencontrée : *transitif*, parce que le défaut se
    cachait derrière un helper (`_read_task_notes`) et non derrière `q` ;
    *sensible aux définitions imbriquées*, parce que `_move_problem` et
    `_schedule_problems` sont définies DANS le corps de leur commande — sans
    cette seconde propriété le balayage n'en voyait que 4 sur 6, et les deux
    qu'il manquait étaient les deux sites d'origine du bug. Ce qu'il ne couvre
    pas, et qui reste à traiter : la seule forme syntaxique
    `if not wait_for_effect(...)` est reconnue, et la clôture des lecteurs
    s'enracine sur le nom `q`.
  - **Le filet de la sonde ne couvre que ce qui est réellement transitoire**
    (`sqlite3.OperationalError`). `sqlite3.Error` couvrait aussi
    `ProgrammingError` et `InterfaceError` : un SQL fautif devenait 15 s de
    boucle muette puis un message qui accusait l'application. Aucun faux
    succès, mais un diagnostic impossible.
  - **Le balayage voit toutes les formes par lesquelles `sleep` s'atteint**,
    pas la seule qui était employée. Il ne lisait que `time.sleep(...)` :
    mesuré sur trois formes de contrôle (`import time as t`,
    `from time import sleep`, `from time import sleep as nap`), il en voyait
    **0 sur 3**. Il lit maintenant les imports pour établir les noms liés, et
    refuse tout import non relu — une nouvelle surface d'attente force à
    relire la garde au lieu de passer inaperçue.
  - **L'obligation d'attendre se dérive de l'AST**, plus d'une liste tenue à
    la main : toute fonction qui sollicite l'application — par `osa()`,
    `url_open()`, ou en nommant `OPEN`/`OSASCRIPT` dans un `subprocess` —
    doit attendre par la boucle. Une liste en dur ne couvre que ce qu'on a
    pensé à y inscrire ; une commande qui n'attendrait *rien* y échappait par
    construction.
- **Refus explicite sur ambiguïté, jamais de choix implicite.** Dès qu'une
  résolution par titre peut matcher 0 ou 2+ objets, la commande refuse et
  n'exécute rien, plutôt que de « prendre le premier ». Pattern posé par
  `delete-task` (`--title` ambigu → refus, `--id` recommandé), repris à
  l'identique par `create-heading` (résolution du projet cible), par
  `add-task` (résolution de `--list`/`--heading`, BUG-005) et par
  `complete-task`, qui partage littéralement le résolveur de `delete-task`
  (`_resolve_task_by_title`, paramétré par le suffixe de message) plutôt que
  d'en dupliquer un deuxième. Ce résolveur ne joint **jamais** sur `project` :
  une tâche sous heading a cette colonne vide, la joindre la rendrait
  inatteignable.
  `resolve_uuid()` (utilisé par `uuid`/`deeplink`/`headings`/`tasks`, des
  commandes de **lecture**) fait exception : il signale l'ambiguïté sur
  `stderr` mais retourne le premier résultat, ce qui est acceptable pour de
  la lecture informative, pas pour une action qui modifie l'état.
- **Idempotence par vérification préalable.** Toute commande de création
  vérifie d'abord si la cible existe déjà (`create-area`, `create-heading`)
  et sort en succès sans rien recréer si c'est le cas. Même règle pour les
  changements d'état : `complete-task` sur une tâche déjà terminée, comme
  `delete-task` sur une tâche déjà à la Corbeille, sort en `0` en le
  signalant, **sans solliciter l'application** — l'absence d'appel est
  testée, pas seulement le code retour.

  **Sur `move-project`, cette règle n'est pas un confort : c'est le seul
  endroit d'où le défaut est arrêtable.** Un projet DÉJÀ dans l'area visée
  satisfait la vérification post-action *avant même l'ordre* — la relecture
  ne distingue alors pas « fait » de « rien fait », et la commande annonçait
  « projet déplacé » dans les deux cas, avec un code retour `0` parfaitement
  légitime, donc hors de portée de l'invariant central. `move-project` et sa
  jumelle `move-task` visent toutes deux une **appartenance** — un état que
  la cible peut déjà avoir — contrairement aux autres commandes d'écriture
  du script, qui visent un état que la cible n'a pas encore (`trashed`,
  `status`, une valeur de notes) ou une création dont l'existence préalable
  est déjà gardée. **Seule `move-project` garde aujourd'hui ce cas** ; le
  message est DISTINCT (« déjà dans l'area … »), et ni `osa` ni
  `ensure_running` ne sont appelés — c'est cette absence d'appel qui est
  testée
  (`test_a_project_already_in_the_target_area_is_a_no_op_without_any_solicitation`),
  avec sa contre-épreuve contre le sur-court-circuit
  (`test_the_no_op_message_is_not_reused_for_a_real_move`). `move-task`
  n'avait **aucun** pré-check de ce type au 2026-08-26 — `cmd_move_task`
  appelait `osa` directement, sans jamais comparer l'appartenance actuelle à
  la cible.

  **Depuis US-010 (2026-08-27), sa voie `--to-heading` en a un**, et
  l'asymétrie interne est délibérée plutôt que subie : la voie en-tête est
  du code NEUF, et y laisser un défaut connu au motif qu'il existe ailleurs
  serait l'adopter. Le pré-check est celui de `move-project`, à l'identique —
  message DISTINCT (« tâche déjà sous l'en-tête »), aucune sollicitation, et
  c'est cette absence d'appel qui est testée
  (`test_a_task_already_under_the_target_heading_is_a_no_op_without_any_solicitation`),
  avec sa contre-épreuve contre le sur-court-circuit
  (`test_the_no_op_message_is_not_reused_for_a_real_move`). Les voies
  `--to-project` et `--to-area` restent SANS pré-check : leur extension est
  le ticket de suivi séparé déjà ouvert, et l'élargir ici l'aurait fait
  passer pour traité.
- **Une garde d'état se décide par opération, jamais par recopie.** `set-notes`
  et `append-notes` sur une tâche refusent la **Corbeille** et **acceptent** une
  tâche `completed` comme `canceled` — ce n'est pas l'ensemble de gardes de
  `complete-task`, et c'est délibéré. Annoter ne change aucun état : rien de ce
  que l'utilisateur a décidé n'est réécrit, et documenter après coup
  (compte-rendu, lien vers le livrable, motif d'abandon) est précisément
  l'usage attendu — là où `complete-task` refuse `canceled` parce qu'il
  *convertirait* cette décision. La Corbeille, elle, est refusée pour un motif
  qui lui est propre : y écrire déposerait du contenu dans un objet qui
  n'affiche rien dans l'application et disparaît au vidage. Comme pour
  `complete-task`, le refus **précède** toute sollicitation de l'application, et
  les deux formes d'adressage sont gardées séparément (`--task-id` traverse la
  garde, `--task` n'y parvient jamais, `_resolve_task_by_title` filtrant
  `trashed=0` en SQL).
- **Un append ne perd jamais l'existant.** `append-notes` relit les notes
  courantes, concatène derrière et écrit le tout ; l'existant repart à l'octet
  près. C'est le défaut le plus grave possible pour cette commande — silencieux
  et irrécupérable — donc il est gardé par un test qui compare l'**intégralité**
  de l'avant/après, pas la seule présence de la ligne ajoutée
  (`test_append_preserves_existing_notes_byte_for_byte`).
- **La vérification post-action porte sur l'effet DEMANDÉ, jamais sur une
  trace qui l'accompagne — et sur un objet NOUVEAU, jamais sur un homonyme.**
  `add-task` relisait `resolve_uuid("task", titre)` : l'**existence** de la
  tâche. Or elle existe aussi quand le rangement a échoué — Things ignore
  silencieusement un `list` qui ne nomme rien et dépose la tâche en Inbox. La
  vérification était donc verte exactement dans le cas qu'elle devait
  attraper, et le message annonçait un rangement qui n'avait pas eu lieu
  (BUG-005). Elle porte désormais sur le **rattachement relu**
  (`project`/`area`/`heading`), comme `_move_problem` le fait déjà pour
  `move-task`. Second temps, moins visible : « une tâche de ce titre est
  rangée sous la cible » ne prouve rien non plus, puisqu'elle pouvait y être
  avant l'appel — les uuid déjà présents sont donc relevés **avant** d'écrire,
  et seule une tâche absente de ce relevé peut valoir constat
  (`test_a_preexisting_task_at_the_target_never_vouches_for_the_write`).
  Corriger la première moitié sans la seconde ne fait que déplacer le défaut
  d'un cran.
- **Une cible d'écriture se résout AVANT l'envoi, jamais après.** `--list`
  partait tel quel dans le payload. Le refus doit précéder `ensure_running()`
  lui-même — pas seulement `url_open` : lancer l'application est déjà un effet
  visible pour l'utilisateur, sur une commande qui prétend n'avoir rien fait
  (`test_the_resolution_precedes_every_solicitation_of_the_application`).
  Le résolveur est celui de `find-task` (`_resolve_find_target`), pas un
  deuxième : il traite déjà `--list` + `--heading` et refuse la collision
  projet/area, mesurée instanciée sur la base réelle.
- **Le statut de la cible n'est pas filtré à la résolution — et ce silence est
  une décision, pas un oubli.** Les quatre résolveurs de projet par titre du
  script filtrent `trashed=0` et rien d'autre : un projet `completed` se
  résout et se laisse viser, par `move-task` comme par `add-task`. Consigner
  après coup dans un projet qu'on vient de clore est un usage légitime, et le
  sur-refus serait du côté qui casse. Ce que Things fait réellement d'un
  `list` nommant un projet terminé n'est **pas établi** : l'établir impose
  d'écrire dans la base réelle de l'utilisateur, ce qu'aucun test du dépôt ne
  fait. La conséquence est bornée par construction — si la tâche atterrit
  ailleurs, la vérification de rattachement le constate et la commande échoue
  bruyamment, en citant le statut du projet visé **relevé avant l'écriture**.
  Ce statut est un **fait sur la cible**, pas une cause : le lien entre « le
  projet est terminé » et « la tâche part en Inbox » n'est ni mesuré ni
  affirmé ici (mesuré le 2026-08-25 :
  `select status, count(*) from TMTask where type=1 and trashed=0 group by status`
  → 55 ouverts, 4 `completed`, 0 `canceled`).
- **Un refus qui nomme un remède devient faux le jour où le remède change —
  et il devient faux en SILENCE.** `complete-task`, `cancel-task` et
  `reschedule-task` refusent chacune un état terminal, et leur message
  renvoyait l'utilisateur vers l'interface graphique (« Rouvrir la tâche dans
  Things ») ou affirmait qu'aucune opération inverse n'existait. `reopen-task`
  EST cette opération : les trois messages sont devenus faux le jour de son
  ajout, sans qu'aucun test ne tombe — le correctif du premier (`675b750`)
  n'était lui-même couvert par rien, rejoué contre son parent la suite rendait
  `36 passed`. Les trois sont désormais gardés ENSEMBLE, par assertion sur la
  chaîne EXACTE et non sur la présence du mot `reopen-task`, plus un balayage
  des littéraux du script qui exclut les docstrings — la narration qui CITE
  l'ancienne formulation pour dire qu'elle a cessé d'être vraie n'est pas un
  message d'erreur (`test_the_class_of_refusals_is_swept_not_sampled` et sa
  contre-épreuve). Deux sites sur trois avaient été corrigés à la main ; c'est
  le balayage, pas la relecture, qui a trouvé le troisième.
- **Un état terminal qui n'est pas celui visé n'est pas un quasi-succès.**
  L'énumération `status` de Things distingue `canceled` de `completed` :
  `complete-task` **refuse** une tâche annulée plutôt que de la basculer.
  L'annulation est une décision de l'utilisateur prise dans l'application
  (« on ne le fera pas »), pas une étape vers la complétion ; la convertir
  réécrirait cette décision, et le CLI n'expose aucune opération inverse
  pour la défaire. Symétriquement, la vérification post-action porte sur la
  valeur **exacte** attendue, jamais sur « le statut a changé »
  (`test_failure_when_status_lands_on_canceled`).
- **On n'agit jamais sur un objet que l'utilisateur a délibérément mis dans
  l'état où il est.** Même motif que l'annulation, appliqué à la Corbeille :
  `complete-task` **refuse** une tâche à la Corbeille et invite à la
  restaurer (« Put Back ») plutôt que de la terminer là où elle est —
  compléter un objet jeté produirait une tâche à la fois « faite » et
  supprimée, état que l'utilisateur n'a pas demandé et que le CLI n'expose
  aucune commande pour défaire. Le refus **précède** toute sollicitation de
  l'application, et c'est cette absence d'appel qui est testée, pas seulement
  le code retour : sans la garde, le code retour reste non nul (rattrapé par
  la vérification post-action) alors que l'AppleScript a déjà été envoyé —
  un refus après écriture n'est pas un refus. Les deux formes d'adressage
  sont gardées **séparément**, car elles ne convergent pas : `--id` traverse
  la garde de `cmd_complete_task`, tandis que `--title` n'y parvient jamais,
  `_resolve_task_by_title` filtrant `trashed=0` en SQL (la tâche est déjà
  « introuvable » à la résolution). D'où
  `test_trashed_task_is_refused_by_id_without_any_osa_call` **et**
  `test_trashed_task_is_unreachable_by_title_without_any_osa_call` ; le
  corollaire du filtre — un homonyme à la Corbeille ne rend pas le titre
  ambigu et ne détourne pas la complétion de la tâche active — est gardé par
  `test_trashed_homonym_neither_blocks_nor_diverts_the_active_task`.
- **Un objet dont l'écriture est un no-op silencieux se refuse en amont, il ne
  se tente pas.** Mesuré le 2026-08-12 sur la vraie base : `to do id` résout un
  **heading** (`type=2`) et `set notes` y retourne `0`, mais la valeur n'est ni
  relue par AppleScript ni présente en base ensuite. C'est le pire profil pour
  ce projet — la surface applicative acquiesce, et seul l'invariant de
  relecture rattrape. `--project-id` refuse donc un heading **avant tout
  envoi** (`test_heading_is_refused_without_any_osa_call`), avec le motif
  mesuré : envoyer l'ordre laisserait croire à un lien posé. Une **area** ne se
  refuse pas, elle n'est simplement pas adressable : elle vit dans `TMArea`,
  table sans colonne `notes`, et sa classe AppleScript hérite de `list`, pas de
  `to do` — aucune surface n'expose de notes, il n'y a rien à tenter.
- **Une voie d'adressage par identifiant est ouverte quand la voie par titre
  est structurellement inutilisable**, pas par confort. `--project-id` existe
  parce que le vault ne connaît que `things_id` : retraduire l'identifiant en
  titre pour le repasser à `--project` réintroduirait exactement l'homonymie
  que le projet refuse. La voie par identifiant réutilise `_write_task_notes`
  telle quelle — la classe `project` héritant de `to do`, la surface d'écriture
  est littéralement la même, donc elle n'est pas dupliquée.
- **La surface préférée est celle qui marche SANS configuration de
  l'utilisateur.** Mesuré le 2026-08-17 en construisant `reschedule-task` : le
  schéma d'URL sait replanifier (`operation: "update"`), mais **exige un jeton
  d'authentification** (Things > Réglages > Général). Sans jeton, l'application
  affiche une boîte de dialogue à l'utilisateur et **ne mute rien**, tandis que
  le script, lui, voit un `open` réussi — c'est le mode d'échec « commande
  envoyée ≠ effet constaté » sous sa forme la plus trompeuse, doublé d'une
  interruption pour l'utilisateur. La règle de préférence (URL > AppleScript)
  ne l'emporte donc pas sur ce critère : entre une surface qui impose à
  l'utilisateur d'aller chercher et de stocker un secret, et une surface
  AppleScript qui n'en demande aucun, c'est la seconde — comme pour
  `delete-task` et `complete-task`. Une surface ne « fonctionne » qu'à
  configuration constante.

  **Amendement du 2026-08-27 (US-010) : le critère tient, sa prémisse était
  fausse.** Le jeton n'impose rien à l'utilisateur — il se LIT en base,
  colonne `TMSettings.uriSchemeAuthenticationToken`, en `mode=ro` comme tout
  le reste (`_uri_scheme_token`). La mesure de 2026-08-17 avait établi qu'un
  `update` sans jeton ne mute rien ; elle n'avait pas établi que le jeton
  était hors de portée, et c'est cette moitié non mesurée qui avait été
  écrite comme un fait. `move-task --to-heading` emploie donc la surface URL
  `update` sans demander la moindre configuration, et le critère la classe
  désormais AU-DESSUS de l'AppleScript, pas en dessous. La règle n'est pas
  affaiblie : elle est appliquée à la bonne prémisse. Rejeu :

      sqlite3 "file:<base Things>?mode=ro" \
        "select length(uriSchemeAuthenticationToken) from TMSettings"

  Ce que l'amendement ne change PAS : un jeton ABSENT ou VIDE reste un refus
  AVANT tout envoi, parce qu'un `update` sans jeton est un no-op silencieux
  que `open` rend malgré tout en 0. La surface reste écartée là où elle
  n'apporte rien (`reschedule-task` a un AppleScript qui marche) ; elle est
  retenue là où elle est la SEULE (l'en-tête).
- **Une chaîne de date n'est jamais interprétée par AppleScript.** Mesuré le
  2026-08-17 : `date "2026-09-05"` a produit **2011-03-19**, la session
  appliquant sa propre locale à la chaîne. Toute date passée à `schedule` ou à
  `due date` est construite champ par champ (`set year/month/day of (current
  date)`, `_applescript_date_lines`), jamais interpolée en littéral. Le défaut
  est silencieux côté script — l'ordre réussit — et n'est rattrapé que par la
  vérification post-action, qui compare la date **exacte** et non « une date a
  été posée ».
- **La replanification refuse une tâche terminée ou annulée, là où l'annotation
  les accepte** — garde décidée pour cette opération, sur mesure, pas recopiée.
  Mesuré le 2026-08-17 : `schedule` sur une tâche `completed` retourne 0, pose
  bien la date et laisse le statut inchangé. La vérification post-action ne
  rattrape RIEN (la date est constatée en base) alors que le résultat est une
  planification posée sur un objet absent de toute liste — l'utilisateur
  croirait la tâche replanifiée. Le seul endroit où ce défaut est arrêtable est
  en amont, avant sollicitation de l'application. La Corbeille est refusée pour
  son motif propre, identique à celui des notes.
- **Un libellé de liste intégrée est localisé, comme un libellé de menu.**
  Mesuré : `exists list "Anytime"` rend `false` sur ce poste, `"À tout moment"`
  rend `true`. `THINGS_LIST_LABELS` est donc un tuple de candidats par liste, et
  le script n'agit que sur celui qui existe réellement, avec un marqueur d'échec
  explicite (`_NO_LIST_MARKER`) si aucun ne convient — même invariant que
  `HEADING_MENU_LABELS`, pour la même raison.
- **Ce qu'aucune surface n'expose se refuse, jamais ne s'ignore.**
  `reschedule-task` refuse `evening` (aucune liste « Ce soir » n'existe) et le
  suffixe `@HH:MM` (`reminderTime` n'est porté par aucune propriété du
  dictionnaire). Les accepter poserait une planification **différente** de celle
  demandée, en silence et avec un code retour 0 — un quasi-succès, que ce projet
  traite comme un échec.
- **Toute suppression est une mise à la Corbeille, jamais une destruction
  définitive** (`delete-task`) — cohérent avec le comportement natif de
  Things, pas un choix du CLI.
- **`_esc()` échappe pour AppleScript** (antislash, guillemets, retours à la
  ligne) — à réutiliser pour toute nouvelle chaîne interpolée dans un
  script `osascript`, jamais réimplémenté. Mesuré le 2026-08-12 sur
  U+0001–U+001F, U+0022, U+005C, U+007F, U+2028/U+2029, accents et emoji :
  l'échappement est **fidèle** — la valeur reconstituée côté AppleScript est
  exactement la chaîne source, aucun caractère n'est altéré. Cette fidélité
  est ce qui rend `keystroke` dangereux (voir § Zones sensibles 2) : `_esc`
  n'est PAS une validation de saisie et ne doit jamais en tenir lieu.
  Mesure complémentaire du 2026-08-12, **de bout en bout** cette fois (écriture
  réelle sur une tâche jetable, puis relecture en base) pour les notes d'une
  tâche : accents, tiret cadratin, LF, **CR seul**, **CRLF**, tabulation, URI
  percent-encodée (`%C3%AE` non décodé ni ré-encodé), guillemet et antislash,
  emoji hors BMP, U+2028 — la valeur relue est à chaque fois **exactement** la
  chaîne envoyée. Conséquence tranchée sur pièce : `set-notes`/`append-notes`
  ne refusent **aucune** classe de caractère. Le fait que `_esc` n'échappe pas
  `\r` n'est pas un trou : la mesure montre qu'`osascript` le transporte
  littéralement et que Things le stocke tel quel. Refuser une classe qui passe
  serait un sur-refus, aussi fautif que laisser passer une classe qui casse.
  Cette décision est désormais **épinglée par assertion**
  (`test_the_escape_function_neutralises_the_three_characters_that_break_out`),
  pour qu'on ne l'« ajoute pas par symétrie » avec le LF. Remesuré le
  2026-08-26 par le chemin exact d'`osa()`, et les deux commandes sont écrites
  à côté de l'assertion : `osascript -e $'return "a\rdo shell script
  \\"echo PWNED\\""'` rend la chaîne ENTIÈRE avec rc=0 — le CR ne referme pas
  le littéral — et `osascript -e $'return "a\rb"' | xxd` donne `610d 620a`,
  octet pour octet. Sa contrepartie est gardée aussi : ce qui n'est pas
  échappé doit arriver INTACT en base, sinon l'utilisateur obtient un objet
  d'un autre nom que celui qu'il a demandé, avec un code retour 0.
- **`osa()` et `time.sleep` sont les points d'injection pour les tests.**
  Toute nouvelle commande d'écriture doit passer par `osa()` (jamais
  `subprocess.run(["osascript", ...])` directement) pour rester mockable.
  `wait_for_effect` n'en ajoute pas un troisième : elle n'emploie **que**
  `time.sleep`, jamais d'horloge, précisément pour que les **38** stubs de
  `time` qui préexistaient continuent de la neutraliser sans être touchés. Le
  chiffre est revérifiable, et il l'a été par deux méthodes concordantes le
  2026-08-25 (« 45 » figurait ici, faux) :
  `git grep -h 'setattr(.*"time"' master -- tests/ | wc -l` → 38, et un
  comptage AST des mêmes appels sur l'arbre `master` → 38. Un test qui veut
  piloter le temps le fait par un `sleep` qui **avance une horloge virtuelle**
  — aucun test du dépôt ne dépend du temps réel.
- **Tests : pas de dépendance à l'application ni à la vraie base.** Chaque
  fichier de test construit sa propre base SQLite jetable (`tmp_path`) avec
  le sous-ensemble de schéma nécessaire, redirige `db_path()` dessus via
  `monkeypatch`, et mocke `osa`/`ensure_running`/`time.sleep`. Le pilotage
  réel de l'interface (clic de menu, frappe clavier) n'est **pas testable
  automatiquement** — voir § Limite de couverture assumée.

## Tests

- `pytest`, config dans `pyproject.toml` (`pythonpath = ["."]`).
- `tests/conftest.py` charge `bin/thingskit` comme module importable sous le
  nom `thingskit_cli` (le script n'a pas d'extension `.py`, ce n'est pas un
  package) — toute nouvelle fonction interne testable doit être accessible
  comme attribut de ce module (pas de classe, pas d'encapsulation).
- Convention de nommage : `tests/test_<sous-commande-ou-domaine>.py`.
- Baseline au 2026-08-12 : 27 tests avant `create-heading`, 40 après, 56
  après le garde de saisie (`_untypable_chars`), 71 après `complete-task`, 74 après la
  couverture du refus sur tâche à la Corbeille, 110 après les notes de tâche
  (`tests/test_task_notes.py`), 122 après l'adressage par identifiant de projet
  (`tests/test_project_id_notes.py`), 133 avant `find-task`, 148 après
  (`tests/test_find_task.py`, US-001), 160 après le fix du filtrage
  `--horizon` de `agenda` (`tests/test_agenda.py`, BUG-001), 165 après le
  non-vol de focus, 224 après la replanification
  (`tests/test_reschedule_task.py`, US-002), 248 après l'empaquetage en bundle
  signé (`tests/test_bundle.py`, US-004) — mesuré le 2026-08-18, baseline
  d'avant relevée à 225 ; 299 mesurée avant `rename-task`, 318 après
  (`tests/test_rename_task.py`, US-005) ; 331 après la garde d'identité de
  code de l'entrée CLI (`tests/test_code_identity.py`, BUG-009), baseline
  d'avant relevée à 318 le 2026-08-19.
  Baseline relevée à 360 le 2026-08-19 avant les durcissements de BUG-011,
  366 après (`tests/test_code_identity.py`).
  Baseline relevée à 507 le 2026-08-25 avant l'attente adaptative, 550 après,
  563 après le rétablissement de la marge d'affichage de `create-heading` et
  les durcissements de gardes qui l'accompagnent (`tests/test_write_wait.py`,
  BUG-016) — plus un échec préexistant hors
  périmètre (`test_invocation_through_the_bundle_launcher_is_let_through`, dont
  l'assertion lexicale ne tient pas devant les codes ANSI qu'argparse émet sous
  Python 3.14) et un saut.
  Baseline relevée à 466 le 2026-08-21 avant la fermeture des deux angles morts
  de la garde C-4 et du défaut d'annotation, 494 après
  (`tests/test_annotations_resolve.py`, contre-épreuves de la garde C-4).
  Baseline relevée à **606** le 2026-08-25 avant la résolution de cible et la
  garde de placement d'`add-task`, **668** après (`tests/test_add_task.py`,
  BUG-005) — 61 dans le fichier, plus 1 instance du contrôle paramétré de
  `test_annotations_resolve.py`, qui balaie les fichiers de test. Chaque
  chiffre porte la commande qui l'établit, parce qu'un compte inscrit ici
  sera repris sans être recontrôlé :

      python3 -m pytest --collect-only -q | tail -1   -> 668 tests collected
      python3 -m pytest tests/test_add_task.py -q     -> 61 passed
      python3 -m pytest -q                            -> 1 failed, 666 passed,
                                                         1 skipped

  Ce compte a été faux entre-temps : il disait **639 / 32**, chiffres justes
  à la première passe de BUG-005 et périmés par son rework de sécurité, qui
  a ajouté 29 tests sans les mettre à jour. La correction est la règle même
  que `BUG-025` porte — un chiffre dans une trace durable reste juste, ou il
  n'y est pas.

  Baseline relevée à **668** le 2026-08-26 avant `move-project` et
  `reopen-task`, **735** après les deux (`tests/test_move_project.py` US-008,
  `tests/test_reopen_task.py` US-009, l'un et l'autre + BUG-032). Le compte
  est RECOMPOSÉ à la fusion, jamais additionné depuis les deux branches —
  chacune n'avait vu que son propre ajout. L'écart de 67 se décompose, et
  chaque terme est mesuré : 29 dans `test_move_project.py` (21 de l'ajout
  initial, 8 de BUG-032) ; 34 dans `test_reopen_task.py` (22 de l'ajout
  initial, 12 de BUG-032) ; +2 au contrôle paramétré de
  `test_annotations_resolve.py`, qui balaie les fichiers de test (30 -> 32) ;
  +2 à `test_the_derivation_floor_notices_a_command_that_disappeared`, dont
  `cmd_move_project` et `cmd_reopen_task` sont les 3e et 4e paramètres. Les
  commandes :

      python3 -m pytest --collect-only -q | tail -1     -> 735 tests collected
      python3 -m pytest tests/test_move_project.py -q   -> 29 passed
      python3 -m pytest tests/test_reopen_task.py -q    -> 34 passed
      python3 -m pytest tests/test_annotations_resolve.py -q -> 32 passed
      python3 -m pytest -q                              -> 1 failed, 733 passed,


  Baseline relevée à **735** le 2026-08-26 avant BUG-033, US-010 et BUG-017,
  **761** après les trois. L'écart de 26 se décompose, et chaque terme est
  mesuré : 12 dans `tests/test_create_area.py` (BUG-033 — la sous-commande
  n'avait AUCUN test, seule commande d'écriture dans ce cas) ; 6 dans
  `tests/test_applescript_escaping.py` (le balayage de classe et ses
  contre-épreuves) ; +3 dans `test_bundle.py` (87 -> 90 collectés, garde
  anti-fuite d'identité et ses deux contre-épreuves, US-010) ; +3 dans
  `test_code_identity.py` (19 -> 22, décolorisation, BUG-017) ; +2 au contrôle
  paramétré de `test_annotations_resolve.py`, qui balaie les fichiers de test
  (32 -> 34, deux fichiers de plus). Les commandes :

      python3 -m pytest --collect-only -q | tail -1  -> 761 tests collected
      python3 -m pytest tests/test_create_area.py -q -> 12 passed
      python3 -m pytest tests/test_applescript_escaping.py -q -> 6 passed
      python3 -m pytest tests/test_bundle.py -q      -> 89 passed, 1 skipped
      python3 -m pytest tests/test_code_identity.py -q -> 22 passed
      python3 -m pytest tests/test_annotations_resolve.py -q -> 34 passed
      python3 -m pytest -q                           -> 760 passed, 1 skipped

  **La suite n'a plus d'échec.** L'unique échec traîné depuis le 2026-08-25 —
  `test_invocation_through_the_bundle_launcher_is_let_through`, dont
  l'assertion lexicale ne tenait pas devant les codes ANSI qu'argparse émet
  sous Python 3.14 — est corrigé (BUG-017) : la sortie du lanceur est
  DÉCOLORÉE avant toute assertion, et le test gagne au passage l'exigence que
  l'aide soit utilisable, pas seulement qu'elle commence par `usage:`. Le
  remède porte sur la sortie et non sur l'assertion, parce que la direction
  d'ABSENCE (`not in`) du même test virait au VERT sur une chaîne interdite
  coupée en deux par une séquence — un faux vert qu'aucune relecture ne voit.

  Baseline relevée à **761** avant le durcissement du 2026-08-26, **795**
  après le premier lot, **810** après le troisième tour de review du même
  jour. L'écart de 34 du premier lot se décompose, et chaque terme est
  mesuré : +15 dans `test_applescript_escaping.py` (6 -> 21 : parité, portée,
  interdiction hors f-string, et leurs contre-épreuves) ; +12 dans
  `test_bundle.py` (90 -> 102 collectés : portée du balayage, classe « valeur
  personnelle », identifiant nu, protection des allowlists, arbre suivi) ;
  +7 dans `test_create_area.py` (12 -> 19 : trois classes hostiles de plus,
  fidélité de transport, câblage CLI). Les six `test_registered_in_cli_help`
  gagnent une assertion sans gagner de test.

  L'écart de 15 du troisième tour se décompose de même : +10 dans
  `test_applescript_escaping.py` (21 -> 31 : `_esc` masqué localement ou relié
  au niveau module, `global` et double affectation sur une constante
  dispensée, rebinding dans une branche de module, `.join` sur une liste
  inline, `format_spec` dans la parité, et leurs contre-épreuves) ; +5 dans
  `test_bundle.py` (102 -> 107 : prénom accentué, règle de forme sur les deux
  jeux de NOMS et sa contre-épreuve, couverture des motifs et des deux
  vocabulaires par l'empreinte). Les commandes :

      .venv/bin/python -m pytest --collect-only -q | tail -1 -> 810 tests collected
      .venv/bin/python -m pytest tests/test_applescript_escaping.py -q -> 31 passed
      .venv/bin/python -m pytest tests/test_bundle.py -q -> 106 passed, 1 skipped
      .venv/bin/python -m pytest tests/test_create_area.py -q -> 19 passed
      .venv/bin/python -m pytest -q -p no:cacheprovider -> 809 passed, 1 skipped

  Baseline relevée à **810** le 2026-08-26 avant BUG-026, **843** après son
  premier lot, **889** après le second — celui qui borne ce qu'`argparse`
  émet. Ce bloc a porté **843** devant un arbre à 889 jusqu'à l'intégration
  d'ADR-003 : le second lot a ajouté 46 tests sans le mettre à jour, exactement
  la classe que `BUG-025` nomme. Rejeu, sur un arbre détaché à l'état voulu :

      git worktree add /tmp/tk-etat <ref> && cd /tmp/tk-etat \
        && python3 -m pytest --collect-only -q | tail -1

  L'écart de 33 du premier lot se décompose, et chaque terme est mesuré : 32 dans
  `tests/test_untrusted_rendering.py` (le balayage de la classe, ses six
  formes en contre-épreuve, la manipulation réelle du script, les deux
  contre-épreuves de sur-refus, la borne de `_rendered` sur le plan
  multilingue de base, et huit épreuves de bout en bout sur trois natures de
  sortie) ; +1 au contrôle paramétré de `test_annotations_resolve.py`, qui
  balaie les fichiers de test (34 -> 35, un fichier de plus). Les commandes :

      .venv/bin/python -m pytest --collect-only -q | tail -1 -> 889 tests collected
      # (843 au premier lot de BUG-026, 889 au second)
      .venv/bin/python -m pytest tests/test_untrusted_rendering.py -q -> 32 passed
      .venv/bin/python -m pytest tests/test_annotations_resolve.py -q -> 35 passed
      .venv/bin/python -m pytest tests/test_create_area.py -q -> 19 passed
      .venv/bin/python -m pytest -q -p no:cacheprovider -> 842 passed, 1 skipped

  Baseline relevée à **889** le 2026-08-26 avant l'intégration d'ADR-003 —
  l'identité de code attendue devenue configurable au build —, **1076** après
  la troisième passe de review.
  Les chiffres portés ici pendant le chantier (923, puis 983) valaient contre
  une base d'AVANT BUG-026 : ils ont été REMESURÉS après fusion plutôt
  qu'additionnés, parce qu'aucune des deux branches n'avait vu l'autre.
  L'écart de 187 se décompose, et chaque terme est mesuré : +118 dans le
  fichier neuf `tests/test_build_identity.py` (lecture de la configuration,
  configurations hostiles, plancher de forme, accord des deux côtés,
  ordonnancement, ambiguïté d'unité d'organisation, compositions hostiles côté
  build, destination hors forme, et l'aide du point d'entrée) ; +47 dans
  `test_code_identity.py` (22 -> 69 :
  les formes dégénérées du fichier scellé, la dérivation du chemin, et les 20
  compositions hostiles qui tuent la mutation du neutraliseur) ; +9 dans
  `test_applescript_escaping.py` (31 -> 48 : la dispense liée au motif épinglé,
  les six formes qui ne dispensent pas, et les sept routes d'ombrage et
  d'approbation du troisième tour) ; +1 dans `test_bundle.py`
  (107 -> 108) ; +3 dans `test_untrusted_rendering.py` (78 -> 81 : la septième
  racine, ses trois orthographes et sa contre-épreuve) ; +1 au contrôle paramétré de
  `test_annotations_resolve.py`, qui balaie les fichiers de test (35 -> 36).
  Les commandes :

      .venv/bin/python -m pytest --collect-only -q | tail -1 -> 1076 tests collected
      .venv/bin/python -m pytest tests/test_build_identity.py --collect-only -q | tail -1 -> 118
      .venv/bin/python -m pytest tests/test_code_identity.py --collect-only -q | tail -1 -> 69
      .venv/bin/python -m pytest tests/test_applescript_escaping.py --collect-only -q | tail -1 -> 48
      .venv/bin/python -m pytest tests/test_untrusted_rendering.py --collect-only -q | tail -1 -> 81
      .venv/bin/python -m pytest -q -p no:cacheprovider -> 1065 passed, 11 skipped

  Baseline relevée à **1076** le 2026-08-27 avant `move-task --to-heading`
  (US-010), **1105** après. L'écart de 29 tient entièrement dans
  `tests/test_move_task.py` (22 -> 51 collectés) : refus avant sollicitation
  (portée du résolveur, homonyme d'un autre projet, en-tête à la Corbeille,
  couplage `--to-heading`/`--to-project`), jeton du schéma d'URL absent ou
  vide, non-fuite du jeton sur les DEUX sorties, invariants d'uuid et de date
  de création, effet non constaté, course sur le message d'échec, base
  illisible pendant toute l'attente, no-op et sa contre-épreuve, rendu borné
  des titres hostiles sur les deux branches, câblage CLI. Aucun autre fichier
  ne bouge : le fichier de test existait déjà, donc le contrôle paramétré de
  `test_annotations_resolve.py` ne gagne rien. Les commandes :

      .venv/bin/python -m pytest --collect-only -q | tail -1 -> 1105 tests collected
      .venv/bin/python -m pytest tests/test_move_task.py --collect-only -q | tail -1 -> 51
      .venv/bin/python -m pytest -q -p no:cacheprovider -> 1104 passed, 1 skipped

  Baseline relevée à **1105** le 2026-08-27 avant le lot de review d'US-010,
  **1120** après. L'écart de 15 se décompose, et chaque terme est mesuré :
  +12 dans le fichier neuf `tests/test_url_scheme_token.py` (arrivée du jeton
  dans l'URL, encodage, contre-épreuve du sur-ajout, non-fuite par le
  PROCESSUS FILS avec et sans jeton, message d'échec qui ne cite pas l'URL,
  contre-épreuve du sur-bruit, quatre schémas de base dégénérés et leur
  contre-épreuve, non-citation du nom de colonne dans le refus) ; +2 dans
  `test_move_task.py` (51 -> 53 : les deux fenêtres de course) ; +1 au
  contrôle paramétré de `test_annotations_resolve.py`, qui balaie les
  fichiers de test (36 -> 37, un fichier de plus). Les commandes :

      .venv/bin/python -m pytest --collect-only -q | tail -1 -> 1120 tests collected
      .venv/bin/python -m pytest tests/test_url_scheme_token.py --collect-only -q | tail -1 -> 12
      .venv/bin/python -m pytest tests/test_move_task.py --collect-only -q | tail -1 -> 53
      .venv/bin/python -m pytest tests/test_annotations_resolve.py --collect-only -q | tail -1 -> 37
      .venv/bin/python -m pytest -q -p no:cacheprovider -> 1119 passed, 1 skipped

  **Ce lot corrige une affirmation, pas seulement un défaut.** `url_open`
  déclarait qu'« aucune branche, de succès comme d'échec, ne cite l'URL
  construite ici » — vrai des branches Python, FAUX de l'effet observable :
  `subprocess.run(argv, check=False)` sans capture laisse le fils hériter des
  descripteurs 1 et 2, et `open` imprime l'URL entière, jeton d'authentification
  compris, quand LaunchServices ne résout pas le schéma. Les deux tests de
  non-fuite qui existaient ne pouvaient pas le voir : ils remplaçaient
  `url_open` par un faux, donc n'exerçaient jamais le fils. **Un test qui
  remplace la frontière qu'il prétend garder ne garde rien**, et c'est le seul
  enseignement de ce lot qui vaille au-delà de lui. La sortie du fils est
  désormais capturée sur TOUS les chemins — la classe, pas l'instance qui
  portait le secret — et seul le code retour est cité. Rejeu de la fuite,
  contre l'état d'avant (`8a0c699`) :

      # un `open` substitué qui recrache son argv, comme le vrai le fait
      printf '#!/bin/sh\necho "Unable to find application for URL $@" >&2\n' > /tmp/fo
      chmod +x /tmp/fo   # puis url_open(..., auth_token="SECRET") avec OPEN=/tmp/fo
      # -> le jeton apparaît en clair sur le stderr du processus

  **13 mutants, 13 rouges, 0 survivant** sur les gardes de la voie en-tête et
  de la surface URL — c'est l'énumération, pas la relecture, qui avait trouvé
  les deux survivants du tour précédent.

  **L'intégration n'a pas été un simple recollement.** Les deux chantiers se
  croisaient sur un point de fond : ADR-003 fait entrer une valeur d'origine
  externe — le fichier d'identité scellé — dans un message de refus, et la
  garde d'axe portée/puits de BUG-026 l'a signalée dès la fusion, sous sa forme
  « exception interpolée sans conversion ». Le message est donc borné par
  `_rendered`, comme `_parsed_when` et `_parsed_deadline` le font déjà, et le
  prédicat gagne sa **septième racine**. Une fusion qui aurait gardé les deux
  côtés sans les confronter aurait laissé cette valeur sortir brute, avec les
  deux suites vertes.

  **Les sauts passent de 1 à 11, et c'est voulu.** `conforming_bundle_missing`
  exige désormais que le bundle installé porte son fichier d'identité : un
  bundle antérieur à ADR-003 n'en a pas, et les tests qui l'atteignent n'ont
  rien à éprouver dessus. Même mécanisme, et même motif, que pour le shim
  d'ADR-002.

  **« Ils redeviennent actifs à la reconstruction » a été ÉCRIT avant d'être
  établi, et un des dix ne redevenait pas actif — il devenait rouge.**
  `tests/test_bundle.py` atteignait le bundle par `bundle.INSTALL_PATH`, une
  constante qu'ADR-003 retire ; le durcissement du prédicat de saut, venu de
  la même branche, cachait la casse. La phrase est maintenant MESURÉE, sur un
  bundle construit vers une destination temporaire et une copie du dépôt dont
  la fixture y est repointée : **les 10 sauts d'ADR-003 s'exécutent et
  passent**, et le seul saut restant est celui, indépendant, du doublon de
  `LC_RPATH` que ce poste refuse — il saute aussi sur `master`. Rejeu :

      cp -R <dépôt> /tmp/tk-skips && find /tmp/tk-skips -name __pycache__ -prune -exec rm -rf {} +
      python3 -m build.bundle /tmp/tk-skips-app/thingskit.app
      cd /tmp/tk-skips && grep -rl <chemin installé> tests/ \
        | xargs sed -i '' "s#<chemin installé>#/tmp/tk-skips-app/thingskit.app#g"
      python3 -m pytest -q          # -> 1 skipped (le doublon de rpath), le reste passe

  **La purge des `__pycache__` fait partie de la mesure, pas de l'hygiène.**
  Copier le dépôt en les préservant fait charger le script depuis le chemin
  d'ORIGINE encodé dans le bytecode : une mutation posée dans la copie est
  alors mesurée contre le dépôt réel, en silence.
- **Une fixture ne porte jamais une identité de signature réelle, ni une
  valeur personnelle citée en clair.** Le dépôt est public. Les fixtures dites
  « mesurées » documentent la FORME d'une sortie `security find-identity` ou
  `openssl x509 -subject` — cette forme se conserve intégralement avec des
  valeurs fictives, et `build/bundle.py` en donne le gabarit
  (`"Apple Development: Prenom NOM (XXXXXXXXXX)"`).

  **Ce que la garde tient, nommément** (`tests/test_bundle.py`), sur l'arbre
  SUIVI par git — c'est-à-dire ce que le dépôt publie, `bin/thingskit`,
  `constitution.md` et `pyproject.toml` compris :

  1. une identité de signature dans sa forme complète, avec l'identifiant de
     développeur, le sujet DN et l'empreinte du certificat
     (`test_no_real_signing_identity_is_written_anywhere_in_the_repository`) ;
  2. un identifiant d'équipe ou de développeur écrit SEUL — dix caractères
     majuscules mêlant lettres et chiffres
     (`test_no_bare_apple_identifier_is_written_in_the_repository`) ;
  3. un nom légal cité dans un LITTÉRAL DE DONNÉES d'un fichier Python, c'est-
     à-dire dans une fixture (`test_no_personal_value_is_written_in_a_fixture`).

  **Les portées des trois classes ne sont pas les mêmes, et « l'arbre suivi »
  ne vaut que pour les deux premières.** 1 et 2 travaillent sur le texte brut :
  elles couvrent bien les 38 fichiers suivis, `constitution.md` et
  `pyproject.toml` compris. La classe 3 n'a de sens que sur un fichier Python
  analysable, puisqu'elle distingue littéral de données et docstring — les
  6 fichiers suivants sont donc **hors de sa portée**, mesuré le 2026-08-26 :
  `.gitignore`, `constitution.md`, `pyproject.toml`, `spec.md`,
  `thingskit-launch.c.in`, `uv.lock`. La formulation antérieure disait « sur
  l'arbre SUIVI par git […] `pyproject.toml` compris » sans distinguer, ce qui
  surestimait la classe 3.

  **Ce qu'elle ne tient PAS, et qui est dit plutôt que tu** : un nom cité dans
  un commentaire, une docstring ou la prose d'un `.md` ; un nom qui ne suit pas
  la forme « Prénom NOM » (prénom seul, engagement commercial, pseudonyme) ;
  une adresse ou un numéro. Ces trois-là sont des **invariants non gardés** —
  ils tiennent par relecture, ce qui est exactement ce dont ce projet se
  méfie. Le périmètre est un arbitrage MESURÉ, pas un renoncement : la forme
  « Prénom NOM » rend 3 occurrences distinctes dans les littéraux de données,
  38 en incluant commentaires et docstrings, 12 de plus dans la prose des
  `.md` — ce dépôt capitalise pour appuyer (`Le CLI`, `Chemin ABSOLU`,
  `Ne JAMAIS`). Balayer la prose imposait une allowlist qui grossit à chaque
  paragraphe, donc une garde désactivée dans le mois.

  **« La forme Prénom NOM » se lit plus large qu'elle n'est. Les trois formes
  qu'elle ne couvre pas, nommément** (relevé au troisième tour de review) :

  | forme | tenue ? | coût mesuré de la fermer |
  |---|---|---|
  | `Prenom NOM` | oui | — |
  | `Émilie DUPONT` (majuscule accentuée) | **oui depuis ce lot** | 0 faux positif — gratuit, donc fermé |
  | `JEAN DUPONT` (prénom en capitales) | non | **29 faux positifs** |
  | `Jean Dupont` (patronyme capitalisé) | non | **59 faux positifs** |
  | `Dupont` (patronyme seul) | non | non mesuré, la forme n'a pas de signature |

  Les 29 sont, un par un, du DDL SQL et des tournures techniques des fixtures :
  `CREATE TABLE` (11 fichiers), `TEXT PRIMARY KEY` (11), `BEGIN CERTIFICATE`,
  `END CERTIFICATE`, `DTD PLIST`, `DROP TABLE`, `AUTRE PROJET`, `SANS TITRE`,
  `URL JSON`. Les allowlister revient à l'allowlist de prose déjà écartée
  ci-dessus. `Jean Dupont` est pourtant la forme la plus courante d'un nom
  français : c'est le résidu le plus gênant de ce document, et il est **assumé
  et non gardé**, pas fermé en silence. Commande de rejeu de ces trois
  mesures : `.venv/bin/python -m pytest tests/test_bundle.py -q` pour la garde,
  et pour les variantes le script de mesure du lot — substituer la variante à
  `_PERSONAL_NAME_RE` puis compter `_leaked_personal_names()`.

  La garde ne peut PAS être écrite en nommant la valeur interdite — l'y
  écrire la réintroduirait —, elle porte donc sur la forme, et sa
  contre-épreuve COMPOSE ses valeurs synthétiques à l'exécution, pour la même
  raison. Le balayage a d'ailleurs fait échouer sa propre écriture, en
  signalant la valeur réelle citée dans le commentaire qui l'expliquait.

  **Les allowlists sont elles-mêmes gardées** — les **quatre**, depuis ce lot ;
  elles vivent dans un fichier que le balayage lit, et y inscrire une valeur
  réelle la rendait simultanément présente et autorisée. La règle de forme ne
  couvrait en réalité que **deux** jeux sur quatre
  (`_SYNTHETIC_IDENTIFIERS`, `_ALLOWED_FINGERPRINTS`), et laissait sans règle
  précisément les deux qui gouvernent la classe « valeur personnelle » —
  `_FICTIONAL_NAMES` et `_ALLOWED_PERSONAL_NAMES`. Ces deux-là ont désormais
  la leur (`test_every_fictional_name_is_manifestly_fictional`), et elle ne
  peut pas être la même : `Adele` ne porte ni répétition, ni séquence, ni mot
  déclaré, et c'est pourtant une fixture de longue date. La règle des noms est
  donc **énumérative** — chaque mot du nom doit être très court, un mot de
  gabarit, ou une persona canonique de la littérature de test — là où celle
  des identifiants est une règle de **forme**.

  **« IMPOSSIBLE » était faux pour les identifiants, et le chiffre le dit.**
  La règle de forme (répétition d'au moins 3 caractères, séquence `12345678`,
  longueur ≤ 4, ou l'un des 10 mots déclarés) accepte **0,67 %** des
  identifiants de 10 caractères `[A-Z0-9]` tirés au hasard — 1 345 sur 200 000,
  graine 20260826, mesuré le 2026-08-26. Les marqueurs `OLD`, `NEW`, `NOM`,
  `REAL`, `TEAM` sont des sous-chaînes courtes : `KOLDX-12345`, `GOLDMAN-123`
  et `ZQ8TEAM-123` passent tous les trois — **le tiret est ajouté ici pour ne
  pas déclencher la classe 2 sur ce document même**, qui est balayé ; les
  écrire d'un tenant a effectivement fait échouer la garde à la rédaction de
  ce paragraphe. La règle rend donc l'ajout d'une valeur
  réelle **très improbable, pas impossible** — et elle reste une garde utile à
  ce titre, puisqu'une vraie valeur choisie sans intention de tromper a
  99,33 % de chances d'être refusée. Commande de rejeu :

      .venv/bin/python -c "import sys,random;sys.path.insert(0,'tests');\
      import test_bundle as t;random.seed(20260826);\
      A='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';N=200000;\
      print(sum(t._is_manifestly_synthetic(''.join(random.choice(A) \
      for _ in range(10))) for _ in range(N))/N)"

  Pour les deux jeux de NOMS, « impossible » se tient **au sens strict**, et
  pour une raison différente : le vocabulaire admis est **énuméré**, pas
  deviné à la forme. `Jean DUPONT` échoue parce que `JEAN` n'est dans aucune
  des deux listes.

  Le seul identifiant réel admis est nommé avec le motif qui le rend public ;
  et **sept jeux** sont désormais épinglés par
  empreinte, ce qui rend tout ajout VISIBLE en revue. Les trois ajoutés à
  l'empreinte ferment un trou du lot précédent : les **motifs** des
  identifiants réels admis n'y entraient pas — seules les clés — donc réécrire
  ou vider un motif ne se signalait nulle part ; et les deux vocabulaires qui
  ouvrent la règle des noms y entrent aussi, puisqu'y ajouter un mot autorise
  un nom de plus.

  **Ce que ces gardes ne tiennent toujours pas**, dit plutôt que tu :
  `_BARE_IDENTIFIER_RE` exige au moins un chiffre, donc un identifiant Apple
  purement alphabétique n'est pas vu — retirer l'exigence coûte **22 faux
  positifs**, tous des mots français en capitales (`AUJOURDHUI`, `IMPOSSIBLE`,
  `COUVERTURE`, `PYTHONPATH`…), mesuré ; et l'empreinte rend un ajout
  *visible*, jamais *impossible* — c'est une garde de revue, pas un verrou.

  **Un seul identifiant réel est admis** depuis ADR-003, et le motif est écrit
  à côté : celui de Cultured Code, qui préfixe le Group Container de Things et
  ne désigne personne ici. Une garde qui le refuserait serait ininstallable,
  donc désactivée.

  **Le Team ID du projet en est SORTI le 2026-08-26.** Il y figurait pour une
  raison qui a cessé d'être vraie : il était dans `CODE_REQUIREMENT`, donc
  inévitable dans l'arbre publié. L'exigence de code se compose désormais
  depuis une configuration locale non versionnée (ADR-003), et plus aucune
  valeur d'identité du projet ne subsiste dans l'arbre suivi — ni identifiant
  d'équipe, ni identifiant de bundle, `README.md`, `constitution.md` et
  `specs/` compris. Commande de rejeu :

      git grep -c '56AP2N''SB54\|app\.sow''ell' -> aucun résultat

  (les deux motifs sont coupés ici pour ne pas réintroduire dans ce document
  ce que la garde en retire — les recoller avant de lancer la commande).

  Le défaut d'origine n'était pas l'absence de convention — les fixtures
  voisines du MÊME fichier employaient déjà `Zoe`, `Adele`, `Mallory` — mais
  qu'elle ne s'appliquait pas à elle-même : la garde existante interdisait la
  valeur dans `build/` pendant que `tests/` la publiait (US-010).
- Logique pure séparée de l'effet de bord : les fonctions de résolution
  (`resolve_uuid`, `_resolve_project_for_heading`, `_find_heading`), de
  décision (idempotence, ambiguïté) et d'interprétation (`_interpret_ui_outcome`,
  construction de script AppleScript) sont testées isolément, sans jamais
  invoquer `osascript` ni `open` réellement.

## Zones sensibles

### 1. Écriture dans la base d'un gestionnaire de tâches personnel

Le projet ne modifie **jamais** la base SQLite Things directement — c'est un
principe non négociable, pas une préférence. `q()` ouvre systématiquement en
`mode=ro`. Toute écriture passe par une **surface applicative** (schéma
d'URL, AppleScript, automatisation d'interface), jamais par une requête SQL
`insert`/`update`/`delete` sur le fichier de base.

**Fichiers concernés** : `bin/thingskit` — fonctions `cmd_create_area`,
`cmd_create_project`, `cmd_add_task`, `cmd_set_notes`, `cmd_append_notes`,
`_resolve_task_for_notes`, `_write_task_notes`,
`_resolve_project_for_notes`, `cmd_delete_task`, `cmd_create_heading`,
`cmd_complete_task`, `cmd_cancel_task`, `cmd_reopen_task`,
`cmd_reschedule_task`, `cmd_rename_task`, `cmd_move_task`,
`cmd_move_project`. Même dérive que
l'énumération du § Conventions, et même remède : la liste se relit du
balayage d'AST, elle ne se complète pas de mémoire.

**Risque** : la base Things est un fichier SQLite non documenté, sans schéma
officiel publié. Une écriture directe pourrait corrompre l'intégrité
référentielle que l'application maintient elle-même (index de recherche,
horodatages internes, historique de synchronisation iCloud). C'est
irréversible pour un gestionnaire de tâches personnel utilisé au quotidien.

**Invariants** :
- Toute fonction `cmd_*` d'écriture déclenche l'action via une surface
  applicative, puis **relit** la base pour constater l'effet avant de
  retourner `0`.
- **L'attente qui précède la relecture ne peut ni écourter ni convertir la
  vérification.** Elle est bornée et adaptative (`wait_for_effect`), et un
  plafond atteint sans constat reste un échec — jamais un succès « au bénéfice
  du doute ». Symétriquement, un plafond trop bas est un défaut de la même
  gravité : il rend un **faux négatif** sur une écriture réussie, et l'appelant
  qui réessaie duplique dans les données de l'utilisateur (BUG-016). Gardé par
  `tests/test_write_wait.py`, qui rejoue la queue mesurée à 5026 ms comme un
  succès et un effet jamais constaté comme un échec, sur horloge virtuelle.
- Un code retour `0` signifie toujours « effet constaté en base », jamais
  « commande envoyée ». Testé explicitement pour chaque commande d'écriture
  (`test_failure_when_effect_not_observed` — présent dans `test_delete_task`
  ET `test_complete_task` —, `test_ui_succeeds_but_heading_not_observed_fails`,
  `test_failure_when_stored_value_differs` pour les notes, qui exige l'égalité
  **exacte** de la valeur relue, pas « les notes ont changé » ;
  `test_a_task_that_lands_in_the_inbox_is_a_failure_not_a_success` pour
  `add-task`, où l'ancienne vérification — l'existence de la tâche — était
  verte précisément quand le rangement avait échoué).
- **Le message d'échec est composé de la valeur que la sonde a OBSERVÉE, pas
  d'une seconde lecture.** L'effet peut atterrir entre le dernier sondage et
  la composition : la relecture rend alors « aucun problème », et le message
  imprime littéralement `None` (`cmd_move_task`) ou une chaîne vide
  (`cmd_reschedule_task`) — l'échec cesse de dire pourquoi il échoue au moment
  précis où il en a le plus besoin, et coûte au passage une requête SQL de
  plus. La valeur est donc capturée par fermeture au moment du sondage, motif
  déjà employé par `cmd_create_heading` pour sa dernière observation. Gardé
  par `test_the_failure_message_uses_the_observed_problem_not_a_fresh_query`
  et ses homologues de `test_reschedule_task.py` et de `test_add_task.py`
  (`test_the_failure_message_uses_the_observed_placement_not_a_fresh_query`),
  qui reproduisent la course.
- **Une valeur d'origine non contrôlée n'atteint pas la sortie sans
  conversion.** La propriété porte sur la **valeur et son trajet**, jamais sur
  l'appel `print` : une garde formulée sur les sites d'affichage rate par
  construction les fragments composés ailleurs puis interpolés plus loin — et
  c'est là que le défaut se loge. Sont d'origine non contrôlée les arguments
  de la ligne de commande (`--title`, `--list`, `--heading`, `--notes`) **et**
  tout champ relu de la base Things : titres de projets, d'areas, de headings,
  identifiants. Le CLI n'en maîtrise aucun, et ces titres viennent notamment
  de comptes rendus de réunion importés automatiquement — du texte que
  personne ne relit avant qu'il n'atteigne la commande.

  Le dommage n'est **pas** une corruption d'octets. Les octets émis sont
  corrects, et c'est précisément ce qui rend le défaut invisible en relecture
  de code : c'est le **terminal** qui exécute ce qu'il reçoit. Mesuré le
  2026-08-25 sur la ligne de succès d'`add-task` — un titre portant
  `\x1b[2K\r` efface la ligne, et l'utilisateur LIT
  `tâche ajoutée : AUTRE → AUTRE PROJET` sans qu'aucune partie du programme
  ne l'ait écrit. C'est le dommage même que la vérification post-action
  existe pour fermer (« le message annonce un rangement qui n'a pas eu
  lieu »), restauré par un autre vecteur — avec, cette fois, un code retour
  `0` parfaitement légitime, donc hors de portée de l'invariant central.

  La conversion borne une CLASSE de caractères, jamais une liste énumérée —
  une liste ne couvre que ce qu'on a pensé à y inscrire. La classe REFUSÉE est
  nommée par catégorie Unicode : `Cc` (dont ESC/CR/LF), `Cf` (dont l'inversion
  de sens de lecture U+202E et l'espace de largeur nulle U+200B), `Zl`/`Zp`,
  `Cs`, `Co`, `Cn`. Même motif que `_untypable_chars`, qui porte déjà sur la
  classe et non sur les caractères rencontrés.

  **Les séparateurs d'espace (`Zs`) en ont été SORTIS le 2026-08-26**, et ce
  retrait est le correctif d'un sur-refus mesuré. Jusque-là la borne était
  celle de `str.isprintable()`, qui refuse `Zs` : sur la base Things réelle,
  902 titres, la garde en citait 2, et le seul caractère en cause était
  U+00A0 — la typographie française devant `?` et `!`. Zéro caractère de
  classe dangereuse : 100 % de faux positifs en usage réel. Les 17 `Zs` sont
  des espaces VISIBLES de largeur non nulle et n'exécutent rien ; rien du
  modèle de menace n'y est, U+200B et U+202E étant `Cf` et U+2028 `Zl`. La
  couverture du dommage reste donc ENTIÈRE ; le résidu qui s'ouvre — un espace
  cadratin pour désaligner du texte — rejoint le résidu « imprimable et
  trompeur » déjà déclaré. `str.isprintable()` ne sait pas exprimer cette
  classe : le prédicat est explicite (`_REFUSED_CATEGORIES`), et
  `test_the_refused_class_still_holds_every_dangerous_category` l'épingle dans
  les DEUX directions — un `Zs` redevenu refusé échoue, un `Cf` cessant de
  l'être aussi. Mesure rejouable :

      .venv/bin/python - <<'EOF'
      import sqlite3, glob, unicodedata
      p = glob.glob("/Users/donaldo/Library/Group Containers/*/ThingsData-*/"
                    "Things Database.thingsdatabase/main.sqlite")[0]
      con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
      t  = [x for (x,) in con.execute("select title from TMTask "
                                      "where trashed=0 and title is not null")]
      t += [x for (x,) in con.execute("select title from TMArea "
                                      "where title is not null")]
      R = {"Cc","Cf","Zl","Zp","Cs","Co","Cn"}
      print(len(t), sum(not x.isprintable() for x in t),
            sum(any(unicodedata.category(c) in R for c in x) for x in t))
      EOF
      # -> 902 2 0   (titres, cités par l'ancienne borne, cités par la nouvelle)

  Mesuré le 2026-08-25 sur dix formes : les six hostiles sont converties, et
  accents, tiret cadratin, `→`, `›` et emoji restent lisibles — la conversion
  ne coûte donc pas la lisibilité qui la ferait abandonner.

  **L'uniformité se vérifie par EXPRESSION, pas par ligne.** Le défaut se
  loge dans une valeur brute au milieu de trois converties : `add-task`
  rendait l'uuid observé sans conversion quand ses trois voisins de la même
  `f-string` y passaient. Gardé par
  `test_the_success_message_never_emits_a_control_sequence_from_the_title`
  et ses jumeaux `…_from_the_list` / `…_from_the_heading` (chacun paramétré
  sur `\x1b[2K\r`, `\n` et U+202E), par
  `test_the_rendering_keeps_accents_and_arrows_readable` contre le
  sur-échappement, et par
  `test_the_observed_placement_renders_all_four_values_the_same_way`.

  **Portée réelle au 2026-08-26, énoncée avec ce qu'elle exclut.** Toutes les
  fonctions de premier niveau de `bin/thingskit` tiennent l'invariant, tenu par
  un BALAYAGE et non par une relecture —
  `test_no_untrusted_value_reaches_the_output_unconverted`
  (`tests/test_untrusted_rendering.py`), compte résiduel exigé NUL. N'en font
  PAS partie, et sont couverts autrement : ce qu'`argparse` émet (voir
  ci-dessous), et le code de MODULE — le bloc `if __name__ == "__main__"`,
  dont le `print` du contrôle d'identité de code, que le balayage ne collecte
  pas. La formulation « le script entier tient cet invariant », portée ici
  jusqu'au 2026-08-26, était FAUSSE : une valeur sortait brute par `argparse`
  au moment même où elle était écrite.

  **`argparse` émet hors du module, et le balayage est intra-module.** Le
  prédicat porte sur la valeur et son trajet ; sa frontière est celle du
  fichier. `ArgumentParser.error` compose son message dans `argparse`, à
  partir d'`sys.argv`, AVANT que la valeur n'existe comme namespace — aucune
  racine ne l'atteint. Reproduit le 2026-08-26 : un titre commençant par un
  tiret, forme banale d'un compte rendu importé, fait basculer `parse_args`
  sur `unrecognized arguments: %s`, seul des sept gabarits d'`argparse` 3.12.9
  à être brut ET atteignable. ESC et CR y sortaient intacts, le terminal
  effaçait la ligne d'erreur et l'utilisateur lisait un faux succès, avec un
  code retour 2 que personne ne regarde. Deux autres gabarits sont bruts par
  construction (`ambiguous option`, `unexpected option string`) mais leur
  valeur doit être un préfixe d'option déclarée, donc ne peut porter aucun
  caractère de la classe refusée ; les quatre derniers convertissent par `%r`
  ou ne portent que du texte du programme. La borne est posée là où le message
  repasse par nous — `_BoundedParser.error` / `.exit` —, elle échappe EN PLACE
  au lieu de citer le message entier, et elle ne touche ni l'usage ni l'aide.
  `sys.argv` est devenue la sixième racine du prédicat (R6) par la même
  occasion. Gardé par `test_an_unrecognised_argument_never_reaches_stderr_raw`
  et `test_an_argument_error_never_smuggles_a_line_break`. L'énumération des
  sept gabarits vaut pour 3.12.9 et pourrait changer de version en version ;
  la borne n'en dépend pas — elle s'applique au message quel qu'il soit, et
  c'est le motif de la poser au passage plutôt que sur un gabarit nommé.

  **Ce qui a permis de fermer la classe n'est pas un comptage, c'est un
  prédicat.** Cinq mesures l'avaient précédée et avaient rendu cinq résultats
  — 24 sites plus 13 faibles, 237 champs sur 326, 82, 117, 37. Aucune n'était
  un comptage fautif : chacune présumait une définition de « valeur d'origine
  non contrôlée » au lieu de l'écrire. Le prédicat à **sept** racines (namespace
  argparse, `q(...)`, `osa(...)`, paramètre alimenté, retour par slot de
  tuple, `sys.argv`, et le contenu d'un FICHIER lu — `read`, `read_text` ou
  `readlines`, les trois orthographes, parce que n'en reconnaître qu'une était
  exactement le défaut du balayage de `sleep`), son trajet et ses conversions
  sont écrits en tête de `tests/test_untrusted_rendering.py` ; c'est lui qu'il
  faut contester pour contester le chiffre.

  **La septième est arrivée avec ADR-003**, le même jour, par un chantier mené
  en parallèle : le fichier d'identité scellé fait entrer dans le script une
  source externe qu'aucune des six autres n'atteignait. Il est scellé, donc il
  devrait être digne de confiance — mais le cas où la garde d'identité REFUSE
  est exactement celui où il ne l'est pas, et c'est là que son contenu atteint
  un message. Une racine qui ne vaudrait que « quand tout va bien » ne vaudrait
  rien. Ce qu'elle ne voit pas est énoncé dans le prédicat : une lecture
  atteinte par un alias, par `os.read`, par `json.load`, ou par un module
  tiers — aucune n'existe ici, mesuré. Le compte résiduel reste nul, et la
  mesure des 90 valeurs ci-dessous est inchangée : l'état d'avant BUG-026 ne
  lisait aucun fichier.

  **La mesure sous ce prédicat : 90 valeurs dans 34 fonctions**, sur l'état
  d'AVANT le correctif. Le chiffre porté ici jusqu'au 2026-08-26 — « 87 valeurs
  dans 32 fonctions » — n'était produit par aucune lecture (ni le brut à 90/34,
  ni le dédupliqué à 84/34, ni les 78 lignes distinctes), et la commande qui
  l'accompagnait lisait le fichier CORRIGÉ, donc rendait 0. Elle se rejoue,
  cette fois sur ce qu'elle mesure — et le chiffre est lui-même épinglé par
  `test_the_figure_of_the_sweep_is_the_one_written_in_the_constitution` :

      git show cc4ff9d:bin/thingskit > /tmp/avant_bug026.py
      .venv/bin/python - <<'EOF'
      import importlib.util, sys, pathlib
      sp = importlib.util.spec_from_file_location(
          "m", "tests/test_untrusted_rendering.py")
      m = importlib.util.module_from_spec(sp)
      sys.modules["m"] = m; sp.loader.exec_module(m)
      for label, path in (("AVANT", "/tmp/avant_bug026.py"),
                          ("APRÈS", "bin/thingskit")):
          v = m.Sweep(pathlib.Path(path).read_text(encoding="utf-8")).violations()
          print(label, len(v), "valeurs,", len({x[0] for x in v}), "fonctions")
      EOF
      # -> AVANT 90 valeurs, 34 fonctions
      #    APRÈS 0 valeurs, 0 fonctions

  Cette unité n'est PAS un nombre de `print` : une valeur est comptée à son
  ORIGINE dans sa fonction, et un même `task_id` alimente jusqu'à six lignes.
  Le volume du correctif se mesure, lui, en sites de conversion — 82
  occurrences de `_rendered(` dont une définition, soit 81 appels, et `!r}`
  passé de 104 à 171. Ces deux chiffres ont bougé d'un cran et de dix à
  l'intégration d'ADR-003, qui borne le refus d'identité de code et compose
  ses propres messages avec `!r`. Le chiffre « 85 emplacements de texte » porté ici
  jusqu'au 2026-08-26 ne correspondait à aucune commande :

      grep -o '_rendered(' bin/thingskit | wc -l              # -> 82
      grep -o '!r}' bin/thingskit | wc -l                     # -> 171
      git show cc4ff9d:bin/thingskit | grep -o '!r}' | wc -l  # -> 104

  Deux inclusions décident du chiffre, et les deux manquaient aux balayages
  antérieurs. La sortie d'`osascript` est une valeur d'origine non contrôlée —
  son exclusion est ce qui a rendu FAUX le balayage à 37 sites. Un conteneur
  peuplé par `append` transporte la valeur — sans cette règle, la table
  d'`agenda` sortait du balayage (60 valeurs mesurées sans, 80 avec). Une
  exclusion décide autant : une valeur composée brute mais émise convertie
  plus loin n'est pas un défaut — `where = a.list + (f" › {a.heading}" …)`,
  émis par `{where!r}`, est le faux positif vérifié du balayage à 237.

  **Deux conversions, un partage écrit.** `!r` en prose, où les guillemets
  délimitent la valeur ; `_rendered()` là où la position délimite déjà —
  colonne alignée, identifiant entre parenthèses, ligne destinée à un tube.
  `_rendered` ne cite QUE s'il y a lieu, et c'est ce qui laisse les commandes
  de lecture lisibles. Citer inconditionnellement une colonne aurait été un
  sur-refus — refuser une classe qui passe est un défaut au même titre que
  laisser passer une classe qui casse. Les deux ne bornent PAS exactement la
  même classe : `!r` est `repr`, qui échappe en plus les `Zs`. L'écart va dans
  le sens du sur-refus, il subsiste en prose, et il est déclaré ci-dessous. La
  **troncature s'applique à la valeur, jamais à son rendu** — `_rendered(v)[:60]`
  amputait `\x1b` en `\x1` et coupait le guillemet fermant ; corrigé en
  `_rendered(v[:60])`, gardé par
  `test_no_truncation_applies_to_the_result_of_a_conversion`.

  **Ce que ce balayage ne tient pas**, dit ici parce que « la classe est
  fermée » a déjà été affirmé à tort dans ce dépôt — et parce que cette liste
  a dû DOUBLER le 2026-08-26, deux affirmations de couverture s'étant révélées
  plus larges que leur mesure :

  - Les indirections qu'une analyse statique ne suit pas (`%`, `.format`,
    `string.Template`, `.replace`, `io.StringIO`) ne sont pas suivies — elles
    sont INTERDITES par
    `test_no_output_is_composed_by_a_form_the_sweep_cannot_follow`, 0
    occurrence mesurée.
  - Les portées et les puits que le balayage ne modélise pas — conteneur ou
    global de module, méthode de classe, alias de `print` ou d'un flux
    standard, `sys.exit`/`SystemExit` à message composé, défaut de paramètre
    calculé, paramètre variadique, walrus en argument de puits, exception
    interpolée, `writelines`, `os.write`, `subprocess` à stdio hérité dont
    l'argv est interpolé — sont INTERDITS de la même façon, par
    `test_no_output_escapes_the_sweep_by_its_scope_or_by_its_sink` : quinze
    détecteurs pour quatorze formes, 0 occurrence. Trois sites vivants
    relevaient de ces formes et n'étaient bénins que par accident ; ils ont
    été corrigés le 2026-08-26 (deux `{exc}` d'un `except … as`, et un
    `subprocess` dont l'argv portait un uuid non encodé, désormais passé par
    `urllib.parse.quote` comme dans `url_open`). Angle mort de cette garde-là :
    un `subprocess` dont l'argv est passé par un NOM, qu'elle n'inspecte pas.
  - L'analyse est insensible au flot et fusionne les portées imbriquées,
    toujours dans le sens de la sur-approximation.
  - Le code de MODULE n'est analysé par rien (voir « portée réelle »).
  - `json.dumps` compte comme conversion, et sa portée est PLUS ÉTROITE que ce
    qui a été écrit ici jusqu'au 2026-08-26 (« elle échappe la classe Cc »).
    Mesuré sous `ensure_ascii=False` : elle échappe **C0** — 32 caractères sur
    les 65 de `Cc`, U+0000..U+001F, dont ESC, CR et LF. **DEL et tout C1
    traversent** (33 caractères), dont U+0085 NEL, que `str.splitlines()`
    traite comme un saut de ligne : un titre peut donc couper une ligne de
    sortie `--json`. Cf traverse également, sous la même option. Résidu nommé
    et non fermé : **DEL, C1 et Cf traversent une sortie `--json`**.

        .venv/bin/python -c 'import json, unicodedata as u; \
        cc=[chr(c) for c in range(0x110000) if u.category(chr(c))=="Cc"]; \
        print(len(cc), sum(c not in json.dumps("a"+c+"b", ensure_ascii=False) \
        for c in cc))'
        # -> 65 32

  - Le partage « `!r` en prose, `_rendered` en position » n'est mécanisé que
    pour sa forme cumulée (`_rendered(x)!r`). Distinguer la prose d'une
    position délimitée n'est pas décidable à l'AST : les trois écarts du
    2026-08-26 (lignes 882, 890, 1045) ont été trouvés à la relecture.
  - Enfin `!r` comme `_rendered` laissent passer ce qui est imprimable ET
    trompeur — homoglyphe, espace cadratin, titre imitant mot pour mot un
    message du programme. Le quoting en limite la portée, il ne la ferme pas.
    Sortir `Zs` de la classe refusée élargit ce résidu-là, et rien d'autre.

- Aucune requête SQL autre que `select` n'existe dans `bin/thingskit`, y
  compris dans les tests d'intégration à `_make_db` (qui construisent une
  base jetable, distincte de la vraie). Gardé au-delà de la relecture par
  `test_no_sql_write_reaches_the_database`, qui compare les octets du
  fichier de base avant/après une commande d'écriture dont la surface
  applicative est rendue inerte : la base doit être rigoureusement
  inchangée.

### 2. Automatisation d'interface (System Events / osascript)

`create-heading` est la seule commande du projet à piloter Things par
automatisation d'interface (clic de menu + frappe clavier), parce qu'aucune
des deux surfaces plus sûres (URL scheme, AppleScript ciblé) n'expose
l'ajout d'un heading à un projet existant — constaté sur pièce, pas déduit
de la documentation Things (cf. docstring module).

**Fichiers concernés** : `bin/thingskit` — `_build_heading_script`,
`_interpret_ui_outcome`, `HEADING_MENU_LABELS`, `cmd_create_heading`.

**Risques propres à cette surface** :
- **Dépendance au libellé de menu**, qui varie selon la langue de
  l'interface. Un libellé codé en dur et absent silencieusement produirait
  soit un plantage AppleScript opaque, soit — pire — un clic sur le mauvais
  item de menu.
- **Frappe clavier « à l'aveugle »** : contrairement à un appel API, rien ne
  garantit que le focus clavier est effectivement sur le bon champ au
  moment de la saisie. Une automatisation qui « réussit » (rc=0) peut avoir
  tapé le titre dans le mauvais endroit.
- **Permission d'accessibilité macOS** : sans elle, `System Events` échoue.
  L'échec doit être détecté et signalé, pas confondu avec un autre type
  d'erreur.

**Invariants** :
- `HEADING_MENU_LABELS` est une liste, jamais un littéral unique. Le script
  AppleScript essaie chaque libellé connu et n'agit que sur celui qui
  **existe réellement** dans le menu au moment de l'exécution
  (`test_build_heading_script_embeds_all_known_labels`).
- Si aucun libellé connu n'existe dans le menu, la commande échoue
  explicitement (marqueur `_NO_LABEL_MARKER`) plutôt que de cliquer au
  hasard ou de laisser AppleScript planter de façon opaque
  (`test_interpret_ui_outcome_no_label_found`).
- Un refus de permission d'accessibilité est détecté et traduit en message
  actionnable (`test_interpret_ui_outcome_accessibility_denied`,
  `test_ui_reports_accessibility_denied_fails_explicitly`) — pas un
  plantage brut, pas une confusion avec un échec de vérification en base.
- **La saisie clavier n'est déclenchée qu'après vérification que la
  frappe suit un clic de menu réussi** dans le même script AppleScript
  (`_build_heading_script`) — jamais une frappe orpheline découplée du
  clic.
- **Un projet dont le titre est aussi celui d'une area est REFUSÉ avant toute
  sollicitation de l'application** : le nom de fenêtre, seule condition
  observable avant le clic, est identique dans les deux cas
  (`test_area_homonym_refuses_before_any_solicitation`,
  `test_resolve_refuses_a_project_sharing_its_title_with_an_area`, et
  `test_an_area_named_otherwise_does_not_block_the_project` contre le
  sur-refus).
- **Zéro fenêtre lisible vaut refus, quel que soit le titre du projet** —
  explicitement, pas par l'effet de bord d'une comparaison
  (`test_zero_window_is_refused_even_when_the_project_title_is_empty`).
- **La frappe clavier n'est déclenchée qu'après avoir CONSTATÉ que Things est
  l'application au premier plan**, contrôle réaffirmé entre le clic de menu et
  la frappe, jamais un délai deviné
  (`test_the_script_observes_the_foreground_instead_of_guessing_a_delay`,
  `test_the_foreground_is_reasserted_between_the_click_and_the_keystroke`,
  `test_the_foreground_wait_is_bounded_by_a_finite_loop`).
- **Le clic de menu n'est déclenché qu'après avoir CONSTATÉ que la fenêtre au
  premier plan affiche le projet visé.** « Nouvel en-tête » s'applique à la
  fenêtre au premier plan, et rien ne garantissait que ce soit la bonne : on
  envoyait `things:///show?id=<uuid>` puis on attendait une durée devinée.
  L'écart entre le pire affichage mesuré (970 ms sur 3 démarrages à froid, le
  2026-08-25) et l'instant du clic était tombé à ~130-305 ms — contre ~6,9 s
  avant que le lancement ne devienne une condition observée. Le défaut ne
  s'est pas reproduit sur ces trois essais ; c'est la **marge** qui avait
  fondu d'un facteur ~25, sur l'opération dont le mode d'échec est le plus
  coûteux du projet : le titre tapé dans un autre projet, effet de bord
  irréversible que la vérification post-action constate sans pouvoir le
  défaire, et que l'appelant duplique en réessayant.

  La condition n'a aucune trace en base — rien à relire — mais elle est
  **observable par la surface applicative** : `name of window 1` rend le nom
  de la liste affichée. Mesuré et écarté : `current list url` et `current
  list name`, propriétés cachées du dictionnaire, rendent `missing value`.
  La comparaison porte `considering case and diacriticals` — mais ce qui la
  porte est la **casse**, pas les diacritiques. Mesuré le 2026-08-25, sur les
  deux : `"AUJOURDHUI" is "aujourdhui"` rend `true` (la comparaison par défaut
  ignore la casse), tandis que `"ete" is "été"` rend `false` (elle respecte
  déjà les diacritiques). Sans `case`, deux projets ne différant que par une
  majuscule seraient confondus ; `diacriticals` est sans effet ici et n'est
  conservé que pour énoncer l'exigence en entier plutôt que la moitié qui se
  trouve être nécessaire. La première formulation de cette ligne affirmait
  « sans casse **ni diacritiques** » sur la seule mesure de `"AUJOURDHUI"`,
  qui ne comporte aucun diacritique : le code était juste, l'affirmation ne
  l'était pas.

  Les trois comparaisons sont **exécutées** en test — la lecture de la fenêtre
  remplacée par une valeur contrôlée, ce qui tourne étant l'AppleScript
  réellement produit (`test_the_comparison_refuses_a_case_only_difference`,
  `…_an_accent_only_difference`, `…_accepts_the_exact_title`).

  La comparaison n'existe **qu'une fois** (`_shown_list_comparison`) et sert
  deux fois : comme sonde de `wait_for_effect` **avant** d'ouvrir
  l'interface, et comme garde **dans** le script d'automatisation, juste
  avant le clic. Le second contrôle n'est pas redondant : la fenêtre peut
  changer entre les deux, et surtout le trou existait aussi quand Things
  tourne **déjà**, cas où aucune attente n'a jamais été payée. Fail-closed —
  zéro fenêtre, application pas encore servante, délai d'AppleEvent dépassé :
  tout ce qui empêche de lire le nom vaut refus.

  Ce « fail-closed » reposait d'abord sur le seul fait qu'un nom illisible
  laisse `shownList` vide, donc différent du titre. **Faux dans un cas** :
  `"" is not ""` rend faux, donc zéro fenêtre + un projet à titre vide
  **franchissaient** la garde. Non atteignable au 2026-08-25 (aucun projet à
  titre vide sur les 59 mesurés), mais un invariant « par construction » ne
  peut pas dépendre du contenu de la base : le refus sur `shownList` vide est
  désormais **explicite**, avec son propre marqueur `_NO_WINDOW_MARKER` —
  distinct de `_WRONG_VIEW_MARKER`, parce que « j'ai lu un nom qui n'est pas
  le bon » et « je n'ai rien pu lire » ne se diagnostiquent pas pareil.

  **Le nom de fenêtre ne distingue pas un projet d'une area homonyme.** Il
  rend le titre de la liste affichée : la même chaîne dans les deux cas. La
  garde d'affichage est donc satisfaite par la mauvaise vue, et le titre y est
  tapé — sans qu'aucune des deux lectures ne puisse s'en apercevoir. Mesuré le
  2026-08-25 sur la base réelle : « Conventions du vault » est **à la fois**
  un projet et une area, sur 59 projets et 20 areas. La collision est
  instanciée, pas hypothétique. `_resolve_project_for_heading` refuse donc ce
  cas **avant toute sollicitation de l'application** — rien n'est ouvert, rien
  n'est cliqué, rien n'est tapé —, même règle que `_resolve_find_target` qui
  traitait déjà cette collision comme une ambiguïté. Léger sur-refus assumé :
  il est du côté qui ne casse rien.

  La sonde est bornée **côté AppleScript** (`with timeout of … seconds`)
  parce qu'un AppleEvent adressé à une application **en cours de lancement**
  ne rend pas `-600` : il **bloque** jusqu'à pouvoir être servi — 699, 753 et
  854 ms mesurés sur 3 démarrages à froid le 2026-08-25, rc=0. Sans cette
  borne, l'attente serait bornée par le délai d'AppleEvent par défaut
  d'`osascript` (120 s) et le plafond de la boucle ne voudrait plus rien
  dire.

  **L'affichage et le premier plan sont DEUX conditions, pas une.**
  `name of window 1` établit ce que la fenêtre 1 de Things montre ; il
  n'établit jamais que Things est l'application au premier plan du **système**.
  Or `keystroke` de System Events frappe l'app frontmost du système — le clic
  de menu, lui, passe par `tell process` et ne l'exige pas. La seconde
  condition était couverte par un `delay 0.3` après `activate`, c'est-à-dire
  par le pari qu'une activation aboutit en 300 ms : poste chargé, dialogue
  modal d'une autre application, utilisateur qui change de fenêtre, et le
  titre partait dans une **autre application**.

  Elle est pourtant observable sans arbre d'accessibilité et sans créer aucun
  en-tête : `frontmost of application "Things3"` (mesuré le 2026-08-25, rend
  `false` sur une application lancée mais pas au premier plan). Le `delay 0.3`
  est donc remplacé par une boucle **bornée par un compte d'itérations**, qui
  sort au premier constat vrai et ne coûte donc rien dans le cas nominal. La
  borne est un compte, pas un `with timeout` : ce dernier borne chaque
  AppleEvent, jamais la durée d'un `repeat` — une boucle `repeat until` serait
  restée non bornée.

  **Ce que ce compte borne, exactement** (rectifié le 2026-08-25 — il était
  annoncé « 5 s de plafond », ce qui était faux). `HEADING_FRONTMOST_ATTEMPTS`
  x `HEADING_FRONTMOST_POLL_INTERVAL` = **5 s de délais cumulés**, pas la
  durée de la boucle : chaque itération émet EN PLUS un AppleEvent (`frontmost
  of application "Things3"`), borné individuellement à 5 s
  (`HEADING_VIEW_PROBE_TIMEOUT`) par le `with timeout` englobant, et `osa()`
  ne passe aucun `timeout=` à `subprocess.run` — rien ne borne le script côté
  Python non plus. Le **pire cas réel** est donc `ATTEMPTS x (PROBE_TIMEOUT +
  POLL_INTERVAL)` = **505 s**, atteint seulement si chacun des 100 sondages
  épuise ses 5 s. L'invariant, lui, tient : la boucle **termine** et **échoue
  fermé** — c'était le chiffre qui mentait, pas le mécanisme. Le pire cas est
  épinglé par test, pour qu'il ne redevienne pas une phrase que personne ne
  recalcule.

  **Pourquoi il n'est pas borné côté Python.** `osa()` exécute ici un script
  qui clique un menu **puis tape le titre**. Le tuer en cours de route
  laisserait un en-tête créé **sans titre** — précisément la mutation
  partielle silencieuse que `_LOST_FOCUS_MARKER` existe pour signaler. Borner
  le sous-processus échangerait une attente longue, que l'utilisateur voit,
  contre un objet vide dans sa base, qu'il ne voit pas. Et découper le script
  pour ne borner que la sonde rouvrirait le trou entre le contrôle et le clic
  que ce script referme.

  Le premier plan est **réaffirmé entre le clic et la frappe**, et c'est ce
  contrôle-là qui ferme réellement le risque : celui d'en tête de script en
  est séparé par le clic de menu et son délai. Cette seconde vérification
  n'attend pas (`attempts=1`) — après le clic il n'y a plus rien à attendre,
  et patienter ne ferait que taper plus tard dans la mauvaise application. Son
  marqueur est distinct (`_LOST_FOCUS_MARKER`), parce que sa conséquence l'est
  aussi : le clic, lui, a eu lieu, donc un en-tête **sans titre** a pu être
  créé — le message le dit plutôt que de le taire.

  **Ce qui reste une durée devinée sur ce chemin, et qui n'est pas fermé** :
  `delay 0.4` entre le clic et la frappe. Il couvre l'apparition du champ de
  saisie, condition qui n'est observable que par l'arbre d'accessibilité et
  dont l'établissement sur pièce impose de créer réellement un en-tête dans
  une base réelle. Énoncé plutôt que tu, comme les autres résidus de ce
  document. Sa portée est en revanche réduite : la frappe qu'il précède est
  désormais gardée par un constat de premier plan, donc son échec ne peut plus
  faire partir le titre hors de Things — au pire dans une vue de Things qui
  n'a pas encore ouvert son champ.
- **Aucun caractère non saisissable n'atteint jamais une frappe clavier.**
  `keystroke` tape ce qu'on lui donne : dans le champ de saisie d'un
  heading, un retour à la ligne vaut validation — heading créé tronqué,
  reste du titre et `key code 36` partis hors du champ visé. La
  vérification post-action (§ 1) rattrape le *code retour*, jamais cet
  effet de bord déjà produit dans les données réelles de l'utilisateur.
  `cmd_create_heading` refuse donc tout titre contenant un caractère de
  catégorie Unicode `Cc`, `Zl` ou `Zp` (`_untypable_chars`), **avant** la
  résolution du projet et **avant** toute activation de l'application —
  rien n'est ouvert, rien n'est cliqué, rien n'est tapé
  (`test_untypable_title_refused_before_any_activation`, paramétré sur la
  classe ; `test_typable_titles_are_accepted` garde contre le sur-refus).
  Le garde porte sur la **classe**, pas sur les seuls caractères
  rencontrés : toute nouvelle commande pilotant l'interface au clavier
  doit le réutiliser plutôt que d'énumérer des caractères.
- La vérification post-action (§ 1) s'applique intégralement ici : succès
  de l'automatisation d'interface (rc=0, "OK") et présence du heading en
  base sont deux conditions **distinctes**, toutes deux nécessaires
  (`test_ui_succeeds_but_heading_not_observed_fails` couvre le cas où la
  première est vraie et la seconde fausse).
- **Aucune valeur ne sort d'un littéral de chaîne AppleScript.** Un guillemet
  double non échappé referme le littéral, et le reste de la valeur devient du
  CODE — exécuté par `osascript` sous l'identité de processus à laquelle le
  consentement TCC est accordé. La conversion est `_esc`, qui neutralise
  l'antislash, le guillemet et le saut de ligne, dans cet ordre (l'antislash
  d'abord, sans quoi `\\"` deviendrait un antislash littéral suivi d'un
  guillemet qui referme).

  L'invariant ne se tient PAS par relecture. Il était respecté par 17 sites
  sur 18, et le dix-huitième — `cmd_create_area` — a survécu des mois
  (BUG-033) : relire ne distingue pas 17 de 18, et `create-area` était par
  ailleurs la seule sous-commande d'écriture sans aucun test. Il est donc
  gardé par un **balayage d'AST à compte résiduel nul**
  (`tests/test_applescript_escaping.py`), sur le modèle de
  `test_no_executable_is_invoked_by_bare_name`.

  Le prédicat porte sur la **position**, jamais sur le nom d'une commande :
  une interpolation précédée d'un nombre **impair** de guillemets non
  échappés — comptés sur le texte statique **cumulé** depuis le début du
  littéral — est, par construction, à l'intérieur d'un littéral du script
  émis. Une garde formulée par énumération de sites connus rate le prochain
  oubli, qui est précisément le seul cas qui compte. Seule dispense : une
  constante de **module** affectée **une seule fois** à un littéral, **jamais
  visée par un `global`**, et **non masquée dans la portée du site**.

  Les trois conditions ne sont pas décoratives, et les deux dernières ont été
  ajoutées au troisième tour de review : la dispense ajoutait sur la
  **première** affectation et ne retirait **jamais**, si bien qu'un `global`
  depuis n'importe quelle autre fonction du module, ou une seconde affectation
  au niveau module à une valeur calculée, la conservaient — les deux mesurés
  sur `bin/thingskit` lui-même, par manipulation. La formulation précédente
  disait « sa valeur est écrite dans la source, aucune entrée ne l'atteint » :
  elle était fausse dans ces deux cas. Elle reste hors de vue pour
  `globals()[…] = …` et `setattr(sys.modules[__name__], …)` — 0 occurrence,
  mesuré le 2026-08-26 par
  `grep -nE "global |globals[(][)]|setattr" bin/thingskit` (rc=1, vide).

  La **dispense `_esc` est gardée de la même façon**, ce qui n'était pas le cas
  : `node.func.id == "_esc"` n'a, seul, aucune notion de portée, et un
  `_esc = lambda s: s` posé en tête de fonction dispensait tout son corps.
  `_esc` doit désormais être défini **une fois, au niveau module, par un
  `def`**, et n'être relié dans aucune portée englobant le site. Un marqueur
  passé en **paramètre** n'en est pas une :
  `_frontmost_check` recevait le sien ainsi, et lui écrire une exception
  aurait rouvert la classe ; il passe donc par `_esc`, no-op sur ces
  constantes.

  **Le prédicat a d'abord fermé la forme exacte du défaut, pas sa classe**
  (relevé en review le 2026-08-26). Écrit `before.endswith('"')` sur le seul
  fragment constant précédant l'interpolation, il laissait passer quatre
  formes voisines, toutes banales — mesuré :

      osa(f'x name:"{a.name}"')                 -> détectée (la forme de BUG-033)
      osa(f'error "{_esc(M)} sur {a.name}"')    -> MANQUÉE (2e valeur, même littéral)
      osa('x name:"{}"'.format(a.name))         -> MANQUÉE
      osa('x name:"%s"' % a.name)               -> MANQUÉE
      M = "x" ; def f(M): osa(f'error "{M}"')   -> MANQUÉE (paramètre homonyme)

  Quatre durcissements **réduisent** la classe, ils ne la ferment pas — la
  phrase « trois durcissements ferment la classe » a figuré ici et elle était
  fausse (cf. le décompte plus bas). La **parité** cumulée, guillemets
  échappés retirés d'abord — sans ce retrait un `\"` inverserait le compte et
  rendrait la garde aveugle sur toute la suite du littéral. La **portée**, qui
  se remonte jusqu'au module et annule la dispense sur tout nom lié
  localement — **des deux côtés de la dispense**, constante de module *et*
  `_esc`. L'**unicité** de l'affectation d'une constante dispensée. Et
  l'**interdiction des formes hors f-string** — `%`, `.format`, `.join`, `+`
  —, auxquelles un balayage de `JoinedStr` est aveugle par construction : il
  n'en existe aucune occurrence, donc l'interdiction est gratuite à poser, et
  c'est le seul moment où elle l'est.

  Son critère n'est pas uniforme, et c'est mesuré : « porte un guillemet »
  convient au gabarit qui referme ses guillemets autour du trou
  (`'name:"%s"'`), mais rend 2 faux positifs sur `+`, où le dépôt concatène
  légitimement des fragments ÉQUILIBRÉS (`bin/thingskit:1365` et `:1392`) —
  `+` relève donc de la parité. Une garde à faux positifs est une garde
  désactivée. **`.join` a deux critères**, et l'annonce « `.join` est
  interdit » n'en couvrait qu'un : le séparateur portant le guillemet
  (`'", "'.join(…)`), qui est le cas rare. Le cas banal met le guillemet dans
  les fragments et laisse le séparateur vide — `''.join(['name:"', v, '"}'])`
  — et il était invisible ; il l'est encore quand la liste est **hissée dans
  une variable** avant le `.join`, forme que seule une analyse de flot verrait.

  **Ce que ces balayages ne tiennent pas, et le chiffre qui le dit.** Un banc
  de 12 formes d'évasion a été soumis aux prédicats réels le 2026-08-26 :
  **2 vues avant ce lot, 5 après, 7 non vues**. Les sept partagent une
  propriété, et c'est elle qui borne la garde honnêtement — **la valeur
  transite par une indirection que l'analyse statique ne suit pas** :

  1. gabarit ouvert rangé dans une **variable locale**, puis f-string ;
  2. gabarit `%` **hissé** dans une constante de module (`TPL % v`) ;
  3. `'…"@@"…'.replace('@@', v)` ;
  4. liste de fragments **hissée**, puis `''.join(l)` ;
  5. `io.StringIO()` + `.write()` incrémental ;
  6. `string.Template('…"$n"…').substitute(n=v)` ;
  7. `%` dont l'opérande gauche n'est pas un littéral nu.

  Trois angles morts de plus, hors banc : une valeur injectée par une
  interpolation **antérieure** du même littéral (inconnue à l'analyse, traitée
  comme neutre) ; `globals()[…]` et `setattr` sur une constante dispensée ; et
  une interpolation **dans un `format_spec`**, dont le texte statique compte
  désormais dans la parité mais dont le contenu repart d'une parité vierge.

  La garde tient donc la forme **directe** — celle où le gabarit et la valeur
  se rencontrent dans la même expression. C'est la forme de BUG-033 et celle
  de ses 17 voisins sains ; ce n'est pas toute manière d'écrire le défaut. La
  liste ci-dessus est reprise mot pour mot dans la docstring du module de
  test, qui est l'endroit où on la lit en travaillant. **Le sujet est clos à
  ce point** : la complétude d'une analyse statique de Python est un puits
  sans fond, et une garde modeste qui dit exactement ce qu'elle tient vaut
  mieux qu'une garde ambitieuse qui promet plus.

  La garde est elle-même éprouvée par contre-épreuve : elle doit VOIR le corps
  de BUG-033 rejoué, et ne PAS refuser sa forme échappée — sans quoi un
  balayage cassé rendant « rien à signaler » sur tout passerait pour vert.

### 3. Identité de code et consentement TCC

La lecture de la base Things traverse un Group Container, donc le service TCC
`kTCCServiceSystemPolicyAppData`. Ce consentement n'est **pas** accordé à un
fichier de script : il est accordé à une **identité de code**, celle du binaire
qui s'exécute. Un script à shebang n'en porte aucune — c'est l'interpréteur qui
la porte.

**Fichiers concernés** : `build/bundle.py` (construction, relocalisation,
signature, compilation du shim, lecture de `FAST_PATH_COMMANDS`, lecture de la
configuration d'identité), `build/thingskit-launch.c.in` (gabarit du shim),
`build/identity.conf` (origine unique de configuration, **locale et jamais
versionnée**), `bin/thingskit` (`compose_code_requirement`,
`parse_code_identity`, `FAST_PATH_COMMANDS`, `code_identity_refusal`),
`Contents/Resources/code-identity` (fichier d'identité scellé), l'`Info.plist`
généré, le lanceur installé en `~/.local/bin/thingskit`.

**Risques propres à cette surface** :
- **Identité mouvante.** Le Python Homebrew est signé ad-hoc et son identifiant
  change à chaque montée de version (mesuré : `python3-5555494470c5…` en 3.14.6,
  `python3-55554944b6bd…` en 3.14.7). Le grant tombe à chaque `brew upgrade
  python`, et l'outil se met à bloquer sur un dialogue — inutilisable pour toute
  session sans utilisateur devant l'écran.
- **Autonomie apparente.** Mesuré le 2026-08-18 : `otool -L` de l'exécutable
  peut être propre alors que le processus recharge la dylib du Cellar par le
  stub `Versions/*/Resources/Python.app`, et que `sys.prefix` repart vers
  `/opt/homebrew`. Le défaut n'est visible que sous `DYLD_PRINT_LIBRARIES` — il
  ne se déduit pas de la seule inspection de l'exécutable.

  **Le constat vaut aussi de l'OUTIL d'inspection, et c'est la moitié qui est
  restée ouverte jusqu'au 2026-08-23.** Le correctif du 18 a étendu le contrôle
  de l'exécutable seul à tous les Mach-O, en gardant `otool -L` — qui n'imprime
  que `LC_ID_DYLIB` et `LC_LOAD_DYLIB` et ne montre **jamais** `LC_RPATH`,
  c'est-à-dire la première chose que dyld consulte. Mesuré le 23 sur
  `/Applications/thingskit.app` : **92 Mach-O, 83 portant un `LC_RPATH` vers
  `/opt/homebrew`**, `/opt/homebrew/lib` **premier dans l'ordre de recherche sur
  les 83**, et le contrôle vert. Sept d'entre eux — `_ssl`, `_hashlib`,
  `_sqlite3`, `_decimal`, `_lzma`, `_zstd`, `libsqlite3.dylib` — chargent par
  `@rpath` une dylib qui **existe réellement** dans `/opt/homebrew/lib` : seule
  la *library validation* implicite du hardened runtime empêchait le chargement,
  soit une défense unique et implicite pour un invariant déclaré tenu. La cause
  est que la relocalisation **ajoute** les rpaths du bundle sans **retirer** ceux
  du framework Homebrew ; ajouter ne remplace pas.

- **Autonomie apparente, deuxième route : le démarrage de Python.** Mesuré le
  2026-08-23 sur le bundle nettoyé de ses rpaths et par le chemin **exact** (le
  shim pose `-I`) : `sys.path` portait encore
  `/opt/homebrew/lib/python3.14/site-packages`, et `_distutils_hack` — un module
  dont le fichier est sous `/opt/homebrew` — était **importé à chaque
  démarrage**, dans le processus qui porte le consentement TCC. Les trois
  fichiers `.pth` de ce dossier s'exécutent à chaque lancement, et `brew`/`pip`
  les réécrivent sans prévenir. La cause est `lib/python3.14/sitecustomize.py`,
  écrit par Homebrew et copié tel quel avec le framework : sa fin appelle sans
  condition `site.addsitedir('/opt/homebrew/lib/python3.14/site-packages')`.
  `-I` n'y change rien — il implique `-s` (pas de site utilisateur) mais pas
  `-S`, donc `site.py` tourne et importe toujours `sitecustomize`. C'est la
  route jumelle de celle que `_prune_dangling_symlinks` fermait déjà, avec le
  même motif écrit ; une des deux était fermée.
- **Perte du code de sortie.** Un lanceur qui créerait un sous-processus, ou qui
  passerait par `open -a`, détruirait le contrat `0` = effet constaté.
- **Signature invalidée.** `install_name_tool` invalide la signature d'un
  Mach-O ; le binaire est alors tué par le noyau (mesuré : SIGKILL, rc=137).
  Toute réécriture de chemin de chargement impose une re-signature.

**Invariants** :
- Aucune commande ne s'exécute sous une identité de code qui dépend de la
  version d'un interpréteur installé par un gestionnaire de paquets tiers. Le
  build échoue si une **commande de chargement** d'un Mach-O du bundle nomme
  `/opt/homebrew` — `LC_RPATH` compris, et pas seulement les dylibs qu'`otool -L`
  imprime. Le contrôle lit la table des commandes en direct (`build/macho.py`),
  sur **toutes les tranches** d'un binaire universel, sans sous-processus.

  Les **octets bruts** ne sont délibérément pas balayés : `libcrypto.3.dylib`
  porte `OPENSSLDIR`/`ENGINESDIR`/`MODULESDIR`, et le framework Python son
  `PREFIX` de compilation, en **données compilées** par leur paquet amont. dyld
  ne les lit pas, `install_name_tool` ne peut pas les réécrire, et aucune
  reconstruction d'ici ne peut les retirer : les signaler rendrait la garde
  rouge pour toujours sur un défaut que personne ne peut corriger, ce qui est la
  manière la plus sûre d'apprendre à un lecteur à la sauter. La frontière est
  **épinglée par test** (ensemble mesuré, tout nouveau porteur est signalé), pas
  laissée implicite.
- **Le build et la garde de session partagent un seul énumérateur de Mach-O**
  (`build/macho.py`, copié tel quel dans le vault et épinglé identique à
  l'octet près). Ils en avaient deux : mesuré le 2026-08-23, le contrôle du build
  balayait 142 fichiers — dont 55 qui n'étaient pas des Mach-O — et **en ratait
  5**, tous des liens symboliques, dont `Python.framework/Python` et
  `libpython3.14.dylib`, c'est-à-dire les porteurs mêmes de la dépendance. Deux
  énumérateurs pour un invariant, c'est un contrôle de construction et une garde
  de session qui peuvent se contredire sans que personne ne le voie.
- **Aucun chemin hors du bundle sur le `sys.path` de l'interpréteur vendu.**
  Constaté par **mesure** en fin de build (`assert_interpreter_is_self_contained`),
  qui lance l'interpréteur sous `-I` — le drapeau que le shim pose — et refuse
  tout chemin ou module hors préfixe. L'inspection statique ne peut pas répondre
  à cette question : `sys.path` se construit à l'exécution, et un hook de
  démarrage l'ouvre sur un dossier tiers sans qu'un octet du bundle ne le nomme
  après coup. Le build retire donc aussi les hooks de démarrage
  (`sitecustomize.py`, `usercustomize.py`, `*.pth`) qui nomment le gestionnaire
  de paquets, **avant** la signature.
- **Tout binaire externe est invoqué par chemin absolu**, jamais résolu par
  `PATH`. Le balayage du 2026-08-23 a rendu **7 sites** par nom nu dans
  `build/bundle.py` (1 `otool`, 6 `install_name_tool`), plus un huitième
  (`codesign_command`) que la première règle ratait parce qu'elle ne regardait
  que les arguments d'appel et pas les listes affectées. `install_name_tool` est
  le plus exposé des trois outils : c'est lui qui **réécrit** les Mach-O du
  bundle avant signature, et `brew install binutils` en pose un en tête de
  `PATH`. La règle est **enforcée par test sur tout argv du module**, pas sur une
  liste de noms tenue à la main.
- Le lanceur **`exec`** l'exécutable du bundle, il ne le sous-processe jamais :
  la transmission d'`argv` et du code de sortie est structurelle, pas recopiée.
  Gardé par mesure du PID, pas par lecture du mot-clé
  (`test_launcher_replaces_its_process_image_instead_of_forking`) et par
  comparaison des codes de sortie.
- **Quelle identité est attendue est une DONNÉE du bundle, plus une constante
  du dépôt** (ADR-003, 2026-08-26). Elle vient d'une origine unique de
  configuration sous `build/`, lue par le seul build — jamais par un chemin
  d'exécution du CLI. Ce dernier point est balayé, pas relu, et la portée du
  balayage est **plus étroite que son nom** : il voit le chemin écrit dans un
  littéral du script (résidu nul), et la remontée depuis l'emplacement du
  script est fermée à part (`__file__` absent du fichier). Un chemin
  reconstruit par concaténation ou lu dans l'environnement resterait invisible
  — invariant non gardé, écrit plutôt que tu. Elle voyage ensuite par **deux porteurs, tous deux
  scellés** : la chaîne compilée dans le shim, et un fichier de données écrit
  dans `Contents/Resources/` **avant** la signature — donc couvert par le sceau
  des ressources, ce qui est mesuré et non déduit (une ligne ajoutée au fichier
  d'un bundle construit rend `codesign --verify --strict` en `rc=1`, « a sealed
  resource is missing or invalid »). Les deux sortent de la MÊME lecture, dans
  le même appel de `build()`, et leur égalité est littérale.

  Quatre propriétés, chacune tenue par un test :

  - **La lecture est fail-closed sur la CLASSE, pas sur le cas rencontré.**
    Fichier absent, illisible, indécodable, vide, champ manquant, vide,
    dupliqué, inconnu ou hors forme → refus, code 125, et **aucune invocation
    de `codesign`** : refuser après avoir vérifié quelque chose n'aurait pas de
    sens, puisqu'il n'y a rien à vérifier tant que l'attente n'est pas établie.
  - **Le plancher de forme n'est pas configurable.** L'ancrage Apple générique
    et le marqueur d'extension de TYPE de certificat sont écrits dans le code,
    des deux côtés ; le vocabulaire de la configuration est CLOS — trois champs
    au build, deux dans le fichier scellé — et tout autre champ vaut refus.
    Sans ce plancher, une configuration dégradée affaiblirait la garde en
    silence : la seule dégradation invisible, puisque le build réussirait et
    que le CLI démarrerait.
  - **La valeur est neutralisée AU SITE d'interpolation, par refus.** Une
    valeur hors forme n'est pas échappée, elle fait lever — `_requirement_value`
    est un neutraliseur par refus, là où `_esc` neutralise par échappement, et
    c'est le bon outil ici : la grammaire de `csreq` n'est pas celle
    d'AppleScript, et échapper un guillemet y ferait passer une valeur que ce
    dépôt veut voir refusée. Le mécanisme existe **des deux côtés** — script et
    build composent la même chaîne, donc encourent la même injection de clause.

    **Une défense en profondeur non testée n'est pas une défense, c'est une
    phrase** : ce site n'était exercé par aucun test, et un `return value` posé
    en tête de la fonction — qui la rend passe-plat — laissait la suite ENTIÈRE
    verte (912 passed, mesuré le 2026-08-26 en review). Le contrôle de recette
    est écrit ici parce que c'est lui, et non la relecture, qui établit la
    propriété : poser cette mutation doit faire tomber la suite (22 échecs côté
    script, 23 côté build). Les tests qui la tuent visent
    `compose_code_requirement` / `code_requirement`, jamais le parseur — un
    test écrit contre `parse_code_identity` n'atteint pas le site
    d'interpolation, donc ne tue rien.

  - **La dispense que ce neutraliseur ouvre dans le balayage d'échappement
    porte sur le MOTIF, jamais sur le nom de la fonction appelée — et le nom
    de forme ne doit pas être OMBRÉ au site.** La propriété, en une phrase :
    *un nom lié dans une portée englobante du site ne dispense pas*, qu'il
    soit une fonction neutralisante ou un motif, qu'il vienne d'une
    affectation locale, d'un paramètre ou de sa valeur par défaut. Les deux
    moitiés de la dispense passent par la même remontée de portées.

    **Cette ligne a énoncé deux fois une propriété que le code ne tenait
    pas**, et c'est le motif de la réécrire ici en entier. La première
    version accordait la dispense au seul nom de la fonction appelée et
    n'inspectait pas le second argument : `_requirement_value(a.name, ANY)`
    passait, quel que soit `ANY`. La deuxième a lié la dispense à un motif
    épinglé — et n'a fermé que la moitié de la propriété : ce document a
    alors porté « lié une fois au niveau module à `re.compile(<motif épinglé
    au littéral>)` » devant un balayage qui ne regardait ni l'ombrage local
    ni les paramètres. Un site AppleScript neuf dont la forme permissive
    arrivait par la valeur par défaut d'un paramètre passait intégralement,
    la suite restant à sa ligne de base — reproduit en revue.

    Ce que le prédicat d'approbation exige aujourd'hui, condition par
    condition : le nom est d'une **liste close** ; il est lié **une fois**, au
    niveau module ; sa valeur est `re.compile(...)` où `re` est bien le module
    importé — le nom d'attribut seul approuvait `fake.compile(...)` — ; l'appel
    n'a **qu'un** argument, car un drapeau surnuméraire comme `re.I` élargit
    la forme sans toucher au motif ; cet argument est le littéral épinglé ; et
    le nom n'est **ombré dans aucune portée englobante** du site. Chacune est
    gardée dans les deux sens.
  - **Le contenu du fichier est d'origine non contrôlée** — c'est précisément
    dans le cas où il n'est pas scellé que la garde refuse — donc il ne
    traverse jamais un message sans conversion `!r`.

  **Ce que ce changement ne ferme pas, et qui est dit plutôt que tu** : le
  contrôle devient **auto-référentiel** — celui qui fournit le binaire fournit
  aussi l'attente qui lui est opposée. La population capable de produire un
  artefact acceptable passe d'une équipe nommée à l'ensemble des détenteurs
  d'un certificat Apple Developer. C'est un élargissement d'un cran, acté dans
  ADR-003 § « Ce que la garde garantit », et le résidu d'ADR-002 § Decision 5bis
  n'en change pas de nature.

- **La destination de la ligne de commande du build subit la forme de la
  configuration**, comme la valeur configurée. Elle y échappait alors que la
  même donnée, passée par le fichier, était validée : un écart de traitement
  sur une valeur qui atterrit dans une chaîne C et dans un `sh` est une
  invitation, même quand il n'est pas exploitable.

- **Le chemin d'installation vient de la même origine** (ADR-003, INV-003-8) :
  aucune source publiée ne le grave. Le balayage porte sur le CODE — littéraux
  hors docstring des `.py`, lignes non commentées du gabarit C — parce qu'une
  garde qui balaie aussi la narration d'une mesure impose une allowlist qui
  grossit à chaque paragraphe, donc une garde désactivée dans le mois.

- **L'unité d'organisation d'un sujet X.509 ne se lit qu'à l'UNIQUE.** La
  sélection du certificat est le point où la configuration entre dans le
  build : un sujet portant deux `OU=` est **ambigu**, et « le premier gagne »
  y serait un choix implicite sur la valeur qui décide de l'identité du
  build. Il est donc refusé — fail-closed, le certificat devient inéligible,
  jamais éligible par erreur. Même règle que les résolutions par titre du CLI.
  Le découpage RFC2253 (une virgule échappée n'est pas un séparateur) était
  déjà en place et le reste.

- **L'entrée CLI du script source refuse tout interpréteur qui ne porte pas
  l'identité du bundle** (BUG-009, 2026-08-19). Avant ce correctif, rien ne
  forçait le passage par le lanceur : `bin/thingskit` restait exécutable par
  n'importe quel python du poste, et l'accès à la base se faisait alors sous
  l'identité de CET interpréteur — donc une invite `kTCCServiceSystemPolicyAppData`
  par interpréteur, et une nouvelle à chaque `brew upgrade python`. La garde
  oppose `codesign --verify --strict -R=<exigence du bundle>` à
  `sys.executable`, avec le chemin absolu `/usr/bin/codesign` : le discriminant
  est la signature, pas une variable d'environnement ni un chemin, qu'il
  suffirait de poser pour retrouver le défaut. Elle est **fermée par défaut**
  (codesign inexécutable = identité non établie = refus) et rend le code 125,
  distinct de `main` (0/1), d'argparse (2) et du refus de sceau du lanceur (126).
  Elle porte **uniquement sur `__main__`** : `tests/conftest.py` charge le
  fichier comme module, et un refus au niveau module casserait la suite sans
  rien protéger de plus. Corollaire de test : l'aide et la validation
  d'arguments ne s'éprouvent plus en lançant le script en sous-processus —
  la fixture `run_cli` de `conftest.py` appelle `main()` dans le processus.
- **La portée `__main__` est le contour EXACT de la garde d'identité, pas une
  commodité de test.** Elle couvre l'invocation en ligne de commande, et rien
  d'autre : importer `bin/thingskit` comme module puis appeler `main()` l'évite
  entièrement — mesuré, cela rend `rc=0` et accède à la base sous l'identité de
  l'interpréteur appelant. Ce n'est pas un trou à combler mais une limite
  assumée, figée par `test_the_guard_is_not_evaluated_at_import_time` : le
  vecteur qu'ADR-001 ferme est l'exécution du script par un python de passage,
  et quiconque importe le module a déjà, par construction, la main sur le
  processus. Ne pas lire cette garde comme un contrôle exhaustif de tout accès
  à la base.
- **La garde refuse, elle ne pend ni ne plante** (BUG-011, 2026-08-19).
  L'invocation de `codesign` est bornée par `CODESIGN_TIMEOUT` — un
  vérificateur bloqué (volume réseau, `AppleMobileFileIntegrity` occupé) ferait
  autrement pendre toute invocation du CLI sans message —, et la capture
  englobe `OSError`, `subprocess.SubprocessError` (dont `TimeoutExpired`) et
  `ValueError` : tout échec d'invocation devient le refus nommé, avec son code
  125, jamais un traceback rendant 1 qu'on confondrait avec un échec de
  `main()`. La constante est lue à l'appel, pas figée en défaut de `def`.
- **Que `-R` porte le contrôle est figé par un test, pas par la seule structure
  de la commande.** `test_the_requirement_is_what_rejects_an_apple_signed_third_party_binary`
  exécute le vrai `codesign` contre `/bin/ls` — binaire Apple présent sur tout
  macOS, valablement signé mais étranger au bundle : `rc=0` sans `-R`, `rc=3`
  avec l'exigence. Sans lui, `codesign` constate « une signature valide »,
  jamais « LA signature ».
- **Le build ne se déclare réussi qu'après avoir opposé l'exigence de code à
  l'artefact qu'il vient de produire** (BUG-013, 2026-08-19). Le contrôle porte
  sur `dest` tel qu'il est sur disque, après `_sign_everything`, et non sur une
  reconstitution de ce qu'on croit avoir signé : c'est ce qui lui permet de
  contredire le reste du build. Avant ce correctif, le seul `-R=` du dépôt
  vivait dans le lanceur — l'exigence n'était donc opposée qu'à l'exécution,
  chez l'utilisateur. C'était tolérable tant que l'identité de signature était
  nommée en dur ; depuis BUG-010 elle se résout sur le poste, et toute erreur
  de sélection ne se manifestait plus qu'au premier lancement, en refus de
  sceau opaque — sur un poste fraîchement équipé, un build « réussi » puis un
  outil inutilisable. Le refus nomme l'exigence, l'artefact et l'identité
  employée. Gardé par `assert_bundle_satisfies_requirement` et ses tests, qui
  opposent la fonction réelle à des artefacts réellement signés (un `.app`
  signé ad-hoc passe `--verify --strict` et doit malgré tout être refusé) —
  aucune sonde n'y remplace `codesign`.
- **Sur le chemin du lanceur, aucun code venu d'en dehors du sceau ne
  s'exécute sous l'identité qui porte le grant.** La restriction « sur le
  chemin du lanceur » n'est pas une précaution de style : elle est la portée
  réelle de l'invariant, et l'énoncer sans réserve affirmerait une fermeture
  qui n'existe pas (voir « Ce qui n'est pas établi »). Le lanceur passe `-I`
  avant le script embarqué : l'interpréteur
  cesse d'honorer `PYTHONPATH`, `sitecustomize.py` et les `site-packages`
  utilisateur. Mesuré le 2026-08-18, et c'est bien l'invariant central qui était
  en défaut : on construisait une identité de code stable, puis on la laissait
  charger du code arbitraire. Le hardened runtime bloque
  `DYLD_INSERT_LIBRARIES` mais n'a jamais couvert les variables propres à
  Python — l'un ne vaut donc jamais preuve de l'autre. Gardé par
  `test_launcher_neutralises_pythonpath_injection`, qui pose réellement un
  `sitecustomize.py` et constate qu'il ne s'exécute pas, et par
  `test_launcher_passes_isolation_then_the_embedded_script`, qui garde la
  **position** du drapeau (posé après le script, `-I` devient un `argv` inerte).
- **Un exécutable invoqué depuis le processus qui porte le grant l'est par
  chemin absolu, jamais par nom nu.** `-I` ferme les variables `PYTHON*` ; il
  ne ferme pas `PATH`, qui est hérité de l'appelant. Une commande résolue par
  `PATH` est détournable par un homonyme déposé dans un de ses répertoires, et
  le code du stub tourne alors sous l'identité porteuse du grant — même classe
  de défaut que `PYTHONPATH` et que le `codesign` du lanceur, mais avec une
  conséquence plus lourde : ce n'est pas une garde qu'on neutralise, c'est du
  code arbitraire qu'on exécute. Reproduit le 2026-08-18 : un stub `osascript`
  posé dans `PATH` s'exécutait, sceau valide et `-I` posé. `bin/thingskit`
  invoque donc `/usr/bin/osascript`, `/usr/bin/open` et `/usr/bin/pgrep` par
  des constantes nommées (`OSASCRIPT`, `OPEN`, `PGREP`). Gardé par
  `test_no_executable_is_invoked_by_bare_name`, qui balaie l'AST du script et
  exige un compte résiduel nul — pas une liste de trois cas —, et par
  `test_osa_ignores_a_homonym_stub_placed_in_path`, qui rejoue réellement
  l'exploit.

  **Portée exacte du balayage, et sa limite.** Le balayage lit les appels
  `subprocess.<run|Popen|call|check_call|check_output>` et les appels
  `os.<system|popen|exec*|spawn*>`. Il rejette `shell=True`, l'`argv` passé en
  `args=`, une constante de module non absolue, et **toute** forme dont il ne
  sait pas résoudre `argv[0]` — f-string, concaténation, liste construite par
  `append`, argument reçu d'un appelant. Une réaffectation est suivie sur
  **toutes** ses branches, pas sur la première trouvée. C'est ce dernier point
  qui fait tenir l'énoncé « compte résiduel nul » : jusqu'au 2026-08-18,
  `argv[0]` non résolu valait `continue`, donc « je ne sais pas lire » se
  lisait « rien à signaler », et le compte n'était nul que sur les deux formes
  lisibles (sondé sur dix formes : 2 détectées, 8 manquées ; le résidu réel
  était nul, mais la garde promettait plus qu'elle ne tenait). Gardé par
  `test_the_sweep_flags_every_form_it_cannot_vouch_for`, qui rejoue les dix.

  Ce qu'il ne voit **pas**, et qui reste donc à la charge de la revue : une
  invocation passant par un import d'alias (`from subprocess import run`), par
  un autre module (`pty`, `multiprocessing`, `shutil.which`), ou par un attribut
  résolu dynamiquement. `bin/thingskit` n'emploie aujourd'hui aucune de ces
  formes — l'énoncé décrit une limite de portée, pas un résidu.
- **Le sceau est évalué au lancement, jamais seulement a posteriori.**
  `Contents/Resources/thingskit` appartient à l'utilisateur : il est modifiable
  sans élévation, et le code modifié tournerait avec le grant. Le lanceur
  exécute `/usr/bin/codesign --verify --strict` avant l'`exec` et **refuse en
  nommant la cause** — jamais un silence, jamais un code ambigu. Le chemin du
  vérificateur est **absolu** : résolu par `PATH`, il serait détournable par
  l'environnement, soit exactement la classe de défaut que `-I` referme. Le code
  de refus (`126`) n'entre en collision avec aucun code du chemin nominal
  (`main()` rend 0, 1 ou 2 — `cmd_find` rend 2 sur cible de rattachement
  introuvable ou ambiguë, comme argparse sur usage invalide), sans quoi le
  contrat « 0 = effet constaté » cesserait de remonter intact. Une seule
  ambiguïté subsiste, et elle est hors du chemin nominal : `sh` rend lui aussi
  `126` quand son `exec` final échoue — un `Contents/MacOS/thingskit` absent
  produirait donc `126` sans être passé par la branche de refus. Ce sont les
  messages sur `stderr` qui séparent les deux causes, jamais le seul code.
  Coût mesuré (20 runs par cas, 2026-08-18) : 12,6 ms pour `codesign --verify
  --strict` seul ; 80,1 ms pour un `thingskit areas` sans le contrôle, contre
  103,9 ms via le lanceur — ~24 ms de surcoût, dont ~13 ms de `codesign`
  lui-même, le reste étant le processus supplémentaire forké par le lanceur.
- **Un échec de signature arrête le build.** Chaque `codesign` passe par `_run`
  (`check=True`) : un Mach-O imbriqué laissé non signé après `install_name_tool`
  est tué par le noyau (SIGKILL, rc=137), et un build qui rendrait `0` dans cet
  état mentirait sur son artefact — c'est la transposition au build de
  l'invariant « commande envoyée ≠ effet constaté »
  (`test_a_failing_codesign_aborts_the_build`). L'exception est **`-add_rpath`**,
  dont l'échec sur un rpath déjà posé est le régime normal d'une
  reconstruction : il est toléré **ciblément**, par `_try_add_rpath`, jamais par
  une absence générale de contrôle (`test_vendored_dylib_rewrite_tolerates_an_already_present_rpath`).
- `Contents/Resources/thingskit` est une copie **octet pour octet** de
  `bin/thingskit`, vérifiée par le build, jamais éditée sur place.
- **Aucun entitlement n'est posé sans mesure.** `disable-library-validation` a
  été écarté sur pièce : sous hardened runtime et sans entitlement,
  `Contents/MacOS/thingskit -c "import sqlite3"` rend `3.53.4`, rc=0 (mesuré le
  2026-08-18). Ajouter un entitlement « au cas où » affaiblit la signature sans
  motif.
- **La stabilité de `codesign` ne vaut jamais preuve de persistance du grant,
  et `Identifier`/`TeamIdentifier` inchangés ne valent pas stabilité de
  signature au sens de TCC.** Mesuré le 2026-08-21 : le `csreq` enregistré par
  TCC pour l'identifiant du bundle pinne l'`identifier`, l'ancre Apple, le **CN
  du certificat feuille** et le marqueur de l'intermédiaire — **jamais le Team
  ID**. Un bundle re-signé sous une identité de type différent conserve donc
  `Identifier` et `TeamIdentifier`, satisfait `--verify --strict`, et a
  néanmoins perdu le grant. Corollaire mesuré dans l'autre sens : un CDHash
  différent (contenu modifié, même identité) satisfait le `csreq` — un rebuild
  ordinaire ne casse pas l'exigence enregistrée. Les deux conditions
  nécessaires après tout événement susceptible de toucher la signature ou
  l'interpréteur restent donc : l'exigence de code du dépôt est satisfaite par
  l'artefact **et** une commande d'écriture rend la main sans dialogue. La
  première ne se substitue jamais à la seconde. C'est la transposition directe
  de l'invariant « commande envoyée ≠ effet constaté ».
- **L'exigence de code du dépôt est plus stricte que celle enregistrée par TCC
  sur le TYPE de certificat, et délibérément plus permissive sur le CN de la
  feuille** (ADR-002 § Decision 5). Elle porte un discriminant de **type** — le
  marqueur d'extension `1.2.840.113635.100.6.1.2` —, faute de quoi le repli
  nommé par ADR-001 § NC-3 (*Apple Development* → *Developer ID Application*)
  produirait un build « réussi » et un outil qui redemande le consentement au
  premier lancement : Team ID inchangé, sceau valide, grant perdu. Le CN
  individuel n'est **délibérément pas** pinné, à la différence du `csreq` de
  TCC : il change à chaque renouvellement, et le pinner casserait le lanceur à
  échéance fixe. L'écart subsiste donc **dans les deux sens**, et c'est assumé :
  un second certificat *Apple Development* du même Team ID, ou un renouvellement
  qui change le CN, satisfait l'exigence du dépôt et perd le grant. L'énoncé
  « au moins aussi stricte » qui figurait ici jusqu'au 2026-08-21 était contredit
  par sa propre concession sur le CN. Gardé par une contre-épreuve dans les
  **deux** sens — la clause opposée seule refuse un artefact ad-hoc et accepte le
  bundle réel —, sans quoi une clause inerte (OID mal orthographié, syntaxe
  tolérée et sans effet) passerait inaperçue.

  **Ce qui n'est pas établi** : que ce marqueur discrimine effectivement d'un
  *Developer ID Application*. Le trousseau de ce poste ne contient qu'**une**
  identité de signature (`security find-identity -v -p codesigning` → 1) ; la
  contre-épreuve n'y est pas faisable, et ne doit pas être présentée comme
  tranchée dans un sens ni dans l'autre. Ce qui trancherait : opposer cette
  clause à un artefact réellement signé *Developer ID Application*.
- Aucun test automatisé ne dépend du bundle installé (gardé par
  `test_the_suite_does_not_depend_on_an_installed_bundle`) — la suite doit
  passer sur un poste où `thingskit.app` n'existe pas, **et sur un poste dont le
  bundle est antérieur au dernier chantier**.

  **La garde porte sur l'ADÉQUATION du saut, pas sur la présence d'un
  `skipif`** (amendement du 2026-08-21). Elle s'en contentait jusque-là, et
  c'est exactement la classe qu'elle a laissée passer : le `skipif` de
  `test_the_root_seal_does_not_vouch_for_the_shim` décidait sur `not isdir(…)`
  — la seule **présence** du bundle — alors que son corps exige un bundle
  **conforme**, portant le shim d'ADR-002. Sur un poste en cours de mise à
  niveau, le test ne sautait pas et échouait sur `assert shim.is_file()`, avant
  d'atteindre la moindre assertion sur le sceau : l'ordre de travail « suite
  verte, puis build » y devenait intenable, un test exigeant l'artefact que le
  build produit. La garde avait pourtant été refondue lors d'ADR-002 pour porter
  sur l'atteinte réelle du système de fichiers plutôt que sur des exemptions par
  nom de fichier — elle a déplacé son angle mort au lieu de le fermer.

  L'adéquation se décide donc en **un seul endroit**, `conforming_bundle_missing()`
  de `tests/conftest.py` (bundle présent **et** shim présent), exposé par le
  marqueur `requires_conforming_bundle` ; le balayage exige que le saut de toute
  occurrence atteignant le bundle soit décidé par lui. Le prédicat est la seule
  fonction du dépôt autorisée à l'atteindre hors d'un test gardé — **exemption
  structurelle**, attachée au mécanisme et non à un nom de fichier, sans quoi la
  liste d'exemptions que la refonte d'ADR-002 avait supprimée renaîtrait.

  Second angle mort fermé du même coup : le balayage ne reconnaissait la cible
  qu'en **littéral**. `tests/test_code_identity.py` l'atteignait par
  `bundle.INSTALL_PATH` et lui échappait entièrement. Nommer la cible par une
  constante la rend identique, jamais absente.

  Les trois formes sont gardées par contre-épreuve — la garde doit **flaguer**
  un `skipif` inadéquat (`test_the_c4_guard_refuses_a_skipif_that_does_not_decide_on_the_bundle`)
  et une atteinte par la constante
  (`test_the_c4_guard_sees_a_reach_named_by_the_module_constant`), et **ne pas
  crier au loup** sur un test correctement gardé, sur le prédicat lui-même ni
  sur une simple génération de chaîne. Sa limite est inchangée et énoncée sans
  réserve : elle lit une liste d'appels (`_FILESYSTEM_REACH`), donc c'est un
  proxy, pas une preuve — la preuve reste l'exécution sur un poste nu.
- **Une annotation ne nomme jamais ce que le module ne lie pas.** Sous Python
  3.14 (PEP 649) une annotation n'est évaluée que si on la demande : un nom
  jamais importé y est **invisible**, sur ce poste comme sur l'autre. Sous tout
  Python antérieur — et `requires-python` déclare `>=3.11` — c'est un
  `NameError` levé à la **définition** de la fonction, donc, pour une fonction
  imbriquée dans un test, au moment où ce test s'exécute. Constaté le
  2026-08-21 : `tests/test_bundle.py` annotait `def verify(target: Path)` sans
  importer `Path`, et la suite était verte sur les deux postes. Gardé sur la
  **classe** par `tests/test_annotations_resolve.py`, qui balaie tous les
  fichiers Python du dépôt, jamais le seul fichier fautif.

**Responsabilité de processus et consentement AppleEvents** (ADR-002,
2026-08-21). `kTCCServiceSystemPolicyAppData` s'évalue sur le processus
lui-même ; `kTCCServiceAppleEvents` s'évalue sur son processus **responsable**,
que l'`exec` depuis `sh` laisse à l'ancêtre applicatif. D'où le symptôme exact
qu'ADR-001 laissait ouvert : lectures silencieuses, écritures qui redemandent —
et une prolifération sans borne, un sujet TCC par terminal, par hôte et par
**version** d'hôte (cinq clients distincts mesurés en base le 2026-08-21, dont
trois chemins versionnés du même outil).

- **INV-002-1** — Aucune sous-commande sollicitant `osascript` ne s'exécute sans
  que le processus ait été rendu son propre responsable. La partition est
  **fail-closed** : ce qui n'est pas explicitement déclaré en régime rapide est
  disclaimé, y compris une sous-commande inconnue, absente ou invalide. Le sens
  de lecture est la décision, pas un détail — avec une liste d'écritures, une
  commande oubliée retomberait en régime rapide et l'invite reviendrait, défaut
  **silencieux** ; avec la liste des lectures, le même oubli coûte ~10 ms par
  appel, **visible et bénin**. Gardé par mesure du responsable réel dans le
  processus final (`responsibility_get_pid_responsible_for_pid`), jamais par
  relecture de la liste embarquée.

  **Trois chiffres faux ont figuré ici, et le troisième est le plus instructif :
  il mesurait un défaut d'exploitation, pas un coût du disclaim.** Le « ~830 ms »
  d'origine venait d'une mesure prise juste après un rebuild. Son remplaçant du
  tour 1 — « dix appels disclaimés à 0,07 s » — venait d'une invocation qui
  mourait dans `argparse`, rc=2, sans atteindre SQLite ni tccd : elle
  chronométrait l'analyse des arguments. Le troisième — « de l'ordre de la
  seconde à chaque invocation, ~0,8 s via le lanceur, ~1,9 s sur spawner nu,
  et jusque sur `find-task` qui n'envoie aucun Apple Event » — était une mesure
  correcte de la mauvaise chose : elle chronométrait **l'attente d'une réponse
  humaine à une invite TCC**, faute de l'Acces complet au disque décrit en
  INV-002-8. La « permanence » du surcoût et sa présence sur des sous-commandes
  sans Apple Event, qui rendaient l'explication par le coût du disclaim
  bancale, en découlaient directement. L'écart inexpliqué entre les deux
  dispositifs (ADR-002 NC-5) était l'écart entre deux temps de réaction
  humains ; il n'y a plus rien à y expliquer.

  Mesuré le 2026-08-21, une fois le grant accordé, six appels par régime, chemin
  de code et code retour nommés : régime disclaimé (`rename-task` sur un titre
  inexistant, résolution en base atteinte, rc=1) 0,104–0,119 s ; régime rapide
  (`find-task` sur cible inexistante, rc=2) 0,098–0,110 s ; régime rapide
  (`areas`, lecture complète, rc=0) 0,098–0,109 s. Le disclaim coûte donc de
  l'ordre de 10 ms, et la partition ne se justifie plus que par son mode
  d'échec fail-closed — sa seconde justification, épargner un coût de latence
  au chemin chaud, **tombe**. Elle est conservée pour la première seule ; toute
  proposition de l'étendre ou de la supprimer doit être argumentée sur ce
  motif-là, plus sur la performance.
- **INV-002-2** — Aucune sous-commande déclarée en régime rapide n'atteint
  `osa()`. Gardé par balayage du graphe d'appel de `bin/thingskit`, dont **toute
  forme non résolue est un échec**, jamais un silence — même discipline que
  `test_no_executable_is_invoked_by_bare_name`. Le balayage sème aussi le régime
  lent sur toute mention de l'exécutable hors de `osa()`, sans quoi un
  `subprocess.run([OSASCRIPT, …])` posé ailleurs lui échapperait. Sa limite,
  énoncée sans réserve : il ne voit pas une sollicitation passant par un module
  tiers — fermée par une liste blanche d'imports, dont toute sortie est un
  échec, ce qui force la relecture de cette garde avant d'en ajouter un.
- **INV-002-3** — La liste du régime rapide n'existe qu'une fois, dans
  `bin/thingskit` (`FAST_PATH_COMMANDS`). Le shim n'en porte **aucune** copie
  écrite à la main ; `build/bundle.py` l'y lit au build et **échoue** si elle
  est absente, vide, non littérale ou porteuse d'autre chose que des noms de
  sous-commande. Le couplage lanceur ↔ script est un couplage de **build**, pas
  de maintenance. Deviner y serait pire que rien : une liste vide basculerait
  tout le CLI en régime lent sans le dire, une liste devinée ferait revenir
  l'invite.
- **INV-002-4** — Aucun chemin d'invocation n'introduit de processus
  intermédiaire. Le shim remplace son image (`POSIX_SPAWN_SETEXEC`), il ne forke
  jamais : `argv`, stdio et code de sortie traversent structurellement. Gardé
  par mesure du PID **de bout en bout** — `sh` → shim → interpréteur —, pas par
  lecture du mot-clé : les deux maillons pourraient être corrects séparément et
  un processus apparaître entre eux.
- **INV-002-5** — Le sceau du bundle est contrôlé **avant** que le script scellé
  ne soit exécuté, sur tous les chemins et dans les **deux** régimes : le régime
  rapide n'est pas un régime dégradé de contrôle. Un contrôle inexécutable,
  **bloqué** ou **tué** vaut refus (126), jamais passage — reconduction de
  `code_identity_refusal` et de la borne de temps de BUG-011, transposées dans
  le shim. Le message de refus a changé de porteur (`sh` → Mach-O) et reste au
  moins aussi nommé : sans quoi le diagnostic d'un bundle altéré régresserait.

  **Portée réelle** : cet invariant protège `Contents/Resources/thingskit` — le
  script scellé — et rien d'autre. Il ne protège pas le shim qui le porte
  (INV-002-6), et il est inopérant pour qui a remplacé le shim.
- **INV-002-6** — **L'exigence de code du dépôt est opposée au shim au moment du
  build, et à ce moment seulement.** `build/bundle.py` la lui oppose directement,
  après signature, sur l'artefact tel qu'il est sur disque. Le shim est signé
  sous l'identifiant du bundle (`codesign -i`), sans quoi l'identifiant qu'un
  Mach-O imbriqué reçoit — dérivé de son nom de fichier — le rendrait impossible
  à confronter à l'exigence unique du dépôt.

  **Le sceau de la racine ne couvre PAS le shim** — mesuré le 2026-08-21, et
  l'énoncé antérieur (« pas seulement par ricochet du sceau de la racine »)
  présupposait à tort qu'un ricochet existait. Un octet altéré dans
  `Contents/MacOS/thingskit-launch`, puis `codesign --verify --strict -R=` opposé
  à la **racine** du bundle : `rc=0`, sur trois offsets distincts (17000, 20000,
  33000). Contre-épreuve, un octet altéré dans `Contents/Resources/thingskit` :
  `rc=1`, « a sealed resource is missing or invalid ». Le shim, Mach-O imbriqué
  dans `Contents/MacOS/` sans être l'exécutable principal, est hors du sceau de
  ressources.

  **À l'exécution, rien dans le dépôt ne vérifie le shim.** Sa protection est
  l'enforcement noyau — hardened runtime, CDHash —, vérifié actif : un shim
  altéré est tué (rc=137). C'est une garde réelle mais de portée différente :
  elle interdit la **modification** d'un binaire signé, jamais le **remplacement**
  par un binaire ad-hoc valide, lequel s'exécuterait sans vérifier aucun sceau.
  Pour qui contrôle déjà le compte utilisateur, INV-002-5 est donc contournable
  en remplaçant son porteur. Énoncé sans réserve, comme les autres résidus de ce
  document : sur une zone sensible, un invariant qui affirme une fermeture
  inexistante est pire que pas d'invariant.

  Le shim ne se vérifie **pas** lui-même, et c'est délibéré (ADR-002 §
  Decision 5bis) : contre la modification, le noyau agit déjà ; contre le
  remplacement, aucune auto-vérification n'est possible — le remplaçant décide
  seul de ce qu'il vérifie. Gardé par
  `test_the_root_seal_does_not_vouch_for_the_shim`, qui oppose l'exigence
  directement au chemin du shim ET échoue si la vérification de la racine seule
  suffisait à la porter : sans cette seconde assertion, un futur changement
  d'agencement du bundle ferait passer cette non-couverture pour une couverture.
- **INV-002-7** — Aucune affirmation de persistance du consentement AppleEvents
  n'est portée sur la seule satisfaction d'un `csreq` : elle exige une écriture
  réellement effectuée sans dialogue, **après** l'événement considéré.
  Transposition d'INV-001-4 au second service.
- **INV-002-8** — **`/Applications/thingskit.app` doit porter son propre « Accès
  complet au disque ». Sans lui, toute commande d'écriture ne rend jamais la
  main.** Le disclaim ne se contente pas de donner à thingskit une identité
  propre : il lui **retire** la responsabilité de processus du terminal
  appelant, et avec elle l'accès disque que ce terminal portait. Trace `tccd`,
  mesurée le 2026-08-21 avant l'octroi :

  ```
  sans disclaim : AllFiles, Sub:{com.mitchellh.ghostty}  -> Allowed (System Set)
  sous disclaim : AllFiles, Sub:{<identifiant du bundle>}  -> Denied (Service Policy)
                  puis AppData, Sub:{<identifiant du bundle>} -> Unknown -> PROMPT
  ```

  Ce que le manque produit n'est pas une lenteur, c'est **deux défauts selon le
  contexte** — et le second est le vrai. Devant un écran, l'invite s'affiche,
  l'utilisateur répond, la commande aboutit : cela ressemble à 1 à 3 s de
  latence, et c'est ce qui a été inscrit ici comme un « coût du disclaim »
  (INV-002-1). Sous un agent, un cron ou un `launchd`, l'invite ne s'affiche
  nulle part et **rien ne peut y répondre** : la commande se bloque
  indéfiniment, sans message, sans code de sortie, sans trace. C'est ce
  blocage — mesuré `rc=137` après 45 s de `kill -9` — qui a fait annuler
  ADR-002 le 2026-08-21 avant que la cause n'en soit établie.

  **Ce grant n'est pas une propriété du code : c'est un état de poste**, au même
  titre que le build et la signature, qui sont eux aussi par machine. Il ne peut
  donc pas se garder depuis cette suite, dont un invariant exige qu'elle passe
  sur un poste où le bundle n'est même pas installé
  (`test_the_suite_does_not_depend_on_an_installed_bundle`). Il est gardé
  **hors de ce dépôt**, dans l'infrastructure personnelle du mainteneur, en
  deux morceaux :

  1. **La sonde** — `thingskit_fda_probe.py`, lancée une fois par jour à 09:20
     par un agent de planification par poste (par poste, comme le build
     et la signature). Elle invoque **une** sous-commande d'écriture
     **disclaimée** — `complete-task --id <uuid qui ne peut exister>` — sous un
     délai dur de 20 s. Si la commande rend la main, le grant est là ; si le
     délai expire, il manque. **L'expiration EST le signal** : la sonde ne
     répond jamais à l'invite, ne la ferme pas, n'appelle jamais `tccutil` et
     ne lit ni n'écrit la base TCC. À l'expiration elle tue le **groupe de
     processus**, jamais le seul pid — un processus resté bloqué sur l'invite
     après l'abandon de la sonde serait une régression. Elle dépose son verdict
     horodaté dans `~/.thingskit-probe/state.json`.

     **Elle ne peut rien modifier dans Things, et ce n'est pas une précaution :
     c'est structurel.** `cmd_complete_task` résout sa cible **avant** toute
     action — forme de l'uuid, puis `select … from TMTask where uuid=?`, qui
     est précisément l'accès fichier que `tccd` garde. L'uuid ne correspondant
     à aucune ligne, la fonction rend `1` en nommant l'introuvable : elle
     n'atteint ni `ensure_running()` ni `osa()`. La mutation est
     **inatteignable**, donc il n'y a pas de réversibilité à assurer — rien
     n'est écrit, rien n'est à défaire, et Things n'est même pas lancé.
     Mesuré le 2026-08-22 : base `main.sqlite` **rigoureusement identique
     octet pour octet** avant/après une exécution réelle.

     Le verdict se lit dans la **sortie**, jamais dans le seul code retour :
     `rc=1` est aussi ce que rend un uuid malformé (rejeté par une regex avant
     tout accès fichier), `rc=2` ce que rend argparse, `rc=126` ce que rend le
     refus de sceau du shim — tous rapides, tous ne mesurant rien. Seul le
     marqueur imprimé **après** la lecture protégée prouve que la sonde a
     atteint la garde. Tout le reste vaut `inconclusive`, jamais `granted`.

     Le choix de la sous-commande est le point dur du dispositif : le shim ne
     disclaime **que** ce qui est absent de `FAST_PATH_COMMANDS`. Une sonde
     bâtie sur une sous-commande du chemin rapide hériterait de la
     responsabilité du terminal appelant, donc de son accès disque, et rendrait
     `granted` pour toujours — une garde incapable d'échouer sur le défaut
     qu'elle garde. Gardé par deux tests, l'un contre la source de ce dépôt,
     l'autre contre la **table réellement embarquée dans le shim déployé**, les
     deux pouvant diverger.

  2. **Le lecteur** — `thingskit_bundle_guard.py`, exécuté à chaque démarrage
     de session, ne fait que **lire** ce fichier de verdict. **Jamais un vert
     par défaut : un verdict positif, frais et attribué à CETTE machine est la
     seule issue silencieuse.** Toute autre situation rend un constat nommé :

     | Code | Situation |
     |---|---|
     | `FULL-DISK-ACCESS-MISSING` | verdict négatif — le grant manque |
     | `FULL-DISK-ACCESS-UNVERIFIABLE` | verdict `inconclusive`, ou mot de verdict inconnu du lecteur |
     | `FULL-DISK-ACCESS-UNPROBED` | aucun fichier de verdict |
     | `FULL-DISK-ACCESS-PROBE-STALE` | verdict positif de plus de 4 jours — la sonde a cessé de tourner |
     | `FULL-DISK-ACCESS-PROBE-FUTURE` | verdict horodaté dans le futur — l'horloge a sauté, l'âge du verdict n'est plus établissable |
     | `FULL-DISK-ACCESS-PROBE-FOREIGN` | verdict enregistré sur une **autre machine** |
     | `FULL-DISK-ACCESS-PROBE-UNIDENTIFIED` | verdict sans identité de machine, ou identité locale illisible |
     | `FULL-DISK-ACCESS-PROBE-UNREADABLE` | fichier illisible ou malformé |
     | `FULL-DISK-ACCESS-PROBE-UNAVAILABLE` | module de la sonde introuvable — son vocabulaire de verdict en vient, donc plus rien n'est interprétable |
     | `CHECK-CRASHED` | un contrôle a levé une exception inattendue — ce qu'il devait établir est **non vérifié**, et les constats des autres contrôles sortent quand même |

     Cette énumération est **exhaustive et doit le rester** — pour le **lecteur
     du verdict d'accès disque**, qui est son objet. Elle a été lue comme
     exhaustive alors qu'elle ignorait trois codes. Les autres contrôles du même
     garde (identité de code, sceau, autonomie vis-à-vis d'`/opt/homebrew`,
     lanceur, fraîcheur source↔bundle) ont leurs propres codes, hors de ce
     tableau : `CODESIGN-FAILED` et `HOMEBREW-SCAN-FAILED` s'y sont ajoutés le
     2026-08-23, quand ces deux contrôles ont cessé de lire une sortie sans
     regarder le code retour de l'outil qui l'a produite.

     **L'identité de machine se lit dans le matériel, jamais dans le réseau.**
     Le rattachement se fait sur `IOPlatformUUID` (`ioreg`), et non sur le nom
     d'hôte : sur un Mac dont `scutil --get HostName` vaut « not set » — le
     défaut, et le cas de ce poste —, `uname -n` est dérivé de ce que le réseau
     distribue. Mesuré le 2026-08-22 sur deux fichiers d'état écrits par ce
     dispositif à deux heures d'écart, sans redémarrage — noms d'hôte réels
     retirés ici (infrastructure privée du mainteneur), la propriété mesurée
     reste intacte : le nom d'hôte annoncé par la box réseau a changé entre
     les deux écritures, la plus ancienne portant la troncature NetBIOS à 15
     caractères de la forme complète écrite par la plus récente. Une garde comparant
     cette chaîne vire au rouge à l'état nominal dès que la box change d'avis —
     et une ligne rouge non actionnable à chaque démarrage de session est
     exactement ce qui a fait retirer le contrôle de la base TCC. Le nom
     d'hôte reste écrit dans le fichier d'état, à titre **informatif** : il ne
     décide de rien.

  **Ce que ce garde faisait avant le 2026-08-22, et pourquoi il ne le fait
  plus.** Il lisait en seule lecture la ligne `kTCCServiceSystemPolicyAllFiles`
  / l'identifiant du bundle de la base TCC système. Or **lire cette base exige
  elle-même l'Accès complet au disque pour le processus lecteur** — c'est
  exactement la propriété testée. Depuis le terminal qui lance ce garde au
  démarrage de session, ce contrôle n'a donc **jamais** pu aboutir : pas un
  échec intermittent, une infaisabilité par construction. Étant fail-closed, il
  rendait la même ligne « non établi » à chaque session, indéfiniment, sans
  aucune action possible pour le lecteur — le plus court chemin pour qu'on
  cesse de lire le garde entier. Il a été retiré ce jour-là, laissant INV-002-8
  sans aucune garde jusqu'à la mise en place de la sonde ci-dessus.

  **Pourquoi la sonde fonctionnelle avait été écartée, et pourquoi cet argument
  tombe.** Ce document a soutenu jusqu'au 2026-08-22 que la sonde, « pourtant
  plus fidèle », coûtait trop cher : sur un poste où le grant manque, elle
  **déclencherait l'invite qu'elle diagnostique** à chaque démarrage de
  session, et devrait la tuer à l'expiration d'un délai — là où « la lecture de
  la base TCC constate le même prérequis sans le solliciter ». L'objection
  n'était pas fausse ; sa **branche alternative** l'était. Elle opposait la
  sonde à une lecture qui n'a jamais fonctionné : l'arbitrage réel n'était pas
  « sonde ou lecture », mais **« sonde ou rien »**. Et le coût invoqué visait
  une sonde **au démarrage de session**, ce que celle-ci n'est pas : elle
  tourne une fois par jour, hors de ce chemin, et le garde de session ne paie
  que la lecture d'un petit fichier (mesuré le 2026-08-22 : ~1,05 s avant comme
  après le raccordement). Le grant, lui, ne va ni ne vient dans la journée — il
  manque parce qu'il n'a jamais été accordé sur ce poste, ou parce qu'une
  reconstruction ou une mise à jour du système l'a révoqué. Une cadence
  quotidienne suffit à couvrir ces deux événements. L'argument est conservé
  ici, et non effacé : il dit pourquoi le choix inverse avait été fait, et
  redeviendrait valable si l'on remettait la sonde sur le chemin chaud.

**Ce que le disclaim change, et qui a été affirmé à tort.** Ce document a énoncé
jusqu'au 2026-08-21 que le disclaim n'aggravait pas le résidu ci-dessous, au
motif qu'« il est posé par le shim, et rien d'autre ne le pose ». Ce n'est pas
une propriété du système, c'est une observation sur le dépôt :
`responsibility_spawnattrs_setdisclaim` est appelable par tout processus de
l'utilisateur — c'est exactement ce que fait le shim, en une dizaine de lignes de
C. Un processus local quelconque peut donc `posix_spawn`
`Contents/MacOS/thingskit` AVEC le disclaim, sans toucher au bundle et sans
passer par le shim.

Mesuré le 2026-08-21 : `codesign --verify --strict -R=<exigence du dépôt>`
opposé à `Contents/MacOS/thingskit` rend `rc=0` — ce binaire satisfait SEUL
l'exigence ; et la ligne `kTCCServiceAppleEvents | <identifiant du bundle> |
auth_value=2` existe désormais en base, alors qu'elle n'existait pas avant ce
chantier.

Le disclaim ne CRÉE donc pas la faiblesse, mais il en **augmente le rendement**.
Avant, le résidu donnait la lecture de la base Things (`AppData`). Après, il
donne en plus un consentement AppleEvents stable et durable, sous une identité
qui n'est plus liée au terminal appelant — donc la capacité d'automatiser Things,
et de solliciter toute application à laquelle l'identifiant du bundle se verra
accorder AppleEvents plus tard. C'est la contrepartie exacte du bénéfice
principal : un sujet TCC unique et stable l'est pour tout le monde.

`[mesuré 2026-08-21]`, de bout en bout : un spawner tiers posant le disclaim sur
`Contents/MacOS/thingskit` obtient l'Apple Event vers Things SANS invite, sous
l'identité du bundle — `AUTHREQ_SUBJECT: subject=<identifiant du bundle>`,
`authValue: 2`, `osascript rc=0`, aucun prompt dans 8 240 lignes de trace ;
contrôle sans disclaim : `subject=com.mitchellh.ghostty`. Ce binaire satisfait
en outre le `csreq` de TCC lui-même (`codesign --verify --strict -R
csreq_ae.bin` -> rc=0), c'est-à-dire ce que tccd évalue. L'énoncé `[raisonné]`
qui figurait ici est donc confirmé par la mesure, et non plus supposé.

Le résidu ci-dessous n'est donc pas refermé, et sa fermeture — faire cesser
`Contents/MacOS/thingskit` d'honorer `argv` — voit sa valeur AUGMENTER du fait
d'ADR-002.

**Ce qui n'est pas établi — le grant reste atteignable hors du lanceur.**
`Contents/MacOS/thingskit` est un interpréteur Python généraliste. N'importe
quel processus tournant sous l'utilisateur peut l'invoquer directement, sans
`-I` et sans contrôle de sceau, et hériter du grant. Reproduit le 2026-08-18 :

```
/Applications/thingskit.app/Contents/MacOS/thingskit -c "<lecture de la base Things>" "$DB"
→ LECTURE ARBITRAIRE: (892,)   rc=0
```

Ce résidu n'est pas une régression de l'empaquetage : il est inhérent au fait
d'embarquer un interpréteur stock, et le chemin antérieur (script + Python
Homebrew) ne valait pas mieux. C'est pourquoi l'invariant ci-dessus est énoncé
**sur le chemin du lanceur** et pas davantage : sur une zone sensible, un
invariant qui affirme une fermeture inexistante est pire que pas d'invariant,
puisque le lecteur suivant s'y fiera. Fermer réellement ce trou supposerait que
`Contents/MacOS/thingskit` soit un Mach-O qui ignore `argv` et charge le script
scellé en dur — chantier d'architecture distinct, non entrepris ici.

**Ce qui n'est pas établi non plus** : que le consentement survive effectivement à une
montée de version de Python. La vérification exige l'événement déclencheur
(`brew upgrade python`), qui n'a pas eu lieu au moment de la mise en place.
Mesuré en revanche le 2026-08-18, et à ne pas confondre avec une preuve :
l'**ancien** chemin (script + Python Homebrew) lisait la base au même instant
sans dialogue — la session portait donc déjà un accès effectif, ce qui rend le
succès de la première lecture par le bundle **non probant** sur ce point. Le
build et la signature sont **par poste** ; le grant TCC est local à la machine.

## Limite de couverture assumée

Le pilotage réel de l'interface (clic de menu System Events, frappe
clavier) n'est **pas couvert par la suite automatisée**, et ne peut pas
l'être sans faire tourner une vraie session graphique avec Things ouvert et
la permission d'accessibilité accordée — hors de portée d'un `pytest`
exécuté en CI ou en headless. Ce que la suite couvre à la place :

- La construction du script AppleScript envoyé (`_build_heading_script`) :
  présence des libellés, échappement correct du titre.
- L'interprétation du résultat retourné par `osa()` (`_interpret_ui_outcome`) :
  succès, libellé introuvable, permission refusée, échec non identifié.
- Toute la logique de décision autour de l'automatisation (résolution du
  projet, idempotence, vérification post-action) — via `osa` mocké.

**Deux points de plus, depuis ADR-002**, du même ordre — non automatisables,
assumés, et à refaire à la main après tout événement qui touche la signature ou
l'interpréteur :

1. **La persistance du consentement AppleEvents** (INV-002-7). La satisfaction
   du `csreq` ne vaut pas ce constat : il faut relever la ligne
   `kTCCServiceAppleEvents` avant et après le build, puis exécuter une écriture
   **réelle** et constater qu'elle rend la main **sans dialogue**. La suite
   automatisée mesure le disclaim (le processus est bien son propre
   responsable) ; elle ne mesure pas ce que `tccd` en fait.
2. **Le chronométrage des deux régimes.** Il ne coûte plus rien de notable :
   ~10 ms d'écart, mesuré le 2026-08-21 après l'octroi du grant d'INV-002-8
   (six appels par régime, chemin de code et code retour nommés — voir
   INV-002-1). Ce point reste dans cette liste parce que le chiffre qui y
   figurait auparavant — « de l'ordre de la seconde, en permanence, y compris
   sur les sous-commandes sans Apple Event » — était une mesure exacte d'autre
   chose que ce qu'elle prétendait mesurer : l'attente d'une réponse humaine à
   une invite TCC. Elle avait été prise par deux dispositifs indépendants qui
   concordaient sur la direction et la permanence, ce qui l'a rendue crédible
   ; leur divergence sur la valeur (ADR-002 NC-5) était le seul signal qu'il y
   avait autre chose, et il a été inscrit comme « non expliqué » plutôt que
   suivi. **La concordance de deux dispositifs ne dit rien de ce qu'ils
   mesurent l'un et l'autre.** Toute mesure de latence inscrite ici nomme le
   chemin de code atteint, son code retour, et l'état des grants TCC du poste
   au moment de la prise.

Ce que la suite **ne prouve pas** : que le clic atterrit sur le bon item de
menu dans une vraie fenêtre Things, ou que la frappe clavier suit le focus
attendu. Elle prouve en revanche qu'aucun titre de la classe non
saisissable n'atteint cette frappe — le refus est constaté sans
l'application, puisqu'il précède toute sollicitation de celle-ci. Cette limite est assumée, pas masquée — toute évolution de
`create-heading` doit être validée manuellement au moins une fois sur ce
poste avant d'être considérée fiable, et tout heading de test créé
manuellement doit être nettoyé après coup.
