"""Calendrier des annonces majeures (historique, pour le filtre testable en backtest).

Sources :
  - FOMC (Fed) : dates officielles extraites des pages de la Réserve fédérale (2010 -> aujourd'hui).
  - NFP (emploi US) : reconstruit par la règle du BLS (3e vendredi après la semaine contenant le 12 du mois),
    décalé d'une semaine s'il tombe un 1er janvier / 4 juillet. Approximation : quelques dates peuvent différer.
  - BCE, BoE, BoJ, CPI US : AUCUN historique accessible depuis l'environnement cloud (sites dynamiques / 403).
    -> non inclus dans le backtest ; le module news en direct (news.py) les couvre via le calendrier de la semaine.
Sortie : data/events.csv (date, heure_utc, devise, evenement)
"""
from __future__ import annotations

import re
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"], 1)}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")


def fomc_dates(start=2010, end=None) -> list[date]:
    """Jour de la décision FOMC (dernier jour de la réunion)."""
    end = end or date.today().year
    out = set()
    pat = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)"
                     r"(?:/(January|February|March|April|May|June|July|August|September|October|November|December))?"
                     r"\s+(\d{1,2})(?:-(\d{1,2}))?\s*(?:\(unscheduled\)\s*)?Meeting")
    for y in range(start, end + 1):
        try:
            html = _get(f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm")
        except Exception:
            continue
        for m1, m2, d1, d2 in pat.findall(html):
            mon = MONTHS[m2 or m1]
            out.add(date(y, mon, int(d2 or d1)))
    # années récentes : page du calendrier courant (structure différente : mois puis jours "27-28")
    try:
        html = _get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
        for block in re.split(r'<div class="panel panel-default">', html)[1:]:
            ym = re.search(r"(\d{4}) FOMC Meetings", block)
            if not ym:
                continue
            y = int(ym.group(1))
            for mon, days in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?'
                                        r'fomc-meeting__date[^>]*>([^<]+)<', block, flags=re.S):
                mon = mon.split("/")[-1]
                dd = re.findall(r"\d{1,2}", days)
                if mon in MONTHS and dd and "unscheduled" not in days.lower():
                    out.add(date(y, MONTHS[mon], int(dd[-1])))
    except Exception:
        pass
    return sorted(d for d in out if start <= d.year <= end)


def nfp_dates(start=2010, end=None) -> list[date]:
    end = end or date.today().year
    out = []
    for y in range(start - 1, end + 1):
        for m in range(1, 13):
            d12 = date(y, m, 12)
            sat = d12 + timedelta(days=(5 - d12.weekday()) % 7)   # samedi de la semaine (dim-sam) du 12
            fri = sat + timedelta(days=6) + timedelta(days=14)    # 3e vendredi après ce samedi
            if (fri.month, fri.day) in ((1, 1), (7, 4)):
                fri += timedelta(days=7)
            if start <= fri.year <= end:
                out.append(fri)
    return out


def build_events(path: Path | None = None) -> pd.DataFrame:
    rows = [(d, "18:00", "USD", "FOMC") for d in fomc_dates()]
    rows += [(d, "12:30", "USD", "NFP") for d in nfp_dates()]
    ev = pd.DataFrame(rows, columns=["date", "heure_utc", "devise", "evenement"]).sort_values("date")
    path = path or ROOT / "data" / "events.csv"
    ev.to_csv(path, index=False)
    return ev


def load_events() -> pd.DataFrame:
    p = ROOT / "data" / "events.csv"
    ev = build_events(p) if not p.exists() else pd.read_csv(p)
    ev["date"] = pd.to_datetime(ev["date"])
    return ev


def event_flags(ev: pd.DataFrame, index: pd.DatetimeIndex, ccys: tuple[str, ...], days_before: int = 0,
                which: tuple[str, ...] | None = None) -> pd.Series:
    """True pour les bougies D1 qui tombent le jour d'une annonce (ou dans les `days_before` jours ouvrés avant)
    concernant l'une des devises. Les dates d'annonces sont connues à l'avance : pas de fuite du futur."""
    e = ev[ev["devise"].isin(ccys)]
    if which:
        e = e[e["evenement"].isin(which)]
    days = pd.DatetimeIndex(e["date"]).normalize()
    flag = pd.Series(index.normalize().isin(days), index=index)
    for k in range(1, days_before + 1):
        flag |= pd.Series((index.normalize() + pd.offsets.BDay(k)).isin(days), index=index)
    return flag


def instrument_ccys(sym: str) -> tuple[str, ...]:
    from .instruments import UNIVERSE
    i = UNIVERSE[sym]
    if i.kind == "index":
        return (i.quote,)
    return (i.base, i.quote)


if __name__ == "__main__":
    ev = build_events()
    print(ev.groupby("evenement").size())
    print(ev.tail(8))
