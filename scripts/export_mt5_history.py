"""Exporte l'historique D1 depuis votre terminal MT5 (Windows) vers data/mt5/, prioritaire sur Yahoo.
Ensuite, relancez scripts/ideas_final.py pour vérifier le backtest sur les VRAIES bougies du broker.

  python scripts/export_mt5_history.py            # compte démo
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from swingbot.instruments import HOLDOUT, RESEARCH  # noqa: E402
from swingbot.mt5_broker import export_history  # noqa: E402

if __name__ == "__main__":
    done = export_history(RESEARCH + HOLDOUT, bars=5000)
    print(f"{len(done)} instruments exportés :", done)
