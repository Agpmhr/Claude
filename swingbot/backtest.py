"""Moteur de backtest au niveau du trade.

Principe (sans fuite du futur) :
  - le signal est calculé à la CLÔTURE de la bougie t (données <= t uniquement) ;
  - l'ordre est exécuté à l'OUVERTURE de la bougie t+1, en payant le demi-spread + glissement ;
  - le stop initial = k x ATR(t) ; un stop touché est exécuté au niveau du stop moins un glissement,
    ou à l'ouverture si le marché a « gappé » au-delà (week-end, annonce) ;
  - si stop et objectif sont touchés dans la même bougie, on suppose le STOP en premier (prudent) ;
  - les swaps sont débités/crédités à chaque nuit tenue (x3 le mercredi pour le FX, le vendredi pour les indices).

Résultat : chaque trade est exprimé en multiples de R (R = perte si le stop initial est touché, hors coûts).
C'est ce qui permet de comparer l'or, l'EUR/USD et le S&P 500 sur la même échelle.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .costs import CostModel, swap_rates
from .indicators import atr as atr_fn
from .instruments import UNIVERSE


@dataclass
class ExitRules:
    stop_atr: float = 2.0         # stop initial en ATR
    trail_atr: float | None = None  # stop suiveur (chandelier) en ATR, mis à jour à la clôture
    target_r: float | None = None   # objectif en R (None = pas d'objectif)
    max_bars: int = 60            # sortie au temps
    atr_n: int = 20


TRADE_COLS = ["sym", "strategy", "dir", "signal_time", "entry_time", "exit_time", "entry_i", "exit_i",
              "entry", "exit", "stop_dist", "atr", "bars", "reason", "r_gross", "c_spread", "c_comm",
              "c_swap", "c_slip", "r_net", "notional_per_r"]


def simulate(sym: str, df: pd.DataFrame, entry: pd.Series, exit_long: pd.Series | None = None,
             exit_short: pd.Series | None = None, rules: ExitRules = ExitRules(), cost: CostModel = CostModel(),
             rates: pd.DataFrame | None = None, event_days: pd.Series | None = None, strategy: str = "",
             tf: str = "D1", forced_bars: np.ndarray | None = None, forced_entries: np.ndarray | None = None,
             forced_dirs: np.ndarray | None = None) -> tuple[pd.DataFrame, list[np.ndarray]]:
    """Simule les trades d'une stratégie sur un instrument.

    entry : série {-1, 0, +1} décidée à la clôture. Une position à la fois ; un signal opposé retourne la position.
    forced_* : utilisé par la stratégie aléatoire (entrées et durées imposées).
    Retourne (DataFrame des trades, liste des trajectoires R marquées au marché à chaque clôture).
    """
    inst = UNIVERSE[sym]
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    idx = df.index
    n = len(df)
    a = atr_fn(df, rules.atr_n).to_numpy()
    ent = np.nan_to_num(entry.reindex(idx).to_numpy(float)) if entry is not None else np.zeros(n)
    xl = exit_long.reindex(idx).fillna(False).to_numpy(bool) if exit_long is not None else np.zeros(n, bool)
    xs = exit_short.reindex(idx).fillna(False).to_numpy(bool) if exit_short is not None else np.zeros(n, bool)
    ev = event_days.reindex(idx).fillna(False).to_numpy(bool) if event_days is not None else np.zeros(n, bool)
    sw_long, sw_short = swap_rates(inst, rates, idx, markup=inst.swap_markup * cost.swap_markup_mult * cost.cost_mult) if cost.swaps else (np.zeros(n), np.zeros(n))
    dow = idx.day_of_week.to_numpy()
    if tf == "D1":
        night_w = np.where(dow == inst.triple_swap_day, 3.0, 1.0)  # nuit qui suit la bougie i
    else:  # H1 : swap prélevé au passage de 22h UTC (approximation du rollover 17h New York)
        hr = idx.hour.to_numpy()
        night_w = np.where(hr == 21, np.where(dow == inst.triple_swap_day, 3.0, 1.0), 0.0)
    hours = idx

    trades, paths = [], []

    def run_trade(sig_i, d, max_bars):
        """Ouvre à l'open de sig_i+1 ; renvoie (dict trade, chemin R, index de sortie)."""
        ei = sig_i + 1
        if ei >= n or not np.isfinite(a[sig_i]) or a[sig_i] <= 0:
            return None
        spr_in = cost.spread(inst, hours[ei], tf, ev[ei], o[ei])
        slip_in = cost.slippage_spreads * spr_in
        px_in = o[ei] + d * (spr_in / 2 + slip_in)
        stop_dist = rules.stop_atr * a[sig_i]
        stop = o[ei] - d * stop_dist            # stop mesuré depuis le prix « milieu »
        target = o[ei] + d * rules.target_r * stop_dist if rules.target_r else None
        comm = cost.commission(inst, o[ei])
        c_spread = (spr_in / 2) / stop_dist
        c_slip = slip_in / stop_dist
        c_comm = comm / stop_dist
        c_swap = 0.0
        best = c[ei]
        path = []
        reason, px_out, xi = "fin", None, None
        for i in range(ei, n):
            # 1) exécution intrabougie : gap à l'ouverture, puis stop, puis objectif (y compris bougie d'entrée)
            if True:
                if i > ei and d * (o[i] - stop) <= 0:
                    reason, xi, raw = "gap", i, o[i]
                elif (l[i] <= stop) if d > 0 else (h[i] >= stop):
                    reason, xi, raw = "stop", i, stop
                elif target is not None and d * ((h[i] if d > 0 else l[i]) - target) >= 0:
                    reason, xi, raw = "objectif", i, target
                if xi is not None:
                    spr = cost.spread(inst, hours[i], tf, ev[i], o[i])
                    extra = cost.stop_slippage_spreads * spr if reason in ("stop", "gap") else 0.0
                    px_out = raw - d * (spr / 2 + extra)
                    c_spread += (spr / 2) / stop_dist
                    c_slip += extra / stop_dist
                    break
            # 2) marquage à la clôture (sortie hypothétique au prix milieu, hors coût de sortie)
            mtm = d * (c[i] - px_in) / stop_dist - c_comm - c_swap
            # 3) décisions à la clôture -> exécution à l'ouverture suivante
            bars = i - ei + 1
            want_exit = (d > 0 and xl[i]) or (d < 0 and xs[i]) or bars >= max_bars or (ent[i] == -d)
            if i == n - 1:
                want_exit = True
            # swap pour la nuit qui suit la bougie i (prudent : toujours payé si on est en position à la clôture)
            sw = sw_long[i] if d > 0 else sw_short[i]
            c_swap -= night_w[i] * sw / 360.0 * c[i] / stop_dist  # (swap positif = crédit = coût négatif)
            path.append(mtm)
            if want_exit:
                xi = min(i + 1, n - 1)
                reason = "signal" if i + 1 < n else "fin"
                if bars >= max_bars and not ((d > 0 and xl[i]) or (d < 0 and xs[i])):
                    reason = "temps"
                if ent[i] == -d:
                    reason = "retournement"
                spr = cost.spread(inst, hours[xi], tf, ev[xi], o[xi])
                raw = o[xi] if i + 1 < n else c[i]
                px_out = raw - d * (spr / 2 + cost.slippage_spreads * spr)
                c_spread += (spr / 2) / stop_dist
                c_slip += cost.slippage_spreads * spr / stop_dist
                if i + 1 >= n:
                    xi = i
                break
            if rules.trail_atr is not None and np.isfinite(a[i]):
                best = max(best, c[i]) if d > 0 else min(best, c[i])
                new_stop = best - d * rules.trail_atr * a[i]
                if d * (new_stop - stop) > 0:
                    stop = new_stop
        if px_out is None:
            return None
        c_comm += cost.commission(inst, px_out) / stop_dist
        # R brut = mouvement du prix « milieu » entre l'entrée et la sortie ; les coûts sont comptés à part
        r_gross = d * (raw - o[ei]) / stop_dist
        r_net = r_gross - c_spread - c_slip - c_comm - c_swap
        path = path[: xi - ei] + [r_net]   # une valeur par clôture, la dernière = résultat final net
        tr = dict(sym=sym, strategy=strategy, dir=d, signal_time=idx[sig_i], entry_time=idx[ei],
                  exit_time=idx[xi], entry_i=ei, exit_i=xi, entry=px_in, exit=px_out, stop_dist=stop_dist,
                  atr=a[sig_i], bars=xi - ei, reason=reason, r_gross=r_gross, c_spread=c_spread,
                  c_comm=c_comm, c_swap=c_swap, c_slip=c_slip, r_net=r_net,
                  notional_per_r=o[ei] / stop_dist)
        return tr, np.array(path), xi

    if forced_entries is not None:
        for si, d, mb in zip(forced_entries, forced_dirs, forced_bars):
            res = run_trade(int(si), int(d), int(mb))
            if res:
                trades.append(res[0]); paths.append(res[1])
    else:
        i = 0
        while i < n - 1:
            d = int(ent[i])
            if d == 0:
                i += 1
                continue
            res = run_trade(i, d, rules.max_bars)
            if res is None:
                i += 1
                continue
            tr, p, xi = res
            trades.append(tr); paths.append(p)
            # prochain signal possible : à la clôture de la bougie de sortie (ou retournement immédiat)
            nxt = xi - 1 if tr["reason"] == "retournement" else xi
            i = max(nxt, i + 1)
    out = pd.DataFrame(trades, columns=TRADE_COLS)
    return out, paths


def summarize_r(r: pd.Series | np.ndarray) -> dict:
    """Statistiques simples d'une série de résultats en R."""
    r = np.asarray(r, float)
    if len(r) == 0:
        return dict(n=0, win=np.nan, avg_r=np.nan, pf=np.nan, total_r=0.0)
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return dict(n=len(r), win=float((r > 0).mean()), avg_r=float(r.mean()),
                pf=float(gains / losses) if losses > 0 else np.inf, total_r=float(r.sum()))
