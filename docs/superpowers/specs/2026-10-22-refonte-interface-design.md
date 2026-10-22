# Refonte de l'interface web — conception

22 octobre 2026 · décidé en séance avec Max (« repenser l'affichage global de l'interface web » ; direction B retenue sur maquettes ; page de détail par porteuse ; onglet Historique remplacé ; réalisation en JavaScript simple)

Prolonge `2026-10-22-etat-ird-design.md`. Maquettes de référence : `.superpowers/brainstorm/13647-1792688346/content/direction-b-pages.html` (non versionnées ; leur contenu est décrit ci-dessous).

## 1. Intention

La page s'est construite par couches depuis le 17/10 (graphes, puis cartes d'IRD, tuiles, seuils, fenêtres de réglage) : elle juxtapose plusieurs styles, répète des informations (la carte d'un IRD dans sa porteuse redit sa tuile) et s'allonge vite. Max veut **plus de lisibilité, de simplicité, d'ergonomie, de professionnalisme et d'homogénéité dans le design**.

Direction retenue (« B ») : interface **claire et sobre, menu à gauche**, chiffres clés en haut, **cartes identiques**, couleur réservée aux états ; les réglages deviennent des pages ; un clic sur un IRD ouvre la **page de sa porteuse**, où l'on compare ses IRD et où l'on remonte dans le temps.

Réussite :

- Max juge l'interface lisible, sobre et homogène, sur un écran d'ordinateur portable comme sur un grand écran ;
- toutes les fonctions actuelles restent accessibles (rien de perdu) ;
- l'état du parc se lit d'un coup d'œil sur la vue d'ensemble ; le diagnostic (« un IRD seul ou toute la porteuse ? ») se fait sur la page de la porteuse.

## 2. Périmètre

**Objectifs** : nouvelle structure de navigation, nouvelles pages, système visuel unique appliqué partout.

**Non-objectifs** : nouvelles mesures ou alertes, changement des seuils ou de la détection, mode sombre, adaptation au téléphone, ouverture au réseau (consultation depuis un autre PC : chantier séparé, voir HISTORIQUE).

**À éviter absolument** (même si « ça marche ») :

- perdre une information ou une fonction présente aujourd'hui ;
- une alerte moins visible qu'aujourd'hui ;
- deux façons de présenter la même chose (deux styles de carte, deux habillages de graphe…) ;
- une page plus lente ou qui saccade en direct.

## 3. Contraintes

- **Lecture seule envers les appareils, inchangée** ; le service Python, l'API et les données ne changent pas, sauf ajouts strictement nécessaires (à justifier dans le plan).
- **JavaScript simple, sans framework ni compilation**, uPlot (déjà dans `web/vendor`) pour les graphes, une feuille de style unique ; la page doit continuer de s'empaqueter dans `MonitoringSat.exe` sans étape supplémentaire.
- Ordinateur uniquement : largeur utile de 1 280 px (portable) à grand écran ; pas de version téléphone.
- Tout le texte en français.
- Les tests Python existants restent verts ; vérification de la page sur la démonstration (`python3 -m tests.demo_server`) et sur le service réel.

## 4. Approches envisagées

1. **Réécrire la page en JavaScript simple, comme aujourd'hui** — navigation par l'adresse (`#/porteuse/…`), feuille de style à variables (couleurs, tailles, espacements), fonctions de construction partagées. Pas d'outil en plus, exécutable inchangé, réutilisation du code existant (tuiles, graphes, seuils). L'homogénéité repose sur la feuille de style et les fonctions partagées.
2. JavaScript simple avec une petite bibliothèque de composants (Preact ou Lit, recopiée dans le dépôt) — composants réutilisables, mais une dépendance et une façon d'écrire à apprendre pour une page de taille modeste.
3. Framework complet avec compilation (React ou Svelte) — outillage riche, mais Node.js et une compilation dans le dépôt et dans la fabrication Windows ; disproportionné, contraire au « rien à installer ».

**Choisie : 1** (Max), pour rester cohérent avec le reste de l'outil.

## 5. Structure et navigation

L'adresse de la page porte la navigation : le bouton Précédent du navigateur et les favoris fonctionnent.

| Page | Adresse |
| --- | --- |
| Vue d'ensemble (page d'accueil) | `#/` |
| Page d'une porteuse | `#/porteuse/<clé>` (+ IRD ciblé et période, voir §7) |
| Analyseur FSH4 | `#/fsh4` (+ période) |
| Journal des événements | `#/journal` |
| Réglages : appareils, porteuses, seuils d'alerte | `#/reglages/appareils`, `#/reglages/porteuses`, `#/reglages/seuils` |

**Menu à gauche**, présent sur toutes les pages, de haut en bas :

- nom de l'outil et indicateur de connexion au service (« en direct », ou « connexion perdue » quand le flux en direct est coupé) ;
- **Vue d'ensemble**, avec le nombre d'alertes en cours ;
- groupe **Porteuses** : une entrée par porteuse actuelle (nom), avec son nombre d'alertes en cours ;
- **Journal des événements** ;
- **Analyseur FSH4** ;
- groupe **Réglages** : Appareils, Porteuses, Seuils d'alerte.

L'entrée de la page affichée est mise en évidence. Disparaissent : le bandeau de pastilles en haut, les onglets Direct / Historique, les cases « Graphes détaillés », les fenêtres Appareils… / Porteuses… / Seuils…

## 6. Vue d'ensemble (`#/`)

- **Titre** « Vue d'ensemble » et heure de la dernière mise à jour.
- **Chiffres clés** (une rangée de quatre) : IRD en service (joignables / suivis), alertes en cours, marge la plus basse (valeur et IRD), état de l'analyseur FSH4 (connecté, en pause, hors ligne), avec le bouton Pause / Reprendre du FSH4 sur ce chiffre ; un clic sur ce chiffre ouvre la page Analyseur FSH4.
- **Une carte par IRD suivi**, en grille, rangées par porteuse puis par nom ; la grille garde son adaptation au nombre d'IRD (grandes cartes s'il y en a peu, compactes s'il y en a beaucoup, défilement au-delà). Contenu de la carte (le même partout, voir §10) : nom, état en clair et en couleur (OK, LOCK PERDU, PAS D'IMAGE, NON DÉCHIFFRÉ, FLUX ARRÊTÉ, SON ABSENT, ERREURS, MARGE BASSE, INJOIGNABLE), cause et durée si l'état n'est pas OK, nombre d'alertes en cours, programme et porteuse, marge avec ses seuils, signal reçu, image, son, température, courbe de marge de la fenêtre du direct avec les deux seuils, bande État dessous.
- **Clic sur une carte** : page de la porteuse de l'IRD, défilée jusqu'à la section de cet IRD. IRD qui n'appartient à aucune porteuse (réglage jamais lu, injoignable depuis le démarrage) : Réglages › Appareils, où l'on voit son état et son adresse *(choix du concepteur)*.
- **Carte « Événements en cours et récents »** à droite : les événements en cours puis les plus récents de la fenêtre du direct ; lien « Tout le journal ».
- **Plancher de bruit** *(choix du concepteur)* : quand des marqueurs FSH4 sont placés hors de toute porteuse, une carte « Plancher de bruit (FSH4) » sous les cartes d'IRD montre leur courbe sur la fenêtre du direct (le graphe « Marqueurs hors porteuse » actuel), avec un lien vers la page Analyseur FSH4 pour remonter dans le temps. Rien quand il n'y en a pas.
- Bandeaux d'avertissement existants conservés en haut de page quand ils s'appliquent (décalage LNB non réglé sur le FSH4).

## 7. Page d'une porteuse (`#/porteuse/<clé>`)

- **En-tête** : chemin « Vue d'ensemble › Porteuses », nom de la porteuse, fréquence, polarisation, débit symbole, modulation, nombre d'IRD ; avertissement « polarisation inconnue » quand il s'applique.
- **Sélecteur de période** : Direct (fenêtre du direct, 10 min par défaut, mise à jour en continu), 1 h, 24 h, 7 j, Période… (dates libres). Hors Direct, la page affiche l'historique de la période (points agrégés si nécessaire, comme aujourd'hui). Glisser sur un graphe resserre la période. La période fait partie de l'adresse (lien partageable, Précédent fonctionnel).
- **Courbes communes à tous les IRD de la porteuse**, sur le même axe de temps, curseur synchronisé :
  - marge (échelle partant de 0 dB) avec la marge minimale (rouge) et, en Direct, le seuil de chute rapide (orange pointillé) ;
  - puissance d'entrée des IRD ;
  - niveau RF du FSH4 (marqueurs de la porteuse) ; à défaut, la raison en clair (FSH4 hors ligne, porteuse hors du span…) ;
  - bande État, une rangée par IRD.
- **Une section par IRD** :
  - en **Direct** : sa carte (même composant que la vue d'ensemble) et ses détails (image, voies audio, configuration des sorties), comme le panneau Détails actuel ;
  - **hors Direct** : la carte ne peut pas rester « en direct » au-dessus d'une courbe du passé, et le service ne garde pas d'historique du décodage, du son, de la température ni des détails. La section montre un **résumé de la période** calculé à partir des séries existantes : nom, marge minimale, temps passé dans chaque état de la bande (lock perdu, image/flux, BER ou son), nombre d'alertes de l'IRD sur la période *(choix du concepteur ; aucune donnée nouvelle côté service)*.
- **Quelles porteuse et quels IRD sur une période passée** *(choix du concepteur)* : la porteuse est retrouvée parmi celles de la période (comme l'Historique actuel, qui les recalcule pour chaque plage) par sa fréquence et sa polarisation, à la tolérance de regroupement près, et non par sa clé exacte ; la page montre les IRD qui étaient sur elle pendant la période, chacun pour ses périodes d'appartenance.
- **Porteuse qui n'existe plus aujourd'hui** (ancien site, IRD retiré ou re-réglé) : la page s'ouvre quand même pour une période passée (depuis le journal, ou une adresse mémorisée) et affiche la porteuse telle qu'elle était, avec la mention « n'existe plus aujourd'hui ». Elle n'apparaît pas dans le menu (qui ne liste que les porteuses actuelles).
- **Événements de la porteuse** (ses IRD et ses marqueurs) sur la période, à droite ; un clic sur un événement cale la période sur lui.
- Porteuse introuvable sur la période demandée (mauvaise adresse, ou aucune mesure) : message clair et lien vers la vue d'ensemble.

## 8. Journal des événements (`#/journal`)

- Tous les événements du parc, les plus récents en haut : gravité (couleur), type en clair, source (IRD, porteuse, FSH4), début, fin ou « en cours », détail.
- **Filtres** : période (24 h, 7 j, 30 j, Période…), porteuse ou IRD, « en cours seulement ».
- **Clic sur un événement** d'un IRD ou d'une porteuse : page de la porteuse où se trouvait l'IRD (ou de la porteuse concernée) **au moment de l'événement**, calée sur l'événement (avec la marge habituelle avant et après), même si elle n'existe plus aujourd'hui. Événement du FSH4 (injoignable, réglage modifié, remontée du bruit, appareils changés) : page Analyseur FSH4 calée sur l'événement. IRD qui n'appartenait à aucune porteuse à ce moment-là : pas de navigation, le détail suffit *(choix du concepteur)*.

## 8 bis. Analyseur FSH4 (`#/fsh4`)

Demandée par Max pour ne rien perdre de l'Historique actuel :

- **En-tête** : état du FSH4 (connecté, en pause, hors ligne, avec la raison), adresse, polarisation de l'entrée, span et RBW quand ils sont connus, bouton Pause / Reprendre.
- **Même sélecteur de période** que les porteuses (Direct, 1 h, 24 h, 7 j, Période…).
- **Plancher de bruit** : courbe des marqueurs hors porteuse sur la période (le graphe « Marqueurs hors porteuse » actuel) ; s'il n'y en a pas, la raison en clair.
- **Tous les marqueurs** allumés sur la période, chacun avec la porteuse à laquelle il est rattaché, pour retrouver le niveau RF d'une porteuse qui n'existe plus.
- **Événements du FSH4** sur la période (injoignable, réglage modifié, remontée du bruit, chutes de niveau des marqueurs, appareils changés) ; un clic cale la période.

## 9. Réglages

Mêmes contenus et mêmes actions que les fenêtres actuelles, présentés en pages :

- **Appareils** : FSH4 (adresse, port, polarisation de l'entrée, Pause / Reprendre) ; tableau des IRD (nom, adresse, polarisation, porteuse, état, Retirer) ; ajout par adresse ; recherche sur le réseau et ses résultats.
- **Porteuses** : liste (renommage ; suppression des porteuses saisies à la main) ; ajout d'une porteuse.
- **Seuils d'alerte** : chute rapide et marge minimale, avec leur explication.

Messages d'erreur affichés dans la page, au plus près du champ ou de l'action concernés.

## 10. Système visuel

Un seul jeu de règles, appliqué partout (variables de la feuille de style) :

- **Fond** gris clair ; **cartes** blanches à bord fin et coins légèrement arrondis ; ombre légère ou nulle.
- **Une police** (celle du système), une **échelle de tailles** limitée (titre de page, titre de carte, texte, petit texte, grands chiffres) et un **espacement régulier**.
- **Couleurs** : bleu réservé aux actions, aux liens et à la navigation ; vert, orange, violet, rouge réservés aux états, toujours avec le même sens que la bande État (normal ; BER ou son ou marge basse ; image, déchiffrement ou flux ; lock perdu ou injoignable) ; gris pour « pas de mesure » ou « en attente ».
- **Composants uniques** : une seule carte d'IRD, un seul habillage de graphe (titre, unité, légende sous la courbe), un seul style de liste d'événements, de tableau, de bouton, de champ.
- Pas de mode sombre.

## 11. Laissé au constructeur

- Découpage des fichiers JavaScript et CSS, nom des fonctions partagées, rendu exact (tailles, espacements, ombres) dans le respect du §10.
- Comportement de détail du sélecteur de période et du filtre du journal (format des dates, valeurs par défaut).
- Ce que devient l'adresse d'une porteuse dont la clé a changé.
