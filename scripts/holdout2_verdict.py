"""Verdict sur l'univers RÉSERVÉ n°2 (14 instruments jamais regardés) — figé et commité AVANT exécution.

  v2 = idx_dip(lo=5) + carry_safe(thr=2 %, VIX<22)                          (inchangé depuis la vague 2)
  v3 = v2 + dip_vote(votes=2, trend=True)   (seule idée de la vague 4 passant la règle 3/4 sur le pool ;
                                             réglage au meilleur min(entraînement, validation))
Période : 2010-2026 complète. Même moteur, mêmes coûts officiels, risque 0,75 %, capital 10 000 €.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from ideas_final import build, verdict  # noqa: E402
from swingbot.calendar_events import load_events  # noqa: E402
from swingbot.data import _read, clean, eur_conversion, load_rates, load_universe  # noqa: E402
from swingbot.instruments import HOLDOUT2, POOL  # noqa: E402
from swingbot.portfolio import RiskConfig, metrics, run_portfolio  # noqa: E402

V2 = {"idx_dip": dict(lo=5, trend=False), "carry_safe": dict(thr=0.02, vix_max=22.0)}
V3 = {**V2, "dip_vote": dict(votes=2, trend=True)}


def main():
    pool, _ = load_universe("D1", POOL)
    h2, _ = load_universe("D1", HOLDOUT2)
    alld = {**pool, **h2}
    vix, _ = clean(_read("VIX", "D1"), "VIX", "D1")
    ctx = dict(rates=load_rates(), events=load_events(), vix=vix, all_data=alld)
    days = pd.DatetimeIndex(sorted(set().union(*[d.index for d in alld.values()])))
    conv = eur_conversion(alld, days)
    a, b = pd.Timestamp("2010-06-01"), pd.Timestamp("2026-11-05")
    out = {}
    for name, sysm in (("v2", V2), ("v3", V3)):
        for uni, d in (("RESERVE2_jamais_vu", h2), ("pool_32", pool), ("tout_46", alld)):
            t, paths = build(d, ctx, system=sysm)
            r = run_portfolio(t, paths, d, conv, RiskConfig(capital=10000), a, b)
            m = metrics(r.equity, r.trades)
            key = f"{name}|{uni}"
            out[key] = {k: (round(float(v), 3) if isinstance(v, (float, np.floating)) else v) for k, v in m.items()}
            out[key].update(verdict(m, r.trades))
            out[key]["par_idee_R"] = r.trades.groupby("family").r_net.mean().round(3).to_dict()
            if uni == "RESERVE2_jamais_vu":
                r.trades.groupby("sym").agg(trades=("pnl_eur", "size"), pnl=("pnl_eur", "sum")).round(1) \
                    .to_csv(ROOT / "results" / f"{name}_reserve2_par_instrument.csv")
            print(key, out[key], flush=True)
    json.dump(out, open(ROOT / "results" / "holdout2_verdict.json", "w"), indent=2, ensure_ascii=False, default=str)


if __name__ == "__main__":
    main()
