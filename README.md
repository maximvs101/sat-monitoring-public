# Monitoring satellite FSH4 + IRD

Surveillance d'une réception satellite : un analyseur de spectre **R&S FSH4** et des décodeurs **Ateme DR5000** sont relevés en continu, sur une même ligne de temps, et une page web locale montre le direct, l'historique et les alertes. On relie ainsi une coupure ou une dégradation d'un décodeur à ce qui se passait côté RF, et on compare les IRD qui reçoivent la même porteuse.

- **Lecture seule** : l'outil ne change aucun réglage des appareils.
- **Rien à installer** : Python 3.11+ (bibliothèque standard uniquement), ou un exécutable Windows autonome.
- **Alertes** : perte de lock, chute ou marge basse, BER, image perdue, son absent, programme non déchiffré, flux arrêté, défaut matériel, IRD seul en défaut sur sa porteuse, chute du niveau RF, remontée du bruit, appareil injoignable.

## Lancer

- **Windows, sans rien installer** : extraire l'archive `MonitoringSat-Windows` (voir [Exécutable Windows](#exécutable-windows)) dans un dossier fixe et double-cliquer sur `MonitoringSat.exe`. L'exécutable n'est pas signé : « Informations complémentaires » puis « Exécuter quand même ».
- **Windows avec Python** : double-cliquer sur `monitoring.bat`.
- **Mac** : double-cliquer sur `monitoring.command`.

La page **http://localhost:8080** s'ouvre. Le service tourne tant que sa fenêtre reste ouverte (la machine ne se met pas en veille) ; Ctrl-C ou fermer la fenêtre pour l'arrêter. La machine doit être sur le réseau du FSH4 et des IRD.

Au premier lancement, `config.toml` est créé à partir de `config.example.toml` : renseigner le FSH4 et ajouter les IRD depuis **Réglages › Appareils** (ou **Rechercher sur le réseau**). Sans FSH4 joignable, l'outil fonctionne avec les IRD seuls : leur puissance d'entrée tient lieu de niveau RF.

**Démonstration sans appareils** (trois faux IRD et un faux FSH4, qui décrochent régulièrement) :

```bash
python3 -m tests.demo_server 8091
```

puis **http://localhost:8091**.

## La page

- **Vue d'ensemble** : chiffres clés, une carte par IRD (état, cause et durée d'un défaut, marge, signal reçu, température, marge des 10 dernières minutes), événements en cours, plancher de bruit.
- **Une page par porteuse** : période au choix (Direct, 1 h, 24 h, 7 j, dates libres, ou glisser sur un graphe) ; marge, puissance d'entrée, niveau RF et bande « État » de tous ses IRD sur le même axe ; détails ou résumé de chaque IRD ; ses événements.
- **Journal** des événements, filtrable ; un clic ouvre la porteuse au moment de l'événement.
- **Analyseur FSH4** : état, réglages lus, marqueurs et plancher de bruit, pause pour rendre la main sur l'appareil.
- **Réglages** : appareils, porteuses, seuils d'alerte sur la marge, son des alertes.
- **Son des alertes** : alarme répétée pour une alerte critique jusqu'à « Acquitter » (15 min au plus, « Silence 1 h » pendant une intervention), bip pour un avertissement, notification du navigateur en option.

Tout le détail (alertes et leurs seuils, marqueurs, réglages, données, API) : **[docs/GUIDE.md](docs/GUIDE.md)**.

## Limites

- Firmware FSH4 V1.20 : la trace n'est pas lisible à distance, l'outil relève jusqu'à 6 **marqueurs** placés sur l'appareil, pas le spectre complet.
- En DVB-S2, le BER passe de zéro à la perte de lock en moins d'une seconde : la marge et le niveau des marqueurs sont les vrais indicateurs d'alerte précoce.
- L'IRD n'expose ni les compteurs TR 101 290 ni les niveaux audio, ni l'état réel de ses sorties (seulement leur configuration).

## Exécutable Windows

Le workflow GitHub Actions `.github/workflows/windows.yml` lance les tests sous Windows, fabrique `MonitoringSat.exe` avec PyInstaller (Python et la page embarqués), vérifie qu'il démarre, puis produit l'archive `MonitoringSat-Windows` (avec `LISEZMOI-Windows.txt`).

- **À la main** : onglet **Actions** → « Exécutable Windows » → **Run workflow** ; l'archive est dans la rubrique **Artifacts** de l'exécution (90 jours).
- **Version publiée** : pousser un tag `v…` (par exemple `v1.0.0`) ; l'archive est jointe à la **Release**.

Un simple push ne lance pas le workflow.

## Tests

```bash
python3 -m unittest discover tests
node --test "web/tests/*.test.js"
```

307 tests Python et 26 tests de la page, sans appareil ni réseau (faux FSH4 et faux IRD locaux) ; ils passent sur Mac et sous Windows.

## Documentation

- [docs/GUIDE.md](docs/GUIDE.md) : guide d'utilisation détaillé.
- [docs/HISTORIQUE.md](docs/HISTORIQUE.md) : évolutions, essais sur site, points à faire.
- `docs/propositions/` : proposition de notifications (seul le son dans la page a été fait).
- `docs/superpowers/` : conceptions et plans de chaque étape (prototype, plusieurs porteuses, état des IRD, refonte de la page).
