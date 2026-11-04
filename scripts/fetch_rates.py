"""Télécharge les taux courts (FRED) pour modéliser le carry et les swaps. Sortie : data/rates.csv (quotidien, en %)."""
import io
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from swingbot.instruments import FRED_RATES  # noqa: E402


def main():
    cols = {}
    for ccy, sid in FRED_RATES.items():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
        raw = urllib.request.urlopen(url, timeout=60).read().decode()
        df = pd.read_csv(io.StringIO(raw), na_values=".")
        df.columns = ["date", ccy]
        df["date"] = pd.to_datetime(df["date"])
        cols[ccy] = df.set_index("date")[ccy]
        print(ccy, sid, df["date"].iloc[0].date(), "→", df["date"].iloc[-1].date())
    rates = pd.concat(cols, axis=1).sort_index()
    idx = pd.date_range("2005-01-01", pd.Timestamp.today().normalize(), freq="D")
    # Les séries mensuelles OCDE sont publiées avec retard : on décale d'un mois pour rester honnête
    monthly = [c for c, s in FRED_RATES.items() if s.endswith("M156N")]
    rates[monthly] = rates[monthly].shift(1)
    rates = rates.reindex(rates.index.union(idx)).ffill().reindex(idx)
    rates.index.name = "date"
    rates.to_csv(ROOT / "data" / "rates.csv")
    print(rates.tail(3))


if __name__ == "__main__":
    main()
