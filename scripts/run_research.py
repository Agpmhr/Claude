"""Recherche complète sur ENTRAÎNEMENT + VALIDATION uniquement (le bloc test n'est jamais lu ici).

Étapes :
  1. trades candidats de toutes les configurations (moteur de coûts complet)
  2. sélection walk-forward des configurations (fenêtre glissante de 4 ans, re-sélection chaque année)
  3. méta-labellisation walk-forward (LightGBM réentraîné chaque année, purge des trades à cheval)
  4. comparaison de variantes de portefeuille, sur l'entraînement hors échantillon (2013-2018) et la validation
Sorties : results/research_*.csv, results/cache/*.pkl
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from swingbot.ai import build_features, expected_r, instrument_features, walk_forward_meta  # noqa: E402
from swingbot.calendar_events import event_flags, instrument_ccys, load_events  # noqa: E402
from swingbot.data import eur_conversion, load_rates, load_universe  # noqa: E402
from swingbot.portfolio import RiskConfig, metrics, run_portfolio  # noqa: E402
from swingbot.research import TEST_START, VAL_START, cached, generate_all  # noqa: E402
from swingbot.strategies import N_CONFIGS  # noqa: E402

OUT = ROOT / "results"
WF_WINDOW = 4  # années


def wf_select(trades: pd.DataFrame, years, window=WF_WINDOW, min_n=40) -> pd.Series:
    """Pour chaque année Y et chaque famille : la configuration au meilleur R moyen sur [Y-window, Y)
    (trades clôturés avant Y). La famille n'est retenue que si ce R moyen est > 0."""
    sel = pd.Series(False, index=trades.index)
    log = []
    for Y in years:
        cut = pd.Timestamp(f"{Y}-01-01")
        is_ = trades[(trades.exit_time < cut) & (trades.entry_time >= cut - pd.DateOffset(years=window))]
        st = is_.groupby(["family", "strategy"]).r_net.agg(["size", "mean"])
        st = st[st["size"] >= min_n]
        for fam, g in st.groupby(level=0):
            best = g["mean"].idxmax()
            if g.loc[best, "mean"] > 0:
                m = (trades.strategy == best[1]) & (trades.signal_time >= cut) & \
                    (trades.signal_time < pd.Timestamp(f"{Y + 1}-01-01"))
                sel |= m
                log.append(dict(annee=Y, famille=fam, config=best[1], r_moyen_is=round(g.loc[best, "mean"], 3)))
    return sel, pd.DataFrame(log)


def main():
    OUT.mkdir(exist_ok=True)
    data, qc = load_universe("D1")
    rates = load_rates()
    ev = load_events()
    trades, paths = cached("trades_base", generate_all, data, rates)
    pre = trades[(trades.signal_time < TEST_START)].copy()
    # purge : un trade de validation qui sort pendant le test n'est pas utilisé pour la recherche
    pre = pre[pre.exit_time < TEST_START]
    years = list(range(2013, 2025))

    # --- 2. sélection walk-forward
    sel, sel_log = wf_select(pre, years)
    pre["wf_sel"] = sel
    sel_log.to_csv(OUT / "research_wf_selection.csv", index=False)

    # --- 3. méta-labellisation walk-forward
    feats = cached("features", instrument_features, data, rates, ev)
    X = build_features(pre, feats)
    p = walk_forward_meta(pre, X, years)
    pre["p"] = p
    pre["er"] = expected_r(p, pre)
    # filtre calendrier : entrée le jour même ou la veille d'un FOMC/NFP sur la devise de l'instrument
    flag = pd.Series(False, index=pre.index)
    for s, g in pre.groupby("sym"):
        f = event_flags(ev, data[s].index, instrument_ccys(s), days_before=1)
        flag.loc[g.index] = f.reindex(g.entry_time).to_numpy()
    pre["event_entry"] = flag.astype(bool)
    pre.to_pickle(OUT / "cache" / "pre_scored.pkl")

    # --- 4. variantes de portefeuille (capital 10 000 € pour isoler l'effet de l'arrondi des lots)
    days = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    conv = eur_conversion(data, days)
    is_index_short = (pre.sym.isin(["US500", "NAS100", "GER40"])) & (pre.dir < 0)
    variants = {
        "A_wf_sans_IA": (pre[pre.wf_sel], RiskConfig(capital=10000)),
        "B_tout_IA_score": (pre[pre.er.notna()], RiskConfig(capital=10000, score_col="er", min_score=0.0)),
        "C_wf_plus_IA": (pre[pre.wf_sel], RiskConfig(capital=10000, score_col="er", min_score=0.0)),
        "D_C_sans_short_indices": (pre[pre.wf_sel & ~is_index_short],
                                   RiskConfig(capital=10000, score_col="er", min_score=0.0)),
        "E_D_plus_calendrier": (pre[pre.wf_sel & ~is_index_short & ~pre.event_entry],
                                RiskConfig(capital=10000, score_col="er", min_score=0.0)),
        "F_tout_IA_score_sans_short_idx": (pre[pre.er.notna() & ~is_index_short],
                                           RiskConfig(capital=10000, score_col="er", min_score=0.0)),
    }
    rows = []
    curves = {}
    for name, (cand, cfg) in variants.items():
        for blk, (a, b) in {"entrainement_hors_ech_2013_2018": (pd.Timestamp("2013-01-01"), VAL_START),
                            "validation_2019_2024": (VAL_START, TEST_START)}.items():
            res = run_portfolio(cand, paths, data, conv, cfg, a, b)
            m = metrics(res.equity, res.trades)
            m.update(variante=name, bloc=blk, arret_dd=str(res.halted), **{f"refus_{k}": v for k, v in res.skipped.items()})
            rows.append(m)
            curves[(name, blk)] = res.equity
            print(name, blk, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()
                              if k in ("cagr", "sharpe", "max_dd", "pf", "n_trades", "avg_r")}, flush=True)
    rep = pd.DataFrame(rows)
    rep.to_csv(OUT / "research_variants.csv", index=False)
    pd.to_pickle(curves, OUT / "cache" / "research_curves.pkl")
    json.dump(dict(n_configs_strategies=N_CONFIGS, n_variantes_portefeuille=len(variants)),
              open(OUT / "research_trials.json", "w"))


if __name__ == "__main__":
    main()
