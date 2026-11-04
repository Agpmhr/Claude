"""Univers d'instruments et caractéristiques de trading chez IC Markets (EU, Raw Spread, cTrader).

TOUTES LES VALEURS DE COÛT SONT DES HYPOTHÈSES PRUDENTES, À VÉRIFIER chez IC Markets :
- spread moyen « Raw » en pips (hors nuit / annonces, voir costs.py pour les multiplicateurs)
- commission cTrader : 3 USD par 100 000 USD de notionnel et par côté (= 6 USD aller-retour / lot standard FX)
- marge de swap (« markup ») : ce que le broker prélève en plus du différentiel de taux, en % annuel
- levier ESMA : 30 majeures, 20 autres paires / or / grands indices, 10 argent
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    sym: str
    yahoo: str
    kind: str            # "fx", "metal", "index"
    base: str            # devise de base (ou "XAU", "IDX")
    quote: str           # devise de cotation (devise du P&L)
    pip: float           # taille d'un pip / d'un point
    contract: float      # unités par lot
    spread_pips: float   # spread moyen en heures normales (pips/points)
    leverage: float      # levier max ESMA
    commission: bool = True   # commission cTrader au notionnel (FX, métaux)
    swap_markup: float = 0.015   # % annuel retenu par le broker de chaque côté
    triple_swap_day: int = 2     # 2 = mercredi (FX/métaux), 4 = vendredi (indices)
    rate_ccy: str = ""           # devise du taux de financement (indices, métaux)


UNIVERSE: dict[str, Instrument] = {i.sym: i for i in [
    Instrument("EURUSD", "EURUSD=X", "fx", "EUR", "USD", 1e-4, 1e5, 0.2, 30),
    Instrument("GBPUSD", "GBPUSD=X", "fx", "GBP", "USD", 1e-4, 1e5, 0.4, 30),
    Instrument("USDJPY", "USDJPY=X", "fx", "USD", "JPY", 1e-2, 1e5, 0.3, 30),
    Instrument("USDCHF", "USDCHF=X", "fx", "USD", "CHF", 1e-4, 1e5, 0.4, 30),
    Instrument("AUDUSD", "AUDUSD=X", "fx", "AUD", "USD", 1e-4, 1e5, 0.3, 20),
    Instrument("USDCAD", "USDCAD=X", "fx", "USD", "CAD", 1e-4, 1e5, 0.5, 30),
    Instrument("NZDUSD", "NZDUSD=X", "fx", "NZD", "USD", 1e-4, 1e5, 0.6, 20),
    Instrument("EURJPY", "EURJPY=X", "fx", "EUR", "JPY", 1e-2, 1e5, 0.6, 30),
    Instrument("GBPJPY", "GBPJPY=X", "fx", "GBP", "JPY", 1e-2, 1e5, 1.2, 30),
    Instrument("AUDJPY", "AUDJPY=X", "fx", "AUD", "JPY", 1e-2, 1e5, 0.8, 20),
    Instrument("EURGBP", "EURGBP=X", "fx", "EUR", "GBP", 1e-4, 1e5, 0.6, 30),
    # Métaux : contrat 100 oz (or), 5000 oz (argent). Yahoo = contrats à terme continus (GC=F, SI=F)
    Instrument("XAUUSD", "GC=F", "metal", "XAU", "USD", 1e-2, 100, 15, 20,
               swap_markup=0.025, rate_ccy="USD"),
    Instrument("XAGUSD", "SI=F", "metal", "XAG", "USD", 1e-3, 5000, 25, 10,
               swap_markup=0.025, rate_ccy="USD"),
    # Indices CFD : 1 unité par lot = 1 devise par point, pas de commission, swap triple le vendredi
    Instrument("US500", "^GSPC", "index", "IDX", "USD", 1.0, 1, 0.5, 20, commission=False,
               swap_markup=0.025, triple_swap_day=4, rate_ccy="USD"),
    Instrument("NAS100", "^NDX", "index", "IDX", "USD", 1.0, 1, 1.5, 20, commission=False,
               swap_markup=0.025, triple_swap_day=4, rate_ccy="USD"),
    Instrument("GER40", "^GDAXI", "index", "IDX", "EUR", 1.0, 1, 1.5, 20, commission=False,
               swap_markup=0.025, triple_swap_day=4, rate_ccy="EUR"),
]}

COMMISSION_PER_SIDE = 3e-5   # 3 USD par 100k USD de notionnel, par côté (cTrader Raw)
MIN_LOT = 0.01
LOT_STEP = 0.01

# Séries FRED des taux courts (pour le carry / les swaps). Mensuelles OCDE sauf USD/EUR quotidiennes.
FRED_RATES = {
    "USD": "DFF",
    "EUR": "ECBDFR",
    "GBP": "IRSTCI01GBM156N",
    "JPY": "IRSTCI01JPM156N",
    "CHF": "IRSTCI01CHM156N",
    "AUD": "IRSTCI01AUM156N",
    "CAD": "IRSTCI01CAM156N",
    "NZD": "IRSTCI01NZM156N",
}


def currencies_of(inst: Instrument) -> tuple[str, str]:
    """Devises d'exposition, pour les limites par devise (l'or compte comme 'XAU', les indices comme leur devise + 'EQ')."""
    if inst.kind == "fx":
        return inst.base, inst.quote
    if inst.kind == "metal":
        return inst.base, inst.quote
    return "EQ", inst.quote
