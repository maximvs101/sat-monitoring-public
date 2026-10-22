# Plusieurs porteuses et plusieurs IRD — conception

21 octobre 2026 · relu par un constructeur, questions tranchées · à valider par Max

Prolonge `2026-10-17-monitoring-sat-design.md` (le prototype à une porteuse et un IRD), qui reste valable pour tout ce qui n'est pas repris ici.

## 1. Intention

L'outil est emmené d'une installation à l'autre ; ce qu'il faut surveiller change à chaque fois. Max doit pouvoir :

- suivre **plusieurs porteuses** en même temps, éventuellement très éloignées (autres transpondeurs, autre polarisation) ;
- mettre **plusieurs IRD sur une même porteuse** pour les **comparer** : si un seul décroche alors que les autres tiennent, le problème est l'équipement, pas la réception ;
- mettre des IRD sur **des porteuses différentes** quand ce sont des programmes différents.

Critères de réussite :

- ajouter, retirer ou renommer un IRD depuis la page, sans éditer de fichier ni redémarrer ;
- chaque porteuse suivie a sa section sur la page, avec ses IRD comparés sur les mêmes graphes ;
- un IRD qui décroche seul sur sa porteuse produit l'alerte « IRD seul en défaut » ;
- l'historique du 17/10 reste consultable.

## 2. Ce qui est décidé (par Max)

| Sujet | Décision |
| --- | --- |
| Analyseur | **Un seul** (le FSH4). Il **reste à la main de l'opérateur** : le service ne change jamais ses réglages, comme aujourd'hui. |
| Mesure RF d'une porteuse | Seulement si un marqueur du FSH4 tombe dans sa bande, dans le span choisi par l'opérateur. Sinon la porteuse est suivie par ses seuls IRD. |
| Définition des porteuses | **Les deux** : formées automatiquement à partir des IRD (chacun annonce ce qu'il reçoit) **et** saisies à la main (pour une porteuse suivie seulement au FSH4, par exemple). |
| Page | **Disposition A** : cases à cocher pour choisir les porteuses affichées ; une section par porteuse avec ses graphes RF, marge et BER / lock, une courbe par IRD. |
| Nouvelle alerte | **« IRD seul en défaut »** quand un IRD se dégrade alors que les autres IRD de la même porteuse tiennent. |
| Entrée du FSH4 | Réglage de polarisation de l'entrée du FSH4 dans **Appareils…** ; les marqueurs ne se rattachent qu'aux porteuses de cette polarisation ; avertissement si les marqueurs sont sous 3 GHz (décalage LNB non réglé sur l'appareil). |

## 3. Non-objectifs et anti-objectifs

Non-objectifs : piloter le FSH4 (centre, span, marqueurs), plusieurs analyseurs, lecture de trace / spectrogramme (toujours bloquée par le firmware V1.20), alertes par mail, accès à distance.

Anti-objectifs (échecs même si « ça marche ») :

- toute écriture vers le FSH4 ou vers un IRD ;
- une alerte « IRD seul en défaut » quand les IRD comparés ne reçoivent pas la même porteuse, ou quand l'IRD est simplement injoignable sur le réseau ;
- perdre ou rendre illisible l'historique existant ;
- une page qui ralentit ou devient illisible avec 6 à 8 IRD.

## 4. Ce que l'IRD fournit (relevé le 21/10/2026 sur 192.168.10.141)

La réponse `<refresh/>` contient, en plus du bloc `probe/input/sat` déjà utilisé :

- `presets/v1[@id=0]/channel/input/sat/downlinkFrequency` (kHz, fréquence RF configurée, ex. `12583060`) ;
- `…/sat/oscillatorFrequency` (kHz, oscillateur du LNB, ex. `10600000`) ;
- `…/sat/lnb/polarization` (`vertical` / `horizontal`) ;
- `probe/input/sat/frequency` (IF mesurée, kHz), `symbolRate`, `rollOff`.

La fréquence RF reçue vaut `IF + oscillateur` (ici 1 983,023 + 10 600 = 12 583,023 MHz, cohérent avec `downlinkFrequency`). La requête `<status/>` donne `systemName` (nom de l'IRD).

À vérifier par le constructeur : que `presets/v1[@id=0]` est bien la configuration **active** quand l'IRD a plusieurs préréglages ; sinon trouver l'indicateur du préréglage courant. En repli, utiliser `IF + oscillateur` si l'oscillateur est lisible, sinon la seule `downlinkFrequency`.

## 5. Modèle

### IRD

- Liste d'IRD dans `config.toml` (`[[ird]]` : `serial`, `name`, `host`). Un IRD est identifié par **son numéro de série** (`system/id` de la requête `<status/>`, ex. `1000-00001`), lu à l'ajout et vérifié à chaque reconnexion : c'est lui qui relie les mesures à l'IRD, pas l'adresse (qui change d'un site à l'autre) ni le nom (modifiable). *(Choix du concepteur : le 21/10, l'IRD de 192.168.10.141 s'est révélé être un autre appareil — « IRD 1 », n° 1000-00001 — que l'« IRD 5 » du 17/10, n° 1000-00002.)*
- Si l'appareil trouvé à une adresse n'a pas le numéro de série attendu, l'IRD passe en état « autre appareil à cette adresse » (alerte « IRD injoignable » avec ce motif) au lieu d'enregistrer des mesures sous la mauvaise identité.
- Nom par défaut : `systemName` lu sur l'IRD à l'ajout ; modifiable depuis la page.
- Une boucle de collecte par IRD (1 lecture/s chacune), comme aujourd'hui pour l'IRD unique.
- À chaque lecture, en plus des champs actuels : fréquence RF (kHz), polarisation, débit symbole, roll-off.

### Porteuses

- Chaque lecture IRD enregistre la fréquence RF, la polarisation et le débit symbole ; l'appartenance à une porteuse se **recalcule** à la requête à partir de ces valeurs (pas de table de porteuses figée), ce qui garde l'historique juste quand les porteuses changent. Pendant un décrochage, l'IF mesurée tombe à 0 : la fréquence RF retenue est alors la `downlinkFrequency` configurée. *(Choix du concepteur.)*
- **Porteuse automatique** : un ou plusieurs IRD dont la fréquence RF et la polarisation coïncident. Deux IRD sont sur la même porteuse si leurs fréquences RF diffèrent de moins de la moitié du plus petit débit symbole et qu'ils ont la même polarisation. Nom par défaut : « 12 583,0 V » ; renommable.
- **Porteuse manuelle** : nom, fréquence RF (MHz), polarisation (facultative), débit symbole (MS/s, sert à calculer la bande). Stockée dans `config.toml` (`[[carrier]]`). Une porteuse manuelle qui coïncide avec une porteuse automatique la fusionne (même règle de coïncidence) et lui donne son nom.
- **Bande d'une porteuse** : centre ± débit symbole × (1 + roll-off) / 2 (roll-off 0,20 si inconnu).
- Les noms donnés aux porteuses automatiques sont mémorisés (par fréquence + polarisation) pour survivre à un redémarrage.

### Marqueurs FSH4

- **Entrée du FSH4** (décidé par Max) : réglage dans **Appareils…**, polarisation `V`, `H` ou inconnue, stocké dans `config.toml`. Le FSH4 ne voit qu'une sortie de LNB ; le décalage LNB reste réglé sur l'appareil par l'opérateur (le service ne le touche pas).
- Un marqueur appartient à la porteuse dont la bande contient sa fréquence **et** dont la polarisation est compatible avec l'entrée du FSH4 (une polarisation inconnue, d'un côté ou de l'autre, est compatible). S'il reste une ambiguïté (deux porteuses compatibles à la même fréquence), le marqueur n'est rattaché à aucune et une note l'indique sur la page.
- Si les marqueurs allumés sont sous 3 GHz, la page avertit : « Décalage LNB non réglé sur le FSH4 : les marqueurs sont en fréquence intermédiaire » ; ces marqueurs ne sont rattachés à aucune porteuse et ne servent pas non plus au bruit (pas d'alerte de bruit tant que l'avertissement est affiché).
- Un marqueur hors de toute porteuse est un marqueur de **bruit** ; l'alerte « Remontée du bruit » porte sur la moyenne de ces marqueurs. Le réglage `noise_markers` reste possible et, s'il est rempli, remplace ce choix automatique.
- La chute de niveau RF d'une porteuse se juge sur son marqueur le plus proche du centre.

### Polarisation des IRD (ajout du 22/10/2026, décidé par Max)

Sur la plupart des sites, le LNB est alimenté par un autre équipement que les IRD : le réglage `lnb/polarization` d'un IRD n'est alors pas fiable. Il n'est lu que si `lnb/isPowered` vaut `true` ; sinon la polarisation est inconnue. Chaque IRD a une polarisation imposable dans **Appareils…** (`[[irds]] polarization`, absente = automatique) qui prime sur la valeur lue, et qui est enregistrée avec chaque mesure. Une porteuse dont un IRD a une polarisation inconnue l'affiche (« Polarisation inconnue pour … : à renseigner dans Appareils… »). La clé d'une porteuse n'est reprise d'un calcul à l'autre qu'à polarisation identique.

## 6. Alertes

- Les alertes existantes deviennent **par IRD** (perte de lock, perte de lock TS, BER élevé, chute de marge, IRD injoignable) et **par porteuse** pour la chute de niveau RF. Chaque événement porte sa source (IRD ou porteuse), affichée dans la liste.
- **IRD seul en défaut** (gravité `warning`, ou `critical` si l'IRD a perdu le lock) :
  - porteuse ayant au moins 2 IRD joignables ;
  - ouverte quand un IRD a une perte de lock, une perte de lock TS, un BER élevé ou une chute de marge en cours, **et** qu'aucun autre IRD de la même porteuse n'a d'alerte de réception en cours depuis au moins 3 s (pour laisser aux autres le temps de décrocher aussi) ;
  - fermée quand l'IRD n'a plus d'alerte de réception, ou quand un autre IRD de la porteuse se dégrade à son tour (ce n'est alors plus « seul ») ;
  - un IRD injoignable (réseau) ne déclenche jamais cette alerte et n'est pas compté parmi les IRD qui « tiennent » ;
  - détail : « IRD 6 seul en défaut sur 12 583,0 V (IRD 5 normal) — cause probable : équipement ou câblage ».

## 7. Page (disposition A)

- **Bandeau** : pastille du FSH4, une pastille par IRD (nom, état), bouton **Appareils…**, bouton **Porteuses…**.
- **Sélecteur** : en Direct, les porteuses actuelles (IRD configurés + porteuses manuelles) ; en Historique, les porteuses présentes dans la plage consultée, y compris celles d'IRD retirés depuis, sous leur dernier nom connu. *(Choix du concepteur.)* Une case à cocher par porteuse (nom, fréquence, polarisation, nombre d'IRD, mention « RF » si un marqueur la couvre). Le choix est mémorisé dans le navigateur (préférence d'affichage, pas une donnée partagée).
- **Une section par porteuse cochée**, dans l'ordre des fréquences :
  - en-tête : nom, fréquence, polarisation, débit symbole, pastilles des IRD avec leur marge courante ;
  - graphe RF (marqueurs de la porteuse) — remplacé par « Pas de mesure RF (porteuse hors du span du FSH4) » s'il n'y en a pas ;
  - graphe marge : une courbe par IRD ;
  - graphe BER : une courbe par IRD, échelle log comme aujourd'hui ; pertes de lock en bandes de couleur, une rangée par IRD.
  - Curseur et zoom synchronisés sur toutes les sections ; Direct / Historique comme aujourd'hui.
- **Graphe du bruit** : sous les sections, s'il y a des marqueurs de bruit.
- **Événements** : liste unique à droite, avec la source de chaque événement ; filtrée sur les porteuses cochées.
- **Appareils…** : adresse du FSH4 et polarisation de son entrée ; liste des IRD (nom, adresse, porteuse reçue, état) avec **Ajouter**, **Retirer**, **Renommer** ; la recherche réseau propose **Ajouter** pour chaque IRD trouvé (et **Utiliser** pour le FSH4).
- **Porteuses…** : liste des porteuses (automatiques et manuelles) ; renommer ; ajouter / supprimer une porteuse manuelle.

## 8. Données existantes

La base contient deux séries IRD distinctes (relevé du 21/10) :

| Période | Appareil | Fréquence RF reconstituée | Polarisation |
| --- | --- | --- | --- |
| 17/10 (5 625 lectures, 192.168.20.145) | « IRD 5 », n° 1000-00002 | IF + 9 750 MHz (1 470,8 + 9 750 = 11 220,8 MHz) | inconnue |
| 21/10 à partir des `devices_changed` de 17:19–17:20 (192.168.10.141) | « IRD 1 », n° 1000-00001 | IF + 10 600 MHz (1 983,0 + 10 600 = 12 583,0 MHz) | verticale |

- La coupure entre les deux séries est le premier événement `devices_changed` du 21/10 ; la migration crée les deux IRD (numéros de série ci-dessus) et rattache chaque lecture et chaque événement IRD à l'un ou à l'autre. *(Choix du concepteur.)*
- Une polarisation inconnue est compatible avec n'importe quelle polarisation pour former une porteuse.
- Les marqueurs FSH4 du 17/10 se rattachent à la porteuse 11 220,8 MHz par leur fréquence, comme les nouveaux.
- `noise_markers = [4, 5]` (réglé pour le site du 17/10) est vidé à la migration : le choix automatique du bruit (marqueurs hors porteuse) s'applique. *(Choix du concepteur.)*
- La migration est automatique au démarrage, sans perte, et testée sur une copie de la base actuelle.

## 9. Laissé au constructeur

- Schéma exact des tables (colonnes `ird`, `rf_khz`, `polarization`… et source des événements) et format JSON de l'API.
- Découpage de la page en composants et gestion du graphe par section (création / destruction quand la sélection change).
- Mesure de la charge avec 8 IRD (8 réponses de ~260 Ko par seconde) et, si besoin, allègement.
