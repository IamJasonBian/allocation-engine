"""
Smoke tests for utils.alpaca_broker.AlpacaBroker.

These tests fully mock the alpaca-py TradingClient so no network calls
are made. CI has no Alpaca credentials and no internet access for
brokers, so the tests assert against the mock instead.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# --------------------------------------------------------------------------
# alpaca-py is an optional dependency for the rest of the codebase. If
# it's not installed in the test env, stub just enough of it for the
# AlpacaBroker import to succeed.
# --------------------------------------------------------------------------
def _ensure_alpaca_stub():
    try:
        import alpaca.trading.client  # noqa: F401
        import alpaca.trading.requests  # noqa: F401
        import alpaca.trading.enums  # noqa: F401
        return
    except Exception:
        pass

    alpaca_mod = types.ModuleType('alpaca')
    trading_mod = types.ModuleType('alpaca.trading')
    client_mod = types.ModuleType('alpaca.trading.client')
    requests_mod = types.ModuleType('alpaca.trading.requests')
    enums_mod = types.ModuleType('alpaca.trading.enums')

    class _StubTradingClient:
        def __init__(self, *a, **kw):
            pass

    class _StubLimitOrderRequest:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _StubEnum(str):
        pass

    class _OrderSide:
        BUY = _StubEnum('buy')
        SELL = _StubEnum('sell')

    class _TimeInForce:
        DAY = _StubEnum('day')
        GTC = _StubEnum('gtc')
        IOC = _StubEnum('ioc')
        FOK = _StubEnum('fok')

    class _PositionIntent:
        BUY_TO_OPEN = _StubEnum('buy_to_open')
        SELL_TO_CLOSE = _StubEnum('sell_to_close')
        BUY_TO_CLOSE = _StubEnum('buy_to_close')
        SELL_TO_OPEN = _StubEnum('sell_to_open')

    client_mod.TradingClient = _StubTradingClient
    requests_mod.LimitOrderRequest = _StubLimitOrderRequest
    enums_mod.OrderSide = _OrderSide
    enums_mod.TimeInForce = _TimeInForce
    enums_mod.PositionIntent = _PositionIntent

    sys.modules['alpaca'] = alpaca_mod
    sys.modules['alpaca.trading'] = trading_mod
    sys.modules['alpaca.trading.client'] = client_mod
    sys.modules['alpaca.trading.requests'] = requests_mod
    sys.modules['alpaca.trading.enums'] = enums_mod


_ensure_alpaca_stub()


from utils.alpaca_broker import AlpacaBroker, build_opra_symbol  # noqa: E402


# --------------------------------------------------------------------------
# OPRA encoder
# --------------------------------------------------------------------------
def test_build_opra_symbol_call():
    # XLY 2026-09-18 $120 CALL -> XLY260918C00120000
    assert build_opra_symbol('XLY', '2026-09-18', 120.0, 'call') == 'XLY260918C00120000'


def test_build_opra_symbol_put_fractional_strike():
    # SPY 2026-12-19 $432.5 PUT -> SPY261219P00432500
    assert build_opra_symbol('SPY', '2026-12-19', 432.5, 'put') == 'SPY261219P00432500'


# --------------------------------------------------------------------------
# Broker behavior - dry_run gating + single submit_order call on live
# --------------------------------------------------------------------------
@pytest.fixture
def broker_with_mock_client():
    """Construct an AlpacaBroker whose TradingClient is replaced with a MagicMock."""
    with patch('alpaca.trading.client.TradingClient') as mock_cls:
        mock_client = MagicMock(name='TradingClient')

        # Account stub for the buying-power pre-trade check
        acct = MagicMock()
        acct.options_buying_power = '1000000'
        acct.buying_power = '1000000'
        acct.cash = '500000'
        acct.account_number = 'PA-TEST-1'
        acct.status = 'ACTIVE'
        mock_client.get_account.return_value = acct

        # submit_order stub returns an object with .id / .status
        submitted = MagicMock()
        submitted.id = 'order-abc-123'
        submitted.status = 'accepted'
        submitted.symbol = 'XLY260918C00120000'
        # No model_dump -> _order_to_dict will fall back to attr scrape
        del submitted.model_dump
        mock_client.submit_order.return_value = submitted

        # No positions in the way (sell-to-close path is not exercised here)
        mock_client.get_all_positions.return_value = []

        mock_cls.return_value = mock_client

        # Provide credentials so AlpacaBroker doesn't whine
        with patch.dict('os.environ', {
            'ALPACA_API_KEY_ID': 'test-key',
            'ALPACA_API_SECRET_KEY': 'test-secret',
            'ALPACA_PAPER': 'true',
        }, clear=False):
            broker = AlpacaBroker(paper=True)
            broker._client = mock_client  # belt and suspenders
            yield broker, mock_client


def test_dry_run_does_not_call_submit_order(broker_with_mock_client):
    broker, mock_client = broker_with_mock_client

    result = broker.place_option_buy_limit_order(
        symbol='XLY',
        expiration='2026-09-18',
        strike=120.0,
        option_type='call',
        quantity=1,
        price=4.50,
        dry_run=True,
    )

    # Dry run returns an empty dict (no order actually placed)
    assert result == {}
    # Crucially: the live submit_order endpoint is NOT called
    mock_client.submit_order.assert_not_called()


def test_live_calls_submit_order_once_with_correct_opra(broker_with_mock_client):
    broker, mock_client = broker_with_mock_client

    result = broker.place_option_buy_limit_order(
        symbol='XLY',
        expiration='2026-09-18',
        strike=120.0,
        option_type='call',
        quantity=2,
        price=4.50,
        dry_run=False,
    )

    # submit_order called exactly once
    assert mock_client.submit_order.call_count == 1

    # Pull the order request payload and verify the OPRA symbol + key fields
    _, kwargs = mock_client.submit_order.call_args
    order_data = kwargs.get('order_data')
    assert order_data is not None, "submit_order must be called with order_data=..."
    assert getattr(order_data, 'symbol', None) == 'XLY260918C00120000'
    assert getattr(order_data, 'qty', None) == 2
    assert float(getattr(order_data, 'limit_price', 0)) == 4.50

    # And the broker surfaces the order id back to the caller
    assert result.get('id') == 'order-abc-123'
