# Aperçu de l'image décodée des IRD — plan de réalisation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** afficher dans la carte de chaque IRD l'image que décode l'IRD (`/thumbnail.jpg` du DR5000), rafraîchie toutes les ~2,5 s, sans stockage ni surveillance.

**Architecture :** le lecteur d'IRD (`IrdReader`) récupère l'image comme ses requêtes annexes, dans le même fil que la lecture habituelle (jamais deux requêtes simultanées vers l'IRD : pas de « 503 occupé » provoqué par l'image). L'`App` garde en mémoire la dernière image de chaque IRD et l'heure où une image *nouvelle* est arrivée (`thumb_ts`), la sert par `GET /api/ird/<n° de série>/image.jpg`, et ajoute `thumb_ts` au message `ird` du flux en direct et à `/api/status`. La carte (`web/card.js`) montre l'image à droite des valeurs, recharge l'`<img>` quand `thumb_ts` change, la grise quand elle est ancienne, l'ouvre en grand au clic.

**Tech Stack :** Python 3.12 stdlib (urllib, http.server, unittest) ; JS vanilla (portée globale unique), tests `node --test`.

**Spec :** `docs/superpowers/specs/2026-10-24-apercu-ird-design.md`

## Global Constraints

- Lecture seule sur les IRD : un `GET /thumbnail.jpg?forceRefresh=<ms>` et rien d'autre ; jamais de commande ni d'écriture.
- Sans proxy : passer par `open_url` de `monitoring/ird.py` (comme toutes les requêtes IRD).
- Aucune image en base, sur disque ou dans un export ; mémoire seulement, une image par IRD.
- L'image ne doit jamais faire échouer ni retarder notablement la lecture habituelle (délai `EXTRAS_TIMEOUT_S` = 1 s, échec journalisé au premier échec et au retour, comme « état » et « journal »).
- Pas d'image tant que le numéro de série n'est pas vérifié (c'est déjà la règle d'`IrdReader.read`) ; plus d'image servie pour un IRD retiré.
- Tests : `python3 -m unittest discover tests` (307 avant ce plan) et `~/.local/node/bin/node --test "web/tests/*.test.js"` (26 avant ce plan) restent verts.
- Textes de la page en français, comme le reste ; commentaires en français, densité du code voisin.
- Ne jamais committer `config.toml`, ne jamais pousser (Max pousse via GitHub Desktop), branche `apercu-ird`.

## Review Focus

1. IRD qui renvoie autre chose qu'un JPEG (page d'erreur HTML en 200, corps vide, image énorme) : rien n'est gardé, l'image précédente reste, pas d'exception dans la boucle.
2. IRD qui répond 503 ou expire sur l'image : la mesure du même tour est quand même rendue (`IrdReading.sample` présent).
3. Même image renvoyée deux fois (IRD figé, ou deux lectures dans les mêmes 2,5 s côté IRD) : `thumb_ts` ne bouge pas, la page ne recharge pas et finit par griser l'image — c'est le signe visuel d'un aperçu figé.
4. IRD retiré pendant qu'une page est ouverte : la route répond 404, la carte disparaît comme aujourd'hui.
5. Page de la porteuse sur une période passée : l'image est masquée (aucune image en direct à côté de courbes anciennes).

---

### Task 1 : lecture de l'image par le lecteur d'IRD

**Files :**
- Modify : `monitoring/ird.py` (constantes en tête, `IrdReading`, `IrdClient`, `IrdReader`)
- Test : `tests/test_ird.py` (`FakeIrd` : ajouter `do_GET`, paramètres `thumb` et `thumb_status`)

**Interfaces :**
- Produces :
  - `THUMB_EVERY_S = 2.5`, `THUMB_MAX_BYTES = 200_000`
  - `IrdReading.thumb: bytes | None = None` (nouveau champ, en dernier : les appels existants `IrdReading(sample)` / `IrdReading(sample, health, logs)` restent valides)
  - `IrdClient.thumbnail() -> bytes` : `GET http://<host>/thumbnail.jpg?forceRefresh=<ms>` via `open_url`, délai `EXTRAS_TIMEOUT_S` ; lève `IrdError` si HTTP ≠ 200, si le corps ne commence pas par `b"\xff\xd8"` (« aperçu : pas une image JPEG »), ou s'il dépasse `THUMB_MAX_BYTES` (lire au plus `THUMB_MAX_BYTES + 1` octets) ; `IrdBusy` sur 503 comme `_post`.
  - `IrdReader.__init__(…, thumb_every_s: float = THUMB_EVERY_S)` ; `read()` ajoute l'image via `self._extra("aperçu", self.client.thumbnail)` quand `thumb_every_s` s'est écoulé depuis la dernière tentative (horloge `self.clock`, indépendante des 10 s des autres annexes). Le retour anticipé actuel (`return IrdReading(sample, None, None)`) doit porter `thumb` aussi.

- [ ] **Step 1 : tests en échec** dans `tests/test_ird.py`. `FakeIrd` gagne `thumb=JPEG` (constante de test `JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100`) et `thumb_status=200`, et note les chemins GET dans `self.gets`.
  - `ClientTest.test_thumbnail_returns_jpeg` : `IrdClient(srv.host, IrdConfig()).thumbnail() == JPEG` et `srv.gets[0].startswith("/thumbnail.jpg?forceRefresh=")`.
  - `ClientTest.test_thumbnail_rejects_non_jpeg` : `thumb=b"<html>…"` → `IrdError`.
  - `ClientTest.test_thumbnail_rejects_oversized` : `thumb=b"\xff\xd8" + b"x" * THUMB_MAX_BYTES` → `IrdError`.
  - `ClientTest.test_thumbnail_busy` : `thumb_status=503` → `IrdBusy`.
  - `ReaderTest.test_reader_returns_thumb_every_2_5s` : horloge factice à 100 → `r.thumb == JPEG` ; à 101 → `None` ; à 102.5 → `JPEG`.
  - `ReaderTest.test_reader_thumb_failure_keeps_sample` : `thumb_status=500` → `r.sample.program_number == 1`, `r.thumb is None`, pas d'exception.
- [ ] **Step 2 :** `python3 -m unittest tests.test_ird -v` → les 6 nouveaux tests échouent (`AttributeError` / champ absent).
- [ ] **Step 3 :** implémenter les interfaces ci-dessus.
- [ ] **Step 4 :** `python3 -m unittest tests.test_ird -v` → tout passe ; puis `python3 -m unittest discover tests` → vert.
- [ ] **Step 5 :** commit `feat(ird): lecture de l'aperçu de l'image décodée`.

### Task 2 : dernière image en mémoire, route et `thumb_ts`

**Files :**
- Modify : `monitoring/app.py` (`__init__`, `_on_ird`, `remove_ird`, `status`, nouvelle méthode `thumbnail`)
- Modify : `monitoring/server.py` (`_get` : nouvelle route)
- Test : `tests/test_app.py`, `tests/test_server.py`

**Interfaces :**
- Consumes : `IrdReading.thumb` (Task 1).
- Produces :
  - `App.thumbs: dict[str, tuple[int, bytes]]` — n° de série → (`thumb_ts` en ms, octets), protégé par un verrou dédié (plusieurs fils d'IRD + fil HTTP).
  - Dans `_on_ird` : si `reading.thumb` est présent et diffère des octets gardés, `thumbs[serial] = (ts, reading.thumb)` ; identiques → rien (le `thumb_ts` d'une image figée ne bouge pas). Ignoré si l'IRD a été retiré (même garde que le reste de `_on_ird`).
  - Le message `{"type": "ird", …}` gagne `"thumb_ts": <ms ou None>` ; `status()["irds"][serial]["latest"]` aussi (mêmes clés que le message, pour que `Store` lise pareil au chargement et en direct).
  - `App.thumbnail(serial) -> bytes | None`.
  - `remove_ird` retire `thumbs[serial]`.
  - Route `GET /api/ird/<serial>/image.jpg` → 200, `Content-Type: image/jpeg`, `Cache-Control: no-store` ; 404 `{"erreur": …}` si IRD inconnu ou pas encore d'image. Le n° de série est pris tel quel dans le chemin (décodé par `unquote`), jamais utilisé comme chemin de fichier.
- Ruling (consigné ici, à reporter au ledger) : `thumb_ts` est l'heure du service à la réception d'une image *différente*, pas le `Last-Modified` de l'IRD — l'horloge de l'IRD peut dériver de celle du PC, ce qui fausserait l'heure affichée et le grisé. Le spec l'admet (« à défaut l'heure de réception »). Coût si faux : l'heure affichée a jusqu'à ~2,5 s de retard sur la génération réelle.

- [ ] **Step 1 : tests en échec.**
  - `test_app.py` : `test_thumb_kept_and_timestamped_on_change` (deux lectures avec la même image → `thumb_ts` inchangé ; une troisième avec une autre image → `thumb_ts` = ts de cette lecture ; le message publié sur le bus porte `thumb_ts`) ; `test_thumb_dropped_on_remove` (`remove_ird` → `app.thumbnail(serial) is None`) ; `test_status_has_thumb_ts`.
  - `test_server.py` : `test_ird_image_route` (après une lecture avec image : 200, `image/jpeg`, `no-store`, corps identique) ; `test_ird_image_404` (IRD inconnu → 404 ; IRD connu sans image → 404). `FakeIrds` du test doit pouvoir rendre un `IrdReading` avec `thumb`.
- [ ] **Step 2 :** `python3 -m unittest tests.test_app tests.test_server -v` → échecs attendus.
- [ ] **Step 3 :** implémenter.
- [ ] **Step 4 :** suite Python complète verte.
- [ ] **Step 5 :** commit `feat: dernière image des IRD servie à la page`.

### Task 3 : image de démonstration

**Files :**
- Create : `tests/fixtures/apercu-demo.jpg` (JPEG 320×180 neutre — mire ou dégradé, **jamais une image d'antenne réelle** : le dépôt a une copie publique)
- Modify : `tests/demo_server.py` (`factory.read` : renvoyer `thumb` toutes les `THUMB_EVERY_S`, sauf pour un IRD dont `video_lost(serial)` est vrai → `None`)

**Interfaces :**
- Consumes : `IrdReading.thumb`, `THUMB_EVERY_S` (Task 1).

- [ ] **Step 1 :** produire la mire : un PNG 320×180 écrit en Python stdlib (zlib + struct, barres de couleur), converti par `sips -s format jpeg in.png --out tests/fixtures/apercu-demo.jpg` ; vérifier `file tests/fixtures/apercu-demo.jpg` → `JPEG image data … 320x180`.
- [ ] **Step 2 :** brancher la démo ; lancer `preview_start name "monitoring-demo"` et vérifier `curl -s -o /dev/null -w '%{http_code} %{content_type}' http://127.0.0.1:8091/api/ird/<serial démo>/image.jpg` → `200 image/jpeg`.
- [ ] **Step 3 :** suite Python verte (`test_packaging` peut lister les fixtures : l'adapter si besoin).
- [ ] **Step 4 :** commit `test: mire d'aperçu pour le mode démo`.

### Task 4 : l'image dans la carte IRD

**Files :**
- Modify : `web/core.js` (fonction pure + export), `web/card.js` (`build`, `update`, `updateSummary`), `web/style.css`
- Test : `web/tests/core.test.js`

**Interfaces :**
- Consumes : `latest.thumb_ts` (Task 2), route `/api/ird/<serial>/image.jpg` (Task 2).
- Produces :
  - `THUMB_STALE_MS = 10_000` (core.js).
  - `thumbView(thumbTs, now, connected) -> { state: "fresh" | "old" | "none", prefix: string }` (core.js, exportée ; `fmtTime` vit dans common.js, non chargé par les tests Node : la carte compose `prefix + fmtTime(thumbTs)`) :
    - `thumbTs == null` → `{ state: "none", prefix: "pas d'image" }` (sans heure) ;
    - `connected && now - thumbTs <= THUMB_STALE_MS` → `{ state: "fresh", prefix: "image " }` ;
    - sinon → `{ state: "old", prefix: "image de " }`.
  - Carte : dans `build`, un `figure.ird-thumb` (`img` + `figcaption`) placé avec `h.values` dans un conteneur `div.ird-top` (grille deux colonnes : valeurs | image) ; la courbe reste en dessous, pleine largeur.
  - `update` : `src = /api/ird/${encodeURIComponent(serial)}/image.jpg?t=${thumb_ts}` affecté **seulement** quand `thumb_ts` change (pas de requête à chaque rendu) ; classe `old` (grisé : `filter: grayscale(1); opacity: .55`) ou `none` (cadre vide avec le libellé) selon `thumbView`. Une erreur de chargement de l'`<img>` passe la figure en `none`.
  - `updateSummary` (périodes passées) : `figure.hidden = true` ; `update` la ré-affiche.
  - Clic sur l'image : ouvre un `<dialog>` unique (créé une fois) montrant l'image à 320×180 avec le nom de l'IRD et le libellé ; `stopPropagation` pour ne pas déclencher le clic de tuile (qui mène à la porteuse) ; fermeture par clic ou Échap.
  - CSS : largeur de l'image `calc(128px * var(--s))`, ratio 16/9, `object-fit: cover`, coins arrondis comme les autres éléments ; en mode `.ird-grid.compact`, `calc(88px * var(--s))`. Sombre/clair : utiliser les variables existantes (`--border`, `--muted`, `--surface`).

- [ ] **Step 1 : tests en échec** dans `web/tests/core.test.js` : `thumbView` — `null` → `{ state: "none", prefix: "pas d'image" }` ; `NOW - 3000` connecté → `{ state: "fresh", prefix: "image " }` ; `NOW - 10_000` → `fresh` (borne incluse) ; `NOW - 11_000` → `{ state: "old", prefix: "image de " }` ; `NOW - 3000` avec `connected = false` → `old`.
- [ ] **Step 2 :** `~/.local/node/bin/node --test "web/tests/*.test.js"` → échec (`thumbView` absente).
- [ ] **Step 3 :** implémenter `thumbView` puis la carte et le CSS.
- [ ] **Step 4 :** tests Node verts ; puis vérification dans la démo (port 8091) avec le navigateur intégré :
  - Vue d'ensemble : image à droite des valeurs sur chaque tuile, l'heure avance toutes les ~2,5 s (`read_network_requests` : une requête image par changement, pas par seconde) ;
  - IRD démo sans vidéo : cadre « pas d'image » ;
  - clic sur l'image : la fenêtre s'ouvre, la page ne change pas ; Échap la ferme ;
  - page porteuse en direct : image présente ; en période « 24 h » : masquée ;
  - service démo arrêté : l'image se grise avec « image de … » ;
  - capture d'écran pour Max.
- [ ] **Step 5 :** commit `feat(page): aperçu de l'image décodée dans la carte IRD`.

### Task 5 : vérification sur les vrais IRD et documentation

**Files :**
- Modify : `docs/GUIDE.md` (section des cartes IRD : l'image, son heure, le grisé, le clic), `docs/HISTORIQUE.md` (entrée du 24/10 : aperçu IRD, relevé de `/thumbnail.jpg`, ruling `thumb_ts`)

- [ ] **Step 1 :** redémarrer le vrai service (`preview_stop` puis `preview_start name "monitoring"`), recharger l'onglet de l'outil ; vérifier dans le journal du service l'absence d'« aperçu » en échec pour l'IRD 1, et que l'IRD 3 (sans lock) montre son image rouge à jour. Ne rien envoyer d'autre aux IRD.
- [ ] **Step 2 :** mettre à jour les deux docs.
- [ ] **Step 3 :** suites Python et Node vertes.
- [ ] **Step 4 :** commit `docs: aperçu de l'image décodée des IRD`.
