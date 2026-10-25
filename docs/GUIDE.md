# Guide d'utilisation

Fonctionnement détaillé de l'outil : page, alertes, réglages, marqueurs du FSH4, données enregistrées, API. Pour lancer l'outil, voir le [README](../README.md).

## Fichiers et lancement

- `config.toml`, `data/` et `logs/` sont à côté de l'exécutable (ou à la racine du dépôt). `config.toml` n'est pas suivi par git : chaque machine a le sien, créé au premier lancement à partir de `config.example.toml` (aucun appareil).
- Si l'outil tourne déjà, un second lancement ouvre simplement sa page ; si le port est pris par autre chose, un message le dit.
- Windows : détails dans `LISEZMOI-Windows.txt`, joint à l'archive de l'exécutable.
- Démonstration (`python3 -m tests.demo_server 8091`) : trois faux IRD, un faux FSH4, base temporaire effacée à l'arrêt ; l'IRD 2 décroche toutes les 40 s, l'IRD 3 perd l'image toutes les 60 s.
- Sans FSH4 joignable : l'événement « Appareil injoignable » du FSH4 reste en cours, les porteuses indiquent la raison de l'absence de niveau RF, et la puissance d'entrée des IRD tient lieu de niveau RF.

## Porteuses et IRD

- Chaque IRD annonce la fréquence, la polarisation et le débit symbole qu'il reçoit. Les IRD qui reçoivent la même chose sont regroupés sous une même **porteuse** (par exemple « 12 583,0 V »).
- Une porteuse peut aussi être **saisie à la main** (**Réglages › Porteuses**), par exemple pour en suivre une seulement au FSH4. Les porteuses se renomment depuis la même page.
- Un IRD est reconnu par son **numéro de série** : s'il change d'adresse, on corrige l'adresse ; si un autre appareil répond à son adresse, rien n'est enregistré sous son nom et il passe en « injoignable » avec le motif « autre appareil à cette adresse ».

## La page

Un menu à gauche mène à chaque page ; l'adresse de la page (avec sa période) peut être mise en favori ou partagée, et le bouton Précédent du navigateur revient à la page d'avant. Sous le titre du menu, « ● en direct » (ou « ● connexion perdue » quand le flux en direct est coupé).

- **Vue d'ensemble** (`#/`) : quatre chiffres clés (IRD en service, alertes en cours, marge la plus basse, état du FSH4 avec **Pause** / **Reprendre** et un lien vers l'analyseur), puis **une carte par IRD** suivi, rangées par porteuse. La grille choisit le nombre de colonnes et la taille des cartes pour remplir l'écran : grandes cartes s'il y a peu d'IRD, cartes compactes quand il y en a beaucoup ; au-delà de ce qui tient, la page défile (12 IRD sans défilement en 1280 × 800). Chaque carte :
  - nom et badge d'état (OK, LOCK PERDU, PAS D'IMAGE, NON DÉCHIFFRÉ, FLUX ARRÊTÉ, SON ABSENT, ERREURS, MARGE BASSE, INJOIGNABLE) dans la couleur de la bande « État » ; si l'état n'est pas OK, la cause en clair, depuis quand, et le nombre d'alertes en cours ; la pastille donne au survol l'état de l'appareil et l'heure de sa dernière mesure ;
  - programme et porteuse ; marge (avec ses seuils), signal reçu, température (l'image et le son sont résumés par le badge d'état — « PAS D'IMAGE », « SON ABSENT »… — et détaillés sur la page de la porteuse) ;
  - à droite de la courbe de marge (à droite des valeurs sur les cartes compactes, sous la courbe sur une carte étroite), l'**aperçu de l'image décodée** (celui du « Decoding preview » de l'interface du DR5000, 320 × 180), rafraîchi toutes les ~2,5 s, avec l'heure de l'image. Une image qui n'a pas changé depuis plus de 10 s (IRD figé ou injoignable, direct coupé) reste affichée **grisée** avec « image de 17:44 » ; un IRD sans décodage renvoie une image rouge unie, toujours la même, donc grisée elle aussi. « pas d'image » tant qu'aucune n'a été reçue. Un clic sur l'aperçu l'agrandit (sans ouvrir la porteuse) ; Échap ou un clic le referme. Simple affichage : l'image n'est ni surveillée, ni enregistrée ;
  - marge des 10 dernières minutes, de 0 dB au-dessus, avec la ligne orange « alerte si chute » et la ligne rouge du minimum, et bande « État » dessous.

  Si le direct est coupé ou qu'un IRD n'a plus envoyé de mesure depuis 10 s, sa carte passe à « SANS NOUVELLES » (valeurs « — ») plutôt que de garder un « OK » figé. Un clic sur une carte ouvre la page de sa porteuse. À droite, les événements en cours et récents (un clic ouvre la porteuse calée sur l'événement) ; dessous, le **plancher de bruit** (marqueurs hors porteuse). Avertissement « Décalage LNB non réglé » en haut quand il s'applique.
- **Page d'une porteuse** (`#/porteuse/<clé>`, une entrée par porteuse dans le menu, avec le nombre d'alertes en cours) : nom, fréquence, polarisation, débit symbole, modulation, nombre d'IRD ; « Polarisation inconnue pour … » quand il le faut. **Période** : Direct (10 dernières minutes, mises à jour chaque seconde), 1 h, 24 h, 7 j ou **Période…** (dates libres) ; **glisser sur un graphe** resserre la période. Graphes communs à tous les IRD de la porteuse, même axe de temps, curseur synchronisé :
  - **marge** (une courbe par IRD), sur une échelle partant de 0 dB (décrochage), la marge minimale en rouge et, en Direct, le seuil de chute rapide de chaque IRD en pointillé orange ;
  - **puissance d'entrée** reçue par chaque IRD (le niveau RF quand il n'y a pas de FSH4 : une chute de puissance suivie par la marge désigne l'antenne ou le LNB, une marge qui baisse à puissance stable plutôt du bruit ou un brouillage) ;
  - **niveau RF** des marqueurs du FSH4 sur la porteuse, ou la raison de son absence (FSH4 injoignable ou sans réponse, en pause, aucun marqueur sur la porteuse) ;
  - **bande « État »**, une rangée par IRD : couleur de chaque seconde de mesure, la pire l'emporte — rouge lock perdu, violet image perdue / programme non déchiffré / flux d'entrée arrêté, orange BER non nul ou son absent, vert normal, gris pas de mesure. Elle montre aussi les défauts d'une seconde qui ne durent pas assez pour une alerte. Sur une longue période (points regroupés), chaque intervalle prend sa pire couleur. Au survol, la légende donne l'état de chaque IRD en clair.

  Puis **une section par IRD** : en Direct, sa carte (avec l'aperçu de l'image) et ses **détails** — image (codec, profil, niveau, résolution, images/s, format, PID), chaque voie audio configurée (codec, canaux, fréquence, débit, PID, active ou muette) et la **configuration** des sorties (formats SDI, ASI, IP, genlock, ce qu'envoie l'IRD quand l'image est perdue). L'IRD n'indique pas si ses sorties portent réellement un signal : c'est sa configuration, pas une mesure, et ces détails ne sont pas enregistrés. Hors Direct, un **résumé de la période** (sans aperçu : aucune image n'est gardée) : marge minimale, temps passé lock perdu, image ou flux perdu, dégradé (BER, son, marge), nombre d'alertes. À droite, les événements de la porteuse sur la période ; un clic cale la période sur l'événement.

  Sur une période passée, la porteuse est retrouvée parmi celles de la période par sa fréquence et sa polarisation : une porteuse qui n'existe plus (IRD retiré ou re-réglé, ancien site) s'affiche telle qu'elle était, avec la mention « n'existe plus aujourd'hui ». Les graphes en dBm couvrent au moins 10 dB, pour que les petites fluctuations ne remplissent pas l'écran.
- **Journal des événements** (`#/journal`) : tous les événements du parc (les 500 plus récents au plus), les plus récents en haut, avec gravité, type, source (IRD, porteuse, FSH4), début, fin ou « en cours », détail. Filtres : période (24 h, 7 j, 30 j, Période…), porteuse, IRD ou FSH4, « en cours seulement ». Un clic ouvre la porteuse où se trouvait l'IRD **au moment de l'événement** (même si elle n'existe plus), calée sur l'événement ; un événement du FSH4 ouvre l'analyseur.
- **Analyseur FSH4** (`#/fsh4`) : état (et raison s'il ne donne aucune mesure), adresse, polarisation de l'entrée, span, RBW, heure de la dernière mesure, **Pause** / **Reprendre** ; même sélecteur de période ; plancher de bruit ; **tous les marqueurs** de la période avec la porteuse à laquelle chacun était rattaché, et leur niveau minimal, moyen et maximal ; événements du FSH4.
- **Réglages** : pages **Appareils**, **Porteuses** et **Seuils d'alerte** (ci-dessous). Les erreurs s'affichent sous le bloc de l'action concernée.

| Événement | Déclenchement |
| --- | --- |
| Perte de lock / Perte de lock TS | démodulateur ou flux transport d'un IRD déverrouillé |
| BER élevé | BER > 1e-6 ; fermé après 5 s sans erreur |
| Chute de marge | médiane des 3 dernières s d'un IRD inférieure de 2 dB (réglable) à sa référence |
| Marge basse | marge sous le minimum fixe depuis 3 s (désactivée par défaut, réglable) ; fermée 0,5 dB au-dessus du minimum |
| **IRD seul en défaut** | sur une porteuse d'au moins 2 IRD joignables, un seul a une alerte de réception depuis 3 s pendant que les autres tiennent : cause probable équipement ou câblage. Image perdue et programme non déchiffré comptent aussi, mais seulement entre IRD qui décodent le même programme |
| Image perdue | IRD verrouillé, plus d'activité vidéo depuis 3 s |
| Son absent | au moins une voie audio configurée, aucune active depuis 3 s alors que l'image est là (avertissement) |
| Programme non déchiffré | programme chiffré non déchiffré depuis 3 s |
| Flux d'entrée arrêté | démodulateur verrouillé, débit d'entrée à 0 depuis 3 s |
| Défaut matériel | surchauffe, ventilateur ou alimentation signalé par l'IRD (relevé toutes les 10 s) |
| Journal IRD | nouveau message du journal de l'IRD (relevé toutes les 10 s) ; un message d'information est une simple trace, un avertissement ou une erreur reste ouvert jusqu'à sa fin. Les messages présents au démarrage sont ignorés |
| Chute du niveau marqueur | idem sur le marqueur central d'une porteuse, seuil 3 dB |
| Remontée du bruit | idem en hausse sur la moyenne des marqueurs hors porteuse, seuil 3 dB |
| Appareil injoignable | 3 échecs de suite (un IRD qui répond « occupé », HTTP 503, n'est pas en échec) |
| Réglage FSH4 modifié | marqueur allumé, éteint ou déplacé, span ou RBW changé |
| Appareils changés | nouvelle adresse du FSH4 |

Une perte de lock ferme les alertes image, son, déchiffrement et flux en cours (« réception perdue ») : seule la cause la plus haute reste affichée. De même, un flux d'entrée arrêté n'ouvre que « Flux d'entrée arrêté » (pas « Image perdue » en plus). Un IRD injoignable ferme ses alertes de réception, de décodage, de journal et de matériel (« IRD injoignable ») : elles se rouvrent à la première lecture si le défaut est toujours là. Un FSH4 mis en pause ferme son « Appareil injoignable ».

La référence d'une chute est la médiane de la minute précédente. Elle est figée pendant une alerte et environ deux minutes après (le temps que la fenêtre d'une minute ne contienne plus que des mesures d'après l'alerte), pour qu'un retour partiel ne la fasse pas dériver. Un changement de modulation, de débit symbole ou de porteuse sur un IRD remet sa référence de marge à zéro et ferme une chute de marge en cours (« réception modifiée »).

## IRD déverrouillé

Quand le démodulateur décroche, le DR5000 met sa puissance d'entrée, son débit symbole et sa fréquence intermédiaire à 0 et sa modulation à « automatic unknown unknown », et son décodeur garde ses dernières valeurs (voies audio « actives », débit vidéo). L'outil n'en tient pas compte : puissance, modulation, son et détails de décodage s'affichent « — » (seule la configuration des sorties reste), la porteuse garde le débit symbole du dernier lock, et les anciennes lectures à 0 dBm ne sont plus tracées. La marge, elle, est tracée à 0 dB : c'est le décrochage.

## Son des alertes dans la page (Réglages › Notifications)

- **Alerte critique** (perte de lock, image perdue, flux arrêté, programme non déchiffré, appareil injoignable…) : alarme à deux tons, répétée toutes les 30 s (réglable : 15 s, 1 min, jamais) jusqu'à ce qu'on clique **Acquitter** dans le bandeau rouge en haut de la page, ou que l'alerte se referme d'elle-même.
- La répétition s'arrête d'elle-même après 15 min (réglable : 5 min, 1 h, jamais) : un appareil éteint volontairement ne sonne pas indéfiniment ; le bandeau reste affiché, avec la mention « alarme arrêtée après … ».
- **Silence 1 h** (dans le bandeau) : plus d'alarme pour cet appareil pendant une heure, le temps d'une intervention ; cartes, journal et bandeau des autres appareils restent à jour.
- Une alerte acquittée que le service rouvre en redémarrant (il ferme les alertes en cours en s'arrêtant, avec la mention « — service arrêté » : à l'heure de l'arrêt, ou après un arrêt brutal — veille, coupure — à la dernière heure où il tournait, notée toutes les 30 s) ne resonne pas : l'acquittement vaut pour cet appareil et ce type d'alerte jusqu'à ce que l'alerte se termine vraiment. Les alertes déjà en cours au chargement de la page comptent comme acquittées.
- **Avertissement** (BER élevé, chute ou marge basse, son absent…) : un bip, une seule fois.
- Seules les alertes qui s'ouvrent après le chargement de la page sonnent : recharger la page ne relance pas l'alarme des alertes déjà en cours.
- **Son coupé / activé** depuis le menu (sous « en direct ») ; son coupé, le bandeau d'acquittement reste affiché. Les navigateurs n'autorisent le son qu'après un premier clic dans la page : le menu affiche alors « Activer le son ».
- **Notifications du navigateur** en option (à autoriser) : une notification apparaît même quand l'onglet n'est pas au premier plan ; un clic ouvre la page de l'alerte.
- Réglages propres à chaque navigateur (enregistrés dans le navigateur, pas dans `config.toml`) : deux postes peuvent avoir des réglages différents.

## Réglages › Seuils d'alerte : alertes sur la marge

Deux réglages, appliqués tout de suite à tous les IRD et enregistrés dans `config.toml` (`[detect]`) :

- **Chute rapide** (`margin_drop_db`, 2 dB par défaut) : « Chute de marge » si la marge perd autant en quelques secondes par rapport à la minute précédente. Détecte une dégradation soudaine, quel que soit le niveau de départ.
- **Marge minimale** (`margin_min_db`, 0 = désactivée) : « Marge basse » si la marge reste 3 s sous cette valeur. Détecte aussi une baisse lente, que la chute rapide ne voit pas.

Changer un seuil ferme une « Marge basse » en cours (« seuil modifié ») ; elle se rouvre 3 s plus tard si la marge est toujours sous le nouveau minimum.

## Réglages › Appareils : FSH4 et IRD

- **FSH4** : adresse, port, et **polarisation de son entrée** (le FSH4 ne voit qu'une sortie de LNB). Ses marqueurs ne se rattachent qu'aux porteuses de cette polarisation.
- **Pas de FSH4 sur le site** : laisser l'adresse vide (`host = ""` dans `[fsh]`). Le service ne le lit pas, n'ouvre aucune alerte « injoignable », la vue d'ensemble affiche « Analyseur FSH4 : aucun » et les pages de porteuse n'ont plus de graphe « Niveau RF » ; la puissance d'entrée des IRD tient lieu de niveau RF. Après un changement de FSH4, la « dernière mesure » de l'ancien n'est plus affichée.
- **IRD** : tableau des IRD suivis (nom modifiable, adresse modifiable, **polarisation**, porteuse reçue, état), **Retirer** (son historique est conservé), champ **Ajouter un IRD** par son adresse.
- **Polarisation d'un IRD** : en général le LNB est alimenté par un autre équipement (répartiteur, multiswitch…) et le réglage de polarisation de l'IRD ne veut rien dire. En *automatique*, l'outil ne le prend en compte que si l'IRD alimente lui-même le LNB ; sinon la polarisation est inconnue et la section de la porteuse affiche « Polarisation inconnue pour … : à renseigner dans Réglages › Appareils ». Choisir *verticale* ou *horizontale* une fois pour toutes : c'est ce qui sépare deux transpondeurs V et H de même fréquence.
- **Rechercher sur le réseau** parcourt les réseaux auxquels la machine est branchée (lus avec `ifconfig` sur Mac et Linux, `ipconfig` sous Windows ; chaque réseau limité à 254 adresses) : **Utiliser** pour un FSH, **Ajouter** pour un IRD Ateme pas encore suivi. La recherche n'envoie que des requêtes de lecture : `*IDN?` sur le port 5555, et la requête d'état de l'interface web des IRD.

Tout est appliqué sans redémarrer le service et enregistré dans `config.toml` (`[[irds]]`, `[[carriers]]`, `[fsh]`).

## Marqueurs FSH4

Le service lit tous les marqueurs allumés sur le FSH4 (jusqu'à 6). Chaque marqueur est rattaché à la porteuse dont la bande le contient ; un marqueur hors de toute porteuse mesure le **plancher de bruit**. Les marqueurs se placent sur l'appareil, par exemple un au centre de chaque porteuse et deux sur le bruit de part et d'autre.

- Si les marqueurs sont sous 3 GHz, la page avertit « Décalage LNB non réglé sur le FSH4 : les marqueurs sont en fréquence intermédiaire » : le décalage LNB doit être réglé sur l'appareil pour que les marqueurs soient en fréquence satellite.
- Le service n'allume ni n'éteint jamais un marqueur : il vérifie qu'un marqueur est toujours allumé avant de lire son niveau (sur ce FSH4, lire un marqueur éteint le rallume). Il laisse aussi 200 ms de silence entre deux cycles (`min_idle_s`), sans quoi le FSH4 ne rafraîchit plus sa mesure.
- `noise_markers` dans `[detect]` permet d'imposer les marqueurs de bruit (vide par défaut : choix automatique).

## FSH4 sans l'option FSH-K40

À partir du firmware V1.30, un FSH4 ne répond par le réseau (ou l'USB) aux commandes de mesure qu'avec l'option de commande à distance **R&S FSH-K40** (licence 1304.5606.02). Sans elle, il répond à `*IDN?` mais pas à `FREQ:SPAN?`, et sa file d'erreurs donne -113 « Undefined header ». L'outil le reconnaît et l'écrit en clair : « le FSH4 ne connaît pas FREQ:SPAN? (erreur -113) : option de commande à distance R&S FSH-K40 absente ? ». Seules les commandes de fichiers (`MMEM`) restent ouvertes : pas de mesure en continu possible sans l'option.

## Pause du FSH4

Tant que le service lit le FSH4, l'analyseur reste en mode **Remote** : ses touches sont bloquées. **Pause** (vue d'ensemble, page Analyseur FSH4 ou Réglages › Appareils) ferme la connexion et rend la main sur l'appareil ; **Reprendre** relance la lecture. Changer l'adresse ou la polarisation du FSH4 pendant la pause la laisse en pause : la nouvelle adresse n'est utilisée qu'à la reprise.

## config.toml et lecture seule

Tout est dans `config.toml` : appareils, porteuses saisies, cadences, seuils de détection, port de la page. Les appareils et les porteuses se gèrent depuis la page ; pour le reste, relancer le service après une modification.

Le service ne fait que lire : il ne change aucun réglage du FSH4 ni des IRD. Les marqueurs du FSH4 se placent depuis l'appareil. Ce qui est envoyé, en tout et pour tout :

- **FSH4** (SCPI, port 5555) : `FREQ:SPAN?`, `BAND?` et `CALC:MARKn?` toutes les 10 s, `CALC:MARKn:X?` puis `CALC:MARKn:Y?` seulement pour un marqueur que `CALC:MARKn?` vient de dire allumé ; `*IDN?` pendant une recherche réseau.
- **IRD** (`POST /xml-rpc`) : `<refresh/>` chaque seconde, `<status type="status"/>` et `<logs type="logs"/>` toutes les 10 s.

## Données

- Base SQLite : `data/monitoring.db` (environ 30 Mo par jour et par marqueur actif côté FSH4, un peu moins par IRD). Purge automatique des données de plus de `retention_days` jours (30 dans la configuration actuelle ; 0 = tout garder).
  - `fsh_marker` : niveau et fréquence de chaque marqueur, 4 fois par seconde ;
  - `ird_sat` : chaque lecture d'IRD (réception, décodage, programme, code de la bande « État ») ;
  - `ird_health` : températures et défauts matériels, toutes les 10 s ;
  - `events`, `device_status`, `ird_names` : alertes, états des appareils, noms des IRD.
  Les détails (codecs, PID, configuration des sorties) ne sont gardés qu'en mémoire, pour le Direct.
- Journal : `logs/monitoring.log` (rotation, 5 fichiers de 5 Mo).

## API

Toutes les réponses sont en JSON ; horodatages en millisecondes.

| Requête | Rôle |
| --- | --- |
| `GET /api/status` | état des appareils, dernières mesures, porteuses, réglages utiles à la page |
| `GET /api/series?from=…&to=…&max_points=…` | séries FSH4 et IRD d'une plage (agrégées au-delà de `max_points`) |
| `GET /api/events?from=…&to=…` | événements d'une plage |
| `GET /api/devices`, `GET /api/carriers` | appareils suivis, porteuses actuelles |
| `GET /api/live` | flux direct (Server-Sent Events) : mesures, états, événements |
| `GET /api/ird/<série>/image.jpg` | dernier aperçu de l'image décodée d'un IRD (JPEG, gardé en mémoire seulement ; 404 tant qu'il n'y en a pas) ; son heure est `thumb_ts` dans les mesures d'IRD |
| `POST /api/fsh`, `/api/fsh/pause`, `/api/fsh/resume` | FSH4 : adresse et polarisation, pause, reprise |
| `POST /api/irds`, `/api/irds/<série>`, `/api/irds/<série>/remove` | IRD : ajout, nom/adresse/polarisation, retrait |
| `POST /api/carriers`, `/api/carriers/<clé>`, `/api/carriers/<clé>/remove` | porteuses saisies : ajout, nom, suppression |
| `POST /api/detect` | seuils de marge : `margin_drop_db` (0,5 à 20), `margin_min_db` (0 à 30, 0 = désactivée) |
| `POST /api/discover` | recherche réseau |

Les `POST` ne sont acceptés que depuis la page elle-même (même origine). Toute requête dont le nom d'hôte n'est ni `localhost` ni une adresse IP est refusée (protection contre le rattachement DNS : une page d'un autre site ne peut pas lire les données du service). Les fichiers de la page sont revalidés à chaque chargement (`Cache-Control: no-cache`) : après une mise à jour, le navigateur ne garde pas d'anciens scripts.

## Limites en détail

- FSH4 : le firmware V1.20 refuse `TRAC:DATA?`. La commande existe en V3.30 ; les firmwares sont sur le site R&S, derrière un compte myRohde&Schwarz. Après mise à jour : lecture de trace et spectrogramme synchronisé.
- BER : à une lecture IRD par seconde, un BER non nul n'est vu qu'exceptionnellement en DVB-S2 (d'où la bande « État » au lieu d'un graphe).
- « Son absent » repose sur l'activité des voies décodées (pas de niveaux audio). Pour surveiller les sorties SDI, ASI ou IP, il faut un équipement en aval.
- Points connus restant à traiter : « À faire » dans [HISTORIQUE.md](HISTORIQUE.md).
