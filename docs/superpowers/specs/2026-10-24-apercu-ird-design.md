# Aperçu de l'image décodée des IRD — design

Date : 24/10/2026. Petite évolution, design court.

## Intention

Voir dans l'outil l'image que décode chaque IRD (le « Decoding preview » de l'interface du DR5000), pour constater
d'un coup d'œil qu'il y a bien une image à l'antenne, sans ouvrir l'interface de chaque IRD.

## Objectifs, non-objectifs, anti-objectifs

- **Objectif** : l'image de chaque IRD, quasi en direct, dans sa carte (tuiles de la vue d'ensemble et page porteuse).
- **Non-objectifs** (décidés par Max) : aucune surveillance de l'image (pas d'alerte image noire, figée ou rouge), pas
  d'historique, pas de stockage en base, pas d'export.
- **Anti-objectifs** : ralentir ou faire échouer la lecture habituelle de l'IRD à cause de l'image ; afficher comme
  fraîche une image ancienne ; afficher l'image d'un autre appareil que l'IRD attendu.

## Ce que fournit l'IRD (relevé du 24/10 sur 192.168.10.141 et .143)

- `GET http://<ip>/thumbnail.jpg` (la page de l'IRD ajoute `?forceRefresh=<ms>` contre le cache), sans identifiant.
- JPEG 320×180, ~13 ko, régénéré toutes les 2 à 3 s ; en-têtes `Last-Modified` et `ETag` à chaque nouvelle image.
- Sans décodage (IRD 3 sans lock), l'IRD renvoie une image rouge unie, toujours régénérée.
- Lecture seule : un simple GET, aucune commande ; conforme à la règle « rien écrire sur les appareils ».

## Approches envisagées

1. **Le service lit l'image et la sert à la page** — retenue par Max. Une lecture par IRD quel que soit le nombre de
   pages ouvertes ; la page n'a jamais besoin de joindre les IRD ; image prise seulement sur un IRD dont le numéro de
   série a été vérifié ; heure de l'image fiable. Coût : un peu de code côté service, une route, des tests.
2. La page lit l'image directement sur l'IRD — écartée : chaque page interroge l'IRD, le PC qui affiche doit joindre les
   IRD, pas de vérification du numéro de série, et le navigateur ne peut pas lire l'heure de l'image (pas de CORS), donc
   pas d'indication d'image ancienne.
3. L'image dans le flux en direct (base64 dans le SSE) — écartée : ~10 ko/s par page ouverte pour rien.

## Ce qui est décidé

- **Rythme** (Max) : au rythme de l'IRD, une lecture de l'image toutes les ~2,5 s par IRD.
- **Placement** (Max) : maquette A — l'image à droite des lignes Marge / Signal reçu / Température, la courbe de marge
  garde toute la largeur en dessous. Même carte pour les tuiles et la page porteuse.
- **Heure sous l'image** (ma proposition) : « image 17:45:43 », heure de l'image côté IRD (`Last-Modified`), à défaut
  l'heure de réception.
- **Image ancienne ou IRD injoignable** (ma proposition) : la dernière image reste affichée grisée avec « image de
  17:44 » ; jamais reçue : cadre vide « pas d'image ». Seuil « ancienne » au choix du constructeur, de l'ordre de 10 s.
- **Période passée sur la page porteuse** (ma proposition) : l'image est masquée (rien n'est stocké, une image en direct
  à côté de courbes d'hier serait trompeuse).
- **Clic sur l'image** (ma proposition) : l'image s'ouvre en grand (320×180, taille réelle) sans quitter la page.
- **Côté service** : dernière image et son heure en mémoire seulement, par IRD ; servies par
  `GET /api/ird/<n° de série>/image.jpg` (404 tant qu'aucune image) ; la page est prévenue d'une nouvelle image par le
  flux en direct (heure de l'image, pas le contenu) et recharge l'`<img>`.
- **Sûreté** : la lecture de l'image ne doit ni retarder ni faire échouer la lecture habituelle de l'IRD (même principe
  que les requêtes annexes « état » et « journal » : un échec est journalisé au premier échec et au retour, sans plus) ;
  pas de lecture tant que le numéro de série n'est pas vérifié ; un IRD retiré n'a plus d'image servie ; contenu
  vérifié comme JPEG et taille bornée avant d'être servi ; sans proxy, comme les autres requêtes IRD.

## Laissé au constructeur

- Où vit la lecture (dans la boucle de l'IRD entre deux lectures, ou une boucle dédiée par IRD) tant que la lecture
  habituelle n'est pas retardée ; attention à l'IRD qui répond « occupé » (HTTP 503) : l'image ne doit pas provoquer ce
  refus sur la lecture principale.
- Le délai d'attente de la requête image, la taille maximale acceptée, le seuil « ancienne ».
- Le mode démo : une image de démonstration pour les IRD simulés, ou « pas d'image ».
- Mise à jour de docs/GUIDE.md et docs/HISTORIQUE.md.
