---
name: Rapport de bug
about: Signaler un comportement de thingskit qui ne correspond pas à ce qui est documenté ou attendu
title: "[BUG] "
labels: bug
assignees: ''
---

## Description

Décrivez le comportement observé et ce qui était attendu à la place.

## Sous-commande concernée

Ex. `thingskit complete-task`, `thingskit agenda --horizon 7`…

## Étapes de reproduction

1.
2.
3.

## Comportement observé

## Comportement attendu

## Environnement

- Version macOS :
- Version de Things 3 :
- `thingskit` lancé via le bundle installé, ou depuis les sources (`bin/thingskit` directement, en dev) ?
- Sortie de `codesign -dv --verbose=4 /Applications/thingskit.app` si le problème touche au lancement ou à la signature :

## Contexte additionnel

Sortie complète de la commande (stdout/stderr), code de sortie, extrait de
log pertinent. Ne collez pas le contenu de votre base Things si elle contient
des informations personnelles — un extrait minimal reproduisant le problème
suffit.
