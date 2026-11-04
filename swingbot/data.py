"""Chargement des données + contrôle qualité + données synthétiques de secours.

Priorité des sources : data/mt5/ (export du broker) puis data/dukascopy/<SYM>_<TF>.csv puis data/yahoo/.
Toutes les dates sont en UTC. Les bougies D1 sont indexées par date (sans heure).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .instruments import RESEARCH, UNIVERSE

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


@dataclass
class QCReport:
    sym: str
    n_raw: int = 0
    duplicates: int = 0
    weekend: int = 0
    ohlc_fixed: int = 0
    spikes: int = 0
    gaps_over_4d: int = 0
    notes: list[str] = field(default_factory=list)

    def row(self):
        return dict(sym=self.sym, bougies=self.n_raw, doublons=self.duplicates, week_end=self.weekend,
                    ohlc_corrigees=self.ohlc_fixed, pics_aberrants=self.spikes, trous_4j=self.gaps_over_4d)


def _read(sym: str, tf: str) -> pd.DataFrame | None:
    for src in ("mt5", "dukascopy", "yahoo"):
        p = DATA / src / f"{sym}_{tf}.csv"
        if p.exists():
            df = pd.read_csv(p)
            df.columns = [c.lower() for c in df.columns]
            tcol = "time" if "time" in df.columns else df.columns[0]
            t = pd.to_datetime(df[tcol], utc=True) if not pd.api.types.is_numeric_dtype(df[tcol]) \
                else pd.to_datetime(df[tcol], unit="ms", utc=True)
            df = df.assign(time=t)[["time", "open", "high", "low", "close"]]
            df.attrs["source"] = src
            return df
    return None


def clean(df: pd.DataFrame, sym: str, tf: str) -> tuple[pd.DataFrame, QCReport]:
    """Contrôle qualité : doublons, week-ends, OHLC incohérents, pics aberrants, trous."""
    qc = QCReport(sym, n_raw=len(df))
    df = df.copy()
    if tf == "D1":
        df["time"] = df["time"].dt.tz_convert(None).dt.normalize()
    else:
        df["time"] = df["time"].dt.tz_convert(None)
    qc.duplicates = int(df.duplicated("time").sum())
    df = df.drop_duplicates("time", keep="last").sort_values("time")
    wk = df["time"].dt.dayofweek >= 5
    qc.weekend = int(wk.sum())
    df = df[~wk]
    # supprime la bougie du jour (incomplète)
    if tf == "D1" and len(df) and df["time"].iloc[-1] >= pd.Timestamp.utcnow().tz_convert(None).normalize():
        df = df.iloc[:-1]
    # pics aberrants : clôture à > 15 écarts robustes ET qui revient presque entièrement le lendemain.
    # Règle volontairement stricte : les vrais krachs (mars 2020, interventions sur le yen) doivent RESTER
    # dans les données, sinon le backtest serait trop optimiste.
    lr = np.log(df["close"]).diff()
    mad = lr.abs().rolling(100, min_periods=20).median() * 1.4826
    z = lr / mad
    spike = (z.abs() > 15) & ((lr + lr.shift(-1)).abs() < 0.2 * lr.abs())
    qc.spikes = int(spike.sum())
    if qc.spikes:
        df.loc[spike, ["open", "high", "low", "close"]] = np.nan
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].ffill()
    # mèches aberrantes (fréquent sur Yahoo FX) : mèche > 6x l'amplitude médiane -> rabotée
    body_hi = df[["open", "close"]].max(axis=1)
    body_lo = df[["open", "close"]].min(axis=1)
    rng = (df["high"] - df["low"]).rolling(50, min_periods=10).median().bfill()
    bad_hi = (df["high"] - body_hi) > 6 * rng
    bad_lo = (body_lo - df["low"]) > 6 * rng
    df.loc[bad_hi, "high"] = body_hi[bad_hi] + rng[bad_hi]
    df.loc[bad_lo, "low"] = body_lo[bad_lo] - rng[bad_lo]
    # cohérence OHLC
    hi = df[["open", "high", "low", "close"]].max(axis=1)
    lo = df[["open", "high", "low", "close"]].min(axis=1)
    fix = (df["high"] != hi) | (df["low"] != lo) | bad_hi | bad_lo
    qc.ohlc_fixed = int(fix.sum())
    df["high"], df["low"] = hi, lo
    gaps = df["time"].diff().dt.days
    qc.gaps_over_4d = int((gaps > 4).sum())
    return df.set_index("time"), qc


def load_universe(tf: str = "D1", syms: list[str] | None = None, start: str = "2010-01-01"):
    """Retourne (dict sym -> DataFrame OHLC, liste de QCReport). Utilise le synthétique si rien n'est trouvé."""
    syms = syms or list(RESEARCH)
    out, reps = {}, []
    for s in syms:
        raw = _read(s, tf)
        if raw is None:
            continue
        df, qc = clean(raw, s, tf)
        qc.notes.append(raw.attrs.get("source", "?"))
        out[s] = df[df.index >= start]
        reps.append(qc)
    if not out:
        out = synthetic_universe(syms, tf=tf, start=start)
        reps = [QCReport(s, notes=["synthétique"]) for s in out]
    return out, reps


def panel(data: dict[str, pd.DataFrame], field_: str = "close") -> pd.DataFrame:
    """Tableau date x instrument (valeurs alignées, sans remplissage vers l'avant)."""
    return pd.concat({s: d[field_] for s, d in data.items()}, axis=1).sort_index()


def synthetic_universe(syms, tf="D1", start="2010-01-01", end="2026-10-30", seed=7):
    """Données synthétiques réalistes : marche aléatoire à volatilité GARCH + facteur dollar commun + sauts.
    Sert uniquement à tester le code (aucune stratégie ne doit y gagner après coûts)."""
    rng = np.random.default_rng(seed)
    freq = "B" if tf == "D1" else "h"
    idx = pd.date_range(start, end, freq=freq)
    if tf != "D1":
        idx = idx[idx.dayofweek < 5]
    n = len(idx)
    usd = rng.standard_normal(n)
    out = {}
    for k, s in enumerate(syms):
        inst = UNIVERSE[s]
        base_vol = {"fx": 0.006, "metal": 0.011, "index": 0.011}[inst.kind] / (1 if tf == "D1" else 5)
        h = np.empty(n); r = np.empty(n); h[0] = base_vol ** 2
        e = 0.6 * rng.standard_t(5, n) / np.sqrt(5 / 3) + 0.4 * usd * (1 if inst.quote == "USD" else -1)
        for t in range(n):
            if t:
                h[t] = base_vol ** 2 * 0.05 + 0.9 * h[t - 1] + 0.05 * r[t - 1] ** 2
            r[t] = np.sqrt(h[t]) * e[t]
        p0 = {"fx": 1.2, "metal": 1500.0, "index": 3000.0}[inst.kind]
        if inst.quote == "JPY":
            p0 = 120.0
        c = p0 * np.exp(np.cumsum(r))
        o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, base_vol * 0.1, n))
        wick = np.abs(rng.normal(0, base_vol * 0.5, (2, n))) * c
        out[s] = pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick[0],
                               "low": np.minimum(o, c) - wick[1], "close": c}, index=idx)
    return out


def load_rates() -> pd.DataFrame:
    p = DATA / "rates.csv"
    if not p.exists():
        raise FileNotFoundError("data/rates.csv manquant : lancez python scripts/fetch_rates.py")
    return pd.read_csv(p, index_col=0, parse_dates=True) / 100.0


def eur_conversion(data: dict[str, pd.DataFrame], index: pd.DatetimeIndex) -> pd.DataFrame:
    """Combien d'EUR vaut 1 unité de chaque devise de cotation, par date (dernier cours connu)."""
    def c(sym):
        return data[sym]["close"].reindex(index).ffill().bfill()
    eurusd = c("EURUSD")
    conv = pd.DataFrame(index=index)
    conv["EUR"] = 1.0
    conv["USD"] = 1 / eurusd
    conv["JPY"] = 1 / c("EURJPY") if "EURJPY" in data else 1 / (eurusd * c("USDJPY"))
    conv["GBP"] = 1 / c("EURGBP") if "EURGBP" in data else c("GBPUSD") / eurusd
    conv["CHF"] = 1 / (eurusd * c("USDCHF"))
    conv["CAD"] = 1 / (eurusd * c("USDCAD"))
    conv["AUD"] = c("AUDUSD") / eurusd
    conv["NZD"] = c("NZDUSD") / eurusd
    conv["HKD"] = conv["USD"] / 7.8          # dollar de Hong Kong arrimé au dollar US
    return conv
