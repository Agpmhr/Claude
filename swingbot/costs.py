"""Modèle de coûts « sans indulgence » : spread variable, commission, swaps (triple), slippage, gaps.

Tous les coûts sont exprimés PAR UNITÉ d'instrument et dans la devise de cotation,
puis convertis en multiples de R (risque initial) dans le moteur.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .instruments import COMMISSION_PER_SIDE, Instrument


@dataclass
class CostModel:
    cost_mult: float = 1.0          # 1.5 pour le test de robustesse « coûts x1,5 »
    exec_spread_mult: float = 1.5   # exécution D1 : spread moyen officiel x1,5 (marge de prudence)
    night_spread_mult: float = 4.0  # spread H1 entre 21h et 23h UTC (rollover)
    asia_spread_mult: float = 1.5   # spread H1 entre 23h et 6h UTC
    event_spread_mult: float = 3.0  # jour/heure d'annonce majeure sur la devise
    slippage_spreads: float = 0.5   # glissement à l'entrée/sortie au marché, en fraction de spread
    stop_slippage_spreads: float = 1.5  # glissement supplémentaire sur un stop
    commission_side: float = COMMISSION_PER_SIDE
    swaps: bool = True
    swap_markup_mult: float = 1.0   # multiplie la marge de swap du broker (sensibilité)

    def spread(self, inst: Instrument, ts: pd.Timestamp | None = None, tf: str = "D1",
               event: bool = False, price: float | None = None) -> float:
        """Spread en unités de prix au moment d'une exécution."""
        s = inst.spread_pct * price if (inst.spread_pct and price) else inst.spread_pips * inst.pip
        if tf == "D1":
            m = self.exec_spread_mult
        else:
            h = ts.hour if ts is not None else 12
            m = self.night_spread_mult if 21 <= h < 23 else (self.asia_spread_mult if (h >= 23 or h < 6) else 1.0)
        if event:
            m *= self.event_spread_mult
        return s * m * self.cost_mult

    def commission(self, inst: Instrument, price: float) -> float:
        """Commission d'UN côté, par unité, en devise de cotation (≈ taux x prix)."""
        if not inst.commission:
            return 0.0
        return self.commission_side * price * self.cost_mult


def swap_rates(inst: Instrument, rates: pd.DataFrame | None, dates: pd.DatetimeIndex,
               markup: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Taux de swap annuels (long, short) par date. Négatif = on paie.

    FX     : long = (taux base - taux cotation) - marge ; short = (taux cotation - taux base) - marge
    Métaux : long = -(taux USD + marge)      ; short = taux USD - marge
    Indices: long = -(taux devise + marge)   ; short = taux devise - marge
    """
    mk = inst.swap_markup if markup is None else markup
    n = len(dates)
    if inst.kind == "future":
        lo, sh = inst.fixed_swap or (0.0, 0.0)
        r_usd = rates.reindex(dates, method="ffill")["USD"].fillna(0.0).to_numpy() if rates is not None else 0.0
        return lo - r_usd + 0 * np.ones(n), sh + r_usd + 0 * np.ones(n)
    if rates is None:
        return np.full(n, -mk), np.full(n, -mk)
    r = rates.reindex(dates, method="ffill").fillna(0.0)
    if inst.kind == "fx":
        diff = (r[inst.base] - r[inst.quote]).to_numpy()
        return diff - mk, -diff - mk
    rr = r[inst.rate_ccy].to_numpy()
    return -(rr + mk), rr - mk
