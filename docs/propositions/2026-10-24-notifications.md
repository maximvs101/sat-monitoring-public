# Proposition : notifications des alertes

*24 octobre 2026. Seul le son et la notification dans la page ont été faits (Réglages › Notifications) ; le reste de la proposition n'est pas poursuivi (décision de Max, 24/10).*

## Le besoin

Aujourd'hui, une alerte (perte de lock, chute de marge, IRD injoignable…) n'est visible que si quelqu'un regarde la page. La nuit, le week-end ou quand le poste est dans une autre pièce, une coupure peut passer inaperçue pendant des heures. Les notifications préviennent la bonne personne, sur le bon support, au bon moment, sans la noyer sous les messages.

## Ce qui serait notifié

Les événements existants (déjà détectés et enregistrés), sans nouvelle détection :

- **ouverture** d'une alerte : type, appareil, porteuse, détail (« IRD 3 : démodulateur déverrouillé ») ;
- **fermeture**, avec sa durée (« IRD 3 : lock retrouvé après 4 min 12 s ») ;
- en option, un **rappel** tant qu'une alerte critique reste ouverte (toutes les 30 min, par exemple) ;
- en option, un **résumé quotidien** (alertes de la veille, disponibilité par porteuse).

Règles réglables par destinataire :

| Règle | Exemple | À quoi ça sert |
| --- | --- | --- |
| Gravité minimale | critique seulement | ne pas déranger pour un avertissement |
| Types | perte de lock, injoignable, image perdue | choisir ce qui compte pour chacun |
| Délai avant envoi | 30 s | une alerte refermée entre-temps n'est jamais envoyée (micro-coupures) |
| Regroupement | 10 s | une coupure qui ouvre 6 alertes d'un coup donne **un** message, pas six |
| Plages horaires | toujours / heures ouvrées | un destinataire de jour, un d'astreinte |
| Fin d'alerte | oui / non | savoir que c'est revenu, sans rouvrir la page |

Exemple de message regroupé :

> **Monitoring satellite — 3 alertes critiques (24/10 02:14)**
> • Porteuse 12 583,0 V : IRD 1 et IRD 3 — perte de lock (marge 0 dB, puissance d'entrée -52 dBm)
> • FSH4 : chute du niveau marqueur M1 -61,0 dBm (référence -50,2 dBm)
> Cause probable : réception (les deux IRD de la porteuse sont touchés).
> Ouvrir : http://<poste>:8080/#/porteuse/125830-V

## Les canaux possibles

Tous réalisables avec la bibliothèque standard de Python (pas de dépendance), en lecture seule envers les appareils : seul le service envoie, vers l'extérieur.

| Canal | Arrive sur | Prérequis sur le site | Effort | Remarques |
| --- | --- | --- | --- | --- |
| **Son et notification dans la page** | le poste qui affiche la page | aucun | faible | ne prévient que si la page est ouverte ; idéal en salle de contrôle |
| **Webhook Teams / Slack / Google Chat** | le canal de l'équipe, sur PC et téléphone | accès Internet sortant (HTTPS) | faible | un lien de webhook à coller dans les réglages ; Teams est souvent déjà là |
| **ntfy** (ntfy.sh ou serveur interne) | notification push sur téléphone (appli ntfy) | Internet sortant, ou un serveur ntfy sur le réseau | faible | le plus simple pour du push téléphone, priorité « urgente » qui sonne même en silencieux |
| **Telegram** | téléphone, groupe d'astreinte | Internet sortant | faible | un bot à créer (une fois), son jeton dans config.toml |
| **E-mail (SMTP)** | boîte mail | un serveur SMTP joignable (interne ou Office 365 / Gmail) | moyen | compte et mot de passe d'envoi dans config.toml ; parfois bloqué ou lent selon la politique de l'entreprise |
| **Syslog** | une supervision existante (Zabbix, Nagios, PRTG…) | une supervision à l'écoute | faible | une ligne par événement ; la supervision fait le reste (escalade, astreinte) |
| **Trap SNMP** | une supervision existante | une supervision SNMP | moyen | utile si la supervision ne lit que du SNMP |

## Comment ce serait réglé

- **Réglages › Notifications** dans la page : une liste de destinataires (canal, adresse ou lien, règles), un bouton **Envoyer un test** par destinataire, et l'état du dernier envoi (« envoyé à 02:14 », « échec : délai dépassé »).
- Enregistré dans `config.toml` (`[[notify]]`), qui n'est pas suivi par git : les liens de webhook, jetons et mots de passe ne sortent jamais du poste, ni vers le dépôt public.

## Fiabilité

- Les envois se font dans un fil à part, avec une file d'attente : un canal lent ou en panne ne retarde jamais la collecte ni la détection.
- Nouvel essai en cas d'échec (3 fois, à 10 s, 1 min, 5 min), puis l'échec est enregistré et affiché dans la page (événement « Notification non envoyée »).
- Au redémarrage du service, les alertes déjà notifiées ne le sont pas une seconde fois.
- Limite de débit par canal (une tempête d'alertes ne déclenche pas des centaines de messages).

## Recommandation

1. **D'abord** : son et notification dans la page (aucun prérequis), et **un canal « équipe »** parmi Teams, ntfy ou Telegram selon ce qui existe déjà. C'est ce qui change le plus le quotidien pour le moins d'effort : environ une demi-journée à une journée, tests compris.
2. **Ensuite** : e-mail, résumé quotidien et rappels.
3. **Si une supervision existe déjà** sur le site : syslog (ou trap SNMP) vers elle, qui s'occupera elle-même de l'astreinte.

## Questions à trancher avant de développer

1. **Qui doit être prévenu, et où ?** L'exploitant devant l'écran, une astreinte sur téléphone, toute l'équipe ?
2. **Le poste qui fait tourner le service a-t-il accès à Internet ?** Le réseau des appareils (192.168.10.x) est souvent isolé ; sinon, seuls le son dans la page, un serveur interne (SMTP, ntfy, supervision) ou un second réseau sur le poste sont possibles.
3. **Quels outils existent déjà** dans l'entreprise : Teams, Slack, un serveur mail interne, une supervision ?
4. **Quelles alertes méritent de réveiller quelqu'un la nuit** ? Proposition : perte de lock, image perdue, flux arrêté, IRD ou FSH4 injoignable ; le reste en heures ouvrées ou en résumé.
