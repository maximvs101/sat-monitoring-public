# État des IRD : décodage, matériel, journal — conception

22 octobre 2026 · décidé en séance avec Max (« ajoute tout et réduis le BER à une bande »)

Prolonge `2026-10-21-multi-porteuses-design.md`.

## 1. Intention

Le BER d'un IRD reste à 0 jusqu'aux toutes dernières secondes avant une perte de lock (DVB-S2) : son graphe est presque toujours une ligne plate. En revanche l'IRD expose d'autres informations qui permettent de distinguer une cause **RF** d'une cause **flux** ou **équipement**, objectif de l'étude de départ : une image perdue avec une marge intacte désigne le flux ; un IRD en surchauffe ou seul à perdre l'image désigne l'appareil.

Réussite :

- les nouvelles mesures sont relevées et enregistrées pour chaque IRD, sans ralentir la collecte (1 lecture/s) ;
- les nouvelles alertes s'ouvrent et se ferment correctement ;
- le graphe BER est remplacé par une bande « État » compacte, lisible d'un coup d'œil avec plusieurs IRD ;
- l'en-tête d'un IRD résume son état ;
- tout reste en lecture seule.

Non-objectifs : compteurs TR 101 290 ou continuity counter sur l'entrée satellite (non exposés par l'IRD), niveaux audio, vignettes vidéo, pilotage de l'IRD.

## 2. Ce que l'IRD fournit (relevé le 22/10/2026 sur l'IRD 1, 192.168.10.141)

Dans la réponse `<refresh/>` déjà lue chaque seconde, sous `probe` :

| Champ | Chemin | Exemple |
| --- | --- | --- |
| Activité vidéo | `decode/currentProgram/video/hasActivity` | `true` |
| Débit vidéo (bit/s) | `decode/currentProgram/video/decoded/bitrate` | `13656952` |
| Format d'image | `decode/currentProgram/video/mainFormat` | `hd_1920x1080i_50` |
| Voies audio (16) | `decode/currentProgram/audio[@id=0..15]` : `configuration/mode` (`decode` ou `disable`), `status/hasActivity`, `status/decoded/bitrate` | 5 voies en `decode`, toutes actives, 384 000 bit/s |
| Débit du flux d'entrée (kbit/s) | `input/bitrate` (déjà stocké : `bitrate_kbps`) | `15678` |
| Programme chiffré / déchiffré | `decode/currentProgram/scrambled`, `.../descrambled` | `true` / `true` |
| Programme décodé | `tsDescriptor/program[selected=true]` : `programNumber`, `serviceName` | `1`, « P100/2/CHN/FEED_A1/MAIN » |

Requête `xml=<status type="status"/>` (déjà utilisée pour l'identification) : `system/fpgaTemp`, `system/cpuTemp` (°C), `system/overheat`, `system/fanFailure`, `system/powerFailure` (booléens).

Requête `xml=<logs type="logs"/>` (utilisée par l'interface web de l'IRD) : `logs/message[@id]` avec `nId` (identifiant), `nTimeBegin` et `nTimeEnd` (µs, `-1` = en cours), `cDetails` (texte), `level` (`information`, `warning`, …). Relevé du 22/10 : un seul message, « Program is scrambled », niveau `information`, resté à `nTimeEnd = -1` depuis le démarrage de l'IRD — il existe des messages d'information permanents.

L'IRD accepte les trois requêtes en parallèle sans erreur (5 essais : `status` et `logs` en 20 à 200 ms, `refresh` en ~0,33 s).

## 3. Mesures

- À chaque lecture (1/s) : activité et débit vidéo, format d'image, nombre de voies audio configurées (mode ≠ `disable`) et actives, débit audio de la première voie active, chiffré, déchiffré, numéro et nom du programme décodé. Champ absent → valeur inconnue (pas d'alerte).
- Toutes les 10 s par IRD : requêtes `status` et `logs`, dans la boucle de collecte de l'IRD (elles sont rapides et l'IRD les accepte en parallèle), avec un délai maximal de 1 s chacune *(choix du concepteur)*. Leur échec n'est pas un échec de lecture de l'IRD (la mesure principale reste valable) ; il est journalisé.
- Tout est enregistré avec la mesure (nouvelles colonnes de `ird_sat`) ; l'état matériel dans une table à part, une ligne par relevé.

## 4. Alertes (par IRD, source `ird:<série>`, détail préfixé du nom de l'IRD)

| Type | Libellé | Ouverture | Fermeture | Gravité |
| --- | --- | --- | --- | --- |
| `video_lost` | Image perdue | démodulateur et flux transport verrouillés, activité vidéo fausse depuis 3 s | activité vidéo revenue | critical |
| `audio_silent` | Son absent | au moins une voie audio configurée, aucune active depuis 3 s, alors que l'image est là | une voie active | warning |
| `not_descrambled` | Programme non déchiffré | chiffré et non déchiffré depuis 3 s | déchiffré, ou plus chiffré | critical |
| `ts_stopped` | Flux d'entrée arrêté | démodulateur verrouillé et débit d'entrée à 0 depuis 3 s | débit revenu | critical |
| `hw_fault` | Défaut matériel | surchauffe, ventilateur ou alimentation signalé (détail : lesquels) | plus aucun signalé | critical |
| `ird_log` | Journal IRD | nouveau message du journal de l'IRD | `nTimeEnd` renseigné, ou message disparu du journal | `information` → info, `warning` → warning, autre → critical |

- Journal : au premier relevé d'un IRD (démarrage du service, ajout de l'IRD), les messages présents sont mémorisés sans créer d'événement ; seuls les suivants en créent. Messages datés à l'heure du Mac (l'horloge de l'IRD peut être fausse). Dédoublonnage par `nId`. Si les `nId` reculent (IRD redémarré, journal vidé), on repart comme au premier relevé. **Un message de niveau `information` crée un événement déjà fermé** (simple trace), car certains restent ouverts indéfiniment sur l'IRD ; les messages `warning` et au-delà restent ouverts jusqu'à `nTimeEnd` ou leur disparition du journal. *(Choix du concepteur.)*
- **Hiérarchie des alertes** *(choix du concepteur)* : quand le démodulateur ou le flux transport se déverrouille, les alertes `video_lost`, `audio_silent`, `ts_stopped` et `not_descrambled` en cours sont fermées avec le motif « réception perdue » et ne se rouvrent pas tant que la réception n'est pas revenue ; quand l'IRD devient injoignable, ces alertes et `hw_fault` sont fermées avec le motif « IRD injoignable ». Un délai de 3 s interrompu repart de zéro.
- « IRD seul en défaut » compte aussi `video_lost` et `not_descrambled` comme dégradation, mais **seulement entre IRD qui décodent le même programme** (même `programNumber`) ; deux IRD d'une même porteuse sur des programmes différents ne se comparent que sur la réception. *(Choix du concepteur.)*
- Retrait d'un IRD : ces alertes sont fermées avec le motif « IRD retiré », comme les autres.

## 5. Page

- Le graphe BER de chaque porteuse est remplacé par une **bande « État »** : une rangée par IRD, sur la ligne de temps commune, colorée selon la priorité :
  rouge = lock perdu ; violet = image perdue, programme non déchiffré ou flux d'entrée arrêté ; orange = BER non nul ou son absent ; vert = normal ; gris = pas de mesure.
  Les couleurs viennent **des mesures brutes de chaque seconde** : un violet d'une seconde peut apparaître sans alerte, c'est voulu (la bande montre ce qui s'est passé, la liste ce qui a duré). En historique agrégé, chaque intervalle prend la pire couleur de ses mesures. Le défaut matériel n'est pas dans la bande (en-tête et événements). *(Choix du concepteur.)*
  Au survol, la légende donne l'état de chaque rangée en clair.
- En-tête de chaque IRD dans sa porteuse : nom du programme décodé, marge, image (format abrégé + débit, ex. « 1080i50 13,7 Mb/s » ou « pas d'image »), son (« son 5/5 » voies actives/configurées), débit d'entrée (« TS 15,7 Mb/s »), température FPGA, et BER s'il est non nul.
- Les nouveaux types d'événements ont un libellé français dans la liste.

## 6. Laissé au constructeur

- Noms exacts des colonnes et de la table d'état matériel ; forme des messages SSE.
- Rendu exact de la bande (hauteur, légende) dans le style des graphes existants.
