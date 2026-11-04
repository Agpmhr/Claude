"""Système v2 INCHANGÉ : effet du risque par trade et du capital (pas de nouvelle optimisation des signaux).
Version « petit compte » = idx_dip seul (indices : granularité 0,01 lot très fine), carry_safe exige ~3 000 €."""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from ideas_final import build  # noqa: E402
from swingbot.calendar_events import load_events  # noqa: E402
from swingbot.data import _read, clean, eur_conversion, load_rates, load_universe  # noqa: E402
from swingbot.instruments import HOLDOUT, RESEARCH  # noqa: E402
from swingbot.portfolio import RiskConfig, metrics, run_portfolio  # noqa: E402

data, _ = load_universe("D1", RESEARCH + HOLDOUT)
vix, _ = clean(_read("VIX", "D1"), "VIX", "D1")
ctx = dict(rates=load_rates(), events=load_events(), vix=vix, all_data=data)
days = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
conv = eur_conversion(data, days)
t, paths = build(data, ctx)
a, b = pd.Timestamp("2010-06-01"), pd.Timestamp("2026-11-05")
rows = []
for label, cand in [("v2 complet", t), ("idx_dip seul (petit compte)", t[t.family == "idx_dip"])]:
    for cap in (200, 500, 1000, 3000, 10000):
        for risk in (0.005, 0.0075, 0.01, 0.015):
            r = run_portfolio(cand, paths, data, conv, RiskConfig(capital=cap, risk_per_trade=risk,
                                                                  max_open_risk=max(0.05, 6 * risk)), a, b)
            m = metrics(r.equity, r.trades)
            yr = r.equity.resample("YE").last().pct_change().dropna()
            rows.append(dict(systeme=label, capital=cap, risque=risk, cagr=round(m["cagr"], 4),
                             sharpe=round(m["sharpe"], 2), max_dd=round(m["max_dd"], 3),
                             pire_annee=round(yr.min(), 3), annees_pos=f"{(yr > 0).sum()}/{len(yr)}",
                             gain_moyen_an_eur=round(cap * m["cagr"], 1), trades=m.get("n_trades"),
                             refus_lot=r.skipped["lot_min"]))
df = pd.DataFrame(rows)
df.to_csv(ROOT / "results" / "v2_sizing.csv", index=False)
print(df.to_string())
