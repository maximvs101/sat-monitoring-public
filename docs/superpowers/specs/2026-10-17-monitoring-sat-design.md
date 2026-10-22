# Monitoring satellite FSH4 + IRD Ateme — conception du prototype

> Prolongée le 21/10/2026 par [plusieurs porteuses et plusieurs IRD](2026-10-21-multi-porteuses-design.md), qui remplace ce document pour la gestion des IRD, des porteuses et des marqueurs.

17 octobre 2026 · validé en séance avec Max · mis à jour en fin de journée avec les évolutions et l'essai de dépointage (voir `docs/HISTORIQUE.md`)

Source : *Étude de faisabilité — Monitoring satellite FSH + IRD Ateme* (27/09/2026), phase 1 « Prototype ».

## 1. Objectif

Enregistrer en continu, sur une ligne de temps commune, le niveau RF d'une porteuse satellite relevé par un R&S FSH4 et l'état de réception d'un IRD Ateme DR5000, puis les consulter dans une page web locale, en direct et en historique, pour relier une dégradation du décodeur à ce qui se passait côté RF.

Critères de réussite :

- les deux flux sont collectés sans trou pendant au moins 10 minutes d'essai réel, aux cadences visées ;
- la page affiche le direct (rafraîchi chaque seconde) et permet de remonter dans l'historique ;
- un clic sur un événement détecté montre les trois graphes centrés sur cet instant ;
- un appareil injoignable n'interrompt pas la collecte de l'autre.

## 2. Matériel et accès (relevés le 17/10/2026)

| Appareil | Adresse | Accès retenu | Constat |
| --- | --- | --- | --- |
| R&S FSH4, firmware V1.20 | 192.168.20.222, TCP 5555 | SCPI texte, terminaison `\n` | `CALC:MARK:X?` / `CALC:MARK:Y?` répondent ; `TRAC:DATA? TRACE1` renvoie `-113 Undefined header` |
| Ateme DR5000 « IRD 5 », firmware 2.9.3.0 | 192.168.20.145, HTTP 80 | `POST /xml-rpc`, `Content-Type: text/plain; charset=utf-8`, corps brut `xml=<refresh/>` | Réponse ≈ 260 Ko en 0,25–0,35 s ; bloc `response/…/input/sat` complet ; pas de mot de passe ; horloge système en 2000 |

Porteuse suivie : 11221 MHz V, 24,76 MS/s, DVB-S2 QPSK 5/6, IF 1470,8 MHz ; occupée de 11 207 à 11 235 MHz, plancher de bruit ≈ −67 dBm (span 50 MHz, RBW 300 kHz depuis le 17/10). Les DR5000 « IRD 3 » (192.168.20.143) et « IRD 4 » (192.168.20.144) sont en entrée IP et hors périmètre.

Particularités du FSH4 V1.20 relevées en exploitation : lire `CALC:MARKn:X?` ou `:Y?` d'un marqueur éteint l'allume (seul `CALC:MARKn?` est neutre) ; sans environ 200 ms de silence entre deux salves de requêtes, l'appareil ne rafraîchit plus sa mesure. Le SNMP Ateme (enterprise 27338) est écarté faute de MIB.

Champs IRD utilisés (dans `<input>`) : `sat/locked`, `sat/snr` et `sat/snrMargin` (dixièmes de dB : 157 → 15,7 dB), `sat/ber`, `sat/power` (dBm), `sat/frequency` (kHz IF), `sat/symbolRate`, `sat/modulation`, `sat/fec`, `sat/mode`, `tsLocked`, `bitrate`. `ber` est un entier à l'échelle 1e8 : BER = `ber` / 1e8, et 0 signifie « < 1e-8 » (relevé le 17/10/2026 dans le JavaScript de l'interface web du DR5000). La valeur brute est aussi stockée.

## 3. Décisions

- **Spectre** : le prototype relève les marqueurs du FSH4 (jusqu'à 6, niveau et fréquence). La lecture de trace et le spectrogramme viendront après la mise à jour du firmware ; Max n'a pas encore accès aux firmwares R&S (compte myRohde&Schwarz requis).
- **Architecture** : un seul service Python sur ce Mac, base SQLite et page web servie par le même processus.
- **Dépendances** : bibliothèque standard Python 3.12 uniquement. Seule exception, uPlot (≈ 60 Ko) copié dans le projet depuis cdnjs pour fonctionner hors ligne.
- **Lecture seule** : le service n'envoie aucune commande qui modifie un réglage du FSH4 ou de l'IRD. Le marqueur est placé sur la porteuse par l'opérateur.
- **Base de temps** : l'horloge du Mac (UTC). Les horloges des appareils sont ignorées.

## 4. Architecture

```
monitoring-sat/
  config.toml           adresses, cadences, seuils
  monitoring/
    fsh.py              client SCPI FSH4
    ird.py              client XML-RPC DR5000 + parseur
    store.py            SQLite (WAL)
    detect.py           détection d'événements
    collector.py        boucles de collecte (une par appareil, un thread chacune)
    server.py           point d'entrée : collecteurs + HTTP + flux direct
  web/
    index.html, app.js, style.css
    vendor/uPlot.iife.min.js, uPlot.min.css
  tests/
    fixtures/ird_refresh.xml
    test_*.py
  logs/                 journal (non versionné)
  data/                 base SQLite (non versionnée)
  monitoring.command    lancement par double-clic
```

### 4.1 Collecte

| Collecteur | Requêtes | Cadence | Délai max |
| --- | --- | --- | --- |
| FSH4 | pour chaque marqueur allumé (n = 1 à 6) : `CALC:MARKn?` puis `CALC:MARKn:Y?` seulement s'il est toujours allumé (en V1.20, lire `:X?` ou `:Y?` d'un marqueur éteint le rallume) ; au moins 200 ms de silence entre deux cycles (`min_idle_s`), sans quoi le FSH4 ne rafraîchit plus sa mesure ; connexion TCP persistante ; toutes les 10 s : `FREQ:SPAN?`, `BAND?`, `CALC:MARKn?` (allumé ou non) et `CALC:MARKn:X?` | 4 lectures/s visées | 2 s par requête |
| IRD 5 | `xml=<refresh/>` | 1 lecture/s | 3 s |

Chaque mesure est horodatée en UTC (millisecondes) au milieu de l'intervalle entre l'envoi de la requête et la fin de la réponse. Une boucle qui prend du retard ne rattrape pas : elle reprend à la cadence normale.

### 4.2 Stockage

Fichier `data/monitoring.db`, mode WAL, horodatages en millisecondes UTC (entier).

| Table | Colonnes |
| --- | --- |
| `fsh_marker` | `ts`, `freq_hz`, `level_dbm` |
| `ird_sat` | `ts`, `locked`, `ts_locked`, `cn_db`, `margin_db`, `ber`, `ber_raw`, `power_dbm`, `if_khz`, `symbol_rate`, `modcod`, `bitrate_kbps` |
| `device_status` | `ts`, `device`, `state` (`up` / `down`), `detail` |
| `events` | `id`, `ts_start`, `ts_end`, `kind`, `severity`, `detail` |

Index sur `ts` pour chaque table. Volume estimé : environ 30 Mo par jour. Purge facultative : `retention_days` dans `[paths]` (0 par défaut, tout est gardé) ; au démarrage puis toutes les heures, les mesures, états et événements terminés plus anciens sont supprimés.

### 4.3 Détection

Règles évaluées à chaque nouvelle mesure, seuils dans `config.toml` :

| Type | Condition de début | Fin |
| --- | --- | --- |
| `lock_lost` | `locked` passe à faux | `locked` revient à vrai |
| `ts_lock_lost` | `ts_locked` passe à faux | retour à vrai |
| `ber_high` | BER > seuil (défaut 1e-6) | BER sous le seuil pendant 5 s |
| `margin_drop` | médiane des 3 dernières s (`drop_confirm_s`) < médiane des 60 s précédentes − 2 dB ; début daté au début de ces 3 s | médiane des 3 dernières s revenue à moins de 1 dB de la référence |
| `marker_drop` | même logique, seuil 3 dB (un point bas isolé ne déclenche rien : avec une RBW étroite, le niveau fluctue de ±3 dB) | même logique |
| `device_down` | 3 échecs consécutifs d'un appareil (une réponse HTTP 503 de l'IRD, « occupé », n'est pas un échec tant qu'elle ne se répète pas plus de 10 fois de suite) | première réponse valide |
| `noise_rise` | moyenne des marqueurs `noise_markers` : médiane des 3 dernières s > médiane des 60 s précédentes + 3 dB | revenue à moins de 1 dB de la référence |
| `fsh_settings` (gravité `info`) | un marqueur s'allume, s'éteint ou change de fréquence, ou le span ou la RBW changent | immédiate ; ferme l'éventuelle `marker_drop` en cours (motif « réglage FSH4 modifié ») et repart d'une référence vide |

Pour les chutes et remontées (`margin_drop`, `marker_drop`, `noise_rise`), les mesures prises pendant une alerte et dans la minute qui suit sa fermeture sont écartées de la référence ; pendant cette minute, la référence reste celle d'avant l'alerte (essai de dépointage du 17/10/2026 : sans cela, un retour partiel faisait dériver la référence).

Un événement ouvert garde `ts_end` vide jusqu'à sa fin ; la page l'affiche « en cours ». Au redémarrage du service, les événements restés ouverts sont fermés à l'heure de la dernière mesure connue (jamais avant leur début).

## 5. Page web

Servie sur `http://localhost:8080`, usage sur ordinateur uniquement.

- **Bandeau d'état** : une pastille par appareil (vert/rouge, heure de la dernière mesure) et les valeurs courantes : lock, C/N, marge, BER, puissance, niveau et fréquence du marqueur. Bouton **Pause FSH** (ferme la connexion SCPI, ce qui sort le FSH4 du mode Remote ; **Reprendre** la rouvre).
- **Trois graphes uPlot empilés**, axe du temps commun, curseur et zoom synchronisés :
  1. niveau du marqueur FSH4 (dBm) ;
  2. C/N et marge IRD (dB) ;
  3. BER en échelle logarithmique, pertes de lock en bandes rouges.
- **Mode Direct** : fenêtre glissante (10 min par défaut, réglable), mise à jour chaque seconde par Server-Sent Events.
- **Mode Historique** : choix d'une plage de dates, zoom et déplacement. Au-delà de 5 000 points par série, le serveur agrège par intervalle (min, max, moyenne).
- **Liste des événements** à droite, la plus récente en haut. Un clic passe en mode Historique centré sur l'événement, avec 30 s avant et après (réglable).

### Points d'accès HTTP

| Méthode et chemin | Réponse |
| --- | --- |
| `GET /` et fichiers de `web/` | page et ressources statiques |
| `GET /api/status` | état des appareils et dernières valeurs |
| `GET /api/series?from=&to=&max_points=` | séries FSH et IRD sur la plage, agrégées si besoin |
| `GET /api/events?from=&to=` | événements de la plage |
| `GET /api/live` | flux SSE : une mesure ou un changement d'état par message |
| `POST /api/fsh/pause` et `/api/fsh/resume` | suspend / reprend la collecte FSH |

Le serveur n'écoute que sur `127.0.0.1`.

### Appareils et réseau variables (ajout du 17/10/2026)

Les adresses du FSH4 et de l'IRD changent d'une installation à l'autre. Bouton **Appareils…** de la page :

| Méthode et chemin | Rôle |
| --- | --- |
| `GET /api/devices` | adresses en cours (`fsh_host`, `fsh_port`, `ird_host`) |
| `POST /api/devices` (JSON) | valide, écrit dans `config.toml` (commentaires gardés), puis bascule les boucles de collecte à chaud ; événement `devices_changed` (info), alertes en cours fermées avec le motif « appareils changés » |
| `POST /api/discover` | parcourt les réseaux IPv4 du Mac (lus par `ifconfig`, hors boucle locale et lien local, chacun limité au /24 du Mac) : `*IDN?` sur TCP 5555 (R&S FSH uniquement), `xml=<status type="status"/>` puis `<refresh/>` sur HTTP 80 (fabricant Ateme) |

Les requêtes `POST` venant d'une autre origine que la page sont refusées (403) ; `POST /api/devices` exige `Content-Type: application/json` (415 sinon).

## 6. Robustesse

- Toute réponse illisible ou hors délai compte comme un échec, sans arrêter le service.
- Après 3 échecs consécutifs : appareil à l'état `down`, événement `device_down`, nouvel essai toutes les 5 s (reconnexion TCP pour le FSH4).
- Journal `logs/monitoring.log` en rotation (5 fichiers de 5 Mo) : démarrage, pertes et retours de connexion, erreurs.
- Arrêt propre sur Ctrl-C : fermeture de la connexion FSH4 (retour en mode Local), des événements ouverts et de la base.

## 7. Lancement

- `monitoring.command` : se place dans le dossier du projet et lance `caffeinate -i python3 -m monitoring.server`, pour empêcher la veille du Mac pendant l'enregistrement. On arrête en fermant la fenêtre Terminal ou avec Ctrl-C.
- `.claude/launch.json` : même commande, pour la vérification dans le navigateur intégré.

## 8. Tests

Avec `unittest`, sans dépendance.

- **`ird.py`** : analyse d'une vraie réponse `<refresh/>` de l'IRD 5, réduite au bloc `input` (fixture) ; cas « non verrouillé », champ manquant, XML invalide, `status='error'`.
- **`fsh.py`** : faux serveur SCPI local dans un thread ; réponses normales, délai dépassé, connexion coupée, `-113`.
- **`store.py` et `detect.py`** : séries fabriquées pour le test (chute de marge, perte de lock, BER qui monte, appareil muet) ; vérification des événements ouverts et fermés, et de l'agrégation des séries.
- **Serveur** : `/api/status`, `/api/series` et `/api/events` sur une base de test.
- **Essai réel** : 10 minutes contre le FSH4 et l'IRD 5. On vérifie les cadences obtenues (≥ 3,5 lectures/s FSH, ≥ 0,95 lecture/s IRD), l'absence de trous et l'affichage de la page dans le navigateur intégré.

## 9. Hors périmètre du prototype

Lecture de trace et spectrogramme (après mise à jour du firmware du FSH4), alertes par mail, plusieurs liaisons simultanées, FSH3, Titan Edge, déploiement sur un PC d'acquisition, accès à distance.

## 10. Évolution prévue : traces FSH4

Après la mise à jour du firmware : table `fsh_trace` (`ts`, `start_hz`, `stop_hz`, `rbw_hz`, `points` en BLOB de 631 float32), un collecteur de traces en plus de celui du marqueur, et un panneau spectrogramme synchronisé au-dessus des graphes. Les autres composants ne changent pas.
