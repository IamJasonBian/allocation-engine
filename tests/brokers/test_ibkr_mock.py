"""Tests for the in-memory IBKR-style mock client."""

from trading_system.brokers.ibkr_mock import MockIbkrClient


def test_connect_disconnect_and_account_tags():
    c = MockIbkrClient()
    assert c.connected is False
    assert c.connect() is True
    assert c.connected is True
    summary = c.get_account_summary()
    assert "NetLiquidation" in summary
    assert "TotalCashValue" in summary
    assert float(summary["NetLiquidation"]) == 1_000_000.0
    c.disconnect()
    assert c.connected is False


def test_market_buy_reduces_cash_and_creates_position():
    c = MockIbkrClient(initial_cash=100_000.0, last_prices={"SPY": 400.0})
    c.connect()
    r = c.place_market_order("SPY", "BUY", 10)
    assert r["orderType"] == "MKT"
    assert r["status"] == "Filled"
    pos = c.get_positions()
    assert len(pos) == 1
    assert pos[0]["symbol"] == "SPY"
    assert pos[0]["position"] == 10.0
    assert c.get_open_orders() == []


def test_market_sell_rejected_without_position():
    c = MockIbkrClient(last_prices={"SPY": 100.0})
    c.connect()
    r = c.place_market_order("SPY", "SELL", 5)
    assert r["status"] == "Cancelled"


def test_last_price_setter():
    c = MockIbkrClient(last_prices={})
    c.set_last_price("AAPL", 199.0)
    assert c.get_last_price("aapl") == 199.0


def test_limit_order_stays_open():
    c = MockIbkrClient()
    c.connect()
    r = c.place_limit_order("SPY", "BUY", 1, 100.0)
    assert r["orderType"] == "LMT"
    assert r["status"] == "PreSubmitted"
    open_o = c.get_open_orders()
    assert len(open_o) == 1
    assert c.cancel_order(r["orderId"]) is True
    assert c.get_open_orders() == []


def test_no_auto_fill_when_disabled():
    c = MockIbkrClient(auto_fill_market=False, last_prices={"QQQ": 100.0})
    c.connect()
    r = c.place_market_order("QQQ", "BUY", 1)
    assert r["status"] == "Submitted"
    assert c.get_open_orders()
