# Tuiles d'IRD épurées et adaptatives — plan de réalisation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** des tuiles d'IRD qui ne montrent que l'état, la marge (chiffre + courbe 10 min) et l'image, en disposition « chiffre d'abord », dont la grille remplit l'écran jusqu'à 9 IRD et dont le contenu s'ajuste à la taille de chaque tuile.

**Architecture :** une fonction pure `tileGrid(n, width, height)` (core.js, testée sous Node) choisit colonnes et taille de tuile ; `Card.applyLayout` l'applique (colonnes, `--card-h`), sans facteur `--s` ni mode compact. La carte (`card.js`) est reconstruite en zones de grille CSS (`head`, `cause`, `sub`, `num`, `thumb`, `chart`) ; la tuile est un conteneur CSS (`container-type: size`) et polices, image et courbe sont dimensionnées en `cqw`/`cqh` avec `clamp()`. La page de la porteuse réutilise la tuile (rapport 16:10) et reçoit programme, signal reçu et température dans son bloc de détails.

**Tech Stack :** JS vanilla (portée globale unique), CSS (grilles, unités de conteneur, `clamp()`), canevas 2D pour la courbe ; tests `node --test`.

**Spec :** `docs/superpowers/specs/2026-10-25-tuiles-ird-design.md`

## Global Constraints

- Tuile = état, marge (chiffre + courbe 10 min), image ; programme, porteuse, signal reçu, température, indications grises, titre et axe de la courbe, étiquettes des seuils, nombre d'alertes : **retirés de la tuile** de la vue d'ensemble.
- 9 IRD sans défilement ; sur un écran 1280 × 720 (zone des tuiles ≈ 736 × 470 px) cette règle l'emporte sur la taille minimale ; minimums lisibles en 3 × 3 : chiffre ≥ 22 px, image ≥ 80 px de large, courbe ≥ 24 px de haut.
- Hauteur de tuile plafonnée à 420 px ; la largeur remplit sa colonne ; au-delà de 9 IRD, taille d'une tuile « 9 IRD » et autant de colonnes que la largeur permet.
- Plus de `--s` ni de mode compact pour les cartes ; le menu et la colonne d'événements ne changent pas.
- Écrans visés 1280 × 720 à 2560 × 1440 ; navigateurs Chrome, Edge, Firefox récents.
- Portée globale unique : pas deux `const`/`function` de même nom entre fichiers de `web/`.
- Rien ne change côté service ni appareils.
- Tests : `python3 -m unittest discover tests` (336) et `~/.local/node/bin/node --test "web/tests/*.test.js"` (31) verts ; nouvelle logique pure testée sous Node.
- Branche `tuiles-ird` ; ne jamais pousser ; ne jamais committer `config.toml`.

## Review Focus

1. Nom d'IRD long (« IRD 12 — Régie B secours ») et badge long (« NON DÉCHIFFRÉ ») dans une tuile 3 × 3 sur 1280 × 720 : le nom se tronque avec « … », le badge reste entier.
2. Cause longue (« le décodeur ne reçoit plus de vidéo — depuis plus de 10 min ») : une ligne, tronquée avec « … », texte complet au survol de la tuile.
3. Tuile qui passe de OK à LOCK PERDU puis revient : la ligne de cause apparaît puis disparaît sans changer la hauteur de la tuile au-delà de sa place (la courbe cède).
4. Fenêtre redimensionnée pendant l'affichage : grille recalculée, courbe redessinée à la nouvelle taille (canevas non étiré).
5. Page de la porteuse en période passée : tuile « résumé » (marge minimale, trois durées, programme · porteuse), sans courbe ni image, sans erreur JS.

---

### Task 1 : choix de la grille (logique pure)

**Files :**
- Modify : `web/core.js` (nouvelle fonction + export)
- Test : `web/tests/core.test.js`

**Interfaces :**
- Produces : `tileGrid(n, width, height) -> { cols: number, tileH: number }` dans `web/core.js`, exportée.
  - Constantes : `TILE_GAP = 12`, `TILE_REF = [300, 200]` (proportion de référence pour noter une disposition), `TILE_MAX_H = 420`, `TILE_FIT_MAX = 9`.
  - n ≤ 9 : pour chaque `cols` de 1 à n, `rows = ceil(n / cols)`, `w = (width − GAP·(cols−1)) / cols`, `h = (height − GAP·(rows−1)) / rows` ; note = `min(w / 300, h / 200)` ; on garde la meilleure note (à égalité, le moins de colonnes) ; `tileH = min(h, 420)`.
  - n > 9 : on calcule la disposition de 9 (`w9`, `h9`) ; `cols = max(cols9, floor((width + GAP) / (w9 + GAP)))` ; `tileH = min(h9, 420)`.
  - n = 0 : `{ cols: 1, tileH: 420 }`.

- [ ] **Step 1 : tests en échec** dans `web/tests/core.test.js` :
  - `tileGrid(2, 1380, 800)` → `cols 2`, `tileH 420` (plafond) ;
  - `tileGrid(9, 736, 470)` → `cols 3`, `tileH` = `(470 − 24) / 3` (≈ 148,7) ;
  - `tileGrid(4, 1380, 800)` → `cols 2` ;
  - `tileGrid(6, 1380, 800)` → `cols 3` ;
  - `tileGrid(1, 1380, 800)` → `cols 1`, `tileH 420` ;
  - `tileGrid(12, 1860, 1100)` → `cols ≥ 4` et `tileH` égal à celui de `tileGrid(9, 1860, 1100)` ;
  - `tileGrid(12, 736, 470).tileH === tileGrid(9, 736, 470).tileH` et `cols 3`.
- [ ] **Step 2 :** `~/.local/node/bin/node --test "web/tests/*.test.js"` → échec (`tileGrid` absente).
- [ ] **Step 3 :** implémenter.
- [ ] **Step 4 :** tests Node verts.
- [ ] **Step 5 :** commit `feat(page): choix de la grille des tuiles d'IRD`.

### Task 2 : la tuile « chiffre d'abord »

**Files :**
- Modify : `web/card.js` (`build`, `update`, `updateSummary`, `drawSpark`, `drawBand`, `layout`, `applyLayout`), `web/core.js` (`marginTone` + export)
- Test : `web/tests/core.test.js`

**Interfaces :**
- Consumes : `tileGrid` (Task 1).
- Produces :
  - `marginTone(margin, minimum, live) -> "bad" | ""` (core.js, exportée) : `"bad"` si `live` et (`margin <= 0` ou (`minimum > 0` et `margin < minimum`)), sinon `""`.
  - `Card.build(serial, { onClick, hint })` construit, dans cet ordre (enfants directs de `article.ird-card`) :
    `header` (`.ird-name`, `.badge`), `p.ird-why` (cause), `p.ird-sub` (programme · porteuse, **masqué en Direct**), `div.ird-num` (`span.ird-num-label`, `span.ird-num-value`, `span.ird-num-unit`), `figure.ird-thumb` (inchangée : préchargement, grisé, agrandissement, légende), `div.ird-chart` (`canvas.ird-spark`, `canvas.ird-band`), `dl.ird-durations` (période passée seulement, masquée en Direct).
    Supprimés : `.dot`, `dl.ird-values`, `.ird-chart-title`, `.ird-axis`, `setRows` s'il ne sert plus.
  - `Card.update(h, { latest, down, deviceState, series, cfg, range })` (plus de `carrier`, `openAlerts`, `scale`, `compact`) :
    badge = `irdStatus(...)[0]`, son infobulle = état de l'appareil + « dernière mesure HH:MM:SS » (ex-infobulle de la pastille) ;
    cause = `why — since(...)` si l'état n'est pas OK, vide sinon (plus de nombre d'alertes) ;
    chiffre = `num(margin_db, 1)` ou « — » si pas de mesure fraîche, classe `marginTone(...)` ;
    `h.el.title` = cause + hint ;
    légende de l'image : vide si l'image est à jour, « image de HH:MM » si ancienne, « pas d'image » si aucune.
  - `drawSpark(canvas, d, range, drop, minimum)` : lignes de seuil sans texte ; pas de libellés de graduation (choix du plan : la courbe est un indicateur de tendance, la valeur est le grand chiffre) ; épaisseurs en `devicePixelRatio`. `drawBand(canvas, d, range)` : trait plein largeur. Plus de paramètres `scale`/`compact` ni de décalage gauche de 30 px.
  - `Card.updateSummary(h, summary, { program, carrier })` : badge inchangé (PAS DE MESURE / SANS DÉFAUT / DÉFAUTS SUR LA PÉRIODE), cause = « N alertes sur la période » ou vide, `ird-sub` visible (programme · porteuse), libellé « MARGE MIN. » et chiffre = `summary.margin_min`, `ird-durations` visible avec « Lock perdu », « Image ou flux perdu », « Dégradé » (durées `fmtDuration`, classes `bad` / `violet` / `warn` si > 0), image et courbe masquées.
  - `Card.layout(n, width, height)` = `tileGrid(n, width, height)` ; `Card.applyLayout(grid, { cols, tileH })` : `grid-template-columns: repeat(cols, minmax(0, 1fr))` et `--card-h` ; ne touche plus à `--s` ni à `compact`.
  - Ruling (à reporter au ledger) : pas de graduations sur la courbe — le spec laisse « deux valeurs discrètes ou aucune » ; aucune pour la place en 3 × 3. Coût si faux : on ne lit plus l'échelle (0 à N dB) sur la tuile ; la page de la porteuse la montre.

- [ ] **Step 1 : tests en échec** (`marginTone`) : `(6.3, 3, true)` → `""` ; `(2.5, 3, true)` → `"bad"` ; `(0, 0, true)` → `"bad"` ; `(2.5, 3, false)` → `""` ; `(5, 0, true)` → `""`.
- [ ] **Step 2 :** tests Node → échec.
- [ ] **Step 3 :** implémenter `marginTone`, puis la nouvelle carte dans `card.js` (avec `web/pages/overview.js` et `web/pages/carrier.js` adaptés aux nouvelles signatures de `update`/`applyLayout`, sans encore toucher au CSS).
- [ ] **Step 4 :** tests Node et Python verts ; la page de démo se charge sans erreur console (`preview_start name "monitoring-demo"`, `read_console_messages onlyErrors`).
- [ ] **Step 5 :** commit `feat(page): tuile d'IRD « chiffre d'abord »`.

### Task 3 : mise en page et ajustement à la taille de la tuile

**Files :**
- Modify : `web/style.css` (bloc « Carte d'IRD » et règles `.ird-grid.compact`, `.ird-section`)

**Interfaces :**
- Consumes : structure DOM de Task 2, `--card-h` posé par `applyLayout`.
- Produces (CSS) :
  - `.ird-card` : `container-type: size` ; hauteur `var(--card-h)` dans la vue d'ensemble ; grille `grid-template-columns: minmax(0, 1fr) auto` et zones `"head head" "cause cause" "sub sub" "num thumb" "chart chart"`, rangée `chart` en `minmax(…, 1fr)` ; bord supérieur coloré par `data-tone` (couleurs actuelles) ; plus aucun `var(--s)`.
  - Tailles en `clamp(min, cqw/cqh, max)` : nom ~`clamp(13px, 6cqw, 20px)` ; badge `clamp(10px, 4cqw, 12px)` ; chiffre de marge ~`clamp(22px, 22cqh, 56px)` (plus grosse écriture à toute taille) avec unité ~45 % du chiffre ; libellé « MARGE » `clamp(9px, 3.5cqw, 11px)` ; image `width: clamp(80px, 42cqw, 240px)` 16:9 ; cause `clamp(11px, 4cqw, 13px)` sur une ligne tronquée ; courbe : le reste, minimum 24 px.
  - Nom et cause : une ligne, `text-overflow: ellipsis` ; badge `white-space: nowrap`, jamais tronqué.
  - Hors Direct (`.ird-thumb[hidden]`) : `ird-durations` prend la zone `thumb` ; rangée `chart` vide.
  - Page de la porteuse (`.ird-section .ird-card`) : `height: auto; aspect-ratio: 16 / 10; min-height: 200px; max-height: 360px` ; sous 300 px de large (requête de conteneur sur `.ird-section`), zones `"head" "cause" "sub" "num" "chart" "thumb"` (image sous la courbe).
  - Supprimer les règles `.ird-grid.compact …`, `--thumb-w` et les anciennes classes `.ird-values`, `.ird-chart-title`, `.ird-axis` devenues inutiles (vérifier par `grep` qu'aucun autre écran ne les utilise).

- [ ] **Step 1 :** écrire le CSS.
- [ ] **Step 2 :** vérification sur la démo (port 8091), pour chaque taille 1280 × 720, 1920 × 1080, 2560 × 1440 (`resize_window`) et chaque nombre d'IRD 1, 2, 3, 9, 12 (démo à 3 IRD : retirer/ajouter par l'API de la démo, ou cloner les tuiles dans un conteneur de test comme lors de la relecture du 25/10) : script `javascript_tool` qui vérifie pour chaque tuile qu'aucun descendant visible ne dépasse la tuile (droite et bas), qu'aucun texte n'est tronqué hors nom/cause, et relève les tailles calculées du chiffre, de l'image et de la courbe. Expected : aucun débordement ; en 3 × 3 sur 1280 × 720, chiffre ≥ 22 px, image ≥ 80 px, courbe ≥ 24 px ; à 2 IRD sur 1920 × 1080, hauteur de tuile 420 px.
- [ ] **Step 3 :** Review Focus 1 à 4 vérifiés (nom et badge longs injectés dans une tuile clonée ; cause longue ; passage OK → défaut sur la démo ; redimensionnement).
- [ ] **Step 4 :** captures d'écran pour Max (2 IRD et 9 IRD).
- [ ] **Step 5 :** commit `feat(page): tuiles adaptées à leur taille, sans facteur d'échelle`.

### Task 4 : page de la porteuse

**Files :**
- Modify : `web/pages/carrier.js` (`detailLines` ou l'appel qui remplit `sec.details`, appel de `Card.update`/`Card.updateSummary`)

**Interfaces :**
- Consumes : `Card.update`/`Card.updateSummary` (Task 2), CSS de Task 3.
- Produces : en Direct, le bloc de détails commence par « Programme » (`service_name`), « Signal reçu » (`fmt(power_dbm, 0, "dBm")`, « — » si absent), « Température » (`ºC` FPGA, « — » si absente), puis les lignes actuelles (image, son, sorties). En période passée, la tuile résumé (Task 2) et pas de bloc de détails (inchangé).

- [ ] **Step 1 :** implémenter.
- [ ] **Step 2 :** vérification sur le vrai service (port 8080) et la démo : page de la porteuse en Direct (tuiles 16:10, détails avec programme/signal/température, valeurs égales à `/api/status`), en période « 24 h » (Review Focus 5), et en 1000 × 700 (image sous la courbe, rien qui déborde).
- [ ] **Step 3 :** commit `feat(page): page de la porteuse avec la nouvelle tuile`.

### Task 5 : documentation et vérification finale

**Files :**
- Modify : `docs/GUIDE.md` (Vue d'ensemble, page d'une porteuse), `docs/HISTORIQUE.md` (entrée du 25/10)

- [ ] **Step 1 :** GUIDE : décrire la tuile (état, marge, courbe, image ; reste au clic), la grille (jusqu'à 9 IRD sans défilement, plafond de hauteur), la page de la porteuse (détails enrichis) ; retirer « cartes compactes » et la description des anciennes valeurs.
- [ ] **Step 2 :** HISTORIQUE : entrée avec les choix (approche 1, rulings).
- [ ] **Step 3 :** suites Python et Node vertes ; vrai service redémarré (`preview_stop` puis `preview_start name "monitoring"`), vue d'ensemble vérifiée sur les deux IRD réels.
- [ ] **Step 4 :** commit `docs: tuiles d'IRD épurées`.
