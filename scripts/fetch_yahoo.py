"""Télécharge l'historique gratuit Yahoo Finance (D1 depuis 2010, H1 sur ~2 ans).

Utilisé parce que Dukascopy bloque (HTTP 429) depuis l'environnement cloud.
Sur votre PC, préférez Dukascopy (voir scripts/fetch_dukascopy.py) : données plus propres.

Usage : python scripts/fetch_yahoo.py [--start 2010-01-01]
Sortie : data/yahoo/<SYMBOLE>_D1.csv et <SYMBOLE>_H1.csv (UTC, colonnes time,open,high,low,close,volume)
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT)
import sys
sys.path.insert(0, sys_path)
from swingbot.instruments import UNIVERSE  # noqa: E402


def fetch(ticker: str, interval: str, start: int, end: int) -> pd.DataFrame:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}"
           f"?interval={interval}&period1={start}&period2={end}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(4):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=60))["chart"]["result"][0]
            break
        except Exception as e:  # réseau capricieux : on réessaie
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    q = d["indicators"]["quote"][0]
    df = pd.DataFrame({
        "time": pd.to_datetime(d["timestamp"], unit="s", utc=True),
        "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
        "volume": q.get("volume"),
    })
    return df.dropna(subset=["open", "high", "low", "close"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2010-01-01")
    args = ap.parse_args()
    out = ROOT / "data" / "yahoo"
    out.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    s0 = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp())
    for sym, spec in UNIVERSE.items():
        d1 = fetch(spec.yahoo, "1d", s0, now)
        d1.to_csv(out / f"{sym}_D1.csv", index=False)
        # H1 : Yahoo limite à 730 jours glissants
        h1 = fetch(spec.yahoo, "1h", now - 729 * 86400, now)
        h1.to_csv(out / f"{sym}_H1.csv", index=False)
        print(f"{sym:8s} D1 {len(d1):5d} ({d1.time.iloc[0].date()} → {d1.time.iloc[-1].date()})  H1 {len(h1)}")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
