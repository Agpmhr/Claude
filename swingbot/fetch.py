"""Téléchargement des données dans le programme (utilisable depuis Swingbot.exe, sans lancer de script)."""
from __future__ import annotations

import io
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from .instruments import FRED_RATES, UNIVERSE
from .paths import ROOT


def yahoo(ticker: str, interval: str, start: int, end: int) -> pd.DataFrame:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}"
           f"?interval={interval}&period1={start}&period2={end}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(4):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=60))["chart"]["result"][0]
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    q = d["indicators"]["quote"][0]
    df = pd.DataFrame({"time": pd.to_datetime(d["timestamp"], unit="s", utc=True), "open": q["open"],
                       "high": q["high"], "low": q["low"], "close": q["close"], "volume": q.get("volume")})
    return df.dropna(subset=["open", "high", "low", "close"])


def fetch_prices(syms: list[str], start: str = "2010-01-01", extra=("VIX",), log=print) -> None:
    out = ROOT / "data" / "yahoo"
    out.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    s0 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    tickers = {"VIX": "^VIX", "VIX3M": "^VIX3M"}
    for e in extra:
        yahoo(tickers[e], "1d", s0, now).to_csv(out / f"{e}_D1.csv", index=False)
    for s in syms:
        d1 = yahoo(UNIVERSE[s].yahoo, "1d", s0, now)
        d1.to_csv(out / f"{s}_D1.csv", index=False)
        log(f"{s:8s} D1 jusqu'au {d1.time.iloc[-1].date()}")
        time.sleep(0.3)


def fetch_rates(log=print) -> None:
    cols = {}
    for ccy, sid in FRED_RATES.items():
        raw = urllib.request.urlopen(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=60).read()
        df = pd.read_csv(io.StringIO(raw.decode()), na_values=".")
        df.columns = ["date", ccy]
        df["date"] = pd.to_datetime(df["date"])
        cols[ccy] = df.set_index("date")[ccy]
    rates = pd.concat(cols, axis=1).sort_index()
    idx = pd.date_range("2005-01-01", pd.Timestamp.today().normalize(), freq="D")
    monthly = [c for c, s in FRED_RATES.items() if s.endswith("M156N")]
    rates[monthly] = rates[monthly].shift(1)
    rates = rates.reindex(rates.index.union(idx)).ffill().reindex(idx)
    rates.index.name = "date"
    (ROOT / "data").mkdir(exist_ok=True)
    rates.to_csv(ROOT / "data" / "rates.csv")
    log("Taux directeurs mis à jour")
