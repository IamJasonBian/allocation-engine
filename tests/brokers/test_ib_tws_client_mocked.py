"""
Unit tests for :class:`IbTwsClient` with ``ib_insync`` mocked (no TWS process).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from trading_system.brokers.ib_tws_client import IbTwsClient, TwsClientConfig


@pytest.fixture
def mock_ib() -> MagicMock:
    ib = MagicMock()
    ib.isConnected.return_value = True
    return ib


@patch("trading_system.brokers.ib_tws_client.IB")
def test_connect_and_read_account_values(mock_ib_class, mock_ib: MagicMock) -> None:
    av = SimpleNamespace(tag="NetLiquidation", value="100000.00", currency="USD", account="PAPER1")
    mock_ib.accountValues.return_value = [av]
    mock_ib_class.return_value = mock_ib

    c = IbTwsClient(TwsClientConfig(port=7496, client_id=2))
    assert c.connect() is True
    assert c.is_connected
    rows = c.get_account_values()
    assert len(rows) == 1
    assert rows[0]["tag"] == "NetLiquidation"
    assert rows[0]["value"] == "100000.00"
    m = c.get_account_summary_map()
    assert m["NetLiquidation"] == "100000.00"
    c.disconnect()
    assert not c.is_connected
    mock_ib.disconnect.assert_called()


@patch("trading_system.brokers.ib_tws_client.IB")
def test_get_positions(mock_ib_class, mock_ib: MagicMock) -> None:
    pos = SimpleNamespace(
        position=10.0,
        avgCost=50.0,
        contract=SimpleNamespace(symbol="SPY", conId=123, exchange="SMART", currency="USD"),
    )
    mock_ib.positions.return_value = [pos]
    mock_ib_class.return_value = mock_ib

    c = IbTwsClient()
    c.connect()
    out = c.get_positions()
    assert len(out) == 1
    assert out[0]["symbol"] == "SPY"
    assert out[0]["position"] == 10.0
    assert out[0]["avgCost"] == 50.0
    c.disconnect()


@patch("trading_system.brokers.ib_tws_client.IB")
def test_write_limit_and_cancel(mock_ib_class, mock_ib: MagicMock) -> None:
    """Place order returns order id; cancel finds it in openTrades."""
    order = MagicMock()
    order.orderId = 42
    trade = MagicMock()
    trade.order = order
    trade.contract = MagicMock()
    mock_ib.placeOrder.return_value = trade
    mock_ib.openTrades.return_value = [trade]
    mock_ib_class.return_value = mock_ib

    c = IbTwsClient()
    c.connect()
    oid = c.place_limit_order("SPY", "BUY", 1, 1.0)
    assert oid == 42
    mock_ib.qualifyContracts.assert_called_once()
    assert c.cancel_order(42) is True
    mock_ib.cancelOrder.assert_called_once()
    c.disconnect()
