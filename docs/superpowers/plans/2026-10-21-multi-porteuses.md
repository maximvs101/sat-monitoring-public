# Plusieurs porteuses et plusieurs IRD — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Suivre plusieurs IRD (identifiés par numéro de série) regroupés en porteuses automatiques ou manuelles, rattacher les marqueurs du FSH4 aux porteuses, alerter « IRD seul en défaut », et afficher une section par porteuse.

**Architecture:** Une boucle de collecte par IRD ; chaque lecture enregistre fréquence RF, polarisation et débit symbole, et l'appartenance aux porteuses se recalcule à la demande dans un module pur `carriers.py`. `App` quitte `server.py` pour `app.py` ; `server.py` ne garde que le HTTP. La page est découpée en trois scripts.

**Tech Stack:** Python 3.12 bibliothèque standard, SQLite, uPlot (déjà dans `web/vendor/`), tests `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-21-multi-porteuses-design.md` (prolonge `docs/superpowers/specs/2026-10-17-monitoring-sat-design.md`).

## Global Constraints

- Bibliothèque standard Python 3.12 uniquement ; aucune nouvelle dépendance, ni Python ni JavaScript.
- Lecture seule : aucune commande qui modifie le FSH4 ou un IRD. Requêtes IRD autorisées : `xml=<refresh/>`, `xml=<status type="status"/>`.
- Un IRD est identifié par son numéro de série (`<status/>` → `system/id`, ex. `1000-00001`), jamais par son adresse ni son nom.
- Fréquence RF d'un IRD : `IF + oscillatorFrequency` quand verrouillé et IF > 0, sinon `downlinkFrequency` ; champs lus dans `presets/v1[@id=0]/channel/input/sat` (`downlinkFrequency`, `oscillatorFrequency`, `lnb/polarization`) et `probe/input/sat` (`frequency`, `symbolRate`, `rollOff`).
- Même porteuse : |ΔRF| < min(débit symbole) / 2 et polarisations compatibles (inconnue compatible avec tout). Bande : centre ± débit symbole × (1 + roll-off) / 2, roll-off 0,20 si inconnu.
- Marqueurs sous 3 GHz : avertissement « Décalage LNB non réglé sur le FSH4 : les marqueurs sont en fréquence intermédiaire », aucun rattachement, pas d'alerte de bruit.
- Une boucle par IRD, 1 lecture/s ; doit rester fluide avec 8 IRD.
- Textes de l'interface, du journal et des événements en français.
- Tests : `python3 -m unittest discover tests -v` depuis la racine du dépôt.

## Review Focus

1. **Autre IRD à la même adresse** (appareil remplacé ou adresse réattribuée) : aucune mesure enregistrée sous le mauvais numéro de série ; état `down` avec « autre appareil à cette adresse (n° …) » (tâche 2, `test_reader_rejects_other_serial`).
2. **IRD déverrouillé** : son IF tombe à 0, il doit rester sur sa porteuse via `downlinkFrequency`, sinon « IRD seul en défaut » ne se déclenche jamais (tâche 2, `test_rf_falls_back_to_downlink_when_unlocked` ; tâche 5, `test_alone_when_unlocked_ird_keeps_its_carrier`).
3. **Marqueurs en fréquence intermédiaire** (décalage LNB non réglé sur un nouveau site) : aucun rattachement, aucune alerte de bruit, avertissement (tâche 3, `test_if_markers_warn_and_attach_nothing`).
4. **Retrait d'un IRD avec des alertes ouvertes** : alertes fermées avec le motif « IRD retiré », plus de lecture (tâche 6, `test_remove_ird_closes_its_alerts`).
5. **Migration de la base réelle** : deux IRD, deux porteuses (11 220,8 et 12 583,0 MHz), rien de perdu, idempotente (tâche 4, `test_migration_splits_history_by_device_change` et `test_migration_is_idempotent`).

---

### Task 1: Configuration — IRD, porteuses, entrée du FSH4

**Files:**
- Modify: `monitoring/config.py`, `config.toml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) IrdEntry(serial: str, name: str, host: str)` (`serial` vide = pas encore identifié).
  - `@dataclass(frozen=True) CarrierEntry(name: str, freq_mhz: float, polarization: str | None, symbol_rate_msps: float | None, manual: bool)` : `manual=False` sert seulement à mémoriser le nom d'une porteuse automatique.
  - `FshConfig.polarization: str | None = None` (`"vertical"`, `"horizontal"` ou `None`).
  - `Config.irds: tuple[IrdEntry, ...]` et `Config.carriers: tuple[CarrierEntry, ...]` (TOML `[[ird]]`, `[[carrier]]`). `IrdConfig` garde `rate_hz` et `timeout_s` ; sa clé `url` est retirée.
  - Ancien format : une section `[ird]` avec `url` et sans `[[ird]]` donne `irds = (IrdEntry("", "IRD", <hôte de l'url>),)`.
  - `save_fsh(path, host: str, port: int, polarization: str | None) -> None`, `save_irds(path, irds: tuple[IrdEntry, ...]) -> None`, `save_carriers(path, carriers: tuple[CarrierEntry, ...]) -> None`. Les blocs `[[ird]]` / `[[carrier]]` sont réécrits en fin de fichier ; le reste du fichier, commentaires compris, est gardé ; une clé `url` restante dans `[ird]` est supprimée par `save_irds`. Écriture atomique (fichier `.tmp` puis renommage), comme `save_devices`.
  - `save_devices` est supprimé.

- [ ] **Step 1: Write the failing tests** :
  - `test_irds_and_carriers_roundtrip` : `save_irds` avec `(IrdEntry("1000-00001", "IRD 1", "192.168.10.141"),)` et `save_carriers` avec `(CarrierEntry("BBC", 11220.8, "vertical", 24.76, True),)`, puis `load_config` → valeurs identiques, commentaire `# analyseur R&S FSH4` toujours présent ;
  - `test_legacy_ird_url_becomes_one_ird` : `[ird]\nurl = "http://192.168.10.141/xml-rpc"` → `cfg.irds == (IrdEntry("", "IRD", "192.168.10.141"),)` ;
  - `test_fsh_polarization` : `save_fsh(p, "192.168.10.224", 5555, "vertical")` → `load_config(p).fsh.polarization == "vertical"` ; une valeur `"oblique"` → `ValueError` ;
  - `test_noise_markers_emptied_in_repo_config` : `load_config("config.toml").detect.noise_markers == ()` ;
  - adapter `test_repo_config_loads` à la nouvelle forme du fichier.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_config -v` — Expected: FAIL (`IrdEntry` absent).
- [ ] **Step 3: Implement** dans `monitoring/config.py` ; mettre `config.toml` au nouveau format avec les adresses actuelles (FSH4 192.168.10.224, polarisation non renseignée ; un `[[ird]]` 192.168.10.141, `serial` vide, `name = "IRD 1"`), et `noise_markers = []`.
- [ ] **Step 4: Run** `python3 -m unittest tests.test_config -v` — Expected: OK. Les autres modules ne compilent plus (`IrdConfig.url`) : c'est attendu jusqu'à la tâche 6.
- [ ] **Step 5: Commit** `git add monitoring/config.py config.toml tests/test_config.py && git commit -m "Configuration : liste d'IRD, porteuses, entrée du FSH4"`

### Task 2: IRD — fréquence RF, polarisation, identité

**Files:**
- Modify: `monitoring/ird.py`, `monitoring/discover.py`
- Create: `tests/fixtures/ird_refresh_presets.xml`
- Test: `tests/test_ird.py`, `tests/test_discover.py`

**Interfaces:**
- Consumes: `IrdEntry`, `IrdConfig` (tâche 1).
- Produces:
  - `IrdSample` gagne `rf_khz: int | None`, `polarization: str | None`, `roll_off: float | None` (`"0_25"` → 0.25).
  - `parse_refresh(data: bytes) -> IrdSample` remplit ces champs selon la règle des contraintes globales.
  - `parse_status(data: bytes) -> tuple[str, str]` → `(serial, name)` ; `IrdError` si `vendorName` n'est pas `Ateme` ou si `system/id` manque.
  - `class IrdClient(host: str, cfg: IrdConfig)` : `fetch() -> IrdSample`, `identify() -> tuple[str, str]` (POST `xml=<status type="status"/>`).
  - `class IrdReader(client: IrdClient, expected_serial: str)` : `read() -> IrdSample`. Il vérifie le numéro de série à la première lecture et après chaque échec ; si le numéro diffère, il lève `IrdError("autre appareil à cette adresse (n° <lu>)")` et n'appelle pas `fetch`.
  - `probe_ird(...)` renvoie en plus `"serial"`.

- [ ] **Step 1: Capture the fixture** : `POST xml=<refresh/>` sur `192.168.10.141`, réduit à `response/presets/v1[@id=0]/channel/input/sat` et `response/probe/input` (même méthode que `ird_refresh.xml`). Valeurs du 21/10 attendues : `downlinkFrequency` 12583060, `oscillatorFrequency` 10600000, polarisation `vertical`, IF 1983023, 7200000 S/s, `0_25`.
- [ ] **Step 2: Write the failing tests** :
  - `test_rf_from_if_plus_lo` : fixture → `rf_khz == 12583023`, `polarization == "vertical"`, `roll_off == 0.25` ;
  - `test_rf_falls_back_to_downlink_when_unlocked` (*Review Focus 2*) : `locked` faux et `frequency` 0 → `rf_khz == 12583060` ;
  - `test_old_fixture_has_no_rf` : `ird_refresh.xml` (sans presets) → `rf_khz is None`, `polarization is None` ;
  - `test_parse_status` : `(serial, name) == ("1000-00001", "IRD 1")`, et `IrdError` si le fabricant n'est pas Ateme ;
  - `test_reader_rejects_other_serial` (*Review Focus 1*) : faux serveur qui annonce `1000-00002` alors qu'on attend `1000-00001` → `IrdError` dont le message contient « autre appareil » ; le faux serveur n'a reçu aucun `<refresh/>` ;
  - `test_reader_rechecks_after_failure` : une lecture réussie, puis un échec, puis le serveur annonce un autre numéro → la lecture suivante lève `IrdError` ;
  - dans `test_discover.py`, `test_probe_ird` vérifie `found["serial"]`.
- [ ] **Step 3: Run** `python3 -m unittest tests.test_ird tests.test_discover -v` — Expected: FAIL.
- [ ] **Step 4: Implement** dans `monitoring/ird.py` et `monitoring/discover.py`.
- [ ] **Step 5: Run** `python3 -m unittest tests.test_ird tests.test_discover -v` — Expected: OK.
- [ ] **Step 6: Commit** `git add monitoring/ird.py monitoring/discover.py tests && git commit -m "IRD : fréquence RF, polarisation et numéro de série"`

### Task 3: Porteuses (module pur)

**Files:**
- Create: `monitoring/carriers.py`
- Test: `tests/test_carriers.py`

**Interfaces:**
- Consumes: `CarrierEntry` (tâche 1).
- Produces:
  - `@dataclass(frozen=True) Tuning(rf_khz: int, polarization: str | None, symbol_rate: int | None, roll_off: float | None)`.
  - `@dataclass(frozen=True) Carrier(key: str, name: str, rf_khz: int, polarization: str | None, symbol_rate: int | None, roll_off: float | None, irds: tuple[str, ...], manual: bool)`.
    - `key` vaut `f"{rf_khz // 100}-{pol_initial}"`, où `pol_initial` est `V`, `H` ou `?` : par exemple `"125830-V"`.
    - Nom par défaut : `"12 583,0 V"`, ou `"12 583,0"` si la polarisation est inconnue.
  - `compatible_pol(a: str | None, b: str | None) -> bool`.
  - `group_carriers(tunings: dict[str, Tuning], entries: tuple[CarrierEntry, ...]) -> list[Carrier]` :
    - un IRD rejoint une porteuse existante si les deux fréquences sont à moins de la moitié du plus petit débit symbole et que les polarisations sont compatibles ;
    - une porteuse manuelle qui coïncide avec une automatique la fusionne et lui donne son nom, avec `manual=True` ;
    - une entrée `manual=False` ne crée rien : elle donne seulement son nom à la porteuse automatique qui coïncide ;
    - la liste est triée par fréquence.
  - `band_hz(c: Carrier) -> tuple[float, float]`.
  - `@dataclass(frozen=True) MarkerMap(by_carrier: dict[str, tuple[int, ...]], noise: tuple[int, ...], if_warning: bool, ambiguous: tuple[int, ...])`.
  - `attach_markers(carriers: list[Carrier], marker_freqs: dict[int, float], fsh_pol: str | None, noise_markers: tuple[int, ...] = ()) -> MarkerMap` :
    - `noise_markers` non vide remplace le choix automatique du bruit ;
    - `if_warning` est vrai dès qu'un marqueur allumé est sous 3e9 Hz ; dans ce cas, `by_carrier` et `noise` sont vides.
  - `center_marker(c: Carrier, mm: MarkerMap, marker_freqs: dict[int, float]) -> int | None` : le marqueur de la porteuse le plus proche du centre.

- [ ] **Step 1: Write the failing tests** :
  - `test_two_irds_same_carrier` : 12 583 023 et 12 583 060 kHz, polarisation V, 7,2 MS/s → une porteuse qui contient les deux numéros de série ;
  - `test_same_freq_other_pol_two_carriers` : même fréquence, V et H → deux porteuses ;
  - `test_unknown_pol_joins` : V et `None` à la même fréquence → une porteuse ;
  - `test_manual_merges_and_names` : `CarrierEntry("Client A", 12583.0, "vertical", 7.2, True)` → une seule porteuse, nommée « Client A », `manual=True` ;
  - `test_manual_alone_kept` : une porteuse manuelle sans IRD apparaît avec `irds == ()` ;
  - `test_name_only_entry` : une entrée `manual=False` renomme l'automatique, mais ne crée rien quand aucun IRD ne lui correspond ;
  - `test_markers_attached_by_band` : porteuse 11 220,8 MHz V à 24,76 MS/s, entrée du FSH4 V, marqueurs {1: 11 220,8 ; 2: 11 213 ; 4: 11 200 ; 5: 11 242 MHz} → M1 et M2 rattachés, `noise == (4, 5)` ;
  - `test_markers_other_pol_not_attached` : entrée du FSH4 H → aucun rattachement ;
  - `test_if_markers_warn_and_attach_nothing` (*Review Focus 3*) : marqueur à 1 470,8 MHz → `if_warning`, `by_carrier == {}`, `noise == ()` ;
  - `test_center_marker` → 1.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_carriers -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `monitoring/carriers.py`.
- [ ] **Step 4: Run** `python3 -m unittest tests.test_carriers -v` — Expected: OK.
- [ ] **Step 5: Commit** `git add monitoring/carriers.py tests/test_carriers.py && git commit -m "Porteuses : regroupement des IRD et rattachement des marqueurs"`

### Task 4: Stockage multi-IRD et migration

**Files:**
- Modify: `monitoring/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `IrdSample` (tâche 2).
- Produces:
  - `ird_sat` gagne les colonnes `ird TEXT` (numéro de série), `rf_khz INTEGER`, `polarization TEXT` et `roll_off REAL` ;
  - `events` gagne une colonne `source TEXT` : `"fsh"`, `"ird:<série>"`, `"porteuse:<key>"` ou NULL ;
  - nouvelle table `ird_names(serial TEXT PRIMARY KEY, name TEXT, last_seen INTEGER)`.
  - `add_ird(ts: int, serial: str, s: IrdSample) -> None`.
  - `set_ird_name(serial: str, name: str, ts: int) -> None` et `ird_names() -> dict[str, str]`.
  - `open_event(ts, kind, severity, detail, source: str | None = None) -> int` ; `events()` et `event()` renvoient aussi `source`.
  - `ird_series(from_ts, to_ts, max_points) -> dict[str, dict]` :
    - une série par numéro de série, colonnes `locked, ts_locked, cn_db, margin_db, ber, power_dbm` ;
    - plus `tuning` (dernier `rf_khz`, `polarization`, `symbol_rate`, `roll_off` de la plage).
  - `latest()["irds"]` : `{série: dernière mesure}` ; `latest()["ird"]` disparaît.
  - `migrate(db)` est appelé dans `__init__` et peut être relancé sans effet (idempotent). Pour les lignes où `ird` est NULL :
    - **coupure** : `cut = MIN(ts_start)` des événements `devices_changed` dont `ts_start >= 1792540800000` (21/10/2026, 00:00 heure de Paris) ;
    - **avant la coupure** : `ird = '1000-00002'`, `rf_khz = if_khz + 9750000`, `polarization` NULL ;
    - **après la coupure** : `ird = '1000-00001'`, `rf_khz = if_khz + 10600000`, `polarization = 'vertical'` ;
    - **sans coupure** : toutes les lignes sont traitées comme « avant » ;
    - `ird_names` reçoit (`1000-00002`, « IRD 5 ») et, s'il y a une coupure, (`1000-00001`, « IRD 1 ») ;
    - événements anciens : `source = 'fsh'` pour les types `marker_drop`, `noise_rise` et `fsh_settings` ; `'ird:<série>'` selon la période pour `lock_lost`, `ts_lock_lost`, `ber_high` et `margin_drop` ; `device_down` selon son `detail` (`fsh` ou `ird`).

- [ ] **Step 1: Write the failing tests** :
  - `test_ird_series_per_serial` : deux séries, et `tuning["rf_khz"] == 12583023` ;
  - `test_migration_splits_history_by_device_change` (*Review Focus 5*) : base à l'ancien schéma (tables créées comme au 17/10), trois lignes IRD à IF 1 470 806 avant un `devices_changed` daté du 21/10, deux lignes à IF 1 983 023 après → numéros, fréquences RF et polarisations de la règle, aucune ligne perdue ;
  - `test_migration_is_idempotent` : rouvrir deux fois → mêmes valeurs ;
  - `test_event_source_roundtrip` ;
  - `test_latest_irds` ;
  - adapter les tests existants à `add_ird(ts, serial, s)`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_store -v` — Expected: FAIL.
- [ ] **Step 3: Implement** dans `monitoring/store.py`.
- [ ] **Step 4: Run** `python3 -m unittest tests.test_store -v` — Expected: OK.
- [ ] **Step 5: Verify on a copy of the real base** : `cp data/monitoring.db /tmp/copie.db`, ouvrir `Store("/tmp/copie.db")`, puis vérifier :
  - `SELECT ird, COUNT(*), MIN(rf_khz), MAX(rf_khz) FROM ird_sat GROUP BY ird` donne deux lignes : `1000-00002` (environ 11 220 806 kHz) et `1000-00001` (environ 12 583 023 kHz) ;
  - le total des lignes est le même qu'avant ;
  - aucun `source` d'événement n'est NULL.
- [ ] **Step 6: Commit** `git add monitoring/store.py tests/test_store.py && git commit -m "Stockage multi-IRD et migration de l'historique"`

### Task 5: Détection par IRD et par porteuse, « IRD seul en défaut »

**Files:**
- Modify: `monitoring/detect.py`
- Test: `tests/test_detect.py`

**Interfaces:**
- Consumes: `Store` (tâche 4), `Carrier` (tâche 3).
- Produces : dans `Detector`, les règles actuelles deviennent indépendantes **pour chaque IRD** (lock, lock TS, BER, marge) et **pour chaque porteuse** (chute de niveau RF) ; le bruit reste unique.
  - `on_ird(ts: int, serial: str, name: str, s: IrdSample) -> list[dict]` : le `detail` est préfixé du nom de l'IRD (« IRD 1 : marge 4,1 dB (référence 6,3 dB) ») et `source = f"ird:{serial}"`.
  - `on_carrier_level(ts: int, carrier: Carrier, level_dbm: float) -> list[dict]` : `marker_drop` avec `source = f"porteuse:{carrier.key}"`, détail préfixé du nom de la porteuse.
  - `on_noise(ts, level_dbm)` reste tel quel.
  - `on_state(ts, device: str, state, detail)` : `device` vaut `"fsh"` ou `"ird:<série>"`.
  - `on_alone(ts: int, carriers: list[Carrier], reachable: set[str], names: dict[str, str]) -> list[dict]` applique la règle du §6 de la spec :
    - **alerte de réception** : `lock_lost`, `ts_lock_lost`, `ber_high` ou `margin_drop` ouverte pour cet IRD ;
    - **délai** : 3 s, mesurées depuis l'ouverture de la plus ancienne alerte de réception de l'IRD ;
    - **gravité** : `critical` si l'IRD a `lock_lost` ouvert, sinon `warning` ;
    - **détail exact** : « IRD 6 seul en défaut sur 12 583,0 V (IRD 5 normal) — cause probable : équipement ou câblage » ; s'il y a plusieurs IRD normaux, les nommer séparés par des virgules.
  - `remove_ird(ts: int, serial: str) -> list[dict]` : ferme toutes les alertes de l'IRD, y compris `ird_alone` et `device_down`, avec le motif « IRD retiré », puis oublie son état.
  - `on_devices_changed` et `on_fsh_settings` restent ; `on_devices_changed` ne concerne plus que le FSH4.

- [ ] **Step 1: Write the failing tests** :
  - `test_rules_are_per_ird` : l'IRD A perd le lock, l'IRD B non → `lock_lost` ouvert seulement pour A, avec `source == "ird:A"` ;
  - `test_alone_opens_after_3s` : porteuse {A, B}, marge de A en chute, B stable → rien à t+2 s, `ird_alone` à t+3 s, avec le détail exact ;
  - `test_alone_closes_when_other_degrades` : B se dégrade à son tour → `ird_alone` fermée ;
  - `test_alone_not_for_unreachable` : B injoignable (absent de `reachable`) → pas d'`ird_alone` pour A ; une panne réseau de A seule ne déclenche rien non plus ;
  - `test_alone_not_across_carriers` : A et B sur deux porteuses différentes → rien ;
  - `test_alone_when_unlocked_ird_keeps_its_carrier` (*Review Focus 2*) : A déverrouillé, rattaché à sa porteuse via `downlinkFrequency` → `ird_alone` en `critical` ;
  - `test_carrier_level_drop_per_carrier` ;
  - `test_remove_ird_closes_alerts` ;
  - adapter les tests existants aux nouvelles signatures.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_detect -v` — Expected: FAIL.
- [ ] **Step 3: Implement** dans `monitoring/detect.py` : un objet de règles par IRD et une `_DropRule` par porteuse, créés à la première mesure ; garder `_DropRule` telle quelle.
- [ ] **Step 4: Run** `python3 -m unittest tests.test_detect -v` — Expected: OK.
- [ ] **Step 5: Commit** `git add monitoring/detect.py tests/test_detect.py && git commit -m "Détection par IRD et par porteuse, IRD seul en défaut"`

### Task 6: Application multi-IRD

**Files:**
- Create: `monitoring/app.py` (y déplacer `LiveBus`, `App`, `default_clients`, `describe_settings` et les fonctions `_hz` / `_mhz` depuis `server.py`)
- Modify: `monitoring/server.py` (n'importe plus que depuis `app.py`)
- Test: `tests/test_app.py` (nouveau), `tests/test_server.py`

**Interfaces:**
- Consumes: tâches 1 à 5.
- Produces : `App(cfg, fsh_read=None, fsh_close=None, ird_factory=None, web_dir=None, config_path=None, scanner=None)`.
  - `ird_factory(entry: IrdEntry, cfg: Config) -> tuple[Callable[[], IrdSample], Callable[[], tuple[str, str]]]` renvoie la fonction de lecture et la fonction d'identification. Par défaut, `IrdReader(IrdClient(...), entry.serial).read` et `IrdClient.identify`.
  - **Boucles** : `self.ird_loops: dict[str, DeviceLoop]`, une par IRD, avec `device = f"ird:{serial}"`.
  - **Identification au démarrage** : un IRD de `config.toml` dont le `serial` est vide est identifié au démarrage, et la configuration est réenregistrée. Tant qu'il n'est pas identifié, il est retenté toutes les `retry_s` secondes, sans boucle de lecture.
  - **Porteuses** : `carriers() -> list[Carrier]` est calculé à partir du dernier `Tuning` de chaque IRD joignable et de `cfg.carriers`.
  - **Après chaque lecture IRD** : stocker la mesure, appeler `detector.on_ird`, puis `detector.on_alone(...)`. Si la liste des porteuses change (comparée par `key` et par IRD), publier un message `{"type": "carriers", "carriers": [...]}`.
  - **Après chaque lecture FSH4** :
    - `attach_markers` avec `cfg.fsh.polarization` et `cfg.detect.noise_markers` ;
    - `on_carrier_level` pour le marqueur central de chaque porteuse ;
    - `on_noise` si au moins un marqueur de bruit est disponible et qu'il n'y a pas d'`if_warning`.
  - **Gestion des IRD** :
    - `add_ird(host: str) -> IrdEntry` identifie l'appareil. Il lève `ValueError` si ce numéro de série est déjà suivi, et `IrdError` s'il est injoignable ou si ce n'est pas un IRD Ateme. Sinon il enregistre la configuration, démarre la boucle et met le nom dans `ird_names`.
    - `update_ird(serial, name: str | None, host: str | None) -> None`.
    - `remove_ird(serial) -> None` arrête la boucle, appelle `detector.remove_ird` et retire l'IRD de la configuration.
  - **Gestion des porteuses et du FSH4** :
    - `set_carrier_name(key, name)` enregistre un `CarrierEntry(manual=False)`, ou renomme la porteuse manuelle correspondante ;
    - `add_carrier(entry: CarrierEntry)` et `remove_carrier(key)` (porteuses manuelles seulement) ;
    - `set_fsh(host, port, polarization)` remplace `set_devices`.
  - `status()` renvoie `markers`, `irds` (`{série: {name, host, state, latest}}`), `carriers` (liste de `Carrier` en dict, avec en plus `markers` et `center_marker`), `marker_map` (`if_warning`, `noise`, `ambiguous`), `fsh` (`host`, `port`, `polarization`, `state`), `config` et `now`.
  - Les messages SSE `ird` portent `serial` et `name`.

- [ ] **Step 1: Write the failing tests** dans `tests/test_app.py`, avec de faux lecteurs IRD et sans réseau :
  - `test_two_irds_one_carrier_in_status` ;
  - `test_add_ird_identifies_and_saves` : `config.toml` temporaire relu, l'IRD y figure avec son numéro de série ;
  - `test_add_same_serial_twice_raises` ;
  - `test_remove_ird_closes_its_alerts` (*Review Focus 4*) : une alerte `lock_lost` est ouverte, `remove_ird` la ferme avec « IRD retiré », et la boucle est arrêtée ;
  - `test_unidentified_ird_from_legacy_config_is_identified_at_start` ;
  - `test_carriers_message_published_when_grouping_changes` ;
  - `test_marker_drop_uses_center_marker_of_carrier` ;
  - déplacer dans `tests/test_app.py` les tests de `tests/test_server.py` qui visent `App` (pause, réglages FSH, bruit, marqueurs multiples), en les adaptant.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_app -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `monitoring/app.py` et alléger `monitoring/server.py`.
- [ ] **Step 4: Run** `python3 -m unittest discover tests -v` — Expected: OK, sauf les tests HTTP de la tâche 7, encore à écrire.
- [ ] **Step 5: Commit** `git add monitoring tests && git commit -m "Application multi-IRD"`

### Task 7: API HTTP

**Files:**
- Modify: `monitoring/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `App` (tâche 6).
- Produces : toutes les routes `POST` gardent le contrôle d'origine (403) et l'exigence `application/json` quand il y a un corps (415).
  - `GET /api/devices` → `{"fsh": {"host", "port", "polarization"}, "irds": [{"serial", "name", "host", "state", "carrier"}]}`.
  - `POST /api/fsh` `{"host", "port", "polarization"}` → 204. Validation : hôte comme aujourd'hui, port entre 1 et 65535, polarisation `vertical`, `horizontal` ou null ; sinon 400.
  - `POST /api/irds` `{"host"}` → 201 avec l'IRD ajouté. 409 si le numéro de série est déjà suivi ; 502 avec un message en français si l'appareil est injoignable ou n'est pas un IRD Ateme.
  - `POST /api/irds/<série>` `{"name"?, "host"?}` → 204 ; 404 si la série est inconnue.
  - `POST /api/irds/<série>/remove` → 204.
  - `GET /api/carriers` → la liste ; `POST /api/carriers` `{"name", "freq_mhz", "polarization", "symbol_rate_msps"}` → 201 ; `POST /api/carriers/<key>` `{"name"}` → 204 ; `POST /api/carriers/<key>/remove` → 204, ou 409 si la porteuse n'est pas manuelle.
  - `GET /api/series` → `{"fsh": {n: série}, "irds": {série: série}, "names": {série: nom}, "carriers": [...]}`. Les porteuses sont calculées par `group_carriers` à partir des `tuning` de la plage et de `cfg.carriers`, puis complétées par `attach_markers` avec les dernières fréquences de marqueur de la plage.
  - L'ancienne route `POST /api/devices` est retirée.

- [ ] **Step 1: Write the failing tests** :
  - une requête par route, avec ses codes d'erreur ;
  - `test_series_has_carriers_for_range` : une base contenant les deux périodes de la migration → deux porteuses, 11 220,8 et 12 583,0 MHz ;
  - garder les tests d'origine, de chemin statique et d'octet nul.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_server -v` — Expected: FAIL.
- [ ] **Step 3: Implement** dans `monitoring/server.py`.
- [ ] **Step 4: Run** `python3 -m unittest discover tests -v` — Expected: OK.
- [ ] **Step 5: Commit** `git add monitoring/server.py tests/test_server.py && git commit -m "API : IRD, porteuses, entrée du FSH4"`

### Task 8: Page — sélecteur et sections par porteuse

**Files:**
- Create: `web/sections.js` (une section par porteuse : en-tête, graphes RF, marge et BER/lock), `web/dialogs.js` (fenêtres **Appareils…** et **Porteuses…**)
- Modify: `web/app.js` (état, données, SSE, modes Direct/Historique, liste d'événements), `web/index.html`, `web/style.css`

**Interfaces:**
- Consumes: routes de la tâche 7.
- Produces (`sections.js`) : `renderSections(container, carriers, data, range, options)`. Les graphes d'une porteuse sont recréés seulement si ses IRD ou ses marqueurs changent. Le curseur et le zoom sont synchronisés entre toutes les sections, avec la même clé de synchronisation qu'aujourd'hui.

- [ ] **Step 1: Implement** la disposition A du §7 de la spec :
  - **Bandeau** : pastille du FSH4, une pastille par IRD, boutons **Appareils…** et **Porteuses…**.
  - **Sélecteur** : une case par porteuse (nom, fréquence, polarisation, nombre d'IRD, mention « RF »), mémorisée dans `localStorage` sous la clé `monitoring.carriers.selected`, lecture et écriture dans un `try/catch`. Une porteuse nouvelle est cochée par défaut.
  - **Sections** dans l'ordre des fréquences :
    - en-tête de la porteuse ;
    - graphe RF, ou le texte exact « Pas de mesure RF (porteuse hors du span du FSH4) » ;
    - graphe marge (une courbe par IRD) ;
    - graphe BER log avec une bande de lock par IRD.
  - **Avertissement** sous le bandeau si `marker_map.if_warning` : « Décalage LNB non réglé sur le FSH4 : les marqueurs sont en fréquence intermédiaire ».
  - **Graphe du bruit** sous les sections, s'il y a des marqueurs de bruit.
  - **Événements** : la liste affiche la source (nom de l'IRD ou de la porteuse) et n'affiche que les porteuses cochées ; les événements du FSH4 sont toujours affichés.
  - **Historique** : les porteuses viennent de `/api/series`.
  - **Message SSE `carriers`** : il recharge le Direct.
- [ ] **Step 2: Implement** les fenêtres :
  - **Appareils…** :
    - adresse, port et polarisation du FSH4 ;
    - tableau des IRD (nom modifiable sur place, adresse, porteuse, état) avec **Retirer** ;
    - champ « Ajouter un IRD (adresse) » ;
    - la recherche réseau propose **Ajouter** pour chaque IRD trouvé qui n'est pas déjà suivi, et **Utiliser** pour le FSH4.
  - **Porteuses…** :
    - liste des porteuses avec renommage ;
    - formulaire « Ajouter une porteuse » (nom, fréquence en MHz, polarisation, débit symbole en MS/s) ;
    - **Supprimer** pour les porteuses manuelles seulement.
  - Les erreurs de l'API (400, 409, 502) s'affichent dans la fenêtre.
- [ ] **Step 3: Check syntax** : `~/.local/node/bin/node --check web/app.js && ~/.local/node/bin/node --check web/sections.js && ~/.local/node/bin/node --check web/dialogs.js` — Expected: aucune sortie.
- [ ] **Step 4: Verify in the built-in browser** avec une base de test contenant deux IRD sur une porteuse et un IRD sur une autre (faux lecteurs via un petit script de démonstration dans `tests/`, sans réseau). Vérifier :
  - deux sections, avec deux courbes de marge dans la première ;
  - décocher une porteuse masque sa section et ses événements ;
  - « IRD seul en défaut » s'affiche avec sa source ;
  - les deux fenêtres fonctionnent ;
  - aucune erreur dans la console.
  Faire une capture d'écran pour Max.
- [ ] **Step 5: Commit** `git add web tests && git commit -m "Page : une section par porteuse, gestion des IRD et des porteuses"`

### Task 9: Essai réel et documentation

**Files:**
- Modify: `README.md`, `docs/HISTORIQUE.md`, `docs/superpowers/specs/2026-10-17-monitoring-sat-design.md` (renvoi vers la nouvelle spec)

- [ ] **Step 1: Essai réel** :
  - **Préparation** : arrêter le service, copier `data/monitoring.db` vers `data/monitoring-avant-multi.db` (copie de sécurité, non versionnée), puis lancer le service.
  - **Base migrée** : vérifier les deux IRD et l'historique du 17/10 consultable (porteuse 11 220,8 MHz).
  - **IRD réels** : l'IRD 1 (192.168.10.141) est identifié (n° 1000-00001) et forme la porteuse 12 583,0 V. Ajouter depuis la page tout autre IRD Ateme que Max indique.
  - **Mesures** : relevé de 5 minutes, puis cadence par IRD (au moins 0,95 lecture/s), journal sans erreur, page vérifiée.
  - **Si le FSH4 est encore injoignable**, le noter sans bloquer.
- [ ] **Step 2: Documentation** :
  - **`README.md`** : IRD multiples, porteuses, entrée du FSH4, « IRD seul en défaut » ajouté au tableau des événements, avertissement « fréquence intermédiaire » ;
  - **`docs/HISTORIQUE.md`** : entrée du 21/10 ;
  - **ancienne spec** : une ligne renvoyant à la nouvelle.
- [ ] **Step 3: Run** `python3 -m unittest discover tests -v` — Expected: OK.
- [ ] **Step 4: Commit** `git add README.md docs && git commit -m "Documentation multi-porteuses"`
