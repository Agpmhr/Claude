"""Test hors échantillon : la v4 appliquée SANS AUCUN CHANGEMENT à 4 matières premières agricoles jamais regardées
(maïs, blé, soja, sucre ; futures CFD IC Markets sans swap). Figé et commité AVANT exécution.

Règles v4 : Donchian 55/100/150, stop 3 ATR, sortie sur le canal moitié, 0,25 % de risque par signal.
Rapporté : (1) les 4 agricoles seules, coupe-circuit actif comme en démo ; (2) les 8 futures ensemble
(diagnostic sans coupe-circuit) ; trades par instrument, par année.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from futures_verdict import CFG, build  # noqa: E402
from ideas_final import verdict  # noqa: E402
from swingbot.calendar_events import load_events  # noqa: E402
from swingbot.data import eur_conversion, load_rates, load_universe  # noqa: E402
from swingbot.instruments import AGRI, FUTURES  # noqa: E402
from swingbot.portfolio import RiskConfig, metrics, run_portfolio  # noqa: E402


def main():
    rates = load_rates()
    agri, qc = load_universe("D1", AGRI)
    print(pd.DataFrame([q.row() for q in qc]).to_string())
    fut, _ = load_universe("D1", [s for s in FUTURES if s != "VIXF"])
    fx, _ = load_universe("D1", ["EURUSD"])
    alld = {**fut, **agri}
    days = pd.DatetimeIndex(sorted(set().union(*[d.index for d in {**alld, **fx}.values()])))
    conv = eur_conversion({**alld, **fx, "EURJPY": fx["EURUSD"], "EURGBP": fx["EURUSD"], "USDCHF": fx["EURUSD"],
                           "USDCAD": fx["EURUSD"], "AUDUSD": fx["EURUSD"], "NZDUSD": fx["EURUSD"]}, days)
    ctx = dict(rates=rates, events=load_events(), all_data=alld)
    out = {}
    a, b = pd.Timestamp("2011-06-01"), pd.Timestamp("2026-11-05")
    for lab, d, extra in [("AGRI_jamais_vu_coupe_circuit", agri, {}),
                          ("AGRI_jamais_vu_sans_coupe_circuit", agri, dict(max_dd_stop=1.0, daily_stop=1.0,
                                                                            weekly_stop=1.0)),
                          ("HUIT_futures_diagnostic", alld, dict(max_dd_stop=1.0, daily_stop=1.0, weekly_stop=1.0))]:
        t, paths = build(d, ctx)
        cfg = dict(CFG); cfg.update(risk_per_trade=0.0025, max_ccy_units=24, max_positions=24, max_open_risk=0.06)
        cfg.update(extra)
        r = run_portfolio(t, paths, d, conv, RiskConfig(**cfg), a, b)
        m = metrics(r.equity, r.trades)
        res = {k: (round(float(v), 3) if isinstance(v, (float, np.floating)) else v) for k, v in m.items()}
        res.update(verdict(m, r.trades))
        res["arret"] = str(r.halted)
        res["par_instrument"] = r.trades.groupby("sym").agg(n=("r_net", "size"), r=("r_net", "mean"),
                                                            eur=("pnl_eur", "sum")).round(2).to_dict("index")
        yr = r.equity.resample("YE").last().pct_change().dropna()
        res["par_annee"] = {int(i.year): round(float(v), 3) for i, v in yr.items()}
        out[lab] = res
        print(lab, json.dumps(res, ensure_ascii=False, default=str), flush=True)
        if lab == "HUIT_futures_diagnostic":
            r.equity.to_pickle(ROOT / "results" / "cache" / "v4_8fut_equity.pkl")
    json.dump(out, open(ROOT / "results" / "agri_verdict.json", "w"), indent=2, ensure_ascii=False, default=str)


if __name__ == "__main__":
    main()
