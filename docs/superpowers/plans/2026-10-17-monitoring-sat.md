# Monitoring satellite FSH4 + IRD — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un service Python local qui enregistre en continu le marqueur du FSH4 et l'état satellite du DR5000 « IRD 5 » dans SQLite, détecte les dégradations et sert une page web direct + historique.

**Architecture:** Un processus `python3 -m monitoring.server` : deux threads de collecte (FSH4 en SCPI/TCP, IRD en XML-RPC/HTTP) qui écrivent dans SQLite et alimentent un détecteur d'événements ; un `ThreadingHTTPServer` sert une API JSON, un flux SSE et la page statique (uPlot).

**Tech Stack:** Python 3.12, bibliothèque standard uniquement (socket, urllib.request, xml.etree, sqlite3, http.server, threading, tomllib, logging) ; uPlot copié dans `web/vendor/` ; tests `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-17-monitoring-sat-design.md`

## Global Constraints

- Aucune dépendance hors bibliothèque standard Python 3.12, sauf `web/vendor/uPlot.iife.min.js` et `web/vendor/uPlot.min.css` (copiés depuis cdnjs, **avec accord de Max avant téléchargement**).
- Lecture seule sur les appareils : seules commandes FSH4 autorisées `CALC:MARK:X?`, `CALC:MARK:Y?` ; seule requête IRD autorisée `xml=<refresh/>`.
- FSH4 : `192.168.20.222`, TCP `5555`, terminaison `\n`, 4 lectures/s, délai 2 s. IRD 5 : `http://192.168.20.145/xml-rpc`, `POST`, `Content-Type: text/plain; charset=utf-8`, corps brut `xml=<refresh/>`, 1 lecture/s, délai 3 s.
- Horodatages : millisecondes UTC (entier), pris sur l'horloge du Mac au milieu de l'intervalle requête/réponse ; horloges des appareils ignorées.
- Serveur HTTP sur `127.0.0.1:8080` uniquement.
- Base `data/monitoring.db` (WAL) ; journal `logs/monitoring.log` en rotation, 5 fichiers de 5 Mo (`maxBytes=5*1024*1024`, `backupCount=4`).
- Après 3 échecs consécutifs : appareil `down`, nouvel essai toutes les 5 s.
- Textes de l'interface et du journal en français.
- Tests lancés depuis la racine du dépôt : `python3 -m unittest discover tests -v`.

## Review Focus

1. **Réponse FSH4 tardive** : une réponse qui arrive après le délai de 2 s ne doit pas être lue comme la réponse à la requête suivante ; toute erreur ferme la connexion et la suivante repart d'une socket neuve (test dans la tâche 3).
2. **IRD non verrouillé** : quand `locked` est faux, C/N, marge et BER n'ont pas de sens ; seul `lock_lost` doit se déclencher, pas `margin_drop` ni `ber_high` (test dans la tâche 5).
3. **Démarrage à vide** : sans au moins 10 s d'historique dans la fenêtre de médiane, aucune `margin_drop` ni `marker_drop` ne doit être ouverte (test dans la tâche 5).
4. **Redémarrage en plein événement** : un événement resté ouvert dans la base est fermé à l'heure de la dernière mesure connue au démarrage suivant (test dans la tâche 4, appel dans la tâche 7).
5. **Chemins statiques** : `GET /../config.toml` ou `/%2e%2e/config.toml` répond 404, jamais un fichier hors de `web/` (test dans la tâche 7).

---

### Task 1: Squelette du projet et configuration

**Files:**
- Create: `config.toml`, `monitoring/__init__.py`, `monitoring/config.py`, `tests/__init__.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: dataclasses gelées dans `monitoring/config.py`, valeurs par défaut ci-dessous ; `load_config(path: str | Path) -> Config` (tomllib ; une clé absente prend la valeur par défaut ; une section ou clé inconnue lève `ValueError`).
  - `FshConfig(host: str = "192.168.20.222", port: int = 5555, rate_hz: float = 4.0, timeout_s: float = 2.0)`
  - `IrdConfig(url: str = "http://192.168.20.145/xml-rpc", rate_hz: float = 1.0, timeout_s: float = 3.0)`
  - `DetectConfig(ber_threshold: float = 1e-6, ber_clear_s: float = 5.0, margin_drop_db: float = 2.0, margin_recover_db: float = 1.0, marker_drop_db: float = 3.0, marker_recover_db: float = 1.0, median_window_s: float = 60.0, min_history_s: float = 10.0, down_after_failures: int = 3, retry_s: float = 5.0)`
  - `ServerConfig(host: str = "127.0.0.1", port: int = 8080, live_window_min: int = 10, event_padding_s: int = 30, max_points: int = 5000)`
  - `PathsConfig(db: str = "data/monitoring.db", log_dir: str = "logs")`
  - `Config(fsh, ird, detect, server, paths)` ; sections TOML `[fsh] [ird] [detect] [server] [paths]`.

- [ ] **Step 1: Write the failing tests** — `test_defaults_when_file_has_only_comments` (fichier avec un commentaire → `Config()` par défaut, `cfg.fsh.port == 5555`, `cfg.detect.ber_threshold == 1e-6`, `cfg.server.host == "127.0.0.1"`) ; `test_override_one_key` (`[fsh]\nrate_hz = 2.0` → `cfg.fsh.rate_hz == 2.0`, `cfg.fsh.host == "192.168.20.222"`) ; `test_unknown_key_raises` (`[fsh]\nhots = "x"` → `ValueError`) ; `test_repo_config_loads` (`load_config("config.toml")` ne lève pas).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_config -v` — Expected: FAIL (`ModuleNotFoundError: monitoring.config`).
- [ ] **Step 3: Implement** `monitoring/config.py` et `config.toml` (toutes les clés écrites avec leurs valeurs par défaut, commentées en français).
- [ ] **Step 4: Run** `python3 -m unittest tests.test_config -v` — Expected: 4 tests OK.
- [ ] **Step 5: Commit** `git add config.toml monitoring tests && git commit -m "Squelette et configuration"`

### Task 2: Client et parseur IRD

**Files:**
- Create: `monitoring/ird.py`, `tests/fixtures/ird_refresh.xml`
- Test: `tests/test_ird.py`

**Interfaces:**
- Consumes: `IrdConfig` (tâche 1).
- Produces:
  - `class IrdError(Exception)`
  - `@dataclass(frozen=True) IrdSample(locked: bool, ts_locked: bool, cn_db: float | None, margin_db: float | None, ber: float | None, ber_raw: str | None, power_dbm: float | None, if_khz: int | None, symbol_rate: int | None, modcod: str | None, bitrate_kbps: int | None)`
  - `parse_refresh(data: bytes) -> IrdSample` — lit `response/probe/input` : `sat/locked`, `tsLocked`, `sat/snr`/10, `sat/snrMargin`/10, `sat/ber` (brut dans `ber_raw`, `float(raw)` dans `ber`), `sat/power`, `sat/frequency`, `sat/symbolRate`, `bitrate` ; `modcod = f"{mode} {modulation} {fec}"` avec `_` → `/` (ex. `"dvbs2 qpsk 5/6"`). Champ absent → `None`. XML invalide, `status` ≠ `ok` ou `probe/input` absent → `IrdError`.
  - `class IrdClient(cfg: IrdConfig)` avec `fetch() -> IrdSample` (`urllib.request`, en-têtes et corps des contraintes globales ; erreur réseau, délai ou HTTP ≠ 200 → `IrdError`).

- [ ] **Step 1: Capture the fixture** — depuis la racine du dépôt :

```bash
curl -s -m 5 -H 'Content-Type: text/plain; charset=utf-8' --data-binary 'xml=<refresh/>' http://192.168.20.145/xml-rpc > /tmp/refresh.xml
python3 -c "
import xml.etree.ElementTree as ET
r=ET.parse('/tmp/refresh.xml').getroot(); i=r.find('probe/input')
out=ET.Element('response',r.attrib); p=ET.SubElement(out,'probe'); p.append(i)
ET.ElementTree(out).write('tests/fixtures/ird_refresh.xml',encoding='utf-8')"
```

Expected: fichier de quelques Ko contenant `<sat>` et `<tsLocked>`. Noter dans le test les valeurs réelles capturées (au 17/10 : snr 157, snrMargin 105, power −38, frequency 1470806, symbolRate 24760000, qpsk 5_6 dvbs2).

- [ ] **Step 2: Write the failing tests** — `test_parse_fixture` (valeurs de la fixture : `locked is True`, `cn_db == 15.7` à 0,05 près, `margin_db == 10.5`, `power_dbm == -38`, `if_khz == 1470806`, `symbol_rate == 24760000`, `modcod == "dvbs2 qpsk 5/6"`) ; `test_unlocked` (fixture avec `<locked>false</locked>` → `locked is False`) ; `test_missing_field_is_none` (sans `<snrMargin>` → `margin_db is None`) ; `test_invalid_xml_raises` (`b"<resp"` → `IrdError`) ; `test_error_status_raises` (`<response status='error'><message>Unknown command</message></response>` → `IrdError`) ; `test_fetch_against_local_http` (petit `HTTPServer` local qui vérifie que le corps reçu vaut exactement `b'xml=<refresh/>'` et renvoie la fixture → `fetch()` renvoie un `IrdSample`) ; `test_fetch_timeout_raises` (serveur local qui dort 0,5 s, `timeout_s=0.1` → `IrdError`).
- [ ] **Step 3: Run** `python3 -m unittest tests.test_ird -v` — Expected: FAIL (module absent).
- [ ] **Step 4: Implement** `monitoring/ird.py`.
- [ ] **Step 5: Run** `python3 -m unittest tests.test_ird -v` — Expected: 7 tests OK.
- [ ] **Step 6: Commit** `git add monitoring/ird.py tests && git commit -m "Client XML-RPC du DR5000"`

### Task 3: Client SCPI FSH4

**Files:**
- Create: `monitoring/fsh.py`, `tests/fake_scpi.py`
- Test: `tests/test_fsh.py`

**Interfaces:**
- Consumes: `FshConfig` (tâche 1).
- Produces:
  - `class FshError(Exception)`
  - `@dataclass(frozen=True) MarkerSample(freq_hz: float, level_dbm: float)`
  - `class FshClient(cfg: FshConfig)` : `connect() -> None`, `close() -> None` (idempotent), `query(cmd: str) -> str` (connexion paresseuse ; envoie `cmd + "\n"`, lit jusqu'à `\n`, renvoie sans espaces), `read_marker() -> MarkerSample` (`CALC:MARK:X?` puis `CALC:MARK:Y?`, `float()` sur chaque réponse ; ex. `"11.217190476e+9"` → `11217190476.0`). Tout délai dépassé, connexion fermée par l'appareil ou réponse non numérique → `close()` puis `FshError`.
  - `tests/fake_scpi.py` : `FakeScpi(responses: dict[str, str | None], delay_s: float = 0.0)` serveur TCP local dans un thread (`.port`, `.stop()`, `.received: list[str]`) ; `None` = ne jamais répondre (comme le `-113` du FSH4) ; `delay_s` retarde chaque réponse.

- [ ] **Step 1: Write the failing tests** — `test_read_marker` (réponses `"11.217190476e+9"` et `"-40.764"` → `MarkerSample(11217190476.0, -40.764)`, `received == ["CALC:MARK:X?", "CALC:MARK:Y?"]`) ; `test_no_answer_raises` (`CALC:MARK:Y?` → `None`, `timeout_s=0.2` → `FshError`) ; `test_non_numeric_raises` (`"abc"` → `FshError`) ; `test_peer_closed_raises` (serveur arrêté après connexion → `FshError`) ; `test_late_reply_not_reused` (*Review Focus 1* : `delay_s=0.3`, `timeout_s=0.2` → premier `read_marker()` lève `FshError` ; on remet `delay_s=0` ; le second `read_marker()` renvoie les bonnes valeurs et le faux serveur a vu **deux** connexions) ; `test_reconnects_after_error`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_fsh -v` — Expected: FAIL (module absent).
- [ ] **Step 3: Implement** `monitoring/fsh.py` et `tests/fake_scpi.py` (`socket.create_connection` avec `timeout=cfg.timeout_s` ; lecture par `recv` accumulé jusqu'à `\n`).
- [ ] **Step 4: Run** `python3 -m unittest tests.test_fsh -v` — Expected: 6 tests OK.
- [ ] **Step 5: Commit** `git add monitoring/fsh.py tests && git commit -m "Client SCPI du FSH4 (marqueur)"`

### Task 4: Stockage SQLite

**Files:**
- Create: `monitoring/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `MarkerSample` (tâche 3), `IrdSample` (tâche 2).
- Produces: `class Store(path: str | Path)` — crée le dossier parent et les tables du §4.2 de la spec (avec index sur `ts` / `ts_start`), `PRAGMA journal_mode=WAL`, une connexion `check_same_thread=False` protégée par un `threading.Lock`.
  - `add_marker(ts: int, s: MarkerSample) -> None`
  - `add_ird(ts: int, s: IrdSample) -> None` (`modcod` en texte, booléens en 0/1)
  - `add_status(ts: int, device: str, state: str, detail: str) -> None`
  - `open_event(ts: int, kind: str, severity: str, detail: str) -> int` ; `close_event(event_id: int, ts_end: int) -> None`
  - `close_open_events(ts_end: int) -> int` (ferme tout `ts_end IS NULL`, renvoie le nombre)
  - `last_ts() -> int | None` (max des `ts` de `fsh_marker` et `ird_sat`)
  - `series(table: str, columns: list[str], from_ts: int, to_ts: int, max_points: int) -> dict` — `table` et `columns` vérifiés contre une liste blanche (sinon `ValueError`). Si le nombre de lignes ≤ `max_points` : `{"aggregated": False, "ts": [...], col: [...]}`. Sinon intervalles de largeur `ceil((to_ts - from_ts) / max_points)` ms : `{"aggregated": True, "ts": [début d'intervalle], col: [moyenne], col + "_min": [...], col + "_max": [...]}` ; pour `locked` et `ts_locked`, l'agrégat est le **minimum** (une perte de lock ne doit pas disparaître dans la moyenne).
  - `events(from_ts: int, to_ts: int) -> list[dict]` — événements qui chevauchent la plage (`ts_start <= to_ts AND (ts_end IS NULL OR ts_end >= from_ts)`), clés `id, ts_start, ts_end, kind, severity, detail`, du plus récent au plus ancien.
  - `latest() -> dict` — `{"marker": dict | None, "ird": dict | None, "devices": {device: {"state", "ts", "detail"}}}`.
  - `close() -> None`

- [ ] **Step 1: Write the failing tests** (base dans un `tempfile.TemporaryDirectory`) — `test_roundtrip_raw_series` (3 marqueurs → `series("fsh_marker", ["level_dbm"], …, 5000)` renvoie 3 points, `aggregated is False`) ; `test_aggregation` (10 000 lignes IRD, `max_points=100` → au plus 100 points, `aggregated is True`, `margin_db_min ≤ margin_db ≤ margin_db_max` pour chaque point) ; `test_lock_aggregates_to_min` (un seul `locked=0` dans un intervalle → l'intervalle vaut 0) ; `test_series_rejects_unknown_column` (`"level_dbm; DROP"` → `ValueError`) ; `test_events_overlap` (événement 100–200 trouvé par la plage 150–300, absent de 250–300 ; événement ouvert à 100 trouvé par 500–600) ; `test_close_open_events_on_restart` (*Review Focus 4* : ouvrir un événement, fermer la base, rouvrir `Store`, `close_open_events(last_ts())` renvoie 1 et `ts_end == last_ts()`) ; `test_latest`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_store -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `monitoring/store.py` (agrégation en SQL : `GROUP BY (ts - from_ts) / bucket`).
- [ ] **Step 4: Run** `python3 -m unittest tests.test_store -v` — Expected: 7 tests OK.
- [ ] **Step 5: Commit** `git add monitoring/store.py tests/test_store.py && git commit -m "Stockage SQLite"`

### Task 5: Détection d'événements

**Files:**
- Create: `monitoring/detect.py`
- Test: `tests/test_detect.py`

**Interfaces:**
- Consumes: `Store` (tâche 4), `DetectConfig` (tâche 1), `IrdSample`, `MarkerSample`.
- Produces: `class Detector(store: Store, cfg: DetectConfig)` avec `on_ird(ts: int, s: IrdSample) -> list[dict]`, `on_marker(ts: int, s: MarkerSample) -> list[dict]`, `on_state(ts: int, device: str, state: str, detail: str) -> list[dict]`. Chaque méthode renvoie les événements ouverts ou fermés par cette mesure, sous la forme de `Store.events()` (`ts_end` à `None` si ouvert).
  - Règles du §4.3 de la spec. Gravité : `lock_lost`, `ts_lock_lost`, `device_down` → `"critical"` ; `ber_high`, `margin_drop`, `marker_drop` → `"warning"`. `device_down` porte le nom de l'appareil dans `detail` (`"fsh"` ou `"ird"`) et un seul peut être ouvert par appareil.
  - Médianes : fenêtre glissante de `median_window_s` (deque `(ts, valeur)`, valeurs `None` ignorées). Pas de `margin_drop` / `marker_drop` tant que la fenêtre couvre moins de `min_history_s`. La médiane de référence est figée à l'ouverture de l'événement ; la fin vient quand la valeur remonte à moins de `margin_recover_db` (ou `marker_recover_db`) de cette référence.
  - Quand `locked` est faux : seul `lock_lost` est évalué ; `margin_drop` et `ber_high` ouverts restent ouverts, et les valeurs ne sont pas ajoutées à la fenêtre.
  - `ber_high` se ferme quand le BER reste sous `ber_threshold` pendant `ber_clear_s`.
  - `detail` en français, ex. `"marge 7,9 dB (référence 10,5 dB)"`.

- [ ] **Step 1: Write the failing tests** (séries fabriquées à 1 mesure/s, `Store` temporaire) — `test_lock_lost_open_close` ; `test_margin_drop` (60 s à 10,5 dB puis 8,0 dB → un `margin_drop` ouvert ; retour à 9,6 dB → fermé ; retour à 9,4 dB seulement → reste ouvert) ; `test_no_drop_before_min_history` (*Review Focus 3* : 5 s à 10,5 puis 5,0 → aucun événement) ; `test_unlocked_only_lock_lost` (*Review Focus 2* : 60 s stables puis `locked=False` avec `margin_db=0.0`, `ber=0.5` → exactement un événement `lock_lost`) ; `test_ber_high_needs_5s_clear` (BER 1e-5 → ouvert ; 4 s à 0 → encore ouvert ; 5ᵉ s → fermé) ; `test_marker_drop` (−40,8 dBm pendant 60 s puis −44,0 → ouvert) ; `test_device_down_once` (deux `on_state(..., "fsh", "down", ...)` → un seul événement ; `"up"` → fermé).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_detect -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `monitoring/detect.py` (`statistics.median`).
- [ ] **Step 4: Run** `python3 -m unittest tests.test_detect -v` — Expected: 7 tests OK.
- [ ] **Step 5: Commit** `git add monitoring/detect.py tests/test_detect.py && git commit -m "Détection d'événements"`

### Task 6: Boucles de collecte

**Files:**
- Create: `monitoring/collector.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: `DetectConfig.down_after_failures`, `DetectConfig.retry_s`.
- Produces: `class DeviceLoop(threading.Thread)` (daemon) :
  - `__init__(self, device: str, period_s: float, read: Callable[[], object], on_sample: Callable[[int, object], None], on_state: Callable[[int, str, str], None], down_after: int, retry_s: float, on_pause: Callable[[], None] | None = None, clock: Callable[[], float] = time.time)`
  - `step() -> float` : une lecture ; horodatage `int((t0 + t1) / 2 * 1000)` ; en cas de succès `on_sample(ts, valeur)` et, si l'état n'était pas `"up"`, `on_state(ts, "up", "")` ; en cas d'exception, compteur d'échecs +1 et, au `down_after`-ième échec consécutif, `on_state(ts, "down", str(exc))` (une seule fois). Renvoie l'attente avant la prochaine lecture : `retry_s` si l'appareil est `down`, sinon `max(period_s - (t1 - t0), 0)` (pas de rattrapage).
  - `run()` : boucle `step()` puis `self._stop.wait(attente)` ; en pause, attend sans lire.
  - `pause()` : met en pause, appelle `on_pause` (fermeture de la socket FSH4) et émet `on_state(ts, "paused", "")` ; `resume()` : reprend, prochain succès → `"up"` ; `stop()`.
  - `state: str` (`"unknown"`, `"up"`, `"down"`, `"paused"`).

- [ ] **Step 1: Write the failing tests** (horloge factice, `read` factices, sans démarrer le thread sauf pour le dernier test) — `test_midpoint_timestamp` (horloge 10,0 puis 10,2 → `ts == 10100`) ; `test_wait_no_catch_up` (lecture de 0,3 s, période 0,25 → attente 0) ; `test_down_after_three_failures` (3 exceptions → un seul `on_state("down")`, attente `retry_s` ; un 4ᵉ échec n'émet rien) ; `test_up_after_down` ; `test_pause_calls_on_pause_and_stops_reading` ; `test_thread_stops` (`start()`, `stop()`, `join(1)` → thread terminé).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_collector -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `monitoring/collector.py`.
- [ ] **Step 4: Run** `python3 -m unittest tests.test_collector -v` — Expected: 6 tests OK.
- [ ] **Step 5: Commit** `git add monitoring/collector.py tests/test_collector.py && git commit -m "Boucles de collecte"`

### Task 7: Serveur HTTP, flux direct et point d'entrée

**Files:**
- Create: `monitoring/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces:
  - `class LiveBus` : `subscribe() -> queue.Queue`, `unsubscribe(q) -> None`, `publish(msg: dict) -> None` (file bornée à 1 000 messages par abonné ; un abonné plein perd ses plus anciens messages plutôt que de bloquer la collecte).
  - Messages SSE (`data: <json>\n\n`) : `{"type": "marker", "ts", "freq_hz", "level_dbm"}`, `{"type": "ird", "ts", …champs IrdSample}`, `{"type": "state", "ts", "device", "state", "detail"}`, `{"type": "event", …dict d'événement}` ; commentaire `: ping` toutes les 15 s.
  - `class App(cfg: Config, fsh_read=None, ird_read=None)` — construit `Store`, `Detector`, `LiveBus` et les deux `DeviceLoop` (`device="fsh"` et `"ird"`). Les paramètres `*_read` permettent aux tests d'injecter de fausses lectures ; par défaut `FshClient(cfg.fsh).read_marker` et `IrdClient(cfg.ird).fetch`. Callbacks : stockage, puis détecteur, puis publication. `start()` appelle d'abord `store.close_open_events(store.last_ts())` si `last_ts()` n'est pas `None` (*Review Focus 4*). `stop()` arrête les boucles, ferme la socket FSH4, ferme les événements ouverts et la base.
  - `make_server(app: App) -> ThreadingHTTPServer` sur `cfg.server.host:port`, avec les routes du §5 de la spec :
    - `/api/status` → `store.latest()` plus l'état des boucles et `config` (`live_window_min`, `event_padding_s`, `max_points`) ;
    - `/api/series?from=&to=&max_points=` → `{"fsh": series(...), "ird": series(...)}` (FSH : `freq_hz, level_dbm` ; IRD : `locked, ts_locked, cn_db, margin_db, ber, power_dbm`) ; paramètres absents, non entiers ou `from > to` → 400 ; `max_points` plafonné à `cfg.server.max_points` ;
    - `/api/events?from=&to=` ;
    - `/api/live` ;
    - `POST /api/fsh/pause` et `/api/fsh/resume` → 204 ;
    - fichiers statiques de `web/` : chemin décodé, `Path.resolve()` qui doit rester sous `web/`, sinon 404 (*Review Focus 5*).
  - `main(argv: list[str] | None = None) -> None` : `--config config.toml` ; logging vers la console et un `RotatingFileHandler` (`logs/monitoring.log`, 5 Mo, `backupCount=4`) ; journalise le démarrage, chaque changement d'état d'appareil et chaque événement ; `App.start()` ; `serve_forever()` ; `KeyboardInterrupt` → `App.stop()` puis message « Arrêt propre ». Exécutable par `python3 -m monitoring.server`.

- [ ] **Step 1: Write the failing tests** (config temporaire avec port `0`, base temporaire, fausses lectures) — `test_status_after_samples` ; `test_series_bad_params_400` (`from` absent, `from=abc`, `from=10&to=5` → 400) ; `test_series_returns_both` ; `test_events_endpoint` ; `test_static_index` (crée `web/index.html` temporaire ou utilise un dossier `web` injecté) ; `test_static_traversal_404` (`/../config.toml` et `/%2e%2e/config.toml` → 404) ; `test_live_receives_marker` (ouvre `/api/live`, publie un marqueur, lit une ligne `data:` dont le JSON a `type == "marker"`) ; `test_pause_resume` (`POST /api/fsh/pause` → 204 et la boucle FSH passe à `"paused"`) ; `test_start_closes_stale_events`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_server -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `monitoring/server.py` (`App` accepte un argument `web_dir: Path` facultatif, par défaut `<racine>/web`, pour les tests).
- [ ] **Step 4: Run** `python3 -m unittest discover tests -v` — Expected: tous les tests OK.
- [ ] **Step 5: Commit** `git add monitoring/server.py tests/test_server.py && git commit -m "Serveur HTTP, flux direct et point d'entrée"`

### Task 8: Page web

**Files:**
- Create: `web/index.html`, `web/app.js`, `web/style.css`, `web/vendor/uPlot.iife.min.js`, `web/vendor/uPlot.min.css`

**Interfaces:**
- Consumes: routes et messages SSE de la tâche 7.

- [ ] **Step 1: Ask Max before downloading** — `uPlot.iife.min.js` et `uPlot.min.css` (version 1.6.x la plus récente sur cdnjs, ≈ 60 Ko au total) depuis `https://cdnjs.cloudflare.com/ajax/libs/uPlot/<version>/`, vers `web/vendor/`. Ne télécharger qu'après son accord.
- [ ] **Step 2: Implement the page** selon le §5 de la spec :
  - bandeau d'état : pastilles `fsh` / `ird` (vert `up`, rouge `down`, gris `paused`/`unknown`, heure de la dernière mesure), valeurs courantes, bouton **Pause FSH** / **Reprendre** ;
  - trois graphes uPlot empilés, `cursor.sync.key` commun et zoom en X propagé aux deux autres : (1) `level_dbm` ; (2) `cn_db` et `margin_db` ; (3) `ber` en `distr: 3` (log) avec un plancher à 1e-9 (affiché « < 1e-8 » dans la légende), et fond rouge (hook `drawClear`) sur les intervalles `locked == 0` ;
  - mode **Direct** : `/api/series` sur les `live_window_min` dernières minutes, puis `EventSource("/api/live")` qui ajoute les points et retire ceux sortis de la fenêtre ; reconnexion automatique du navigateur ;
  - mode **Historique** : deux champs `datetime-local` (heure locale du Mac), bouton **Charger** ; un zoom recharge `/api/series` sur la plage zoomée pour retrouver le détail ;
  - liste des événements (via `/api/events`, puis messages `event` en direct), la plus récente en haut, gravité en couleur ; un clic passe en Historique sur `[ts_start - event_padding_s, (ts_end ?? maintenant) + event_padding_s]` ;
  - mise en page pour ordinateur (largeur mini 1 024 px), thème clair.
- [ ] **Step 3: Verify in the built-in browser** — ajouter `.claude/launch.json` (`"name": "monitoring"`, `"runtimeExecutable": "python3"`, `"runtimeArgs": ["-m", "monitoring.server"]`, `"port": 8080`), `preview_start` ; vérifier : aucune erreur console, pastilles vertes, trois graphes qui avancent en direct, BER à 0 affiché sans casser l'échelle log, bouton Pause qui passe la pastille FSH en gris et sort le FSH4 du mode Remote, clic sur un événement qui centre l'historique. Capture d'écran pour Max.
- [ ] **Step 4: Commit** `git add web .claude/launch.json && git commit -m "Page web direct et historique"`

### Task 9: Lancement et essai réel

**Files:**
- Create: `monitoring.command`, `README.md`

- [ ] **Step 1: Write `monitoring.command`** — `#!/bin/bash`, `cd "$(dirname "$0")"`, `exec caffeinate -i python3 -m monitoring.server` ; `chmod +x monitoring.command`.
- [ ] **Step 2: Write `README.md`** en français : à quoi sert l'outil, lancement par double-clic, adresse de la page, bouton Pause FSH (mode Remote), emplacement de la base et du journal, limite actuelle (marqueur seul tant que le firmware du FSH4 n'est pas à jour).
- [ ] **Step 3: Essai réel de 10 minutes** — lancer `./monitoring.command`, attendre 10 min, puis :

```bash
python3 - <<'EOF'
import sqlite3; c=sqlite3.connect("data/monitoring.db")
for t in ("fsh_marker","ird_sat"):
    n,a,b=c.execute(f"select count(*),min(ts),max(ts) from {t}").fetchone()
    gap=c.execute(f"select max(ts-lag) from (select ts, lag(ts) over (order by ts) lag from {t})").fetchone()[0]
    print(t, n, "mesures", round(n/((b-a)/1000),2), "/s", "plus grand trou", gap, "ms")
EOF
```

Expected : `fsh_marker` ≥ 3,5 /s et `ird_sat` ≥ 0,95 /s ; plus grand trou < 2 000 ms côté FSH et < 3 000 ms côté IRD. Vérifier aussi `logs/monitoring.log` (pas d'erreur répétée) et la page (capture d'écran).
- [ ] **Step 4: Commit** `git add monitoring.command README.md && git commit -m "Lancement et README"`
