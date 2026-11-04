"""Couche IA : régime de marché, méta-labellisation (LightGBM), espérance en R.

- Régime : seuils simples et explicables (rang de volatilité, efficacité de la tendance) + régime « global »
  (moyenne des rangs de volatilité de tout l'univers). Testé aussi : HMM (voir rapport).
- Méta-labellisation : le modèle NE crée PAS de signaux. Pour chaque signal proposé par une stratégie,
  il estime la probabilité que le trade finisse gagnant (après coûts), à partir d'informations connues
  à la clôture du signal. Il est réentraîné chaque année sur le passé uniquement (walk-forward), en écartant
  les trades encore ouverts à la date de réentraînement (purge).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .calendar_events import event_flags, instrument_ccys
from .indicators import atr, efficiency_ratio, pct_rank, realized_vol, rsi
from .instruments import UNIVERSE

FEATURES = ["fam_code", "dir", "kind_code", "vol_rank", "er20", "er50", "trend200", "mom63", "rsi14",
            "atr_pct", "carry", "dist20", "dow", "event_soon", "mkt_vol", "recent_cfg", "recent_fam"]
FAM_CODES = {f: i for i, f in enumerate(["donchian", "ma_cross", "tsmom", "mean_rev", "rsi2", "xs_mom", "carry", "trend_long", "tsmom_slow"])}
KIND_CODES = {"fx": 0, "metal": 1, "index": 2}


def instrument_features(data: dict, rates: pd.DataFrame | None, events: pd.DataFrame | None) -> dict:
    """Caractéristiques par instrument et par date (toutes causales : valeur en t = info <= t)."""
    vr = {}
    out = {}
    for s, df in data.items():
        c = df.close
        v = realized_vol(c, 20)
        f = pd.DataFrame(index=df.index)
        f["vol_rank"] = pct_rank(v, 252)
        f["er20"] = efficiency_ratio(c, 20)
        f["er50"] = efficiency_ratio(c, 50)
        dvol = np.log(c).diff().rolling(63).std()
        f["trend200"] = (np.log(c) - np.log(c.rolling(200).mean())) / (dvol * np.sqrt(63))
        f["mom63"] = np.log(c / c.shift(63)) / (dvol * np.sqrt(63))
        f["rsi14"] = rsi(c, 14) - 50
        a = atr(df, 20)
        f["atr_pct"] = a / c
        f["dist20"] = (c - c.rolling(20).mean()) / a
        inst = UNIVERSE[s]
        if rates is not None:
            r = rates.reindex(df.index, method="ffill").shift(1)
            f["carry"] = (r[inst.base] - r[inst.quote]) if inst.kind == "fx" else -r[inst.rate_ccy]
        else:
            f["carry"] = 0.0
        if events is not None:
            # annonce dans les 2 prochains jours ouvrés (dates connues à l'avance)
            f["event_soon"] = event_flags(events, df.index, instrument_ccys(s), days_before=2).astype(float)
        else:
            f["event_soon"] = 0.0
        f["kind_code"] = KIND_CODES[inst.kind]
        f["dow"] = df.index.dayofweek
        out[s] = f
        vr[s] = f["vol_rank"]
    mkt = pd.concat(vr, axis=1).mean(axis=1)
    for s in out:
        out[s]["mkt_vol"] = mkt.reindex(out[s].index).ffill()
    return out


def _recent_perf(trades: pd.DataFrame, key: str, n: int = 20) -> pd.Series:
    """Moyenne des R des n derniers trades du même groupe CLÔTURÉS avant le signal (pas de fuite)."""
    out = pd.Series(np.nan, index=trades.index)
    for _, g in trades.groupby(key):
        ex = g.sort_values("exit_time")
        et = ex["exit_time"].to_numpy()
        csum = np.r_[0, np.cumsum(ex["r_net"].to_numpy())]
        st = g["signal_time"].to_numpy()
        k = np.searchsorted(et, st, side="left")          # nb de trades déjà clôturés au signal
        lo = np.maximum(0, k - n)
        cnt = k - lo
        val = np.where(cnt >= 5, (csum[k] - csum[lo]) / np.maximum(cnt, 1), np.nan)
        out.loc[g.index] = val
    return out


def build_features(trades: pd.DataFrame, feats: dict) -> pd.DataFrame:
    X = []
    for s, g in trades.groupby("sym"):
        f = feats[s].reindex(g["signal_time"])
        f.index = g.index
        X.append(f)
    X = pd.concat(X).loc[trades.index]
    X["fam_code"] = trades["family"].map(FAM_CODES)
    X["dir"] = trades["dir"]
    # orienter les variables directionnelles dans le sens du trade
    for col in ["trend200", "mom63", "rsi14", "dist20", "carry"]:
        X[col] = X[col] * trades["dir"]
    X["recent_cfg"] = _recent_perf(trades, "strategy")
    X["recent_fam"] = _recent_perf(trades, "family", 50)
    return X[FEATURES]


def walk_forward_meta(trades: pd.DataFrame, X: pd.DataFrame, years: list[int], min_train_year: int = 2010,
                      params: dict | None = None, label_thr: float = 0.0) -> pd.Series:
    """Probabilité de gain prédite hors échantillon, année par année.
    Pour l'année Y : entraînement sur les trades SORTIS avant le 1er janvier Y (purge), prédiction sur Y."""
    import lightgbm as lgb
    p = pd.Series(np.nan, index=trades.index)
    y = (trades["r_net"] > label_thr).astype(int)
    prm = dict(n_estimators=200, learning_rate=0.03, num_leaves=8, min_child_samples=100, subsample=0.8,
               subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0, verbose=-1, random_state=0)
    prm.update(params or {})
    for Y in years:
        cut = pd.Timestamp(f"{Y}-01-01")
        tr = (trades["exit_time"] < cut) & (trades["entry_time"] >= pd.Timestamp(f"{min_train_year}-01-01"))
        te = (trades["signal_time"] >= cut) & (trades["signal_time"] < pd.Timestamp(f"{Y + 1}-01-01"))
        if tr.sum() < 500 or te.sum() == 0:
            continue
        m = lgb.LGBMClassifier(**prm)
        m.fit(X[tr], y[tr])
        p[te] = m.predict_proba(X[te])[:, 1]
    return p


def fit_meta(trades, X, cut: pd.Timestamp, params=None, label_thr=0.0):
    """Modèle final, entraîné sur tout ce qui est clôturé avant `cut`."""
    import lightgbm as lgb
    prm = dict(n_estimators=200, learning_rate=0.03, num_leaves=8, min_child_samples=100, subsample=0.8,
               subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0, verbose=-1, random_state=0)
    prm.update(params or {})
    tr = trades["exit_time"] < cut
    m = lgb.LGBMClassifier(**prm)
    m.fit(X[tr], (trades.loc[tr, "r_net"] > label_thr).astype(int))
    return m


def expected_r(p: pd.Series, trades: pd.DataFrame, cut_for_stats: dict[int, pd.Timestamp] | None = None):
    """Espérance en R = p x gain moyen - (1-p) x perte moyenne, gains/pertes moyens de la famille
    calculés sur le passé (trades clôturés avant le début de l'année du signal)."""
    er = pd.Series(np.nan, index=trades.index)
    years = trades["signal_time"].dt.year
    for Y in sorted(years.unique()):
        cut = pd.Timestamp(f"{Y}-01-01")
        past = trades[trades["exit_time"] < cut]
        if len(past) < 200:
            continue
        aw = past[past.r_net > 0].groupby("family")["r_net"].mean()
        al = -past[past.r_net <= 0].groupby("family")["r_net"].mean()
        m = years == Y
        fam = trades.loc[m, "family"]
        er[m] = p[m] * fam.map(aw).to_numpy() - (1 - p[m]) * fam.map(al).to_numpy()
    return er
