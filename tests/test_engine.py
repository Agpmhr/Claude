"""Tests du moteur : pas de fuite du futur, coûts toujours payés, stops et gaps corrects."""
import numpy as np
import pandas as pd
import pytest

from swingbot.backtest import ExitRules, simulate
from swingbot.costs import CostModel
from swingbot.data import synthetic_universe
from swingbot import strategies as S


@pytest.fixture(scope="module")
def data():
    return synthetic_universe(["EURUSD", "USDJPY", "XAUUSD", "US500", "EURJPY", "EURGBP", "GBPUSD",
                               "USDCHF", "USDCAD", "AUDUSD"], start="2015-01-01", end="2019-12-31")


def _flat(n=60, price=1.0):
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame({"open": price, "high": price * 1.001, "low": price * 0.999, "close": price}, index=idx)


def test_no_lookahead_in_signals(data):
    """Modifier le futur ne doit RIEN changer aux signaux passés."""
    df = data["EURUSD"]
    cut = len(df) // 2
    fut = df.copy()
    rng = np.random.default_rng(0)
    fut.iloc[cut + 1:] *= rng.uniform(0.5, 1.5, size=(len(df) - cut - 1, 1))
    for name, fam in S.FAMILIES.items():
        if fam.panel:
            continue
        for params in fam.grid[:2]:
            a = fam.fn(df, **params)[0]
            b = fam.fn(fut, **params)[0]
            pd.testing.assert_series_equal(a.iloc[:cut + 1], b.iloc[:cut + 1], check_names=False,
                                           obj=f"{name} {params}")


def test_no_lookahead_in_panel_strategies(data):
    cut_date = pd.Timestamp("2017-06-30")
    fut = {k: v.copy() for k, v in data.items()}
    for v in fut.values():
        m = v.index > cut_date
        v.loc[m] *= 1.3
    for name, fam in S.FAMILIES.items():
        if not fam.panel:
            continue
        a = fam.fn_panel(data, **fam.grid[0])
        b = fam.fn_panel(fut, **fam.grid[0])
        for s in a:
            pd.testing.assert_series_equal(a[s][0].loc[:cut_date], b[s][0].loc[:cut_date], check_names=False)


def test_entry_is_next_open_and_costs_positive():
    df = _flat()
    df.iloc[30:, :] = 1.01                     # saut haussier
    ent = pd.Series(0, index=df.index); ent.iloc[22] = 1
    tr, paths = simulate("EURUSD", df, ent, rules=ExitRules(stop_atr=2, max_bars=40), cost=CostModel(swaps=False))
    t = tr.iloc[0]
    assert t.entry_time == df.index[23]          # exécution à l'ouverture suivante
    assert t.c_spread > 0 and t.c_comm > 0 and t.c_slip > 0
    assert t.r_net < t.r_gross
    assert len(paths[0]) == t.exit_i - t.entry_i + 1


def test_gap_through_stop_fills_at_open():
    df = _flat()
    ent = pd.Series(0, index=df.index); ent.iloc[25] = 1
    df.iloc[35:, :] = 0.95                       # gap baissier énorme
    tr, _ = simulate("EURUSD", df, ent, rules=ExitRules(stop_atr=2, max_bars=40), cost=CostModel(swaps=False))
    t = tr.iloc[0]
    assert t.reason == "gap"
    assert t.r_gross < -1.5                     # perte bien au-delà de 1R


def test_short_stop_and_zero_cost_symmetry():
    df = _flat()
    ent = pd.Series(0, index=df.index); ent.iloc[25] = -1
    df.iloc[35, df.columns.get_loc("high")] = 1.02   # mèche haute qui touche le stop du short
    zero = CostModel(cost_mult=0.0, swaps=False)
    tr, _ = simulate("EURUSD", df, ent, rules=ExitRules(stop_atr=2, max_bars=40), cost=zero)
    t = tr.iloc[0]
    assert t.reason == "stop"
    assert abs(t.r_gross + 1.0) < 1e-9 and abs(t.r_net - t.r_gross) < 1e-9


def test_triple_swap_wednesday():
    df = _flat(40)
    rates = pd.DataFrame({c: 0.0 for c in ["USD", "EUR"]}, index=pd.date_range("2019-01-01", "2021-01-01"))
    rates["EUR"] = -0.36       # base EUR à -36 %/an -> swap long très négatif (exagéré pour le test)
    ent = pd.Series(0, index=df.index); ent.iloc[25] = 1
    tr, _ = simulate("EURUSD", df, ent, rules=ExitRules(stop_atr=2, max_bars=5),
                     cost=CostModel(cost_mult=0.0), rates=rates)
    t = tr.iloc[0]
    # 5 nuits dont un mercredi -> 7 nuits facturées
    nights = sum(3 if d == 2 else 1 for d in df.index[t.entry_i:t.entry_i + 5].day_of_week)
    expected = 0.36 / 360 * nights * 1.0 / t.stop_dist   # cost_mult=0 annule aussi la marge du broker
    assert abs(t.c_swap - expected) / expected < 1e-6
