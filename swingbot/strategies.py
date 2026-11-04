"""Bibliothèque de stratégies de swing (familles à justification économique connue).

Chaque stratégie renvoie, pour un instrument : (entrée {-1,0,+1}, sortie_long, sortie_short, ExitRules).
Les signaux sont calculés à la clôture ; le moteur exécute à l'ouverture suivante.
Les grilles de paramètres sont VOLONTAIREMENT petites (4 à 6 variantes par famille) pour limiter le surapprentissage.
Le dimensionnement par « ciblage de volatilité » est assuré partout par le stop en ATR + le risque fixe en % :
chaque trade risque la même somme quelle que soit la volatilité de l'instrument.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .backtest import ExitRules
from .indicators import atr, donchian, efficiency_ratio, rsi
from .instruments import UNIVERSE


def _z(s):
    return s.fillna(0).astype(int)


# ---------------------------------------------------------------- suivi de tendance
def donchian_breakout(df, n=55, n_exit=20, stop=2.5):
    hi, lo = donchian(df, n)
    xhi, xlo = donchian(df, n_exit)
    ent = pd.Series(np.where(df.close > hi, 1, np.where(df.close < lo, -1, 0)), index=df.index)
    return ent, df.close < xlo, df.close > xhi, ExitRules(stop_atr=stop, trail_atr=None, max_bars=150)


def ma_cross(df, fast=20, slow=100, stop=3.0):
    f, s = df.close.rolling(fast).mean(), df.close.rolling(slow).mean()
    side = np.sign(f - s)
    cross = side.ne(side.shift(1)) & side.shift(1).notna()
    ent = _z(side.where(cross, 0))
    return ent, side < 0, side > 0, ExitRules(stop_atr=stop, trail_atr=None, max_bars=200)


def tsmom(df, lookback=126, stop=3.0, hold=20):
    """Momentum en série temporelle : signe du rendement sur `lookback` jours, réévalué toutes les `hold` séances."""
    r = df.close / df.close.shift(lookback) - 1
    reb = pd.Series(np.arange(len(df)) % hold == 0, index=df.index)
    sig = np.sign(r).where(reb)
    ent = _z(sig)
    held = np.sign(r).where(reb).ffill()
    return ent, held < 0, held > 0, ExitRules(stop_atr=stop, trail_atr=None, max_bars=hold * 6)


# ---------------------------------------------------------------- horizon long (semaines -> mois)
RISK_PREMIUM = ("US500", "NAS100", "GER40", "XAUUSD")


def trend_long_only(df, n=200, stop=5.0, sym=None):
    """Position longue sur les actifs à prime de risque (indices actions, or) tant que le prix est
    au-dessus de sa moyenne longue ; on sort sous la moyenne. Idée : toucher la prime de risque des
    actions en évitant le gros des marchés baissiers. Jamais de vente à découvert."""
    m = df.close.rolling(n).mean()
    above = df.close > m
    ent = pd.Series(np.where(above & ~above.shift(1, fill_value=False), 1, 0), index=df.index)
    # (ré)entrée aussi le premier jour où l'on est au-dessus après une sortie par stop : géré par le moteur
    ent = pd.Series(np.where(above, 1, 0), index=df.index)
    return ent, ~above, pd.Series(False, index=df.index), ExitRules(stop_atr=stop, max_bars=10_000)


def tsmom_slow(df, lookback=252, hold=21, stop=5.0):
    """Momentum lent « 12 mois », réévalué une fois par mois, stop très large."""
    return tsmom(df, lookback=lookback, stop=stop, hold=hold)


# ---------------------------------------------------------------- retour à la moyenne
def mean_reversion(df, n=20, z=2.0, er_max=0.3, stop=2.0):
    """Achète les excès baissiers / vend les excès haussiers, seulement quand le marché est en range."""
    m, sd = df.close.rolling(n).mean(), df.close.rolling(n).std()
    zz = (df.close - m) / sd
    er = efficiency_ratio(df.close, 50)
    rng = er < er_max
    ent = pd.Series(np.where(rng & (zz < -z), 1, np.where(rng & (zz > z), -1, 0)), index=df.index)
    return ent, zz > 0, zz < 0, ExitRules(stop_atr=stop, max_bars=10)


def rsi2(df, lo=10, hi=90, trend_n=200, stop=2.5):
    """RSI(2) dans le sens de la tendance longue (achat de repli en tendance haussière)."""
    r = rsi(df.close, 2)
    t = df.close.rolling(trend_n).mean()
    ent = pd.Series(np.where((df.close > t) & (r < lo), 1, np.where((df.close < t) & (r > hi), -1, 0)), index=df.index)
    return ent, r > 60, r < 40, ExitRules(stop_atr=stop, max_bars=10)


# ---------------------------------------------------------------- stratégies « panel » (tout l'univers)
def xs_momentum(data, lookback=63, k=3, hold=10, stop=3.0, syms=None):
    """Momentum transversal : on achète les k plus forts et vend les k plus faibles (rendement / volatilité)."""
    syms = syms or list(data)
    close = pd.concat({s: data[s].close for s in syms}, axis=1).sort_index().ffill(limit=3)
    ret = close / close.shift(lookback) - 1
    vol = np.log(close).diff().rolling(lookback).std() * np.sqrt(lookback)
    score = ret / vol
    reb = pd.Series(np.arange(len(close)) % hold == 0, index=close.index)
    rk = score.rank(axis=1)
    cnt = score.notna().sum(axis=1)
    top = rk.gt(cnt - k, axis=0) & score.notna()
    bot = rk.le(k, axis=0) & score.notna()
    pos = (top.astype(int) - bot.astype(int)).where(reb)
    held = pos.ffill()
    out = {}
    for s in syms:
        idx = data[s].index
        p = pos[s].reindex(idx)
        hs = held[s].reindex(idx).ffill()
        out[s] = (_z(p.where(p != 0)), hs <= 0, hs >= 0, ExitRules(stop_atr=stop, max_bars=hold * 4))
    return out


def carry(data, rates=None, thr=0.01, trend_n=100, hold=20, stop=3.0):
    """Carry FX : long la devise à taux élevé contre la devise à taux bas, avec filtre de tendance
    (le carry « nu » s'effondre lors des crises ; le filtre évite d'être long d'une devise qui chute)."""
    if rates is None:
        from .data import load_rates
        rates = load_rates()
    out = {}
    for s, df in data.items():
        inst = UNIVERSE[s]
        if inst.kind != "fx":
            continue
        r = rates.reindex(df.index, method="ffill")
        diff = (r[inst.base] - r[inst.quote]).shift(1)   # taux connu la veille
        t = df.close.rolling(trend_n).mean()
        want = pd.Series(np.where((diff > thr) & (df.close > t), 1, np.where((diff < -thr) & (df.close < t), -1, 0)),
                         index=df.index)
        reb = pd.Series(np.arange(len(df)) % hold == 0, index=df.index)
        held = want.where(reb).ffill()
        out[s] = (_z(want.where(reb & (want != 0))), held <= 0, held >= 0, ExitRules(stop_atr=stop, max_bars=hold * 6))
    return out


@dataclass
class Family:
    name: str
    grid: list[dict]
    fn: Callable | None = None
    fn_panel: Callable | None = None
    panel: bool = False
    kind: str = "tendance"


FAMILIES: dict[str, Family] = {f.name: f for f in [
    Family("donchian", [dict(n=n, n_exit=x, stop=s) for n, x in [(20, 10), (55, 20), (100, 50)] for s in (2.0, 3.0)],
           fn=donchian_breakout),
    Family("ma_cross", [dict(fast=f, slow=sl) for f, sl in [(10, 50), (20, 100), (50, 200)]] +
           [dict(fast=20, slow=100, stop=2.0)], fn=ma_cross),
    Family("tsmom", [dict(lookback=lb, hold=h) for lb in (63, 126, 252) for h in (10, 20)], fn=tsmom),
    Family("trend_long", [dict(n=n, stop=st) for n in (100, 200) for st in (4.0, 6.0)], fn=trend_long_only,
           kind="long_terme"),
    Family("tsmom_slow", [dict(lookback=lb) for lb in (189, 252)], fn=tsmom_slow, kind="long_terme"),
    Family("mean_rev", [dict(n=n, z=z) for n in (10, 20) for z in (1.5, 2.0)] + [dict(n=20, z=2.0, er_max=0.2)],
           fn=mean_reversion, kind="range"),
    Family("rsi2", [dict(lo=lo, hi=100 - lo, trend_n=tn) for lo in (5, 10) for tn in (100, 200)], fn=rsi2,
           kind="range"),
    Family("xs_mom", [dict(lookback=lb, k=k) for lb in (21, 63, 126) for k in (2, 3)], fn_panel=xs_momentum,
           panel=True),
    Family("carry", [dict(thr=t, trend_n=n) for t in (0.005, 0.015) for n in (50, 100, 200)], fn_panel=carry,
           panel=True, kind="carry"),
]}

N_CONFIGS = sum(len(f.grid) for f in FAMILIES.values())


def config_id(fam: str, params: dict) -> str:
    return fam + "|" + ",".join(f"{k}={v}" for k, v in sorted(params.items()))
