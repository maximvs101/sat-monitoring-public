# Historique

## État actuel (22 octobre 2026, soir)

- Branche `main` ; 262 tests Python et 13 tests de la page (`node --test`) verts. Dépôt public https://github.com/maximvs101/sat-monitoring-public : copie nettoyée du dépôt de travail (adresses, numéros de série et noms de programme du site remplacés par des exemples). Max pousse via GitHub Desktop. `config.toml` (adresses et numéros de série du site) n'est plus suivi par git depuis le 22/10 : chaque machine a le sien, créé à partir de `config.example.toml`.
- Service lancé depuis ce Mac (page http://localhost:8080), base `data/monitoring.db`, purge à 30 jours.
- Site actuel : réseau 192.168.10.x.
  - IRD 1 (DR5000 n° 1000-00001, 192.168.10.141) sur la porteuse « C1 » (12 583,0 V), programme P100/2, marge 6,3 dB, état normal.
  - IRD 3 (DR5000 n° 1000-00003, 192.168.10.143) sur la porteuse « C2 » (12 592,0 V) : joignable mais **lock perdu depuis 18:56 le 22/10** (puissance d'entrée 0 dBm, marge 0), alerte en cours ; côté réception, pas lié au code.
  - FSH4 : l'outil pointe depuis le 22/10 au soir sur un autre FSH4, 192.168.10.99 (n° 100001, firmware V2.50), qui n'a pas l'option FSH-K40 : aucune mesure possible (voir le guide). L'ancien FSH4 (192.168.10.224, V1.20) n'en avait pas besoin. La puissance d'entrée des IRD tient lieu de niveau RF.
- Page refondue (fusionnée dans main le 22/10) : menu à gauche, vue d'ensemble, une page par porteuse avec période, journal, analyseur FSH4, réglages en pages. Seuils de marge sur ce Mac : chute 2 dB, marge minimale 3 dB.
- Windows : versions publiées sur le dépôt public, dernière **v1.0.2** (https://github.com/maximvs101/sat-monitoring-public/releases/tag/v1.0.2, `MonitoringSat-Windows.zip`) ; pas encore essayée sur un PC du site.

## À faire

- FSH4 192.168.10.99 : licence FSH-K40 à obtenir (ou remettre l'ancien FSH4 en V1.20).
- Obtenir le firmware FSH4 (support R&S ou distributeur, n° de série 100000), puis lecture de trace et spectrogramme.
- Essayer `MonitoringSat.exe` (release v1.0.2) sur un vrai PC Windows du réseau des appareils. Nouvelle version : pousser un tag `v…`.
- Consultation depuis un autre PC (prévue) : le service n'écoute que sur 127.0.0.1 (`host` de `[server]` dans `config.toml`). Il faudra contrôler l'ouverture au réseau, la page en lecture seule depuis le réseau, et les actions (Réglages, Pause FSH4) réservées à la machine du service.
- À vérifier lors d'une vraie coupure : contenu de la réponse `refresh` quand le programme disparaît ou que le décodeur se resynchronise (une « Image perdue » brève après le retour du lock est possible si la resynchro dépasse 3 s).
- Cas laissés en l'état : programme radio sans vidéo → « Image perdue » permanente ; voies audio configurées sur un programme sans son → « Son absent » permanent.
- Point mineur reporté de la revue du 22/10 : une seule cause ouvre plusieurs critiques (flux arrêté + image perdue).
- Points mineurs reportés de la refonte de la page (22/10) :
  - carte « Plancher de bruit » de la vue d'ensemble sans lien vers l'analyseur FSH4 ;
  - menu sans entrée en surbrillance quand la clé de la porteuse ouverte change (ex. 125830-? → 125830-V) ;
  - journal chargé sans limite de nombre (34 événements sur 30 j aujourd'hui) ;
  - clé à polarisation inconnue avec deux porteuses V et H de même fréquence : correspondance au plus proche, arbitraire ;
  - fréquence d'une porteuse saisie : le service accepte toute valeur positive.
- Points mineurs reportés de la vérification complète du 22/10 :
  - alertes de réception (lock, BER, marge) et warnings du journal restent ouverts pendant qu'un IRD est injoignable ; `device_down` du FSH4 reste ouvert pendant une pause, avec « fsh » pour seul détail ;
  - cadence : la durée du traitement (base, détection) s'ajoute à la période ; plus de 10 réponses « occupé » de suite comptent comme échecs ;
  - fenêtre d'une aller-retour entre `CALC:MARKn?` et `:Y?` (marqueur éteint par l'opérateur juste entre les deux : rallumé) — touches verrouillées en mode Remote, risque théorique ;
  - course bénigne sur les règles de porteuse (FSH4 contre IRD) ;
  - page en Direct si `live_window_min × 60 × 4 > max_points` : séries agrégées complétées de points bruts ;
  - SQLite : dernier état des appareils et `events()` sans index adapté ;
  - réécriture de `config.toml` : commentaires entre blocs `[[irds]]`/`[[carriers]]` perdus ; porteuse sans `freq_mhz` ou `rate_hz = 0` : erreur peu claire au démarrage ; ajout de porteuse sans dédoublonnage.

## 22 octobre 2026

- **Version v1.0.2** publiée sur le dépôt public : IRD déverrouillé et message FSH4 sans FSH-K40 (ci-dessous) ; release https://github.com/maximvs101/sat-monitoring-public/releases/tag/v1.0.2 avec `MonitoringSat-Windows.zip` (10 Mo) et notes en français.
- **FSH4 sans FSH-K40** : Max a branché un second FSH4 (192.168.10.99, firmware V2.50). Il répond à `*IDN?`, `*OPT?` (« 0 »), `INST?` (SAN) et aux commandes de fichiers `MMEM`, mais à aucune commande de mesure (-113 « Undefined header ») : option de commande à distance FSH-K40 absente, exigée depuis le firmware V1.30. Port 80 : simple renvoi vers le gestionnaire de licences R&S. L'outil le dit maintenant en clair (après une requête sans réponse, il lit `SYST:ERR?`), et la page n'écrit plus « hors ligne » pour un FSH4 qui répond.
- **Vérification des données des IRD** (réponses brutes `refresh`, `status`, `logs` des IRD 1 et 3 comparées à la lecture de l'outil, à la base, à l'API et à la page) : tout concorde pour un IRD verrouillé. Déverrouillé (IRD 3 depuis 18:56), l'IRD envoie des valeurs qui n'en sont pas : puissance 0 dBm (les 6 666 lectures déverrouillées de la base étaient à 0 dBm, tracées comme un pic), débit symbole 0, modulation « automatic unknown unknown », et garde son décodage d'avant (son « 6 voies sur 6 actives », débit vidéo). Corrigé : ces valeurs ne sont plus lues, la porteuse garde le débit symbole du dernier lock (en direct et sur une période passée), les anciennes lectures à 0 dBm ne sont plus tracées, la carte affiche « — », mire « red » traduite. Remarques sans correction : les voies audio comptées sont les décodages configurés (IRD 1 : 5 voies dont 2 doublons de PID sur les emplacements 8 et 9) ; après un redémarrage du service pendant un décrochage, le débit symbole de la porteuse reste inconnu en Direct jusqu'au prochain lock.
- **Version v1.0.1** publiée sur le dépôt public : les corrections de la revue ci-dessous ; workflow vert (39832993194), release https://github.com/maximvs101/sat-monitoring-public/releases/tag/v1.0.1 avec `MonitoringSat-Windows.zip` (10 Mo) et notes en français. Service de ce Mac relancé avec cette version.
- **Revue du code publié** (10 points, tous corrigés) : fichiers de la page revalidés à chaque chargement (plus d'anciens scripts après une mise à jour) ; requêtes refusées si le nom d'hôte n'est ni `localhost` ni une adresse IP (rattachement DNS) ; pages redessinées au plus une fois par seconde au lieu de chaque mesure (CPU) ; Seuils d'alerte lus sur le service (plus de valeurs par défaut enregistrées par erreur) ; adresse FSH4 en cours de saisie gardée quand un appareil change d'état ; journal : plus de navigation après avoir quitté la page, message si l'IRD n'était sur aucune porteuse ; événements sans destination plus présentés comme cliquables ; périodes « constructor »/« toString » refusées ; « injoignable » partout (plus « hors ligne ») ; pause du FSH4 et périodes fixes partagées.
- **Dépôt public** : publication d'une copie nettoyée (`sat-monitoring-public`), où les adresses, les numéros de série et les noms de programme du site sont remplacés par des exemples ; workflow « Exécutable Windows » vert à la première exécution (tests, exécutable essayé, archive de 10 Mo).
- **Version v1.0.0** publiée : release https://github.com/maximvs101/sat-monitoring-public/releases/tag/v1.0.0 avec `MonitoringSat-Windows.zip` (10 Mo) et notes en français.
- **Documentation** : README raccourci (description, lancement, pages, limites, exécutable, tests) ; le détail (page, alertes, réglages, marqueurs, données, API) est passé dans `docs/GUIDE.md`.
- **Refonte de la page** (spec et plan du 22/10, `docs/superpowers/`) : style unique, clair et sobre (une feuille `web/style.css` à variables) ; menu à gauche (vue d'ensemble, une entrée par porteuse avec ses alertes, journal, analyseur FSH4, réglages) ; adresses par page (`#/porteuse/…?p=24h`), Précédent fonctionnel. Remplacent les onglets Direct / Historique et les fenêtres Appareils… / Porteuses… / Seuils… :
  - vue d'ensemble : chiffres clés, une carte par IRD (même composant partout), événements, plancher de bruit ;
  - page d'une porteuse : période (Direct, 1 h, 24 h, 7 j, dates libres, glisser sur un graphe), graphes communs synchronisés, section par IRD (carte et détails en Direct, résumé de la période sinon), événements ; porteuse disparue affichée telle qu'elle était (vérifié : 11 220,8 du 17/10) ;
  - journal filtrable ; un clic ouvre la porteuse où était l'IRD au moment de l'événement ;
  - analyseur FSH4 : état, span et RBW (ajoutés à `/api/status`), tous les marqueurs avec leur porteuse ;
  - code : `core.js` (logique testée sous Node), `store.js` (données partagées), `charts.js`, `card.js`, une page par fichier dans `web/pages/` ; `app.js`, `sections.js`, `wall.js`, `dialogs.js` retirés. Aucun graphe orphelin après 20 navigations ; 7 jours chargés en 0,4 s sur la base réelle.
- Polarisation des IRD : le réglage de l'IRD n'est lu que s'il alimente le LNB (99 % des sites : LNB alimenté par un autre équipement) ; polarisation imposable par IRD dans Appareils…, avertissement sur les porteuses concernées.
- État des IRD (« ajoute tout et réduis le BER à une bande ») : décodage lu à chaque mesure (image, son, chiffrement, programme), état matériel et journal de l'IRD toutes les 10 s (requêtes `status` et `logs`, lecture seule) ; six nouvelles alertes (image perdue, son absent, programme non déchiffré, flux d'entrée arrêté, défaut matériel, journal IRD) ; graphe BER remplacé par une bande « État » par IRD ; en-têtes détaillés. Essai réel sur l'IRD 1 (1000-00001) et l'IRD 3 (1000-00003, 192.168.10.143, 12 592,0 V) : 0,994 lecture/s, programmes P100/2 et P100/9 en 1080i50, son 5/5 et 6/6, déchiffrés, 49–52 °C. Base sauvegardée avant migration : `data/monitoring-avant-etat.db`.
- Revue finale de l'état des IRD : un champ de décodage absent ne ferme plus une alerte (inconnu ≠ revenu) ; journal IRD remis à zéro détecté dès qu'un nId nouveau est inférieur au plus grand vu, warnings d'avant fermés (« journal IRD réinitialisé ») ; lecture arrivée après le retrait d'un IRD ignorée ; en-tête « injoignable » / « lock TS perdu ».
- Affichage : une carte par IRD (nom, programme, puis marge, image, son, flux TS, FPGA en gros caractères, rouge ou orange si anormal) ; marge sur une échelle partant de 0 dB (décrochage), le bruit de ±0,2 dB n'occupe plus tout le graphe ; seuil d'alerte de chaque IRD en pointillé (Direct) ; graphes en dBm sur 10 dB au minimum. `/api/status` expose `margin_drop_db`, `median_window_s` et `drop_confirm_s`.
- Puissance d'entrée de chaque IRD (graphe et tuile « Entrée ») ; panneau « Détails » : image, voies audio, configuration des sorties. Relevé dans l'interface web de l'IRD (requêtes `status`, `logs`, `network`, `timeConfig`, `levelsAndTargets`) : aucune ne donne l'état réel des sorties SDI/ASI/IP, seule leur configuration (dans `presets`) est lisible. Langue des voies audio vide sur ce flux, donc non affichée.
- Vérification complète de l'outil (fonctionnement réel, démo, relecture de tout le code). Corrigé :
  - pause FSH puis changement d'adresse ou de polarisation : la collecte FSH4 ne reprenait plus jamais (reste maintenant « en pause ») ;
  - relecture des événements par la détection sur la connexion d'écriture : une requête d'historique longue ne bloque plus la collecte ; `latest()` passe par les index (1 ms sur 3 M mesures au lieu d'un parcours complet) ;
  - chute de marge fermée (« réception modifiée ») et référence remise à zéro quand modulation, débit symbole ou porteuse de l'IRD changent ;
  - suppression de `FshClient.read_marker` (lisait `CALC:MARK:X?`/`:Y?` sans vérifier l'état : aurait rallumé le marqueur 1) ;
  - lecture arrivée pendant le retrait d'un IRD : plus d'alertes orphelines ;
  - serveur : erreur imprévue → réponse 500 au lieu d'une connexion coupée ; `Content-Length` négatif refusé ;
  - page : chargements simultanés (seul le dernier s'applique), libellé « 10 dernières minutes » plus écrit en Historique.
- **Vue d'ensemble** (mur d'IRD) : nouvel onglet, une tuile par IRD en grille (état, valeurs clés, mini-courbe de marge, mini-bande « État », alertes en cours) ; 12 IRD sans défilement en 1440 × 900 comme en 1280 × 800 ; clic → vue détaillée de la porteuse. Vérifié sur la démo (tuile synchrone avec la mesure pendant un décrochage) et sur les deux IRD réels.
- Vue d'ensemble plus lisible : badge d'état, cause en clair avec sa durée, libellés et unités explicites, courbe de marge graduée avec le seuil. Taille des tuiles adaptée au nombre d'IRD (grandes tuiles, puis compactes, puis défilement) ; mesuré de 3 à 20 IRD en 1440 × 900 et 1280 × 800, aucun contenu tronqué.
- Seuils de marge réglables depuis la page (bouton **Seuils…**) : chute rapide et nouvelle **marge minimale** fixe (alerte « Marge basse », 0 = désactivée), appliqués sans redémarrer, enregistrés dans `config.toml`, tracés sur les graphes et les tuiles. Onglets « Vue d'ensemble » et « Direct » fusionnés : tuiles en haut de Direct, un clic déplie les graphes de la porteuse dessous. Graduations des mini-courbes plus coupées.
- Troisième passage du workflow Windows (n° 3, commit `c68ae16`, après le nettoyage du code) : tout vert en 6 min 39 s (machine GitHub plus lente) — 251 tests sous Windows, exécutable fabriqué et essayé, archive `MonitoringSat-Windows` (10 Mo).
- Deuxième passage du workflow Windows (n° 2, commit `873cd3a`) : tout vert en 2 min 49 s — 250 tests sous Windows, `MonitoringSat.exe` fabriqué (24 s), lancé et interrogé avec succès, archive `MonitoringSat-Windows` (10 Mo) produite.
- Premier passage du workflow Windows : 246 tests sur 250 verts ; les 4 échecs venaient du nettoyage de tests de la base (fichier SQLite encore ouvert, que Windows refuse de supprimer). Ordre de nettoyage corrigé ; actions GitHub passées en v6 (Node.js 24).
- `config.toml` retiré du suivi git (ignoré) : il contient les adresses et numéros de série du site ; le modèle `config.example.toml` le remplace dans le dépôt. Il a ensuite été retiré de tout l'historique git avant la première publication sur GitHub (copie de l'ancien historique gardée hors du dépôt).
- **Windows** : exécutable autonome `MonitoringSat.exe` fabriqué par GitHub Actions (`.github/workflows/windows.yml`, PyInstaller, essai automatique de l'exécutable), lanceur `monitoring.bat` pour un PC avec Python, recherche réseau via `ipconfig` (anglais et français), mise hors veille Windows et Mac gérée par le service, signaux d'arrêt selon le système, `config.toml` créé depuis `config.example.toml` au premier lancement, message clair si le port est déjà pris. Empaquetage vérifié ici avec la version Mac de PyInstaller ; la version Windows sera vérifiée par le workflow.
- **Nettoyage du code** (relecture complète par trois agents, puis corrections) :
  - page : utilitaires et couleurs partagés dans `web/common.js` ; état de la bande pris du service (plus recalculé par la page) ; tuiles et cartes mises à jour sans reconstruire le DOM chaque seconde ; CSS regroupé et règles mortes retirées ; corrigé au passage : tuiles qui restaient affichées en Historique, « 10 min » écrit en dur sur les tuiles, bouton Pause FSH muet en cas d'erreur ;
  - service : migration ponctuelle du 21/10 retirée (la base est migrée, ses numéros de série étaient codés en dur) ; rattrapage de l'état seulement à l'ajout de la colonne ; réponses d'erreur et validation des noms partagées ; `/api/detect` passe par la même gestion que les autres réglages ; `/api/carriers` sans calculer tout l'état ;
  - détection : `on_ird` découpé ; message de journal sans texte lisible (« message sans texte (warning) ») ;
  - appareils : XML et requêtes IRD partagés avec la recherche réseau ; balayage réseau en un seul passage ; horloges monotones pour les durées (un changement d'heure du système ne bloque plus la collecte) ; compteur « occupé » remis à zéro au changement d'appareil ; échecs des relevés annexes journalisés une fois puis au retour ;
  - 251 tests (3 tests de l'ancienne migration retirés, 7 ajoutés).
- Copies de sécurité de la base d'avant les migrations (`monitoring-avant-multi.db`, `monitoring-avant-etat.db`) supprimées à la demande de Max (mises à la Corbeille).
- Documentation : README complété (démonstration, fonctionnement sans FSH4, liste exacte des requêtes envoyées aux appareils, tables de la base, API, limites des sorties IRD).


## 21 octobre 2026

- Nouveau site : FSH4 192.168.10.224 (injoignable ce jour-là), IRD 1 (DR5000, n° 1000-00001) à 192.168.10.141 sur 12 583,0 MHz V, 7,2 MS/s 8PSK 3/4, oscillateur LNB 10 600 MHz, marge 6,3 dB.
- **Plusieurs porteuses et plusieurs IRD** ([spec](superpowers/specs/2026-10-21-multi-porteuses-design.md), [plan](superpowers/plans/2026-10-21-multi-porteuses.md)) :
  - IRD identifiés par numéro de série, ajoutés, renommés, déplacés ou retirés depuis la page ;
  - porteuses formées automatiquement (fréquence RF + polarisation) ou saisies à la main ;
  - polarisation de l'entrée du FSH4, marqueurs rattachés aux porteuses, avertissement « fréquence intermédiaire » ;
  - alertes par IRD et par porteuse, nouvelle alerte « IRD seul en défaut » ;
  - page : une section par porteuse, cases à cocher, fenêtres Appareils… et Porteuses….
- Migration de la base : l'historique du 17/10 rattaché à l'« IRD 5 » (n° 1000-00002, 11 220,8 MHz, polarisation inconnue), celui du 21/10 à l'« IRD 1 » (12 583,0 MHz V) ; copie de sécurité `data/monitoring-avant-multi.db` (non versionnée).
- Défauts trouvés en chemin : lignes à IF 0 (perte de lock) qui donnaient une fréquence RF aberrante à la migration ; graduations du BER mal étiquetées quand uPlot en masque ; message de démarrage qui utilisait encore l'adresse unique de l'IRD.


## 17 octobre 2026

### Prototype

- Sondage des appareils : FSH4 en SCPI sur TCP 5555 (firmware V1.20, pas de lecture de trace) ; DR5000 « IRD 5 » interrogé par son API XML-RPC (`xml=<refresh/>`), le SNMP sans MIB étant inexploitable.
- Spec, plan en 9 tâches, implémentation : collecte FSH4 + IRD, SQLite, détection, page web direct / historique (uPlot). Essai réel de 10 min : FSH 3,88 lectures/s, IRD 1,00/s.
- Relecture finale indépendante : 3 points importants et 8 mineurs, tous corrigés.

### Évolutions de l'après-midi

- Purge à 30 jours.
- Suivi des réglages du FSH4 (span, RBW, marqueurs) : événement « Réglage FSH4 modifié ».
- Adresses des appareils modifiables depuis la page, avec recherche sur le réseau local ; un IRD 4 (192.168.20.144, entrée IP) a été trouvé.
- Chutes de niveau jugées sur la médiane des 3 dernières secondes : avec la RBW réduite, le marqueur fluctue de ±3 dB (24 fausses alertes en 11 min avant, 0 après). Les 27 fausses alertes enregistrées entre 16:02 et 16:12 ont été supprimées.
- Notes de version FSH V3.30 lues : `TRAC:DATA?` existe en V3.30 ; procédure de mise à jour par clé USB (secteur obligatoire, Preset + 8 au démarrage, reset usine conseillé ensuite). Firmwares derrière un compte R&S, auquel Max n'a pas accès.
- Marqueurs multiples (M1 à M6) et alerte « Remontée du bruit ». Marqueurs placés le 17/10 avec l'accord de Max, après relevé d'un spectre grossier au marqueur 6 : M1 11 220,8 / M2 11 213 / M3 11 229 / M4 11 200 / M5 11 242 MHz, M4 et M5 en bruit.
- Deux pièges du FSH4 trouvés et contournés : lire un marqueur éteint le rallume ; sans silence entre deux cycles, la mesure n'est plus rafraîchie (0,4 → 1,7 valeur nouvelle/s).
- BER du DR5000 : entier à l'échelle 1e8 (relevé dans le JavaScript de son interface web). La valeur brute était prise telle quelle ; corrigé avant tout BER non nul enregistré.

### Essai de dépointage (16:45–16:50, Max à l'antenne)

| Constat | Valeur |
| --- | --- |
| Référence | marge 10,5 dB, C/N 15,7 dB, M1 ≈ −50 dBm, bruit ≈ −67 dBm |
| Perte de lock | vers C/N 5 dB, M1 ≈ −65 dBm (≈ 15 dB sous le nominal) ; minimum M1 −68,9 dBm |
| BER non nul | une seule fois, au reverrouillage : brut 10 000 000 = 1e-1, ce qui confirme l'échelle 1e8 |
| Plancher de bruit | en baisse (−67 → −70 dBm) : aucune fausse « Remontée du bruit », comme attendu pour un dépointage |

Toutes les alertes attendues se sont déclenchées et refermées. Deux défauts corrigés ensuite :

- l'IRD répond HTTP 503 pendant environ 5 s en se reverrouillant : traité comme « occupé », plus comme une panne ;
- un retour partiel refermait la chute du niveau marqueur et la référence absorbait le dépointage (3 chutes avec des références de −50,8, −54,6 et −55,2 dBm) : référence figée pendant l'alerte et la minute qui suit (2 chutes, toutes deux contre −50,8 dBm, sur les mêmes mesures rejouées).
