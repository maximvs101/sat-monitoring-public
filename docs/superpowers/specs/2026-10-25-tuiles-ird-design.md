# Tuiles d'IRD épurées et adaptatives — design

Date : 25/10/2026. Demande de Max : « repenser l'affichage des données dans les tuiles d'IRD, c'est brouillon. Il faut
que ce soit propre, lisible, professionnel et que ça s'adapte correctement selon le nombre d'IRD ou la résolution de
l'écran (ordinateur uniquement). »

## Intention

La vue d'ensemble sert à surveiller d'un coup d'œil un mur d'IRD. Aujourd'hui chaque tuile empile une douzaine
d'éléments de styles différents (badge, cause, programme · porteuse, trois valeurs chacune avec une indication grise,
courbe avec étiquettes de seuils, bande d'état, image avec son heure), et toute la tuile est agrandie ou réduite d'un
même facteur (`--s`, de 0,75 à 2,2) avec une bascule brutale en « compact » : tuiles géantes à 2 IRD, minuscules à
8. Le résultat fait brouillon.

## Ce que Max a décidé

- **Contenu d'une tuile** : l'**état**, la **marge** (le chiffre **et** la courbe des 10 dernières minutes) et
  l'**image** décodée. Tout le reste au clic.
- **Disposition « chiffre d'abord »** (maquette C du 25/10) : en-tête nom + état ; en dessous, la marge en très grand à
  gauche et l'image en vignette à droite ; la courbe de marge sur toute la largeur en bas.
- **Adaptation** : jusqu'à **9 IRD sur un écran sans défilement** ; au-delà la page défile. Ordinateur uniquement.
- **Approche** : la grille choisit colonnes et rangées pour remplir l'écran ; le contenu de chaque tuile s'ajuste à la
  taille de **la tuile** (plus de facteur d'échelle global ni de mode compact), entre une taille minimale lisible et une
  taille maximale ; la disposition reste la même à toutes les tailles.

## Approches envisagées

1. **Grille + contenu ajusté à la taille de la tuile** — retenue. Pas de tuiles géantes à 2 IRD (la hauteur et le
   contenu plafonnent, la largeur remplit l'écran et la courbe prend la place), 9 IRD lisibles en 3 × 3, une seule
   disposition.
2. Garder le facteur d'échelle global et le mode compact avec le nouveau contenu — écartée : c'est la cause des tuiles
   géantes à 2 IRD et du passage brutal en compact.
3. Tuiles de taille fixe (~320 × 220) en mosaïque — écartée : écran à moitié vide à 2 IRD, et 9 tuiles ne tiennent pas
   sans défiler sur un 1280 × 720.

## La tuile (mes propositions, validées en lecture par Max sauf objection)

- **Bord supérieur coloré** selon l'état (vert, orange, violet, rouge, gris) — les couleurs actuelles (`TONE_OF_CODE`).
- **En-tête** : nom de l'IRD (tronqué avec « … » s'il est trop long) et badge d'état (OK, LOCK PERDU, PAS D'IMAGE,
  NON DÉCHIFFRÉ, FLUX ARRÊTÉ, SON ABSENT, ERREURS, MARGE BASSE, INJOIGNABLE, SANS NOUVELLES). Pastille de
  connexion supprimée de la tuile (le badge INJOIGNABLE / SANS NOUVELLES la remplace) ; son infobulle (« dernière
  mesure … ») passe sur le badge.
- **Cause** (seulement si l'état n'est pas OK) : une ligne, « Plus de signal démodulé — depuis 2 min 43 s ». Le nombre
  d'alertes en cours n'est plus sur la tuile (menu et journal le montrent).
- **Marge** : petit libellé « MARGE », chiffre très grand, unité « dB » plus petite. Rouge si la marge est sous le
  minimum réglé ou à 0 (décrochage), sinon couleur du texte. « — » si pas de mesure fraîche.
- **Image** : vignette 16:9 à droite du chiffre. Sans heure quand elle est à jour ; ancienne (> 10 s) : grisée avec
  « image de 10:41 » en petit sur l'image ; jamais reçue : cadre vide « pas d'image ». Clic sur l'image : agrandie
  sur place (comportement actuel) ; clic ailleurs sur la tuile : page de la porteuse.
- **Courbe** : marge sur 10 min, échelle partant de 0 dB, lignes de seuil (chute en orange pointillé, minimum en rouge)
  **sans texte** ; sous la courbe, la bande d'état réduite à un trait fin (couleur par seconde, comme aujourd'hui).
  Graduation minimale : « 0 » et la valeur haute, discrètes, ou aucune si la place manque (au choix du constructeur).
- **Retirés de la tuile** : programme · porteuse, signal reçu, température, indications grises, titre « Marge sur
  10 min », axe « il y a 10 min / maintenant », étiquettes des seuils, heure d'une image à jour.

## Adaptation (mes propositions)

- **Écrans visés** : 1280 × 720 à 2560 × 1440 (fenêtre de navigateur ordinaire, menu à gauche et colonne
  d'événements à droite conservés). « 1280 × 720 » désigne l'écran : la zone utile des tuiles y fait environ
  736 × 470 px.
- **Grille** : nombre de colonnes choisi pour que les tuiles remplissent la zone sans défilement jusqu'à 9 IRD
  (1 → 1, 2 → 2 côte à côte, 3 → 3 ou 2 + 1 selon la forme de l'écran, 4 → 2 × 2, 5–6 → 3 × 2, 7–9 → 3 × 3 ; le
  constructeur peut garder l'algorithme de remplissage actuel s'il donne ces résultats).
- **Au-delà de 9** (ma décision) : tuiles à la taille qu'elles auraient pour 9 IRD sur cet écran, et autant de colonnes
  que la largeur le permet (un écran large peut en afficher plus sans défiler) ; la page défile ensuite.
- **Taille de tuile** (ma décision, pour concilier « pas de tuiles géantes » et « pas d'écran à moitié vide ») : la
  **largeur** remplit la colonne de grille ; la **hauteur** est plafonnée à ~420 px (au-delà, les tuiles gardent cette
  hauteur, alignées en haut) ; le **contenu** (polices, image) plafonne aussi, et c'est la courbe qui absorbe la
  largeur et la hauteur restantes.
- **Priorité sur petit écran** (ma décision) : « 9 IRD sans défilement » l'emporte sur la taille minimale. Sur un
  1280 × 720, une tuile 3 × 3 fait environ 235 × 145 px ; le contenu doit y rester lisible (chiffre de marge
  ≥ 22 px, image ≥ 80 px de large, courbe ≥ 24 px de haut). Le menu et la colonne d'événements ne changent pas.
- **Contenu** : tailles de police, image et courbe calculées d'après la taille de la tuile (unités de conteneur CSS,
  `cqw`/`cqh` sur la tuile, qui a une hauteur définie par la grille, avec `clamp()`), pas d'un facteur global. Le
  chiffre de marge reste la plus grosse écriture de la tuile à toute taille ; la courbe prend toute la hauteur
  restante.
- **Suppression** du mode compact et du facteur `--s` pour la vue d'ensemble.

## Page de la porteuse

La carte d'IRD est partagée avec la page de la porteuse. Proposition : la page de la porteuse utilise la même tuile
(même disposition), et les informations retirées de la tuile (programme, signal reçu, température) passent en tête de
son bloc de **détails** (qui liste déjà image, son, sorties).

- **Hauteur sur la page de la porteuse** (ma décision) : rapport largeur/hauteur fixe d'environ 16 : 10, borné entre
  ~200 et ~360 px de haut, pour que les unités de conteneur aient une hauteur définie.
- **Carte étroite** (ma décision) : sous ~300 px de large (trois IRD sur une petite fenêtre), l'image passe sous la
  courbe, comme aujourd'hui — seule exception à « une seule disposition », limitée à la page de la porteuse.
- **Période passée** (ma décision) : la tuile garde l'en-tête (nom + badge « SANS DÉFAUT » / « DÉFAUTS SUR LA
  PÉRIODE » / « PAS DE MESURE ») ; le grand chiffre devient la **marge minimale** (libellé « MARGE MIN. ») ; à la
  place de l'image, la liste des trois durées (lock perdu, image ou flux perdu, dégradé) colorées comme aujourd'hui ;
  pas de courbe (les graphes de la page la montrent) ; une ligne discrète « programme · porteuse » sous l'en-tête,
  puisque le bloc de détails est masqué hors Direct.

## Contraintes

- Vanilla JS, portée globale unique (pas deux `const`/`function` de même nom entre fichiers), uPlot inchangé.
- Navigateurs : Chrome, Edge, Firefox récents (unités de conteneur et `clamp()` disponibles).
- Tests : `python3 -m unittest discover tests` et `~/.local/node/bin/node --test "web/tests/*.test.js"` verts ; toute
  logique pure ajoutée (choix des colonnes, état d'affichage) testée sous Node.
- Rien ne change côté service ni côté appareils (lecture seule, inchangée).
- Vérification visuelle sur la démo et sur le vrai service à 1, 2, 3, 9 et 12 IRD, en 1280 × 720, 1920 × 1080 et
  2560 × 1440 : aucun texte coupé ou recouvert, rien qui déborde de la tuile.

## Laissé au constructeur

- Les valeurs exactes des `clamp()` (dans les bornes ci-dessus), la police et les graisses (celles de la page).
- Graduations de la courbe (deux valeurs discrètes ou aucune).
- Centrage ou alignement à gauche quand les tuiles atteignent leur taille maximale.
- Docs : `docs/GUIDE.md` (section Vue d'ensemble) et `docs/HISTORIQUE.md`.
