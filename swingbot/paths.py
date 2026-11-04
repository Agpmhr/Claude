"""Dossier de travail du bot (données, journaux, état).

Par défaut : la racine du dépôt. Dans l'application Swingbot.exe : le dossier de l'exécutable
(ou la variable d'environnement SWINGBOT_HOME si elle est définie).
"""
import os
import sys
from pathlib import Path


def home() -> Path:
    if os.environ.get("SWINGBOT_HOME"):
        return Path(os.environ["SWINGBOT_HOME"])
    if getattr(sys, "frozen", False):           # application empaquetée (PyInstaller)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


ROOT = home()
