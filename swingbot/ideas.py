"""Deuxième vague d'idées (horizons courts à moyens, pensées pour MINIMISER les frais de financement).

Leçon de la 1re vague : les swaps (surtout sur indices, ≈10 %/an) tuent tout ce qui dure des mois.
On cherche donc des effets courts (1 à 10 jours) avec une raison économique :

  tom        Tournant du mois sur indices (flux de salaires / fonds de pension en début de mois).
             Littérature : Ariel 1987, Lakonishok & Smidt 1988, McConnell & Xu 2008.
  pre_fomc   Dérive haussière des actions avant les décisions de la Fed (Lucca & Moench 2015).
  idx_dip    Rachat de repli court terme sur indices, uniquement à l'achat (retour à la moyenne
             court terme des indices actions, liquidité fournie aux vendeurs pressés).
  carry_safe Carry FX (on encaisse le swap positif) seulement quand le marché est calme
             (VIX bas et en baisse), sortie dès que la peur monte. IDÉE MAISON : le carry
             « nu » rapporte peu à peu puis perd tout d'un coup lors des paniques.
  me_usd     Flux de couverture de fin de mois : si les actions US ont mieux fait que l'Europe
             dans le mois, les investisseurs étrangers revendent du dollar pour réajuster leurs
             couvertures (Melvin & Prins 2015). Seulement sur les paires en USD.
  vix_crush  IDÉE MAISON : après un pic de panique (VIX très au-dessus de sa médiane) et dès
             que la peur reflue, on achète les indices quelques jours (prime de risque maximale).

Toutes les règles n'utilisent que des informations connues à la clôture du signal.
Le calendrier des fins de mois et des réunions de la Fed est publié à l'avance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import ExitRules
from .indicators import rsi
from .instruments import UNIVERSE


def _idx(sym):
    return UNIVERSE[sym].kind == "index"


def days_to_month_end(index: pd.DatetimeIndex) -> pd.Series:
    """0 = dernier jour de bourse du mois, 1 = avant-dernier, etc. (calendrier connu à l'avance)."""
    s = pd.Series(1, index=index)
    per = index.to_period("M")
    return s.groupby(per).cumsum().groupby(per).transform("max") - s.groupby(per).cumsum()


def tom(data, ctx, k=1, hold=4, stop=3.0):
    """Signal à la clôture du k-ième jour avant la fin du mois -> achat à l'ouverture suivante, tenu `hold` séances."""
    out = {}
    for s, df in data.items():
        if not _idx(s):
            continue
        r = days_to_month_end(df.index)
        ent = pd.Series(np.where(r == k, 1, 0), index=df.index)
        out[s] = (ent, None, None, ExitRules(stop_atr=stop, max_bars=hold))
    return out


def pre_fomc(data, ctx, before=2, hold=2, stop=3.0):
    """Achat des indices `before` séances avant le jour de décision FOMC (signal à la clôture précédente)."""
    fomc = pd.DatetimeIndex(ctx["events"].query("evenement == 'FOMC'")["date"])
    out = {}
    for s, df in data.items():
        if not _idx(s):
            continue
        pos = df.index.searchsorted(fomc)
        sig = pos - before            # clôture du signal ; entrée à l'ouverture de pos-before+1
        sig = sig[(sig >= 0) & (pos < len(df)) & (df.index[np.minimum(pos, len(df) - 1)].isin(fomc))]
        ent = pd.Series(0, index=df.index)
        ent.iloc[sig] = 1
        out[s] = (ent, None, None, ExitRules(stop_atr=stop, max_bars=hold))
    return out


def idx_dip(data, ctx, lo=10, trend=True, stop=3.0):
    """RSI(2) très bas sur un indice -> achat ; sortie quand RSI(2) > 60 ou après 6 séances."""
    out = {}
    for s, df in data.items():
        if not _idx(s):
            continue
        r = rsi(df.close, 2)
        ok = df.close > df.close.rolling(200).mean() if trend else pd.Series(True, index=df.index)
        ent = pd.Series(np.where((r < lo) & ok, 1, 0), index=df.index)
        out[s] = (ent, r > 60, None, ExitRules(stop_atr=stop, max_bars=6))
    return out


def _vix(ctx, index):
    v = ctx["vix"].close
    # le VIX clôture à 21h UTC : connu avant la clôture FX et à la clôture des indices US
    return v.reindex(index, method="ffill")


def carry_safe(data, ctx, vix_max=20.0, thr=0.01, stop=3.0):
    rates = ctx["rates"]
    out = {}
    for s, df in data.items():
        inst = UNIVERSE[s]
        if inst.kind != "fx":
            continue
        r = rates.reindex(df.index, method="ffill").shift(1)
        diff = r[inst.base] - r[inst.quote]
        v = _vix(ctx, df.index)
        calm = (v < vix_max) & (v < v.rolling(20).mean())
        fear = (v > vix_max * 1.25) | (v > 1.3 * v.rolling(20).min())
        want = np.where(calm & (diff > thr), 1, np.where(calm & (diff < -thr), -1, 0))
        ent = pd.Series(want, index=df.index)
        xl = fear | (diff <= thr / 2)
        xs = fear | (diff >= -thr / 2)
        out[s] = (ent, xl, xs, ExitRules(stop_atr=stop, max_bars=60))
    return out


def me_usd(data, ctx, thr=0.0, stop=3.0):
    """3 séances avant la fin du mois : actions US vs Europe depuis le début du mois -> sens du dollar."""
    us, eu = ctx["all_data"]["US500"].close, ctx["all_data"]["GER40"].close
    def mtd(c):
        first = c.groupby(c.index.to_period("M")).transform("first")
        return c / first - 1
    spread = (mtd(us) - mtd(eu))
    out = {}
    for s, df in data.items():
        inst = UNIVERSE[s]
        if inst.kind != "fx" or "USD" not in (inst.base, inst.quote):
            continue
        r = days_to_month_end(df.index)
        sp = spread.reindex(df.index, method="ffill")
        usd_dir = np.where(sp > thr, -1, np.where(sp < -thr, 1, 0))   # US surperforme -> dollar vendu
        d = usd_dir * (1 if inst.base == "USD" else -1)
        ent = pd.Series(np.where(r == 3, d, 0), index=df.index)
        out[s] = (ent, None, None, ExitRules(stop_atr=stop, max_bars=4))
    return out


def vix_crush(data, ctx, spike=1.4, hold=7, stop=3.0):
    out = {}
    for s, df in data.items():
        if not _idx(s):
            continue
        v = _vix(ctx, df.index)
        med = v.rolling(60).median()
        recent_spike = (v / med).rolling(10).max() > spike
        easing = v < 0.9 * v.rolling(5).max()
        ent = pd.Series(np.where(recent_spike & easing, 1, 0), index=df.index)
        out[s] = (ent, None, None, ExitRules(stop_atr=stop, max_bars=hold))
    return out


IDEAS = {
    "tom": (tom, [dict(k=k, hold=h) for k in (1, 3) for h in (4, 6)]),
    "pre_fomc": (pre_fomc, [dict(before=2, hold=2), dict(before=1, hold=1)]),
    "idx_dip": (idx_dip, [dict(lo=lo, trend=t) for lo in (5, 15) for t in (True, False)]),
    "carry_safe": (carry_safe, [dict(vix_max=v, thr=t) for v in (18.0, 22.0) for t in (0.01, 0.02)]),
    "me_usd": (me_usd, [dict(thr=0.0), dict(thr=0.01)]),
    "vix_crush": (vix_crush, [dict(spike=sp, hold=h) for sp in (1.3, 1.6) for h in (5, 10)]),
}
N_IDEA_CONFIGS = sum(len(g) for _, g in IDEAS.values())


def generate_ideas(data, ctx, cost, ideas=None):
    from .backtest import simulate
    from .research import block_of, event_series
    from .strategies import config_id
    evs = event_series(data, ctx["events"])
    rows, paths = [], []
    for name in ideas or IDEAS:
        fn, grid = IDEAS[name]
        for p in grid:
            cid = config_id(name, p)
            for s, (ent, xl, xs, rules) in fn(data, ctx, **p).items():
                tr, pth = simulate(s, data[s], ent, xl, xs, rules, cost, ctx["rates"], evs[s], strategy=cid)
                if len(tr):
                    tr["family"] = name
                    rows.append(tr)
                    paths.extend(pth)
    trades = pd.concat(rows, ignore_index=True)
    trades["block"] = block_of(trades["entry_time"])
    trades["exit_block"] = block_of(trades["exit_time"])
    return trades, paths


# ============================================================================ VAGUE 3
"""
  trend_carry  Suivi de tendance FX UNIQUEMENT dans le sens où le swap est positif : on combine deux primes
               (tendance + portage) et le temps joue pour nous au lieu de nous coûter. Idée maison.
  fx_rev_xs    Retournement transversal hebdomadaire en FX : on achète les paires les plus faibles de la semaine
               et on vend les plus fortes (excès de flux de court terme). Littérature : reversal court terme.
  squeeze      Contraction de volatilité (ATR court / ATR long bas) puis entrée dans le sens de la tendance
               de fond : la volatilité revient toujours, on se place du bon côté. Classique « NR7/squeeze ».
  idx_dip_vix  idx_dip seulement quand le VIX est au-dessus de sa médiane d'un an : on fournit de la liquidité
               quand elle est chère. Raffinement de la vague 2.
  fx_dip_carry Rachat de repli (RSI(2) bas) sur les paires FX, seulement dans le sens du swap positif. Idée maison.
"""


def _carry_dir(ctx, inst, index):
    r = ctx["rates"].reindex(index, method="ffill").shift(1)
    return r[inst.base] - r[inst.quote]


def trend_carry(data, ctx, n=55, thr=0.005, stop=3.0):
    from .indicators import donchian
    out = {}
    for s, df in data.items():
        inst = UNIVERSE[s]
        if inst.kind != "fx":
            continue
        diff = _carry_dir(ctx, inst, df.index)
        hi, lo = donchian(df, n)
        xhi, xlo = donchian(df, n // 2)
        ent = pd.Series(np.where((df.close > hi) & (diff > thr), 1, np.where((df.close < lo) & (diff < -thr), -1, 0)),
                        index=df.index)
        out[s] = (ent, (df.close < xlo) | (diff <= 0), (df.close > xhi) | (diff >= 0),
                  ExitRules(stop_atr=stop, max_bars=150))
    return out


def fx_rev_xs(data, ctx, lookback=5, k=2, hold=5, stop=2.5):
    fx = [s for s in data if UNIVERSE[s].kind == "fx"]
    close = pd.concat({s: data[s].close for s in fx}, axis=1).sort_index().ffill(limit=3)
    vol = np.log(close).diff().rolling(60).std() * np.sqrt(lookback)
    score = np.log(close / close.shift(lookback)) / vol
    reb = pd.Series(close.index.dayofweek == 4, index=close.index)       # chaque vendredi
    rk = score.rank(axis=1)
    cnt = score.notna().sum(axis=1)
    pos = (rk.le(k, axis=0).astype(int) - rk.gt(cnt - k, axis=0).astype(int)).where(reb & (cnt >= 2 * k))
    out = {}
    for s in fx:
        p = pos[s].reindex(data[s].index)
        out[s] = (p.where(p != 0).fillna(0).astype(int), None, None, ExitRules(stop_atr=stop, max_bars=hold))
    return out


def squeeze(data, ctx, ratio=0.6, hold=8, stop=2.5):
    from .indicators import atr as _atr
    out = {}
    for s, df in data.items():
        a5, a50 = _atr(df, 5), _atr(df, 50)
        sq = (a5 / a50) < ratio
        trend = np.sign(df.close - df.close.rolling(100).mean())
        first = sq & ~sq.shift(1, fill_value=False)
        ent = pd.Series(np.where(first, trend, 0), index=df.index).fillna(0).astype(int)
        if UNIVERSE[s].kind == "index":
            ent = ent.clip(lower=0)            # pas de vente à découvert d'indices (prime de risque)
        out[s] = (ent, None, None, ExitRules(stop_atr=stop, max_bars=hold))
    return out


def idx_dip_vix(data, ctx, lo=10, q=0.5, stop=3.0):
    out = {}
    for s, df in data.items():
        if not _idx(s):
            continue
        r = rsi(df.close, 2)
        v = _vix(ctx, df.index)
        fearful = v > v.rolling(252, min_periods=120).quantile(q)
        ent = pd.Series(np.where((r < lo) & fearful, 1, 0), index=df.index)
        out[s] = (ent, r > 60, None, ExitRules(stop_atr=stop, max_bars=6))
    return out


def fx_dip_carry(data, ctx, lo=10, thr=0.01, stop=2.5):
    out = {}
    for s, df in data.items():
        inst = UNIVERSE[s]
        if inst.kind != "fx":
            continue
        diff = _carry_dir(ctx, inst, df.index)
        r = rsi(df.close, 2)
        ent = pd.Series(np.where((r < lo) & (diff > thr), 1, np.where((r > 100 - lo) & (diff < -thr), -1, 0)),
                        index=df.index)
        out[s] = (ent, r > 60, r < 40, ExitRules(stop_atr=stop, max_bars=6))
    return out


IDEAS3 = {
    "trend_carry": (trend_carry, [dict(n=n, thr=t) for n in (20, 55) for t in (0.005, 0.015)]),
    "fx_rev_xs": (fx_rev_xs, [dict(lookback=lb, k=k) for lb in (5, 10) for k in (2, 3)]),
    "squeeze": (squeeze, [dict(ratio=r, hold=h) for r in (0.5, 0.7) for h in (5, 10)]),
    "idx_dip_vix": (idx_dip_vix, [dict(lo=lo, q=q) for lo in (5, 15) for q in (0.5, 0.7)]),
    "fx_dip_carry": (fx_dip_carry, [dict(lo=lo, thr=t) for lo in (5, 15) for t in (0.005, 0.015)]),
}
IDEAS.update(IDEAS3)
N_IDEA3_CONFIGS = sum(len(g) for _, g in IDEAS3.values())


# ============================================================================ VAGUE 4
"""
  ibs        « Internal Bar Strength » sur indices : clôture tout en bas de la séance -> rebond le lendemain
             (Pagonidis 2013, effet IBS très documenté sur les indices actions).
  streak     N clôtures baissières d'affilée sur un indice -> achat, sortie au premier rebond.
  dip_vote   Ensemble de signaux de repli (RSI(2), IBS, série baissière) : on achète quand plusieurs
             signaux indépendants sont d'accord. Idée maison (vote = moins de faux signaux).
  ml_pool    IA « directe » : un modèle LightGBM unique (tous instruments mélangés) prédit le mouvement des
             5 prochaines séances en multiples d'ATR, réentraîné chaque année sur le passé seulement.
             On ne trade que les prédictions fortes. Sur indices : achat seulement.
"""


def _ibs(df):
    rng = (df.high - df.low).replace(0, np.nan)
    return (df.close - df.low) / rng


def ibs(data, ctx, thr=0.15, hold=2, stop=3.0):
    out = {}
    for s, df in data.items():
        if not _idx(s):
            continue
        x = _ibs(df)
        ent = pd.Series(np.where(x < thr, 1, 0), index=df.index)
        out[s] = (ent, x > 0.7, None, ExitRules(stop_atr=stop, max_bars=hold))
    return out


def streak(data, ctx, n=3, trend=False, stop=3.0):
    out = {}
    for s, df in data.items():
        if not _idx(s):
            continue
        down = (df.close < df.close.shift(1)).astype(int)
        run = down.groupby((down == 0).cumsum()).cumsum()
        ok = df.close > df.close.rolling(200).mean() if trend else True
        ent = pd.Series(np.where((run >= n) & ok, 1, 0), index=df.index)
        out[s] = (ent, df.close > df.high.shift(1), None, ExitRules(stop_atr=stop, max_bars=5))
    return out


def dip_vote(data, ctx, votes=2, trend=False, stop=3.0):
    out = {}
    for s, df in data.items():
        if not _idx(s):
            continue
        r2 = rsi(df.close, 2)
        down = (df.close < df.close.shift(1)).astype(int)
        run = down.groupby((down == 0).cumsum()).cumsum()
        v = (r2 < 10).astype(int) + (_ibs(df) < 0.2).astype(int) + (run >= 3).astype(int)
        ok = df.close > df.close.rolling(200).mean() if trend else True
        ent = pd.Series(np.where((v >= votes) & ok, 1, 0), index=df.index)
        out[s] = (ent, r2 > 60, None, ExitRules(stop_atr=stop, max_bars=5))
    return out


def _ml_features(df, ctx, s):
    from .indicators import atr as _atr, efficiency_ratio, pct_rank, realized_vol
    c = df.close
    a = _atr(df, 20)
    f = pd.DataFrame(index=df.index)
    dv = np.log(c).diff().rolling(60).std()
    for h in (1, 2, 5, 10, 20, 60):
        f[f"r{h}"] = np.log(c / c.shift(h)) / (dv * np.sqrt(h))
    f["rsi2"] = rsi(c, 2)
    f["rsi14"] = rsi(c, 14)
    f["ibs"] = _ibs(df)
    for n in (20, 50, 200):
        f[f"d{n}"] = (c - c.rolling(n).mean()) / a
    f["volr"] = pct_rank(realized_vol(c, 20), 252)
    f["er"] = efficiency_ratio(c, 20)
    v = _vix(ctx, df.index)
    f["vix"] = v
    f["vix_ch"] = np.log(v / v.shift(5))
    f["dow"] = df.index.dayofweek
    f["dme"] = days_to_month_end(df.index)
    inst = UNIVERSE[s]
    if inst.kind == "fx":
        f["carry"] = _carry_dir(ctx, inst, df.index)
    else:
        f["carry"] = 0.0
    f["kind"] = {"fx": 0, "metal": 1, "index": 2, "future": 3}[inst.kind]
    # cible : mouvement de l'ouverture t+1 à l'ouverture t+6, en ATR (utilisée SEULEMENT pour l'entraînement)
    y = (df.open.shift(-6) - df.open.shift(-1)) / a
    return f, y


def ml_pool(data, ctx, k=0.3, scope="indices", stop=2.5):
    import lightgbm as lgb
    syms = [s for s in data if (UNIVERSE[s].kind == "index" if scope == "indices" else True)]
    F, Y, S = [], [], []
    for s in syms:
        f, y = _ml_features(data[s], ctx, s)
        F.append(f); Y.append(y.clip(-4, 4)); S.append(pd.Series(s, index=f.index))
    X = pd.concat(F); y = pd.concat(Y); sy = pd.concat(S)
    dates = X.index
    pred = pd.Series(np.nan, index=range(len(X)))
    Xv = X.reset_index(drop=True); yv = y.reset_index(drop=True)
    for Y_ in range(2013, 2027):
        cut = pd.Timestamp(f"{Y_}-01-01")
        tr = (dates < cut - pd.Timedelta(days=10)) & yv.notna().to_numpy()   # purge : la cible voit 6 séances
        te = (dates >= cut) & (dates < pd.Timestamp(f"{Y_ + 1}-01-01"))
        if tr.sum() < 2000 or te.sum() == 0:
            continue
        m = lgb.LGBMRegressor(n_estimators=150, learning_rate=0.03, num_leaves=8, min_child_samples=200,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=10, verbose=-1,
                              random_state=0)
        m.fit(Xv[tr], yv[tr])
        pred[np.where(te)[0]] = m.predict(Xv[te])
    pred.index = pd.MultiIndex.from_arrays([sy.to_numpy(), dates])
    out = {}
    for s in syms:
        p = pred.xs(s).reindex(data[s].index)
        sig = np.where(p > k, 1, np.where(p < -k, -1, 0))
        if UNIVERSE[s].kind == "index":
            sig = np.clip(sig, 0, 1)
        out[s] = (pd.Series(sig, index=data[s].index), None, None, ExitRules(stop_atr=stop, max_bars=5))
    return out


IDEAS4 = {
    "ibs": (ibs, [dict(thr=t, hold=h) for t in (0.1, 0.2) for h in (1, 3)]),
    "streak": (streak, [dict(n=n, trend=t) for n in (3, 4) for t in (False, True)]),
    "dip_vote": (dip_vote, [dict(votes=v, trend=t) for v in (2, 3) for t in (False, True)]),
    "ml_pool": (ml_pool, [dict(k=k, scope=sc) for k in (0.3, 0.6) for sc in ("indices", "all")]),
}
IDEAS.update(IDEAS4)


# ============================================================================ VAGUE 5 : futures CFD sans swap (MT5)
"""
  vix_carry  Prime de volatilité : les futures VIX valent presque toujours plus que le VIX au comptant (courbe
             « en contango ») et glissent vers lui -> on VEND le future VIX quand la courbe est bien en contango
             (VIX / VIX3M bas) et que la peur reflue ; on sort dès que la courbe s'aplatit. Risque connu :
             pertes brutales lors des krachs (février 2018, mars 2020) -> stop + sortie sur aplatissement.
  fut_trend  Suivi de tendance (Donchian) sur pétrole WTI, Brent et indice dollar : SANS swap sur les futures
             CFD, la tendance n'est plus mangée par le financement.
"""


def vix_carry(data, ctx, thr=0.9, stop=2.5):
    out = {}
    if "VIXF" not in data:
        return out
    df = data["VIXF"]
    v = _vix(ctx, df.index)
    v3 = ctx["vix3m"].close.reindex(df.index, method="ffill")
    ratio = v / v3
    calm = (ratio < thr) & (v < v.rolling(10).mean())
    ent = pd.Series(np.where(calm, -1, 0), index=df.index)
    out["VIXF"] = (ent, None, ratio > 0.97, ExitRules(stop_atr=stop, max_bars=40))
    return out


def fut_trend(data, ctx, n=55, stop=3.0):
    from .indicators import donchian
    out = {}
    for s, df in data.items():
        if s not in ("WTIF", "BRENTF", "DXYF", "GOLDF"):
            continue
        hi, lo = donchian(df, n)
        xhi, xlo = donchian(df, max(5, n // 2))
        ent = pd.Series(np.where(df.close > hi, 1, np.where(df.close < lo, -1, 0)), index=df.index)
        out[s] = (ent, df.close < xlo, df.close > xhi, ExitRules(stop_atr=stop, max_bars=250))
    return out


IDEAS5 = {
    "vix_carry": (vix_carry, [dict(thr=t, stop=st) for t in (0.85, 0.92) for st in (2.0, 3.0)]),
    "fut_trend": (fut_trend, [dict(n=n, stop=st) for n in (20, 55) for st in (2.0, 3.0)]),
}
IDEAS.update(IDEAS5)
