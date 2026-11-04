"""Système v2 (figé AVANT le verdict) : idx_dip(lo=5, sans filtre de tendance) + carry_safe(thr=2 %, VIX<22).

Règle de sélection décidée sur l'univers de recherche, entraînement + validation :
une idée est retenue si >= 3 réglages sur 4 sont positifs après coûts sur les DEUX blocs ;
dans l'idée, on garde le réglage au meilleur min(entraînement, validation).

Verdict sur :
  (1) l'univers RÉSERVÉ (16 instruments jamais regardés), toute la période 2010-2026 ;
  (2) l'univers de recherche sur 2024-11 -> 2026-11 (période jamais regardée POUR CES IDÉES).
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
sys.path.insert(0, str(ROOT))
from swingbot.calendar_events import load_events  # noqa: E402
from swingbot.costs import CostModel  # noqa: E402
from swingbot.data import _read, clean, eur_conversion, load_rates, load_universe  # noqa: E402
from swingbot.ideas import IDEAS, N_IDEA_CONFIGS  # noqa: E402
from swingbot.instruments import HOLDOUT, RESEARCH  # noqa: E402
from swingbot.portfolio import RiskConfig, metrics, run_portfolio  # noqa: E402
from swingbot.research import TEST_START, VAL_START  # noqa: E402
from swingbot.strategies import N_CONFIGS  # noqa: E402

OUT = ROOT / "results"
SYSTEM = {"idx_dip": dict(lo=5, trend=False), "carry_safe": dict(thr=0.02, vix_max=22.0)}


def build(data, ctx, cost=CostModel(), system=SYSTEM):
    from swingbot.backtest import simulate
    from swingbot.research import block_of, event_series
    evs = event_series(data, ctx["events"])
    rows, paths = [], []
    for name, p in system.items():
        fn = IDEAS[name][0]
        for s, (ent, xl, xs, rules) in fn(data, ctx, **p).items():
            tr, pth = simulate(s, data[s], ent, xl, xs, rules, cost, ctx["rates"], evs[s], strategy=name)
            if len(tr):
                tr["family"] = name
                rows.append(tr); paths.extend(pth)
    t = pd.concat(rows, ignore_index=True)
    t["block"] = block_of(t.entry_time)
    return t, paths


def run(data, ctx, a, b, cost=CostModel(), system=SYSTEM, capital=10000, conv=None):
    t, paths = build(data, ctx, cost, system)
    days = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    conv = conv if conv is not None else eur_conversion(ctx["all_data"], days)
    r = run_portfolio(t, paths, data, conv, RiskConfig(capital=capital), a, b)
    return r, metrics(r.equity, r.trades), t


def verdict(m, tr):
    yrs = tr.groupby(tr.exit_time.dt.year).pnl_eur.sum()
    ins = tr.groupby("sym").pnl_eur.sum()
    return dict(pf_ok=m.get("pf", 0) > 1.3, sharpe_ok=m["sharpe"] > 0.8, dd_ok=m["max_dd"] > -0.20,
                annees_positives=f"{(yrs > 0).sum()}/{len(yrs)}", instruments_positifs=f"{(ins > 0).sum()}/{len(ins)}",
                n_trades=int(m.get("n_trades", 0)))


def main():
    rates, ev = load_rates(), load_events()
    vix, _ = clean(_read("VIX", "D1"), "VIX", "D1")
    res_data, _ = load_universe("D1", RESEARCH)
    hold_data, qc = load_universe("D1", HOLDOUT)
    all_data = {**res_data, **hold_data}
    ctx = dict(rates=rates, events=ev, vix=vix, all_data=all_data)
    days = pd.DatetimeIndex(sorted(set().union(*[d.index for d in all_data.values()])))
    conv = eur_conversion(all_data, days)
    out = dict(systeme=SYSTEM, essais_total=N_CONFIGS + 9 + N_IDEA_CONFIGS)
    t0, t1 = pd.Timestamp("2010-06-01"), pd.Timestamp("2026-11-05")

    # --- A. univers de recherche, entraînement + validation (là où l'idée a été choisie)
    rows = []
    for blk, (a, b) in {"recherche_entrainement": (t0, VAL_START), "recherche_validation": (VAL_START, TEST_START)}.items():
        r, m, _ = run(res_data, ctx, a, b, conv=conv)
        rows.append(dict(bloc=blk, **m))
    # --- B. robustesse (sur la recherche, entraînement + validation)
    rob = {}
    for name, cost, sysm in [
        ("coûts x1,5", CostModel(cost_mult=1.5), SYSTEM),
        ("paramètres -20 %", CostModel(), {"idx_dip": dict(lo=4, trend=False), "carry_safe": dict(thr=0.016, vix_max=17.6)}),
        ("paramètres +20 %", CostModel(), {"idx_dip": dict(lo=6, trend=False), "carry_safe": dict(thr=0.024, vix_max=26.4)}),
    ]:
        r, m, _ = run(res_data, ctx, t0, TEST_START, cost=cost, system=sysm, conv=conv)
        rob[name] = {k: round(float(m[k]), 3) for k in ("sharpe", "pf", "max_dd", "cagr", "n_trades")}
    out["robustesse_recherche_2010_2024"] = rob

    # --- C. LES VERDICTS (une seule exécution)
    rh, mh, th = run(hold_data, ctx, t0, t1, conv=conv)
    rt, mt, tt = run(res_data, ctx, TEST_START, t1, conv=conv)
    rows.append(dict(bloc="RESERVE_univers_jamais_vu_2010_2026", **mh))
    rows.append(dict(bloc="RECHERCHE_periode_jamais_vue_2024_2026", **mt))
    perf = pd.DataFrame(rows).set_index("bloc")
    perf.to_csv(OUT / "v2_performance.csv")
    out["verdict_reserve"] = verdict(mh, rh.trades)
    out["verdict_periode_neuve"] = verdict(mt, rt.trades)
    rh.trades.groupby("sym").agg(trades=("pnl_eur", "size"), pnl_eur=("pnl_eur", "sum"), r_moyen=("r_net", "mean")) \
        .round(2).to_csv(OUT / "v2_reserve_par_instrument.csv")
    rh.trades.groupby(rh.trades.exit_time.dt.year).agg(trades=("pnl_eur", "size"), pnl_eur=("pnl_eur", "sum")) \
        .round(2).to_csv(OUT / "v2_reserve_par_annee.csv")
    rh.trades.groupby("family").r_net.agg(["size", "mean"]).round(3).to_csv(OUT / "v2_reserve_par_idee.csv")

    # --- D. tout ensemble (32 instruments) + petits capitaux + Monte Carlo
    rall, mall, tall = run(all_data, ctx, t0, t1, conv=conv)
    out["tout_univers_2010_2026"] = {k: (round(float(v), 3) if isinstance(v, (float, np.floating)) else v)
                                     for k, v in mall.items()}
    caps = []
    for cap in (200, 500, 1000, 3000, 10000):
        r, m, _ = run(all_data, ctx, t0, t1, capital=cap, conv=conv)
        caps.append(dict(capital=cap, cagr=round(m["cagr"], 4), max_dd=round(m["max_dd"], 3),
                         n_trades=m.get("n_trades"), gain_eur=round(r.equity.iloc[-1] - cap, 1),
                         refus_lot_min=r.skipped["lot_min"]))
    out["capitaux_tout_univers"] = caps
    pnl = (rall.trades.pnl_eur / 10000).to_numpy()
    rng = np.random.default_rng(0)
    mdd = [((lambda e: (e / np.maximum.accumulate(e) - 1).min())(np.cumprod(1 + rng.permutation(pnl))))
           for _ in range(2000)]
    out["monte_carlo_dd_p50_p95_p99"] = [round(float(np.percentile(mdd, q)), 3) for q in (50, 5, 1)]
    mret = rall.equity.resample("ME").last().pct_change().dropna()
    out["rendement_mensuel_p10_p50_p90"] = [round(float(mret.quantile(q)), 4) for q in (0.1, 0.5, 0.9)]
    out["mois_positifs"] = round(float((mret > 0).mean()), 3)
    yr = rall.equity.resample("YE").last().pct_change().dropna()
    yr.index = yr.index.year
    out["rendement_par_annee_tout_univers"] = {int(k): round(float(v), 3) for k, v in yr.items()}
    print(perf[["cagr", "sharpe", "max_dd", "pf", "n_trades", "avg_r"]].round(3).to_string())
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    json.dump(out, open(OUT / "v2_report.json", "w"), indent=2, ensure_ascii=False, default=str)

    fig, ax = plt.subplots(2, 1, figsize=(11, 7))
    for lab, r, c in [("univers RÉSERVÉ (jamais vu) 2010-2026", rh, "#c0392b"),
                      ("univers de recherche 2010-2026", None, "#7a8ca5")]:
        if r is None:
            r, _, _ = run(res_data, ctx, t0, t1, conv=conv)
        ax[0].plot(r.equity.index, r.equity / r.equity.iloc[0], label=lab, color=c)
    ax[0].axvline(TEST_START, color="grey", ls="--", lw=0.8)
    ax[0].axhline(1, color="grey", lw=0.5); ax[0].legend(); ax[0].grid(alpha=0.3)
    ax[0].set_title("Système v2 (rachat de repli indices + carry protégé par le VIX), après tous les coûts")
    ax[1].plot(rall.equity.index, rall.equity / rall.equity.iloc[0], color="#2e7d32", label="32 instruments ensemble")
    ax[1].axhline(1, color="grey", lw=0.5); ax[1].legend(); ax[1].grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "v2_equity.png", dpi=110)


if __name__ == "__main__":
    main()
