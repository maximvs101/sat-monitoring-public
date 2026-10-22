# Refonte de l'interface web — plan de réalisation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer la page actuelle (onglets Direct / Historique, tuiles, sections dépliables, fenêtres de réglage) par une interface claire à menu latéral : vue d'ensemble, page par porteuse avec sélecteur de période, journal, analyseur FSH4, réglages en pages, un seul système visuel.

**Architecture:** Page unique en JavaScript simple (sans framework ni compilation), navigation par l'adresse (`#/…`). Un module de données (`store.js`) garde l'état en direct (statut, flux SSE, fenêtre du direct) et le partage entre les pages ; chaque page (`pages/*.js`) construit son contenu et se met à jour sur notification. La logique sans DOM (adresses, périodes, correspondance des porteuses, résumés) vit dans `core.js`, testée sous Node. Le service Python ne change que pour exposer span et RBW du FSH4.

**Tech Stack:** JavaScript (navigateur, sans compilation), uPlot 1.6.32 (déjà dans `web/vendor`), CSS à variables ; tests de la logique avec le lanceur intégré de Node 24 (`node --test`, déjà installé dans `~/.local/node`) ; Python 3.12 bibliothèque standard côté service.

**Spec:** `docs/superpowers/specs/2026-10-22-refonte-interface-design.md`

## Global Constraints

- Lecture seule envers les appareils, inchangée ; API inchangée sauf l'ajout de `span_hz` / `rbw_hz` à `status()["fsh"]` (tâche 1).
- JavaScript simple, sans framework ni compilation ; uPlot pour les graphes ; une feuille de style unique à variables ; la page doit s'empaqueter dans `MonitoringSat.exe` sans étape supplémentaire (fichiers statiques de `web/`).
- Ordinateur uniquement (1 280 px de large et plus ; `min-width` de la page conservé) ; pas de mode sombre ; tout en français.
- Système visuel (§10 de la spec) : fond gris clair, cartes blanches à bord fin, une police (système), échelle de tailles limitée, espacement régulier ; bleu = actions / liens / navigation ; vert, orange, violet, rouge = états (sens de la bande État) ; gris = pas de mesure / en attente ; un seul composant par objet (carte d'IRD, graphe, liste d'événements, tableau, bouton, champ).
- Ne rien perdre : chaque fonction actuelle (liste ci-dessous) a sa place dans la nouvelle page.
- Les 251 tests Python restent verts ; `node --check` sur chaque script ; tests Node de `core.js` verts ; vérification dans le navigateur sur la démonstration (`monitoring-demo`, port 8091) puis sur le service réel.

Fonctions actuelles à conserver (contrôle final, tâche 9) : pastilles d'état des appareils et heure de dernière mesure ; Pause / Reprendre FSH4 ; avertissement « décalage LNB non réglé » ; avertissement « polarisation inconnue » ; tuiles d'IRD (état, cause, durée, alertes, valeurs, courbe de marge avec seuils, bande État) et leur grille adaptative ; par porteuse : niveau RF des marqueurs, marge avec seuils, puissance d'entrée, bande État avec légende et survol ; détails d'un IRD (image, voies audio, sorties) ; plancher de bruit ; historique sur une plage, glisser pour zoomer, clic sur un événement pour centrer ; liste d'événements (gravité, source, en cours) ; Appareils (FSH4, IRD : nom, adresse, polarisation, retrait, ajout, recherche réseau) ; Porteuses (renommer, ajouter, supprimer les saisies) ; Seuils (chute, minimum).

## Review Focus

1. **Navigation pendant le direct** : passer d'une page à l'autre (vue d'ensemble → porteuse → journal → retour) ne doit ni dupliquer les abonnements au flux en direct, ni laisser des graphes uPlot ou des minuteries orphelins (mémoire, CPU sur une journée). Test : `core` gère des abonnements désabonnables ; vérification dans le navigateur après 20 navigations (nombre d'instances uPlot vivantes, nombre d'écouteurs).
2. **Clés de porteuse avec « ? »** (polarisation inconnue : `125830-?`) dans l'adresse `#/porteuse/<clé>` : encodage et décodage corrects, Précédent fonctionnel. Test Node sur `parseRoute` / `routeHref`.
3. **Regroupement qui change en cours de session** (IRD re-réglé, polarisation devenue connue) : la page d'une porteuse dont la clé disparaît du direct ne doit pas planter ; elle retrouve la porteuse par fréquence et polarisation ou affiche le message prévu. Test Node sur `matchCarrier`.
4. **Périodes longues** (7 j, 30 j au journal) : chargement agrégé (`max_points` limité comme aujourd'hui), pas de blocage de la page. Vérification dans le navigateur sur la base réelle (7 j).
5. **États vides** : aucun IRD suivi, FSH4 jamais joignable, porteuse sans mesure sur la période, journal vide — chaque page affiche un message clair au lieu d'une page blanche ou d'une erreur. Vérification dans le navigateur (démo sans IRD : retirer les IRD de la démo).

---

## Structure des fichiers

| Fichier | Rôle |
| --- | --- |
| `web/index.html` | Squelette : `<nav id="nav">` (menu latéral) et `<main id="view">` ; charge les scripts dans l'ordre ci-dessous. |
| `web/style.css` | Réécrit : variables (couleurs, tailles, espacements, rayons), mise en page (menu + contenu), composants (carte, chiffre clé, carte de graphe, liste d'événements, tableau, formulaire, bouton, badge, sélecteur de période, bandeau d'avertissement). |
| `web/core.js` | Logique sans DOM, exportée pour Node (`module.exports` si défini) : adresses, périodes, correspondance des porteuses, résumé de période, état d'un IRD en clair, formats. |
| `web/common.js` | Garde : constantes de couleurs, `$`, `el`, formats (déjà là) ; s'appuie sur `core.js` pour ce qui est testable. |
| `web/store.js` | Données partagées : statut (`/api/status`), flux SSE, séries de la fenêtre du direct, dernière mesure par IRD, événements récents ; `subscribe(fn)` → fonction de désabonnement ; `loadRange(from, to)` pour une période. |
| `web/charts.js` | Habillage unique des graphes uPlot (reprend `chartOpts`, `markerChart`, `minSpanRange`, seuils, bande État, curseur synchronisé, glisser pour une période). |
| `web/card.js` | Carte d'IRD unique (reprend le rendu de `wall.js` : état, cause, valeurs, mini-courbe, bande) + grille adaptative ; variante « résumé de période ». |
| `web/router.js` | Lit `location.hash`, choisit la page, appelle `page.mount(view, params)` puis `page.unmount()` en partant ; met à jour le menu. |
| `web/nav.js` | Menu latéral (entrées, porteuses actuelles, badges d'alertes, indicateur de connexion). |
| `web/pages/overview.js` | `#/` |
| `web/pages/carrier.js` | `#/porteuse/<clé>` |
| `web/pages/journal.js` | `#/journal` |
| `web/pages/fsh.js` | `#/fsh4` |
| `web/pages/settings.js` | `#/reglages/appareils|porteuses|seuils` (reprend `dialogs.js`) |
| `web/tests/core.test.js` | Tests Node de `core.js`. |
| Supprimés à la fin | `web/app.js`, `web/sections.js`, `web/wall.js`, `web/dialogs.js`. |
| `monitoring/app.py`, `tests/test_app.py` | Tâche 1 seulement. |

Ordre de chargement dans `index.html` : `vendor/uPlot.iife.min.js`, `core.js`, `common.js`, `store.js`, `charts.js`, `card.js`, `nav.js`, `pages/*.js`, `router.js` (en dernier : démarre l'application).

Commande de test JS : `~/.local/node/bin/node --test web/tests/` ; vérification syntaxique : `for f in web/*.js web/pages/*.js; do ~/.local/node/bin/node --check $f; done`.

---

### Task 1: Statut du FSH4 avec span et RBW

**Files:**
- Modify: `monitoring/app.py` (`status()`)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `App.status()["fsh"]` gagne `span_hz` et `rbw_hz` (float ou `None` tant qu'aucun réglage n'a été lu), tirés de `self._fsh_key` (dernier `FshSettings`).

- [ ] **Step 1: Write the failing test** `test_status_fsh_span_and_rbw` dans `tests/test_app.py` : `None` / `None` avant toute lecture ; après `app.fsh_loop.step()` avec la lecture de test (`fsh({1: …}, span=50e6)`, RBW 300e3), `status()["fsh"]["span_hz"] == 50e6` et `["rbw_hz"] == 300e3`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_app -v` — Expected: FAIL (`KeyError: 'span_hz'`).
- [ ] **Step 3: Implement** dans `status()`.
- [ ] **Step 4: Run** `python3 -m unittest discover tests` — Expected: OK (252 tests).
- [ ] **Step 5: Commit** `git add monitoring/app.py tests/test_app.py && git commit -m "Statut : span et RBW du FSH4"`

### Task 2: Logique de la page (`core.js`) et ses tests

**Files:**
- Create: `web/core.js`, `web/tests/core.test.js`

**Interfaces (Produces, toutes pures, exportées pour Node et globales dans le navigateur) :**
- `parseRoute(hash) -> { page, key?, period?, ird?, tab? }` ; `routeHref({ page, key, period, ird, tab }) -> string`. Pages : `overview` (`#/`), `carrier` (`#/porteuse/<clé encodée>`), `journal` (`#/journal`), `fsh` (`#/fsh4`), `settings` (`#/reglages/<tab>`, tab ∈ `appareils|porteuses|seuils`), `notfound`. Paramètres après `?` dans le hash : `p=live|1h|24h|7j|<from>-<to>` (ms), `ird=<série>`.
- `periodRange(period, now, liveMin) -> [fromMs, toMs]` ; `periodLabel(period) -> string` (« Direct 10 min », « 1 h », « 24 h », « 7 j », « du … au … »).
- `matchCarrier(carriers, ref) -> carrier | null` : `ref` = `{ key }` ou `{ rf_khz, polarization, symbol_rate }` ; d'abord la clé exacte, sinon même polarisation (ou inconnue d'un côté) et écart de fréquence < moitié du plus petit débit symbole connu (sinon 1 000 kHz) — même règle que `monitoring/carriers.py::_close`.
- `carrierKeyRef(key) -> { rf_khz, polarization }` : relit fréquence et polarisation d'une clé `"<rf_khz // 100>-V|H|?"` (pour retrouver une porteuse sur une autre période).
- `periodSummary(series, events) -> { margin_min, state_ms: {0,1,2,3}, alerts }` : à partir des séries d'un IRD (`ts`, `margin_db`, `state`) et de ses événements sur la période ; durées par code de la bande (un point compte jusqu'au suivant, au plus 3 s d'écart).
- `irdStatus(m, down, minimumMarginDb) -> [titre, cause, code|"down"|"idle"]` : reprend `status()` de `wall.js` à l'identique.
- `eventTarget(ev, carriersAt) -> { page, key?, period? } | null` : où mène un clic d'événement (spec §8) ; `carriersAt` = porteuses de la période autour de l'événement (avec `periods`).

- [ ] **Step 1: Write the failing tests** dans `web/tests/core.test.js` (`node:test`, `node:assert`), au minimum :
  - `parseRoute("#/porteuse/125830-%3F?p=24h&ird=S1")` → `{ page: "carrier", key: "125830-?", period: "24h", ird: "S1" }` ; `routeHref` en est l'inverse ; `parseRoute("")` et `"#/"` → overview ; `"#/nimporte"` → notfound ; `"#/reglages/seuils"` → settings/seuils.
  - `periodRange("live", 1_000_000_000_000, 10)` → `[now − 600 000, now]` ; `"7j"` → 7 jours ; `"1700000000000-1700003600000"` → ces bornes ; période invalide → comme `live`.
  - `matchCarrier` : clé exacte ; clé absente mais fréquence à 50 kHz près et même polarisation → trouvée ; polarisation opposée → `null` ; débit 7,2 MS/s et écart 4 MHz → `null`.
  - `carrierKeyRef("125830-?")` → `{ rf_khz: 12583000, polarization: null }`.
  - `periodSummary` sur une série de 5 points à 1 s (codes 0,0,3,3,0 ; marges 7,6,0,0,7) → `margin_min 0`, `state_ms[3] == 2000`, `alerts` = nombre d'événements fournis.
  - `irdStatus` : mêmes cas que l'actuel (`!locked` → LOCK PERDU code 3 ; image absente → PAS D'IMAGE ; marge sous le minimum → MARGE BASSE code ≥ 1 ; `down` → INJOIGNABLE).
  - `eventTarget` : événement `ird:S1` dont la porteuse de l'époque est trouvée → `{ page: "carrier", key, period: "<from>-<to>" }` avec la marge de 30 s ; événement `fsh` → `{ page: "fsh", period }` ; IRD sans porteuse → `null`.
- [ ] **Step 2: Run** `~/.local/node/bin/node --test web/tests/` — Expected: FAIL (`core.js` absent).
- [ ] **Step 3: Implement** `web/core.js` (pur, sans DOM ; fin de fichier : `if (typeof module !== "undefined") module.exports = { … }`).
- [ ] **Step 4: Run** `~/.local/node/bin/node --test web/tests/` — Expected: tous les tests OK.
- [ ] **Step 5: Commit** `git add web/core.js web/tests && git commit -m "Page : logique testée (adresses, périodes, porteuses, résumés)"`

### Task 3: Squelette, style, données partagées, menu et routeur

**Files:**
- Modify: `web/index.html` (réécrit), `web/style.css` (réécrit), `web/common.js`
- Create: `web/store.js`, `web/router.js`, `web/nav.js`, `web/pages/*.js` (squelettes vides qui affichent leur titre)

**Interfaces:**
- Consumes: `core.js` (tâche 2).
- Produces :
  - `Store` : `Store.start()` (statut puis flux SSE ; reconnexion : resynchronisation complète comme aujourd'hui) ; `Store.state` (`cfg`, `carriers`, `followed`, `names`, `states`, `latest`, `irds` = séries de la fenêtre du direct, `fsh`, `events` récents, `noise`, `ifWarning`, `connected`) ; `Store.subscribe(fn) -> unsubscribe` (appelé à chaque mesure, changement d'état, événement, regroupement, et chaque seconde pour faire avancer l'axe) ; `Store.loadRange(from, to) -> Promise<{ fsh, irds, carriers, noise, ifWarning, events, names }>` (séries agrégées comme aujourd'hui, `max_points` selon la largeur) ; `Store.post(url, body)` (reprend `post` de `dialogs.js`).
  - Contrat de page : `Page = { mount(view, params) , update?(), unmount() }` ; le routeur appelle `unmount` de la page quittée (qui détruit ses graphes et se désabonne), puis `mount` de la nouvelle ; `hashchange` déclenche le changement.
  - `Nav.render(route)` : menu (entrées spec §5, porteuses actuelles de `Store.state.carriers`, badges = nombre d'événements en cours par porteuse et au total, indicateur « en direct » / « connexion perdue »), entrée active en évidence.
- [ ] **Step 1: Implement** squelette, style (variables et composants listés dans la structure), `store.js` (repris de `app.js` : `setSeries`, `appendLive`, `trimLive`, `onLive`, `applyStatus`, rejouer le tampon pendant un chargement, jeton de chargement), `router.js`, `nav.js`, pages vides. Les anciens scripts ne sont plus chargés (mais restent dans le dépôt jusqu'à la tâche 9).
- [ ] **Step 2: Check** `node --check` sur tous les scripts — Expected: aucune sortie ; `node --test web/tests/` — Expected: OK.
- [ ] **Step 3: Verify in the built-in browser** sur la démonstration (`preview_start monitoring-demo`) : le menu s'affiche avec les porteuses de la démo et leurs badges ; chaque entrée change l'adresse et le titre de la page ; Précédent revient ; l'indicateur passe à « connexion perdue » quand on arrête la démo puis revient à « en direct » au redémarrage ; aucune erreur dans la console.
- [ ] **Step 4: Commit** `git add web && git commit -m "Page : squelette à menu latéral, style unique, données partagées, routeur"`

### Task 4: Carte d'IRD et vue d'ensemble

**Files:**
- Create: `web/card.js` ; Modify: `web/pages/overview.js`, `web/style.css`

**Interfaces:**
- Consumes: `Store`, `core.irdStatus`, `core.periodSummary`, utilitaires de `common.js`.
- Produces : `Card.build(serial, { onClick }) -> handle` ; `Card.update(handle, { latest, down, series, carrier, openAlerts, cfg, range, scale, compact })` (rendu de la tuile actuelle de `wall.js` : badge d'état, cause et durée, alertes, programme et porteuse, valeurs, mini-courbe de marge avec seuils, bande État) ; `Card.updateSummary(handle, summary, names)` (variante hors Direct) ; `Card.layout(n, width, height) -> { cols, scale, tileH, compact }` (reprend `layout`/`fill` de `wall.js`).
- [ ] **Step 1: Implement** la vue d'ensemble (spec §6) : titre et heure, quatre chiffres clés (IRD en service, alertes en cours, marge la plus basse et son IRD, FSH4 avec Pause / Reprendre et lien vers `#/fsh4`), grille de cartes (rangées par porteuse puis nom ; clic → `routeHref({ page: "carrier", key, ird })`, IRD sans porteuse → `#/reglages/appareils`), carte « Événements en cours et récents » (composant de liste partagé, lien « Tout le journal »), carte « Plancher de bruit (FSH4) » si des marqueurs de bruit existent (graphe de `charts.js` à la tâche 5 : ici un emplacement réservé), bandeau « décalage LNB » si besoin, message « Aucun IRD suivi : en ajouter un dans Réglages › Appareils » si vide.
- [ ] **Step 2: Check** syntaxe et tests Node — Expected: OK.
- [ ] **Step 3: Verify in the built-in browser** sur la démo, en 1 440 × 900 et 1 280 × 800 : chiffres clés justes (comparer à `/api/status`), cartes à jour chaque seconde, l'IRD 2 de la démo passe en LOCK PERDU puis revient, l'IRD 3 en PAS D'IMAGE ; clic sur une carte → adresse de sa porteuse ; Pause / Reprendre FSH4 fonctionne ; 12 IRD (ajout fictif à l'affichage, comme lors des essais précédents) tiennent sans défilement ; aucune erreur console. Capture d'écran.
- [ ] **Step 4: Commit** `git add web && git commit -m "Page : vue d'ensemble et carte d'IRD unique"`

### Task 5: Graphes et page d'une porteuse

**Files:**
- Create: `web/charts.js` ; Modify: `web/pages/carrier.js`, `web/pages/overview.js` (plancher de bruit), `web/style.css`

**Interfaces:**
- Consumes: `Store`, `Card`, `core.matchCarrier`, `core.carrierKeyRef`, `core.periodRange`, `core.periodSummary`.
- Produces : `Charts.marginChart(el, carrier, opts)`, `Charts.powerChart`, `Charts.rfChart`, `Charts.stateBand` (avec légende et survol), `Charts.markerChart(el, title, nums)` ; chaque fonction renvoie `{ setData(…), setRange(from, to), destroy() }` ; curseur synchronisé entre les graphes d'une même page ; `onSelect(from, to)` (glisser) fourni par la page ; `PeriodSelector.build(current, onChange)` (Direct, 1 h, 24 h, 7 j, Période… avec deux champs date-heure).
- [ ] **Step 1: Implement** la page (spec §7) : en-tête (chemin, nom, fréquence, polarisation, débit, modulation, nombre d'IRD, avertissement polarisation inconnue), sélecteur de période (dans l'adresse), courbes communes (marge avec minimum rouge et, en Direct, chute orange ; puissance ; niveau RF ou la raison de son absence ; bande État), sections par IRD (en Direct : `Card` + détails, repris de `detailLines` de `sections.js` ; hors Direct : `Card.updateSummary`), événements de la porteuse sur la période (clic → période calée), défilement vers `ird=` à l'ouverture, glisser sur un graphe → période libre. Direct : mise à jour à chaque notification du `Store` ; autres périodes : `Store.loadRange` une fois, porteuse retrouvée par `matchCarrier` parmi les porteuses de la période (mention « n'existe plus aujourd'hui » si absente du direct ; message et lien si introuvable). Plancher de bruit de la vue d'ensemble : `Charts.markerChart`.
- [ ] **Step 2: Check** syntaxe et tests Node — Expected: OK.
- [ ] **Step 3: Verify in the built-in browser** sur la démo puis le service réel : courbes synchronisées, seuils visibles, Direct qui avance, 1 h / 24 h / 7 j chargés (agrégés) sans blocage sur la base réelle, glisser → période libre, Précédent revient au Direct, section de l'IRD ciblé à l'écran, clé avec « ? » dans l'adresse, porteuse disparue ouverte sur une période passée (base réelle : `11 220,8` du 17/10) avec la mention prévue ; 20 navigations aller-retour sans augmentation du nombre de graphes vivants (contrôle par `javascript_tool`). Capture d'écran.
- [ ] **Step 4: Commit** `git add web && git commit -m "Page : graphes communs et page d'une porteuse avec période"`

### Task 6: Journal des événements

**Files:** Modify: `web/pages/journal.js`, `web/style.css`

**Interfaces:** Consumes: `Store.loadRange` (ou `/api/events` seul pour la liste), `core.eventTarget`, composant de liste d'événements (tâche 4).

- [ ] **Step 1: Implement** (spec §8) : liste complète, filtres période (24 h par défaut, 7 j, 30 j, Période…), porteuse ou IRD, « en cours seulement » (dans l'adresse) ; clic → `eventTarget` (chargement des porteuses de la période autour de l'événement pour savoir où était l'IRD) ; message « Aucun événement sur la période ».
- [ ] **Step 2: Check** — Expected: OK.
- [ ] **Step 3: Verify** sur la base réelle (30 j : tous les événements depuis le 17/10) : filtres, clic sur un événement d'IRD → bonne porteuse au bon moment, clic sur un événement FSH4 → page FSH4 calée. Capture.
- [ ] **Step 4: Commit** `git add web && git commit -m "Page : journal des événements"`

### Task 7: Analyseur FSH4

**Files:** Modify: `web/pages/fsh.js`, `web/style.css`

**Interfaces:** Consumes: `Store` (statut avec `span_hz` / `rbw_hz`, tâche 1), `Charts.markerChart`, `PeriodSelector`.

- [ ] **Step 1: Implement** (spec §8 bis) : en-tête (état et raison, adresse, polarisation, span, RBW, Pause / Reprendre), période, plancher de bruit (ou la raison de son absence), tous les marqueurs de la période avec leur porteuse, événements du FSH4 (clic → période calée).
- [ ] **Step 2: Check** — Expected: OK.
- [ ] **Step 3: Verify** sur la démo (FSH4 simulé) et la base réelle (période du 17/10, FSH4 du premier site) ; FSH4 hors ligne aujourd'hui : message clair en Direct. Capture.
- [ ] **Step 4: Commit** `git add web && git commit -m "Page : analyseur FSH4"`

### Task 8: Réglages en pages

**Files:** Modify: `web/pages/settings.js`, `web/style.css`

**Interfaces:** Consumes: `Store.post`, `Store` (rafraîchissement après chaque action) ; reprend toute la logique de `dialogs.js` (appareils, recherche réseau, porteuses, seuils).

- [ ] **Step 1: Implement** (spec §9) : trois pages, mêmes actions et mêmes messages d'erreur qu'aujourd'hui, erreurs affichées au plus près de l'action.
- [ ] **Step 2: Check** — Expected: OK.
- [ ] **Step 3: Verify** sur la démo seulement (aucune action sur le service réel) : renommer, imposer une polarisation, retirer puis rajouter un IRD, recherche réseau (démo : liste vide attendue), ajouter / renommer / supprimer une porteuse, régler les seuils puis les remettre ; refus attendus (nom vide, fréquence invalide, seuils hors bornes) affichés en clair. Capture.
- [ ] **Step 4: Commit** `git add web && git commit -m "Page : réglages en pages"`

### Task 9: Nettoyage, contrôle de non-régression, documentation

**Files:**
- Delete: `web/app.js`, `web/sections.js`, `web/wall.js`, `web/dialogs.js`
- Modify: `README.md` (section « La page » réécrite), `docs/HISTORIQUE.md`

- [ ] **Step 1: Delete** les anciens scripts ; `grep` dans `web/` qu'aucune référence ne reste.
- [ ] **Step 2: Contrôle « ne rien perdre »** : parcourir la liste des fonctions actuelles (Global Constraints) et noter pour chacune où elle se trouve dans la nouvelle page ; toute absence est un défaut à corriger dans cette tâche.
- [ ] **Step 3: Run** `python3 -m unittest discover tests`, `node --test web/tests/`, `node --check` sur tous les scripts — Expected: OK.
- [ ] **Step 4: Verify** le service réel (relancer `monitoring`) : vue d'ensemble, porteuses de l'IRD 1 et de l'IRD 3, journal, FSH4, réglages (consultation seulement), aucune erreur console ; 15 minutes de Direct sans saccade.
- [ ] **Step 5: Docs** : README (menu, pages, période, journal, analyseur, réglages ; plus d'onglets ni de fenêtres) ; HISTORIQUE (entrée du jour, état actuel).
- [ ] **Step 6: Commit** `git add -A web README.md docs && git commit -m "Page : anciens scripts retirés, documentation"`
