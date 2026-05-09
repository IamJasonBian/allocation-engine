"""
Smoke tests for utils.ibkr_broker.IBKRBroker.

We never connect to a real TWS / IB Gateway here; everything is mocked.
The two assertions matter:

1. dry_run=True must NOT call connect() or placeOrder() (so the engine can be
   imported, banner-printed, and dry-run-tested with no TWS at all).
2. dry_run=False (with a mocked ib_insync.IB) must call connect() then
   placeOrder() exactly once with a LimitOrder of the right action / quantity /
   limit price.
"""

import os
import sys
from unittest.mock import MagicMock, patch

# Make the repo root importable regardless of where pytest is run from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fake_ib_insync():
    """Build a stand-in for the ib_insync module with the symbols we use."""
    fake = MagicMock(name='ib_insync')

    # Option(symbol, expiration_yyyymmdd, strike, right, exchange=, currency=)
    def _option(symbol, expiry, strike, right, exchange='SMART', currency='USD'):
        c = MagicMock(name='Option')
        c.symbol = symbol
        c.lastTradeDateOrContractMonth = expiry
        c.strike = strike
        c.right = right
        c.exchange = exchange
        c.currency = currency
        c.secType = 'OPT'
        return c

    fake.Option.side_effect = _option

    # LimitOrder(action, quantity, lmtPrice)
    def _limit_order(action, quantity, lmt_price):
        o = MagicMock(name='LimitOrder')
        o.action = action
        o.totalQuantity = quantity
        o.lmtPrice = lmt_price
        o.tif = 'GTC'
        return o

    fake.LimitOrder.side_effect = _limit_order

    # IB() instance
    ib_instance = MagicMock(name='IB-instance')
    ib_instance.isConnected.return_value = False  # forces connect() path

    trade = MagicMock(name='trade')
    trade.order.orderId = 4242
    trade.orderStatus.status = 'Submitted'
    ib_instance.placeOrder.return_value = trade

    fake.IB.return_value = ib_instance
    return fake, ib_instance


def test_dry_run_does_not_connect_or_place_order():
    """dry_run=True must never touch ib_insync.IB at all."""
    fake, ib_instance = _fake_ib_insync()

    with patch.dict(sys.modules, {'ib_insync': fake}):
        from utils.ibkr_broker import IBKRBroker

        broker = IBKRBroker(host='127.0.0.1', port=7497, client_id=1, paper=True)
        result = broker.place_option_buy_limit_order(
            symbol='XLY',
            expiration='2026-09-18',
            strike=120.0,
            option_type='call',
            quantity=1,
            price=4.50,
            dry_run=True,
        )

    assert result['status'] == 'dry_run'
    assert result['action'] == 'BUY'
    assert result['symbol'] == 'XLY'
    assert result['quantity'] == 1
    assert result['price'] == 4.50
    # No connection or order should have happened.
    ib_instance.connect.assert_not_called()
    ib_instance.placeOrder.assert_not_called()
    fake.LimitOrder.assert_not_called()


def test_live_run_connects_and_places_one_order_with_correct_limit():
    """dry_run=False must connect() and call placeOrder() exactly once with a LimitOrder of the right shape."""
    fake, ib_instance = _fake_ib_insync()

    with patch.dict(sys.modules, {'ib_insync': fake}):
        from utils.ibkr_broker import IBKRBroker

        broker = IBKRBroker(host='127.0.0.1', port=7497, client_id=1, paper=True)
        result = broker.place_option_buy_limit_order(
            symbol='XLY',
            expiration='2026-09-18',
            strike=120.0,
            option_type='call',
            quantity=2,
            price=4.50,
            dry_run=False,
        )

    # connect() called exactly once
    ib_instance.connect.assert_called_once_with('127.0.0.1', 7497, clientId=1)

    # placeOrder() called exactly once with (contract, order)
    ib_instance.placeOrder.assert_called_once()
    args, _ = ib_instance.placeOrder.call_args
    assert len(args) == 2
    contract, order = args

    # Contract: XLY 20260918 120 C
    assert contract.symbol == 'XLY'
    assert contract.lastTradeDateOrContractMonth == '20260918'
    assert contract.strike == 120.0
    assert contract.right == 'C'

    # Order: BUY, qty 2, limit 4.50
    fake.LimitOrder.assert_called_once_with('BUY', 2, 4.50)
    assert order.action == 'BUY'
    assert order.totalQuantity == 2
    assert order.lmtPrice == 4.50

    # Result envelope
    assert result['status'] == 'submitted'
    assert result['order_id'] == 4242
    assert result['order_status'] == 'Submitted'
