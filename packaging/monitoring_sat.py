"""Point d'entrée de l'exécutable autonome (PyInstaller) : lance le service et ouvre la page dans le navigateur.
config.toml, data/ et logs/ sont à côté de l'exécutable ; config.toml est créé au premier lancement.
`--no-open` : ne pas ouvrir le navigateur (essai automatique de l'exécutable)."""

import sys
import traceback

from monitoring.server import main as service_main


def run(argv, main=service_main, frozen=getattr(sys, "frozen", False), pause=input) -> int:
    args = list(argv)
    if "--no-open" in args:
        args.remove("--no-open")
    else:
        args.insert(0, "--open")
    try:
        main(args)
    except Exception:  # noqa: BLE001
        if not frozen:
            raise
        # Exécutable : la fenêtre se fermerait avant qu'on ait pu lire l'erreur (dossier en lecture seule,
        # config.toml invalide…).
        traceback.print_exc()
        print("\nLe service n'a pas pu démarrer : voir le message ci-dessus.", file=sys.stderr)
        pause("Appuyez sur Entrée pour fermer cette fenêtre.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
