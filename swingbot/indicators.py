"""Indicateurs techniques. Tous « causaux » : la valeur à la date t n'utilise que les bougies <= t (clôturées)."""
import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, n: int = 20) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def donchian(df: pd.DataFrame, n: int):
    """Plus haut / plus bas des n bougies PRÉCÉDENTES (exclut la bougie courante)."""
    return df["high"].rolling(n).max().shift(1), df["low"].rolling(n).min().shift(1)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def efficiency_ratio(close: pd.Series, n: int = 20) -> pd.Series:
    """Kaufman : 1 = tendance parfaite, 0 = bruit pur."""
    net = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n).sum()
    return net / path.replace(0, np.nan)


def realized_vol(close: pd.Series, n: int = 20) -> pd.Series:
    return np.log(close).diff().rolling(n).std() * np.sqrt(252)


def pct_rank(s: pd.Series, n: int = 252) -> pd.Series:
    """Rang centile de la valeur courante dans sa fenêtre glissante passée."""
    return s.rolling(n, min_periods=n // 2).rank(pct=True)
