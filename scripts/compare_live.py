"""Compare le journal de démo/papier (logs/live_decisions.jsonl) au backtest du système v2 sur la même période.

Signale : trades pris en démo mais absents du backtest (et inversement), écart de résultat moyen en R.
Usage : python scripts/compare_live.py
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from ideas_final import build  # noqa: E402
from swingbot.calendar_events import load_events  # noqa: E402
from swingbot.data import _read, clean, load_rates, load_universe  # noqa: E402
from swingbot.instruments import HOLDOUT, RESEARCH  # noqa: E402


def main():
    log = [json.loads(l) for l in open(ROOT / "logs" / "live_decisions.jsonl", encoding="utf-8")]
    ent = pd.DataFrame([l for l in log if l["type"] == "decision_entree" and "lots" in l])
    if ent.empty:
        print("Aucune entrée dans le journal."); return
    ent["date_signal"] = pd.to_datetime(ent["date_signal"])
    a, b = ent.date_signal.min(), ent.date_signal.max()
    data, _ = load_universe("D1", RESEARCH + HOLDOUT)
    vix, _ = clean(_read("VIX", "D1"), "VIX", "D1")
    ctx = dict(rates=load_rates(), events=load_events(), vix=vix, all_data=data)
    bt, _ = build(data, ctx)
    bt = bt[(bt.signal_time >= a) & (bt.signal_time <= b)]
    live_keys = set(zip(ent.sym, ent.idee, ent.date_signal))
    bt_keys = set(zip(bt.sym, bt.family, bt.signal_time))
    print(f"Période {a.date()} -> {b.date()}")
    print(f"Entrées démo : {len(live_keys)} | signaux backtest : {len(bt_keys)} | communs : {len(live_keys & bt_keys)}")
    print("Pris en démo mais pas en backtest :", sorted(live_keys - bt_keys)[:20])
    print("Signal backtest non pris en démo (limites, news, lot min...) :", sorted(bt_keys - live_keys)[:20])
    st = json.loads((ROOT / "state" / "live_state.json").read_text())
    closed = pd.DataFrame(st.get("closed", []))
    if len(closed):
        print(f"R moyen démo/papier : {closed.r.mean():.3f} sur {len(closed)} trades | "
              f"R moyen backtest : {bt.r_net.mean():.3f} sur {len(bt)} trades")
        print("⚠️ Écart important si la différence dépasse ~0,3 R sur plus de 30 trades.")


if __name__ == "__main__":
    main()
