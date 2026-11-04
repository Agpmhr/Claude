"""Le mode réel doit rester verrouillé sans réglage volontaire ; le module news ne peut jamais ouvrir de trade."""
import pytest

from swingbot.ctrader import LIVE_CONFIRM, PaperBroker, make_broker, trading_mode
from swingbot.news import ALLOWED


def test_default_is_paper(monkeypatch):
    monkeypatch.delenv("SWINGBOT_MODE", raising=False)
    assert trading_mode() == "paper"
    assert isinstance(make_broker(), PaperBroker)


def test_live_locked_without_confirmation(monkeypatch):
    monkeypatch.setenv("SWINGBOT_MODE", "live")
    monkeypatch.delenv("SWINGBOT_LIVE_CONFIRM", raising=False)
    with pytest.raises(PermissionError):
        trading_mode()
    monkeypatch.setenv("SWINGBOT_LIVE_CONFIRM", "oui")
    with pytest.raises(PermissionError):
        trading_mode()
    monkeypatch.setenv("SWINGBOT_LIVE_CONFIRM", LIVE_CONFIRM)
    assert trading_mode() == "live"


def test_news_cannot_open_trades():
    assert not any(a in ALLOWED for a in ("acheter", "vendre", "ouvrir", "buy", "sell"))
