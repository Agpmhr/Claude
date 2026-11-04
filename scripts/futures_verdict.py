"""Système v4 « tendance lente sur futures CFD sans swap » — FIGÉ ET COMMITÉ AVANT EXÉCUTION.

Recherche (entraînement 2010-2018 + validation 2019 -> oct. 2024) sur WTIF, BRENTF, DXYF :
  canal de Donchian 55 / 100 / 150 jours tous positifs sur les deux blocs (20 jours = bruit, écarté).
Système : ensemble des 3 durées (55, 100, 150), stop 3 ATR, sortie sur le canal moitié, 0,4 % de risque par signal.
VERDICT (une seule exécution) :
  (A) WTIF, BRENTF, DXYF sur 2024-11-01 -> 2026-11 : période jamais regardée pour ces instruments ;
  (B) GOLDF (or en futures) 2010-2026 : instrument jamais testé avec cette idée.
Données : ETF de substitution corrigés (taux USD retiré aux acheteurs, frais rendus) ; coûts IC Markets (spread).
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from ideas_final import verdict  # noqa: E402
from swingbot.backtest import simulate  # noqa: E402
from swingbot.calendar_events import load_events  # noqa: E402
from swingbot.costs import CostModel  # noqa: E402
from swingbot.data import _read, clean, eur_conversion, load_rates, load_universe  # noqa: E402
from swingbot.ideas import fut_trend  # noqa: E402
from swingbot.instruments import FUTURES, RESEARCH  # noqa: E402
from swingbot.portfolio import RiskConfig, metrics, run_portfolio  # noqa: E402
from swingbot.research import TEST_START, VAL_START, block_of  # noqa: E402

LOOKBACKS = (55, 100, 150)
CFG = dict(capital=10000, risk_per_trade=0.004, max_open_risk=0.06, max_ccy_units=6, max_positions=12,
           one_per_symbol=False, lot_rounding=False)


def build(data, ctx, cost=CostModel(), lookbacks=LOOKBACKS, stop=3.0):
    rows, paths = [], []
    for n in lookbacks:
        for s, (ent, xl, xs, rules) in fut_trend(data, ctx, n=n, stop=stop).items():
            tr, p = simulate(s, data[s], ent, xl, xs, rules, cost, ctx["rates"], None, strategy=f"fut_trend{n}")
            if len(tr):
                tr["family"] = "fut_trend"
                rows.append(tr); paths.extend(p)
    t = pd.concat(rows, ignore_index=True)
    t["block"] = block_of(t.entry_time)
    return t, paths


def main():
    rates = load_rates()
    fut, _ = load_universe("D1", FUTURES)
    fx, _ = load_universe("D1", ["EURUSD"])
    fut = {s: d for s, d in fut.items() if s != "VIXF"}
    ctx = dict(rates=rates, events=load_events(), all_data=fut)
    days = pd.DatetimeIndex(sorted(set().union(*[d.index for d in {**fut, **fx}.values()])))
    conv = eur_conversion({**fut, **fx, "EURJPY": fx["EURUSD"], "EURGBP": fx["EURUSD"], "USDCHF": fx["EURUSD"],
                           "USDCAD": fx["EURUSD"], "AUDUSD": fx["EURUSD"], "NZDUSD": fx["EURUSD"]}, days)
    t, paths = build(fut, ctx)
    out = dict(systeme="fut_trend ensemble 55/100/150, stop 3 ATR, 0,4 %/signal", essais_total_nuit=124)
    a0, end = pd.Timestamp("2010-06-01"), pd.Timestamp("2026-11-05")
    old = ["WTIF", "BRENTF", "DXYF"]
    runs = {
        "A_TEST_2024_2026_petrole_dollar": (t[t.sym.isin(old)], TEST_START, end),
        "B_OR_futures_2010_2026_jamais_teste": (t[t.sym == "GOLDF"], a0, end),
        "recherche_2010_2024_petrole_dollar": (t[t.sym.isin(old)], a0, TEST_START),
        "tout_2010_2026_4_instruments": (t, a0, end),
    }
    curves = {}
    for k, (cand, a, b) in runs.items():
        r = run_portfolio(cand, paths, fut, conv, RiskConfig(**CFG), a, b)
        m = metrics(r.equity, r.trades)
        res = {kk: (round(float(v), 3) if isinstance(v, (float, np.floating)) else v) for kk, v in m.items()}
        res.update(verdict(m, r.trades))
        res["par_instrument_R"] = r.trades.groupby("sym").r_net.agg(["size", "mean"]).round(3).to_dict("index")
        out[k] = res
        curves[k] = r
        print(k, res, flush=True)
    # robustesse sur le tout : coûts x2 (spreads futures très fins -> on double), Monte Carlo, par année, petits capitaux
    t2, p2 = build(fut, ctx, cost=CostModel(cost_mult=2.0))
    r2 = run_portfolio(t2, p2, fut, conv, RiskConfig(**CFG), a0, end)
    m2 = metrics(r2.equity, r2.trades)
    out["robustesse_couts_x2_tout"] = {k: round(float(m2[k]), 3) for k in ("sharpe", "pf", "max_dd", "cagr")}
    rall = curves["tout_2010_2026_4_instruments"]
    pnl = (rall.trades.pnl_eur / 10000).to_numpy()
    rng = np.random.default_rng(0)
    mdd = [((lambda e: (e / np.maximum.accumulate(e) - 1).min())(np.cumprod(1 + rng.permutation(pnl))))
           for _ in range(2000)]
    out["monte_carlo_dd_p50_p95_p99"] = [round(float(np.percentile(mdd, q)), 3) for q in (50, 5, 1)]
    yr = rall.equity.resample("YE").last().pct_change().dropna()
    out["par_annee"] = {int(i.year): round(float(v), 3) for i, v in yr.items()}
    mret = rall.equity.resample("ME").last().pct_change().dropna()
    out["mensuel_p10_p50_p90"] = [round(float(mret.quantile(q)), 4) for q in (0.1, 0.5, 0.9)]
    json.dump(out, open(ROOT / "results" / "v4_futures_verdict.json", "w"), indent=2, ensure_ascii=False, default=str)
    print(json.dumps({k: out[k] for k in ("robustesse_couts_x2_tout", "monte_carlo_dd_p50_p95_p99", "par_annee",
                                          "mensuel_p10_p50_p90")}, indent=1))
    fig, ax = plt.subplots(figsize=(11, 4.5))
    e = rall.equity / rall.equity.iloc[0]
    ax.plot(e.index, e.values, color="#2e7d32", label="v4 tendance futures (pétrole WTI/Brent, dollar, or)")
    ax.axvline(VAL_START, color="grey", ls=":", lw=0.8); ax.axvline(TEST_START, color="red", ls="--", lw=0.8)
    ax.text(TEST_START, e.max(), " test", color="red")
    ax.axhline(1, color="grey", lw=0.5); ax.legend(loc="upper left"); ax.grid(alpha=0.3)
    ax.set_title("Système v4 : équité après coûts (ETF de substitution corrigés), capital 10 000 €")
    fig.tight_layout(); fig.savefig(ROOT / "results" / "v4_equity.png", dpi=110)


if __name__ == "__main__":
    main()
