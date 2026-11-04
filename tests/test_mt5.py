"""Tests du connecteur MT5 avec un faux module MetaTrader5 (le vrai ne tourne que sous Windows)."""
import sys
import types
from types import SimpleNamespace as NS

import pytest

from swingbot.ctrader import CloseIntent, OrderIntent


def fake_mt5(demo=True, positions=()):
    m = types.ModuleType("MetaTrader5")
    m.ACCOUNT_TRADE_MODE_DEMO, m.ACCOUNT_TRADE_MODE_REAL = 0, 2
    m.POSITION_TYPE_BUY, m.POSITION_TYPE_SELL = 0, 1
    m.ORDER_TYPE_BUY, m.ORDER_TYPE_SELL = 0, 1
    m.TRADE_ACTION_DEAL, m.TRADE_RETCODE_DONE = 1, 10009
    m.ORDER_FILLING_FOK, m.ORDER_FILLING_IOC, m.ORDER_FILLING_RETURN = 0, 1, 2
    m.ORDER_TIME_GTC, m.TIMEFRAME_D1 = 0, 16408
    m.sent = []
    m.initialize = lambda **kw: True
    m.shutdown = lambda: None
    m.last_error = lambda: (0, "")
    m.account_info = lambda: NS(trade_mode=0 if demo else 2, equity=10000.0, currency="EUR")
    m.positions_get = lambda **kw: [p for p in positions if not kw or p.ticket == kw.get("ticket")]
    m.symbol_select = lambda n, b: n in ("EURUSD", "USTEC")
    m.symbol_info_exists = {"EURUSD", "USTEC"}
    m.symbol_info = lambda n: (NS(volume_step=0.01, volume_min=0.01, trade_stops_level=10, point=1e-5, digits=5,
                                  filling_mode=1) if n in m.symbol_info_exists else None)
    m.symbols_get = lambda: [NS(name=n, expiration_time=0, description="") for n in m.symbol_info_exists]
    m.symbol_info_tick = lambda n: NS(bid=1.1000, ask=1.1001)
    m.order_check = lambda req: NS(retcode=0, comment="")
    def send(req):
        m.sent.append(req)
        return NS(retcode=10009, order=42)
    m.order_send = send
    return m


def test_demo_mode_refuses_real_account(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5(demo=False))
    from swingbot.mt5_broker import MT5Broker
    with pytest.raises(PermissionError):
        MT5Broker("demo").snapshot()


def test_order_sizing_stop_and_magic(monkeypatch):
    m = fake_mt5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    from swingbot.mt5_broker import MAGIC, MT5Broker
    res = MT5Broker("demo").execute([OrderIntent("EURUSD", 1, 0.237, 0.0050, "v2 carry_safe"),
                                     OrderIntent("NAS100", -1, 0.004, 300.0, "x"),
                                     OrderIntent("FOO", 1, 1, 1, "x")], [])
    assert res[0]["ok"] and m.sent[0]["volume"] == 0.23 and m.sent[0]["magic"] == MAGIC
    assert abs(m.sent[0]["sl"] - (1.1001 - 0.0050)) < 1e-9          # stop sous le prix d'achat
    assert "minimum" in res[1]["statut"] and "introuvable" in res[2]["statut"]


def test_close_only_own_positions(monkeypatch):
    from swingbot.mt5_broker import MAGIC
    mine = NS(ticket=1, magic=MAGIC, symbol="EURUSD", type=0, volume=0.1, price_open=1.1, sl=1.09, time=0)
    manual = NS(ticket=2, magic=0, symbol="EURUSD", type=0, volume=1.0, price_open=1.1, sl=0, time=0)
    m = fake_mt5(positions=(mine, manual))
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    from swingbot.mt5_broker import MT5Broker
    snap = MT5Broker("demo").snapshot()
    assert [p["position_id"] for p in snap["positions"]] == [1]       # le trade manuel est ignoré
    MT5Broker("demo").execute([], [CloseIntent("EURUSD", 2, "test"), CloseIntent("EURUSD", 1, "test")])
    assert len(m.sent) == 1 and m.sent[0]["position"] == 1 and m.sent[0]["type"] == 1


def test_make_broker_selects_mt5(monkeypatch):
    monkeypatch.setenv("SWINGBOT_MODE", "demo")
    monkeypatch.setenv("SWINGBOT_BROKER", "mt5")
    from swingbot.ctrader import make_broker
    from swingbot.mt5_broker import MT5Broker
    assert isinstance(make_broker(), MT5Broker)


def test_futures_sizing_from_tick_value_and_stop_pct(monkeypatch):
    m = fake_mt5()
    m.symbol_select = lambda n, b: True
    m.symbol_info = lambda n: NS(volume_step=0.01, volume_min=0.01, trade_stops_level=0, point=0.01, digits=2,
                                 filling_mode=2, trade_tick_size=0.01, trade_tick_value=0.92)   # 0,92 € par tick
    m.symbol_info_tick = lambda n: NS(bid=70.00, ask=70.04)
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    from swingbot.mt5_broker import MT5Broker
    res = MT5Broker("demo").execute([OrderIntent("WTIF", 1, 0.0, 0.5, "v4", stop_pct=0.05, risk_eur=25.0)], [])
    # stop = 5 % de 70,04 = 3,502 $ -> 350,2 ticks x 0,92 € = 322,2 € par lot -> 25 € = 0,07 lot
    assert res[0]["ok"] and m.sent[0]["volume"] == 0.07 and m.sent[0]["symbol"] == "WTI"
    assert abs(m.sent[0]["sl"] - round(70.04 - 3.502, 2)) < 1e-9


def test_close_reason_detects_expiry_vs_stop(monkeypatch):
    m = fake_mt5()
    m.DEAL_REASON_SL, m.DEAL_REASON_CLIENT = 4, 0
    m.history_deals_get = lambda position: [NS(entry=0, reason=3), NS(entry=1, reason=4 if position == 1 else 5)]
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    from swingbot.mt5_broker import MT5Broker
    assert MT5Broker("demo").close_reason(1) == "stop"
    assert MT5Broker("demo").close_reason(2) == "autre"



def test_auto_connect_picks_logged_in_terminal(monkeypatch):
    m = fake_mt5()
    state = {}
    def init(**kw):
        state["path"] = kw.get("path")
        return True
    m.initialize = init
    m.account_info = lambda: (NS(trade_mode=0, equity=5000.0, currency="EUR", login=123, server="ICMarketsEU-Demo")
                              if state.get("path") == "C:/IC/terminal64.exe" else None)
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    monkeypatch.delenv("MT5_PATH", raising=False)
    import swingbot.mt5_broker as mb
    monkeypatch.setattr(mb, "running_terminals", lambda: ["C:/MetaQuotes/terminal64.exe", "C:/IC/terminal64.exe"])
    snap = mb.MT5Broker("demo").snapshot()
    assert snap["login"] == 123 and state["path"] == "C:/IC/terminal64.exe"


def test_auth_failure_message_is_explicit(monkeypatch):
    m = fake_mt5()
    m.initialize = lambda **kw: False
    m.last_error = lambda: (-6, "Terminal: Authorization failed")
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    import swingbot.mt5_broker as mb
    monkeypatch.setattr(mb, "running_terminals", lambda: [])
    with pytest.raises(RuntimeError, match="aucun compte"):
        mb.MT5Broker("demo").snapshot()


def test_resolve_future_with_expiry_suffix():
    import time
    from swingbot.mt5_broker import resolve_symbol
    now = time.time()
    m = NS(symbol_info=lambda n: None, symbol_select=lambda n, b: True,
           symbols_get=lambda: [NS(name="WTI_X6", expiration_time=now + 86400),        # expire demain : roulé
                                NS(name="WTI_Z6", expiration_time=now + 30 * 86400),
                                NS(name="WTI_F7", expiration_time=now + 60 * 86400),
                                NS(name="WTIUSD", expiration_time=0)])
    assert resolve_symbol(m, "WTI") == "WTI_Z6"
