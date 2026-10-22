"""Point d'entrée de l'exécutable autonome (PyInstaller) : lance le service et ouvre la page dans le navigateur.
config.toml, data/ et logs/ sont à côté de l'exécutable ; config.toml est créé au premier lancement.
`--no-open` : ne pas ouvrir le navigateur (essai automatique de l'exécutable)."""

import sys

from monitoring.server import main

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--no-open" in args:
        args.remove("--no-open")
    else:
        args.insert(0, "--open")
    main(args)
