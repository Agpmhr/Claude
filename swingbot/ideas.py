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
