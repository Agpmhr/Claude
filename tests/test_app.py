"""Réglages de l'application : le mode réel reste verrouillé et les réglages deviennent des variables du bot."""
import os

import pytest

from swingbot import app


def test_apply_env_and_real_lock(monkeypatch):
    s = dict(app.DEFAULTS, mode="demo", risque_pct=0.3)
    app.apply_env(s)
    assert os.environ["SWINGBOT_MODE"] == "demo" and os.environ["SWINGBOT_RISQUE_PCT"] == "0.3"
    assert "SWINGBOT_LIVE_CONFIRM" not in os.environ
    from swingbot.ctrader import trading_mode
    assert trading_mode() == "demo"
    app.apply_env(dict(app.DEFAULTS, mode="live", confirm_reel="non"))
    with pytest.raises(PermissionError):
        trading_mode()
    for k in ("SWINGBOT_MODE", "SWINGBOT_LIVE_CONFIRM"):
        monkeypatch.delenv(k, raising=False)


def test_next_run_skips_weekend():
    from datetime import datetime, timezone
    from swingbot.live import next_run_time
    assert next_run_time(datetime(2026, 11, 6, 23, 0, tzinfo=timezone.utc)).weekday() == 0
    assert next_run_time(datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)).day == 4
