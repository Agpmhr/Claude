"""Univers d'instruments et caractéristiques de trading chez IC Markets (EU, Raw Spread, cTrader).

COÛTS RELEVÉS LE 4 NOV. 2026 sur icmarkets.eu/en/trading-pricing/spreads-and-swaps (Raw Spread) :
- spreads moyens officiels (EURUSD 0,06 arrondi à 0,1 ; NZDUSD affiché 2,36 -> 1,0 retenu, à vérifier)
- marges de swap déduites des swaps publiés (long/short) et des taux directeurs :
  FX ≈ 1 %/an de chaque côté, or ≈ 0,75 %, argent ≈ 0,85 %, INDICES ≈ 5,5 %/an (très cher !)
  ex. US500 : long -2,045 pts/nuit (≈ -10 %/an du notionnel), short -0,24 pt/nuit
Valeurs historiques avant 2026 : inconnues, on applique ces marges à toute la période.
Ancien commentaire :
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
    swap_markup: float = 0.010   # % annuel retenu par le broker de chaque côté
    triple_swap_day: int = 2     # 2 = mercredi (FX/métaux), 4 = vendredi (indices)
    rate_ccy: str = ""           # devise du taux de financement (indices, métaux)
    spread_pct: float = 0.0      # spread en % du prix (futures CFD, données ETF de substitution)
    fixed_swap: tuple = ()       # (long, short) annuels fixes : futures CFD = pas de swap ; frais de l'ETF neutralisés


UNIVERSE: dict[str, Instrument] = {i.sym: i for i in [
    Instrument("EURUSD", "EURUSD=X", "fx", "EUR", "USD", 1e-4, 1e5, 0.1, 30),
    Instrument("GBPUSD", "GBPUSD=X", "fx", "GBP", "USD", 1e-4, 1e5, 0.26, 30),
    Instrument("USDJPY", "USDJPY=X", "fx", "USD", "JPY", 1e-2, 1e5, 0.22, 30),
    Instrument("USDCHF", "USDCHF=X", "fx", "USD", "CHF", 1e-4, 1e5, 0.34, 30),
    Instrument("AUDUSD", "AUDUSD=X", "fx", "AUD", "USD", 1e-4, 1e5, 0.10, 20),
    Instrument("USDCAD", "USDCAD=X", "fx", "USD", "CAD", 1e-4, 1e5, 0.23, 30),
    Instrument("NZDUSD", "NZDUSD=X", "fx", "NZD", "USD", 1e-4, 1e5, 1.0, 20),
    Instrument("EURJPY", "EURJPY=X", "fx", "EUR", "JPY", 1e-2, 1e5, 0.31, 30),
    Instrument("GBPJPY", "GBPJPY=X", "fx", "GBP", "JPY", 1e-2, 1e5, 0.65, 30),
    Instrument("AUDJPY", "AUDJPY=X", "fx", "AUD", "JPY", 1e-2, 1e5, 0.40, 20),
    Instrument("EURGBP", "EURGBP=X", "fx", "EUR", "GBP", 1e-4, 1e5, 0.25, 30),
    # Métaux : contrat 100 oz (or), 5000 oz (argent). Yahoo = contrats à terme continus (GC=F, SI=F)
    Instrument("XAUUSD", "GC=F", "metal", "XAU", "USD", 1e-2, 100, 8, 20,
               swap_markup=0.008, rate_ccy="USD"),
    Instrument("XAGUSD", "SI=F", "metal", "XAG", "USD", 1e-3, 5000, 10, 10,
               swap_markup=0.009, rate_ccy="USD"),
    # Indices CFD : 1 unité par lot = 1 devise par point, pas de commission, swap triple le vendredi
    Instrument("US500", "^GSPC", "index", "IDX", "USD", 1.0, 1, 0.36, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="USD"),
    Instrument("NAS100", "^NDX", "index", "IDX", "USD", 1.0, 1, 1.57, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="USD"),
    Instrument("GER40", "^GDAXI", "index", "IDX", "EUR", 1.0, 1, 1.46, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="EUR"),
]}

# Univers RÉSERVÉ (« holdout ») : instruments jamais regardés pendant la recherche.
# Ils ne servent qu'au verdict final des nouvelles idées (coûts officiels IC Markets du 4 nov. 2026).
RESEARCH = list(UNIVERSE)
HOLDOUT_LIST = [
    Instrument("EURCHF", "EURCHF=X", "fx", "EUR", "CHF", 1e-4, 1e5, 0.42, 30),
    Instrument("EURAUD", "EURAUD=X", "fx", "EUR", "AUD", 1e-4, 1e5, 0.68, 20),
    Instrument("GBPCHF", "GBPCHF=X", "fx", "GBP", "CHF", 1e-4, 1e5, 0.73, 30),
    Instrument("CADJPY", "CADJPY=X", "fx", "CAD", "JPY", 1e-2, 1e5, 0.35, 30),
    Instrument("NZDJPY", "NZDJPY=X", "fx", "NZD", "JPY", 1e-2, 1e5, 0.58, 20),
    Instrument("AUDNZD", "AUDNZD=X", "fx", "AUD", "NZD", 1e-4, 1e5, 0.57, 20),
    Instrument("EURCAD", "EURCAD=X", "fx", "EUR", "CAD", 1e-4, 1e5, 0.49, 30),
    Instrument("GBPAUD", "GBPAUD=X", "fx", "GBP", "AUD", 1e-4, 1e5, 0.80, 20),
    Instrument("CHFJPY", "CHFJPY=X", "fx", "CHF", "JPY", 1e-2, 1e5, 0.56, 30),
    Instrument("AUDCAD", "AUDCAD=X", "fx", "AUD", "CAD", 1e-4, 1e5, 0.45, 20),
    Instrument("UK100", "^FTSE", "index", "IDX", "GBP", 1.0, 1, 1.50, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="GBP"),
    Instrument("JP225", "^N225", "index", "IDX", "JPY", 1.0, 1, 7.63, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="JPY"),
    Instrument("F40", "^FCHI", "index", "IDX", "EUR", 1.0, 1, 0.82, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="EUR"),
    Instrument("STOXX50", "^STOXX50E", "index", "IDX", "EUR", 1.0, 1, 1.60, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="EUR"),
    Instrument("US30", "^DJI", "index", "IDX", "USD", 1.0, 1, 1.09, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="USD"),
    Instrument("AUS200", "^AXJO", "index", "IDX", "AUD", 1.0, 1, 1.20, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="AUD"),
]
HOLDOUT = [i.sym for i in HOLDOUT_LIST]
UNIVERSE.update({i.sym: i for i in HOLDOUT_LIST})

# Univers RÉSERVÉ n°2 : jamais regardé avant la vague 4 (le n°1 a été consommé par le verdict v2).
HOLDOUT2_LIST = [
    Instrument("EURNZD", "EURNZD=X", "fx", "EUR", "NZD", 1e-4, 1e5, 1.11, 20),
    Instrument("GBPCAD", "GBPCAD=X", "fx", "GBP", "CAD", 1e-4, 1e5, 0.69, 30),
    Instrument("GBPNZD", "GBPNZD=X", "fx", "GBP", "NZD", 1e-4, 1e5, 1.60, 20),
    Instrument("NZDCAD", "NZDCAD=X", "fx", "NZD", "CAD", 1e-4, 1e5, 0.71, 20),
    Instrument("NZDCHF", "NZDCHF=X", "fx", "NZD", "CHF", 1e-4, 1e5, 0.38, 20),
    Instrument("CADCHF", "CADCHF=X", "fx", "CAD", "CHF", 1e-4, 1e5, 0.34, 30),
    Instrument("AUDCHF", "AUDCHF=X", "fx", "AUD", "CHF", 1e-4, 1e5, 0.35, 20),
    Instrument("HK50", "^HSI", "index", "IDX", "HKD", 1.0, 1, 7.94, 10, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="USD"),
    Instrument("ES35", "^IBEX", "index", "IDX", "EUR", 1.0, 1, 4.20, 10, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="EUR"),
    Instrument("NETH25", "^AEX", "index", "IDX", "EUR", 0.01, 1, 20, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="EUR"),
    Instrument("SWI20", "^SSMI", "index", "IDX", "CHF", 1.0, 1, 3.40, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="CHF"),
    Instrument("CA60", "^GSPTSE", "index", "IDX", "CAD", 1.0, 1, 0.60, 10, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="CAD"),
    Instrument("US2000", "^RUT", "index", "IDX", "USD", 1.0, 1, 0.40, 10, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="USD"),
    Instrument("IT40", "FTSEMIB.MI", "index", "IDX", "EUR", 1.0, 1, 9.00, 20, commission=False,
               swap_markup=0.055, triple_swap_day=4, rate_ccy="EUR"),
]
HOLDOUT2 = [i.sym for i in HOLDOUT2_LIST]
UNIVERSE.update({i.sym: i for i in HOLDOUT2_LIST})
# Futures CFD d'IC Markets (MT4/MT5 uniquement) : SANS swap, coût de portage intégré au prix du futur.
# Historique : ETF qui détiennent ces futures (VIXY, USO, BNO, UUP) ou l'or physique (GLD).
# Correction : prix ETF = futures + intérêts de la trésorerie - frais de gestion (GLD : comptant - frais, alors
# qu'un future coûte le taux d'intérêt). Donc futures CFD = ETF - taux USD + frais : fixed_swap = (frais, -frais)
# et le taux USD est retiré à l'acheteur / rendu au vendeur dans costs.swap_rates.
FUTURES_LIST = [
    Instrument("VIXF", "VIXY", "future", "VOL", "USD", 0.01, 1, 0, 10, commission=False,
               spread_pct=0.07 / 17, fixed_swap=(0.0085, -0.0085)),
    Instrument("WTIF", "USO", "future", "OIL", "USD", 0.01, 1, 0, 10, commission=False,
               spread_pct=0.04 / 65, fixed_swap=(0.008, -0.008)),
    Instrument("BRENTF", "BNO", "future", "OIL", "USD", 0.01, 1, 0, 10, commission=False,
               spread_pct=0.03 / 70, fixed_swap=(0.010, -0.010)),
    Instrument("DXYF", "UUP", "future", "DXY", "USD", 0.01, 1, 0, 10, commission=False,
               spread_pct=0.03 / 100, fixed_swap=(0.0078, -0.0078)),
    Instrument("GOLDF", "GLD", "future", "XAU", "USD", 0.01, 1, 0, 10, commission=False,
               spread_pct=0.23 / 2000, fixed_swap=(0.004, -0.004)),
]
# Matières premières agricoles (futures CFD IC Markets, sans swap). ETF Teucrium (cash rémunéré, frais ~1 %).
# Réservées : jamais regardées avant le test « v4 inchangée » (scripts/agri_verdict.py).
AGRI_LIST = [
    Instrument("CORNF", "CORN", "future", "AGRI", "USD", 0.01, 1, 0, 10, commission=False,
               spread_pct=0.88 / 430, fixed_swap=(0.010, -0.010)),
    Instrument("WHEATF", "WEAT", "future", "AGRI", "USD", 0.01, 1, 0, 10, commission=False,
               spread_pct=1.15 / 540, fixed_swap=(0.010, -0.010)),
    Instrument("SOYF", "SOYB", "future", "AGRI", "USD", 0.01, 1, 0, 10, commission=False,
               spread_pct=1.85 / 1050, fixed_swap=(0.010, -0.010)),
    Instrument("SUGARF", "CANE", "future", "AGRI", "USD", 0.01, 1, 0, 10, commission=False,
               spread_pct=0.05 / 18, fixed_swap=(0.010, -0.010)),
]
AGRI = [i.sym for i in AGRI_LIST]
UNIVERSE.update({i.sym: i for i in AGRI_LIST})
FUTURES = [i.sym for i in FUTURES_LIST]
UNIVERSE.update({i.sym: i for i in FUTURES_LIST})
POOL = RESEARCH + HOLDOUT   # pool de recherche à partir de la vague 4 (32 instruments)
INDICES = [s for s, i in UNIVERSE.items() if i.kind == "index"]

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
    if inst.kind in ("metal", "future"):
        return inst.base, inst.quote
    return "EQ", inst.quote
