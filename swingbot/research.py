"""Génération des trades candidats pour toutes les configurations + découpage chronologique.

Les trois blocs :
  ENTRAÎNEMENT 2010-01-01 -> 2018-12-31
  VALIDATION   2019-01-01 -> 2024-10-31
  TEST FINAL   2024-11-01 -> fin  (intouché jusqu'au verdict, utilisé UNE seule fois)
Un trade appartient au bloc de sa date d'ENTRÉE ; pour l'apprentissage on écarte les trades dont la SORTIE
déborde sur le bloc suivant (« purge »), pour qu'aucune information du futur ne fuie.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import simulate
from .calendar_events import event_flags, instrument_ccys, load_events
from .costs import CostModel
from .strategies import FAMILIES, config_id

from .paths import ROOT  # noqa: E402
CACHE = ROOT / "results" / "cache"

TRAIN_END = pd.Timestamp("2018-12-31")
VAL_START = pd.Timestamp("2019-01-01")
TEST_START = pd.Timestamp("2024-11-01")


def block_of(t: pd.Series) -> pd.Series:
    return pd.Series(np.where(t < VAL_START, "train", np.where(t < TEST_START, "val", "test")), index=t.index)


def event_series(data, events=None):
    events = load_events() if events is None else events
    return {s: event_flags(events, df.index, instrument_ccys(s)) for s, df in data.items()}


def generate_all(data: dict, rates: pd.DataFrame | None, cost: CostModel = CostModel(), families=None,
                 events=None, param_scale: float = 1.0) -> tuple[pd.DataFrame, list]:
    """Toutes les configurations de toutes les familles sur tous les instruments.
    param_scale : multiplie les paramètres numériques de période (test de robustesse ±20 %)."""
    evs = event_series(data, events)
    rows, paths = [], []
    fams = families or list(FAMILIES)
    for fname in fams:
        fam = FAMILIES[fname]
        for params in fam.grid:
            cid = config_id(fname, params)
            p = _scale(params, param_scale)
            if fam.panel:
                kw = dict(p)
                if fname == "carry":
                    kw["rates"] = rates
                sigs = fam.fn_panel(data, **kw)
            else:
                from .strategies import RISK_PREMIUM
                pool = {s: df for s, df in data.items() if fname != "trend_long" or s in RISK_PREMIUM}
                sigs = {s: fam.fn(df, **p) for s, df in pool.items()}
            for s, (ent, xl, xs, rules) in sigs.items():
                tr, pth = simulate(s, data[s], ent, xl, xs, rules, cost, rates, evs[s], strategy=cid)
                if len(tr):
                    tr["family"] = fname
                    rows.append(tr)
                    paths.extend(pth)
    trades = pd.concat(rows, ignore_index=True)
    trades["block"] = block_of(trades["entry_time"])
    trades["exit_block"] = block_of(trades["exit_time"])
    return trades, paths


def _scale(params: dict, k: float) -> dict:
    if k == 1.0:
        return params
    out = {}
    for key, v in params.items():
        if key in ("n", "n_exit", "fast", "slow", "lookback", "trend_n", "hold") and isinstance(v, int):
            out[key] = max(2, int(round(v * k)))
        elif key in ("stop",):
            out[key] = v * k
        else:
            out[key] = v
    return out


def cached(name: str, fn, *a, **kw):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{name}.pkl"
    if p.exists():
        return pickle.loads(p.read_bytes())
    res = fn(*a, **kw)
    p.write_bytes(pickle.dumps(res))
    return res


def random_benchmark(data, rates, trades: pd.DataFrame, cost: CostModel = CostModel(), n_runs: int = 20,
                     seed: int = 0, events=None) -> pd.DataFrame:
    """Stratégie aléatoire : mêmes instruments, même nombre de trades, mêmes durées, même sens (tirés au hasard),
    mêmes stops et coûts. Renvoie l'espérance en R de chaque tirage."""
    from .backtest import ExitRules
    rng = np.random.default_rng(seed)
    evs = event_series(data, events)
    out = []
    for run in range(n_runs):
        rs = []
        for s, g in trades.groupby("sym"):
            df = data[s]
            n = len(df)
            lo = df.index.searchsorted(g.signal_time.min())
            hi = df.index.searchsorted(g.signal_time.max())
            si = np.sort(rng.integers(max(lo, 30), max(hi, lo + 31), len(g)))
            dirs = rng.choice([-1, 1], len(g))
            bars = np.maximum(1, g["bars"].to_numpy())
            stop = float(np.median(g["stop_dist"] / g["atr"]))
            tr, _ = simulate(s, df, None, rules=ExitRules(stop_atr=stop, max_bars=10 ** 6), cost=cost, rates=rates,
                             event_days=evs[s], forced_entries=si, forced_dirs=dirs,
                             forced_bars=rng.permutation(bars))
            rs.append(tr.r_net.to_numpy())
        r = np.concatenate(rs) if rs else np.array([])
        out.append(dict(run=run, n=len(r), avg_r=r.mean(), pf=r[r > 0].sum() / max(1e-9, -r[r < 0].sum())))
    return pd.DataFrame(out)
