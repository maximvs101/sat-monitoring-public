#!/bin/bash
# Monitoring satellite FSH4 + IRD : double-cliquer pour lancer. La page s'ouvre dans le navigateur
# (http://localhost:8080) ; le Mac ne se met pas en veille tant que le service tourne.
# Fermer cette fenêtre ou Ctrl-C pour arrêter.
cd "$(dirname "$0")" || exit 1
echo "Monitoring satellite — page : http://localhost:8080 (Ctrl-C pour arrêter)"
exec python3 -m monitoring.server --open
