"""VERDICT FINAL : la variante retenue AVANT de regarder le test est passée UNE SEULE FOIS sur le bloc test.

Règle de choix figée avant le test : meilleur Sharpe sur la validation -> variante D
(sélection walk-forward des configurations + score IA + pas de vente à découvert des indices).
Aucun réglage n'est modifié après lecture de ce résultat.
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import norm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from run_research import wf_select  # noqa: E402
from swingbot.ai import build_features, expected_r, fit_meta, instrument_features  # noqa: E402
from swingbot.calendar_events import load_events  # noqa: E402
from swingbot.costs import CostModel  # noqa: E402
from swingbot.data import eur_conversion, load_rates, load_universe  # noqa: E402
from swingbot.portfolio import RiskConfig, metrics, run_portfolio  # noqa: E402
from swingbot.research import TEST_START, VAL_START, cached, generate_all  # noqa: E402
from swingbot.strategies import N_CONFIGS  # noqa: E402

OUT = ROOT / "results"
IDX = ["US500", "NAS100", "GER40"]


def score_all(trades, data, rates, ev, feats):
    """Sélection WF + score IA pour TOUTES les années (2013 -> test), chaque année n'utilisant que son passé."""
    years = list(range(2013, 2027))
    sel, _ = wf_select(trades[trades.exit_time < TEST_START], years[:-2])
    # année(s) du test : sélection et modèle figés avec l'information disponible au 1er nov. 2024
    pre = trades[trades.exit_time < TEST_START]
    cut = TEST_START
    win = pre[pre.entry_time >= cut - pd.DateOffset(years=4)]
    st = win.groupby(["family", "strategy"]).r_net.agg(["size", "mean"])
    st = st[st["size"] >= 40]
    chosen = [g["mean"].idxmax()[1] for _, g in st.groupby(level=0) if g["mean"].max() > 0]
    test_sel = trades.strategy.isin(chosen) & (trades.signal_time >= TEST_START)
    sel = sel.reindex(trades.index, fill_value=False) | test_sel
    X = build_features(trades, feats)
    from swingbot.ai import walk_forward_meta
    p = walk_forward_meta(trades, X, list(range(2013, 2025)))
    m = fit_meta(trades, X, TEST_START)
    tm = trades.signal_time >= TEST_START
    p[tm] = m.predict_proba(X[tm])[:, 1]
    # stats gains/pertes du test figées au 1er nov. 2024
    er = expected_r(p, trades.assign(signal_time=trades.signal_time.where(~tm, TEST_START - pd.Timedelta(days=1))))
    er[tm] = expected_r(p, trades.assign(signal_time=trades.signal_time.where(~tm, pd.Timestamp("2024-10-31"))))[tm]
    out = trades.assign(wf_sel=sel, p=p, er=er)
    return out, chosen


def variant_d(t):
    return t[t.wf_sel & ~(t.sym.isin(IDX) & (t.dir < 0)) & t.er.notna()]


def deflated_sharpe(sr_ann, n_obs_days, n_trials, sr_trials_ann, skew=0.0, kurt=3.0):
    """Deflated Sharpe Ratio (Bailey & López de Prado) : probabilité que le vrai Sharpe soit > 0
    compte tenu du nombre d'essais effectués."""
    sr = sr_ann / np.sqrt(252)
    v = np.var(np.asarray(sr_trials_ann) / np.sqrt(252), ddof=1)
    emax = np.sqrt(v) * ((1 - 0.5772) * norm.ppf(1 - 1 / n_trials) + 0.5772 * norm.ppf(1 - 1 / (n_trials * np.e)))
    z = (sr - emax) * np.sqrt(n_obs_days - 1) / np.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
    return float(norm.cdf(z)), float(emax * np.sqrt(252))


def main():
    data, _ = load_universe("D1")
    rates = load_rates()
    ev = load_events()
    trades, paths = cached("trades_base", generate_all, data, rates)
    feats = cached("features", instrument_features, data, rates, ev)
    t, chosen = score_all(trades, data, rates, ev, feats)
    days = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    conv = eur_conversion(data, days)
    end = days[-1] + pd.Timedelta(days=1)
    cfg = RiskConfig(capital=10000, score_col="er", min_score=0.0)
    cand = variant_d(t)
    report = dict(configs_retenues_pour_le_test=chosen)

    # --- 1. LE test (une seule fois) + courbes des 3 blocs
    blocks = {"entrainement (hors éch.) 2013-2018": (pd.Timestamp("2013-01-01"), VAL_START),
              "validation 2019-2024": (VAL_START, TEST_START), "TEST 2024-11 -> 2026-11": (TEST_START, end)}
    res_blocks, rows = {}, []
    for b, (a, z) in blocks.items():
        r = run_portfolio(cand, paths, data, conv, cfg, a, z)
        res_blocks[b] = r
        m = metrics(r.equity, r.trades)
        m.update(bloc=b, arret=str(r.halted))
        rows.append(m)
    perf = pd.DataFrame(rows).set_index("bloc")
    perf.to_csv(OUT / "final_performance.csv")
    print(perf.round(3).to_string())

    # --- 2. capitaux de 200 / 500 / 1000 / 10 000 € sur le test (arrondi au 0,01 lot)
    caps = []
    for cap in (200, 500, 1000, 10000):
        r = run_portfolio(cand, paths, data, conv, RiskConfig(capital=cap, score_col="er", min_score=0.0),
                          TEST_START, end)
        m = metrics(r.equity, r.trades)
        caps.append(dict(capital=cap, **{k: m.get(k) for k in ("rendement_total", "max_dd", "n_trades")},
                         gain_eur=r.equity.iloc[-1] - cap, refus_lot_min=r.skipped["lot_min"]))
    caps = pd.DataFrame(caps)
    caps.to_csv(OUT / "final_capitaux.csv", index=False)
    print(caps.round(3).to_string())

    # --- 3. références sur le test : aléatoire et achat-conservation
    rt = res_blocks["TEST 2024-11 -> 2026-11"]
    rng = np.random.default_rng(1)
    rand = []
    pool = t[(t.signal_time >= TEST_START)]
    for k in range(50):
        sub = pool.sample(n=min(len(pool), max(1, len(cand[cand.signal_time >= TEST_START]))), random_state=k)
        rr = run_portfolio(sub, paths, data, conv, RiskConfig(capital=10000), TEST_START, end)
        rand.append(metrics(rr.equity)["rendement_total"])
    bh = {}
    for s in ("US500", "XAUUSD", "EURUSD", "GER40"):
        c = data[s].close[data[s].index >= TEST_START]
        bh[s] = float(c.iloc[-1] / c.iloc[0] - 1)
    report.update(aleatoire_test_rendement_median=float(np.median(rand)),
                  aleatoire_test_rendement_p10_p90=[float(np.percentile(rand, 10)), float(np.percentile(rand, 90))],
                  achat_conservation_test_prix_sans_frais=bh)

    # --- 4. robustesse (sur la validation, et le test pour info)
    rob = []
    for name, cm, sc in [("coûts x1,5", CostModel(cost_mult=1.5), 1.0), ("paramètres -20 %", CostModel(), 0.8),
                         ("paramètres +20 %", CostModel(), 1.2)]:
        tr2, p2 = generate_all(data, rates, cm, param_scale=sc)
        # mêmes configurations (par nom d'origine) et même règle : re-score complet
        t2, _ = score_all(tr2, data, rates, ev, feats)
        c2 = variant_d(t2)
        for b in ("validation 2019-2024", "TEST 2024-11 -> 2026-11"):
            a, z = blocks[b]
            r2 = run_portfolio(c2, p2, data, conv, cfg, a, z)
            m2 = metrics(r2.equity, r2.trades)
            rob.append(dict(test=name, bloc=b, sharpe=m2["sharpe"], pf=m2.get("pf"), max_dd=m2["max_dd"],
                            cagr=m2["cagr"], n=m2.get("n_trades")))
    rob = pd.DataFrame(rob)
    rob.to_csv(OUT / "final_robustesse.csv", index=False)
    print(rob.round(3).to_string())

    # Monte Carlo sur l'ordre des trades (train-OOS + validation + test)
    allt = pd.concat([r.trades for r in res_blocks.values()])
    pnl_pct = (allt["pnl_eur"] / 10000).to_numpy()
    mdd = []
    for k in range(2000):
        e = np.cumprod(1 + rng.permutation(pnl_pct))
        mdd.append((e / np.maximum.accumulate(e) - 1).min())
    report["monte_carlo_drawdown_p50_p95_p99"] = [float(np.percentile(mdd, 50)), float(np.percentile(mdd, 5)),
                                                  float(np.percentile(mdd, 1))]

    # par instrument et par année (tous blocs)
    allt["annee"] = allt.exit_time.dt.year
    allt.groupby("sym").agg(trades=("pnl_eur", "size"), pnl_eur=("pnl_eur", "sum"), r_moyen=("r_net", "mean")) \
        .round(2).to_csv(OUT / "final_par_instrument.csv")
    allt.groupby("annee").agg(trades=("pnl_eur", "size"), pnl_eur=("pnl_eur", "sum"), r_moyen=("r_net", "mean")) \
        .round(2).to_csv(OUT / "final_par_annee.csv")

    # Deflated Sharpe : essais = configurations de stratégies + variantes de portefeuille
    var = pd.read_csv(OUT / "research_variants.csv")
    sr_trials = var[var.bloc.str.startswith("validation")].sharpe.tolist()
    cfg_sr = []  # Sharpe « par trade » des configs, ramené grossièrement à l'année
    for _, g in trades[(trades.signal_time >= VAL_START) & (trades.exit_time < TEST_START)].groupby("strategy"):
        cfg_sr.append(g.r_net.mean() / g.r_net.std() * np.sqrt(len(g) / 5.8))
    n_trials = N_CONFIGS + len(sr_trials) + 3   # + 3 scénarios de coûts testés
    sr_test = perf.loc["TEST 2024-11 -> 2026-11", "sharpe"]
    dsr, emax = deflated_sharpe(sr_test, len(rt.equity), n_trials, cfg_sr + sr_trials)
    report.update(nb_essais=n_trials, deflated_sharpe_proba=dsr, sharpe_max_attendu_par_hasard=emax)

    # gain mensuel attendu (10 000 € et 1 000 €) : quantiles des rendements mensuels, tous blocs hors-échantillon
    eq_all = pd.concat([r.equity / r.equity.iloc[0] for r in res_blocks.values()])
    mret = eq_all.resample("ME").last().pct_change().dropna()
    report["rendement_mensuel_p10_p50_p90"] = [float(mret.quantile(q)) for q in (0.1, 0.5, 0.9)]
    json.dump(report, open(OUT / "final_report.json", "w"), indent=2, ensure_ascii=False, default=str)
    print(json.dumps(report, indent=1, ensure_ascii=False, default=str))

    # courbe d'équité des 3 blocs
    fig, ax = plt.subplots(figsize=(11, 4.5))
    level = 1.0
    colors = {"entrainement": "#7a8ca5", "validation": "#d08c2b", "TEST": "#c0392b"}
    for b, r in res_blocks.items():
        e = r.equity / r.equity.iloc[0] * level
        ax.plot(e.index, e.values, color=next(v for k, v in colors.items() if b.startswith(k)), label=b)
        level = e.iloc[-1]
    ax.axhline(1, color="grey", lw=0.6)
    ax.set_title("Variante retenue : équité (base 1) — entraînement / validation / TEST, après tous les coûts")
    ax.legend(loc="upper left"); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "equity_curve.png", dpi=110)


if __name__ == "__main__":
    main()
