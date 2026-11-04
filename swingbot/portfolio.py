"""Simulation du portefeuille complet : allocateur multi-instruments + gestion du risque + coupe-circuit.

Chaque jour (à la clôture) :
  1. on marque toutes les positions ouvertes au marché (équité quotidienne réelle, pas seulement à la sortie) ;
  2. coupe-circuit : perte du jour, de la semaine, ou drawdown maximal -> plus de nouvelles entrées ;
  3. on classe les signaux du jour par score (espérance en R estimée par l'IA, sinon ordre neutre) ;
  4. on accepte les meilleurs tant que les limites tiennent : nombre de positions, risque total ouvert,
     exposition nette par devise (ne pas prendre 5 fois le même pari sur le dollar), marge disponible ;
  5. taille de position = (capital x risque %) / (distance du stop en EUR par lot), arrondie au 0,01 lot.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .instruments import LOT_STEP, MIN_LOT, UNIVERSE, currencies_of


@dataclass
class RiskConfig:
    capital: float = 1000.0
    risk_per_trade: float = 0.0075     # 0,75 % du capital par trade (entre 0,5 et 1 %)
    max_risk_mult: float = 1.33        # le score IA peut moduler la taille entre 0,5x et 1,33x (max 1 %)
    min_risk_mult: float = 0.67
    max_positions: int = 8
    max_open_risk: float = 0.05        # 5 % de risque total ouvert au maximum
    max_ccy_units: float = 2.0         # exposition nette max par devise, en « nombre de trades pleins »
    max_margin_use: float = 0.5        # marge utilisée <= 50 % du capital
    daily_stop: float = 0.03           # coupe-circuit : -3 % sur la journée -> pas d'entrée le lendemain
    weekly_stop: float = 0.06          # -6 % sur la semaine -> pas d'entrée jusqu'à lundi
    max_dd_stop: float = 0.20          # -20 % depuis le plus haut -> arrêt complet (intervention humaine)
    lot_rounding: bool = True          # arrondi au 0,01 lot (contrainte réelle du broker)
    max_round_up: float = 1.5          # si le lot minimum dépasse 1,5x le risque voulu -> trade refusé
    one_per_symbol: bool = True        # False : plusieurs sous-stratégies peuvent tenir le même instrument
    score_col: str | None = None       # colonne de score pour classer et dimensionner (None = pas d'IA)
    min_score: float | None = None     # seuil de filtrage sur le score


@dataclass
class PortfolioResult:
    equity: pd.Series
    trades: pd.DataFrame
    skipped: dict = field(default_factory=dict)
    halted: pd.Timestamp | None = None


def run_portfolio(trades: pd.DataFrame, paths: list, data: dict, conv: pd.DataFrame, cfg: RiskConfig,
                  start: pd.Timestamp, end: pd.Timestamp) -> PortfolioResult:
    """trades : candidats (colonnes du moteur) ; paths[i] : trajectoire R du trade d'index i."""
    cand = trades[(trades.signal_time >= start) & (trades.signal_time < end)].copy()
    if cfg.min_score is not None and cfg.score_col:
        cand = cand[cand[cfg.score_col] > cfg.min_score]
    days = pd.DatetimeIndex(sorted(set().union(*[d.index[(d.index >= start) & (d.index < end)]
                                                 for d in data.values()])))
    by_day = {d: g for d, g in cand.groupby("signal_time")}
    eq = cfg.capital
    peak = eq
    curve = np.empty(len(days))
    open_pos: list[dict] = []
    done = []
    skipped = dict(limites=0, lot_min=0, coupe_circuit=0, marge=0, doublon=0)
    day_start_eq, week_start_eq = eq, eq
    block_until = None
    halted = None
    conv_f = conv.reindex(days, method="ffill")
    for k, d in enumerate(days):
        # 1) marquage au marché
        realized = 0.0
        still = []
        for pos in open_pos:
            j = np.searchsorted(pos["dates"], d, side="right") - 1
            if j >= 0:
                pos["val"] = pos["risk_eur"] * pos["path"][j]
            if d >= pos["exit_time"]:
                realized += pos["risk_eur"] * pos["path"][-1]
                done.append(pos)
            else:
                still.append(pos)
        open_pos = still
        eq_closed = cfg.capital + sum(p["risk_eur"] * p["path"][-1] for p in done)
        eq = eq_closed + sum(p.get("val", 0.0) for p in open_pos)
        curve[k] = eq
        peak = max(peak, eq)
        # 2) coupe-circuit
        if k and d.weekday() < days[k - 1].weekday():
            week_start_eq = curve[k - 1]
        day_ret = eq / (curve[k - 1] if k else cfg.capital) - 1
        if halted is None and eq / peak - 1 <= -cfg.max_dd_stop:
            halted = d
        if day_ret <= -cfg.daily_stop:
            block_until = d + pd.Timedelta(days=1)
        if eq / week_start_eq - 1 <= -cfg.weekly_stop:
            block_until = d + pd.Timedelta(days=7 - d.weekday())
        # 3) nouveaux signaux
        if d not in by_day:
            continue
        g = by_day[d]
        if halted is not None or (block_until is not None and d < block_until):
            skipped["coupe_circuit"] += len(g)
            continue
        if cfg.score_col:
            g = g.sort_values(cfg.score_col, ascending=False)
        else:
            g = g.sample(frac=1.0, random_state=int(d.value % 2**31))   # ordre neutre
        for ti, t in g.iterrows():
            inst = UNIVERSE[t.sym]
            if any(p["sym"] == t.sym and (cfg.one_per_symbol or p["strategy"] == t.strategy) for p in open_pos):
                skipped["doublon"] += 1
                continue
            mult = 1.0
            if cfg.score_col:
                s = t[cfg.score_col]
                mult = float(np.clip(1.0 + 2.0 * s, cfg.min_risk_mult, cfg.max_risk_mult)) if np.isfinite(s) else 1.0
            want = eq * cfg.risk_per_trade * mult
            eur_per_unit = conv_f.at[d, inst.quote]
            loss_per_lot = t.stop_dist * inst.contract * eur_per_unit
            lots = want / loss_per_lot
            if cfg.lot_rounding:
                lots = np.floor(lots / LOT_STEP) * LOT_STEP
                if lots < MIN_LOT:
                    if MIN_LOT * loss_per_lot <= cfg.max_round_up * want:
                        lots = MIN_LOT
                    else:
                        skipped["lot_min"] += 1
                        continue
            risk_eur = lots * loss_per_lot
            # limites de portefeuille
            open_risk = sum(p["risk_eur"] for p in open_pos)
            exp = {}
            for p in open_pos:
                for c_, sgn in zip(p["ccys"], (1, -1)):
                    exp[c_] = exp.get(c_, 0.0) + sgn * p["dir"] * p["units"]
            ccys = currencies_of(inst)
            units = mult
            ok_ccy = all(abs(exp.get(c_, 0.0) + sgn * t.dir * units) <= cfg.max_ccy_units + 1e-9
                         for c_, sgn in zip(ccys, (1, -1)))
            if (len(open_pos) >= cfg.max_positions or open_risk + risk_eur > cfg.max_open_risk * eq or not ok_ccy):
                skipped["limites"] += 1
                continue
            notional_eur = lots * inst.contract * t.entry * eur_per_unit
            margin = notional_eur / inst.leverage
            used = sum(p["margin"] for p in open_pos)
            if used + margin > cfg.max_margin_use * eq:
                skipped["marge"] += 1
                continue
            dts = data[t.sym].index[int(t.entry_i): int(t.entry_i) + len(paths[ti])]
            open_pos.append(dict(idx=ti, sym=t.sym, strategy=t.strategy, dir=t.dir, ccys=ccys, units=units, risk_eur=risk_eur,
                                 lots=lots, margin=margin, path=paths[ti], dates=dts.to_numpy(),
                                 exit_time=t.exit_time, val=0.0, mult=mult))
    eqs = pd.Series(curve, index=days)
    tk = [dict(idx=p["idx"], lots=p["lots"], risk_eur=p["risk_eur"], pnl_eur=p["risk_eur"] * p["path"][-1],
               mult=p["mult"]) for p in done + open_pos]
    tdf = pd.DataFrame(tk).set_index("idx") if tk else pd.DataFrame(columns=["lots", "risk_eur", "pnl_eur", "mult"])
    tdf = trades.loc[tdf.index].join(tdf) if len(tdf) else tdf
    return PortfolioResult(eqs, tdf, skipped, halted)


def metrics(eq: pd.Series, trades: pd.DataFrame | None = None) -> dict:
    r = eq.pct_change().dropna()
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    dd = eq / eq.cummax() - 1
    out = dict(rendement_total=eq.iloc[-1] / eq.iloc[0] - 1,
               cagr=(eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1.0,
               sharpe=float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0,
               max_dd=float(dd.min()))
    if trades is not None and len(trades):
        p = trades["pnl_eur"]
        out.update(n_trades=len(p), win=float((p > 0).mean()),
                   pf=float(p[p > 0].sum() / -p[p < 0].sum()) if (p < 0).any() else np.inf,
                   avg_r=float(trades["r_net"].mean()))
    return out
