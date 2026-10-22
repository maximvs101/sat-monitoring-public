"""Ce qui dépend du système : chemins (code source ou exécutable autonome), modèle de configuration,
mise en veille. Le reste de l'outil n'utilise que la bibliothèque standard de Python."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Windows : SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) empêche la mise en veille du PC
# tant que le processus tourne (l'écran peut toujours s'éteindre).
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def frozen() -> bool:
    """Lancé depuis l'exécutable PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Dossier de config.toml, de la base et des journaux : à côté de l'exécutable, ou racine du dépôt."""
    return Path(sys.executable).resolve().parent if frozen() else ROOT


def _bundle() -> Path:
    return Path(sys._MEIPASS) if frozen() else ROOT


def web_dir() -> Path:
    """Page web : embarquée dans l'exécutable, ou dossier web/ du dépôt."""
    return _bundle() / "web"


def example_config() -> Path:
    return _bundle() / "config.example.toml"


def ensure_config(path: Path) -> bool:
    """Premier lancement : config.toml créé à partir du modèle. Jamais écrasé. Renvoie True s'il a été créé."""
    path = Path(path)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example_config(), path)
    return True


def keep_awake(platform: str = sys.platform, kernel32=None) -> str | None:
    """Empêche la mise en veille tant que l'outil tourne ; renvoie ce qui a été fait (pour le journal)."""
    if platform == "win32":
        if kernel32 is None:
            import ctypes
            kernel32 = ctypes.windll.kernel32
        if kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
            return "veille désactivée tant que l'outil tourne"
        return None
    if platform == "darwin":
        caffeinate = shutil.which("caffeinate")
        if caffeinate:
            # S'arrête tout seul avec le service (-w : attend la fin de ce processus).
            subprocess.Popen([caffeinate, "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return "veille désactivée tant que l'outil tourne"
    return None
