"""
Tests for MomentumDcaStrategy
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_system.strategies.momentum_dca_strategy import MomentumDcaStrategy


@pytest.fixture
def strategy():
    """Create a MomentumDcaStrategy instance"""
    return MomentumDcaStrategy(
        symbols=['BTC', 'SPY'],
        coverage_threshold=0.20,
        stop_offset_pct=0.015,
        proximity_pct=0.0075
    )


def _make_position(symbol, quantity, price):
    return {
        'symbol': symbol,
        'quantity': quantity,
        'current_price': price,
        'equity': quantity * price,
    }


def _make_sell_order(symbol, quantity, limit_price=None, stop_price=None,
                     order_type='Limit', trigger='immediate'):
    return {
        'symbol': symbol,
        'side': 'SELL',
        'order_type': order_type,
        'trigger': trigger,
        'quantity': quantity,
        'limit_price': limit_price,
        'stop_price': stop_price,
    }


class TestCoverageCalculation:
    """Test coverage % calculation"""

    def test_fully_covered(self, strategy):
        """Position with >= 20% sell orders → COVERED"""
        position = _make_position('SPY', 100, 450.0)
        orders = [_make_sell_order('SPY', 25, limit_price=460.0)]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['signal'] == 'COVERED'
        assert signal['order'] is None

    def test_exactly_at_threshold(self, strategy):
        """Position with exactly 20% coverage → COVERED"""
        position = _make_position('SPY', 100, 450.0)
        orders = [_make_sell_order('SPY', 20, limit_price=460.0)]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['signal'] == 'COVERED'

    def test_under_covered_no_existing_orders(self, strategy):
        """Position with 0% coverage, no existing orders → COVER_GAP"""
        position = _make_position('SPY', 100, 450.0)
        orders = []
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['signal'] == 'COVER_GAP'
        assert signal['order']['action'] == 'stop_limit_sell'
        assert signal['order']['quantity'] == 20  # 20% of 100

    def test_partial_coverage(self, strategy):
        """Position with 10% coverage → gap of 10%"""
        position = _make_position('SPY', 100, 450.0)
        orders = [_make_sell_order('SPY', 10, limit_price=460.0)]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['order'] is not None
        assert signal['order']['quantity'] == 10  # need 20 - have 10

    def test_multiple_order_types_count(self, strategy):
        """All sell order types count toward coverage"""
        position = _make_position('SPY', 100, 450.0)
        orders = [
            _make_sell_order('SPY', 5, limit_price=460.0),
            _make_sell_order('SPY', 5, stop_price=440.0,
                             order_type='Stop Loss', trigger='stop'),
            _make_sell_order('SPY', 5, limit_price=438.0, stop_price=440.0,
                             order_type='Stop Limit', trigger='stop'),
            _make_sell_order('SPY', 5, limit_price=435.0),
        ]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        # 5+5+5+5 = 20 shares = 20% → COVERED
        assert signal['signal'] == 'COVERED'

    def test_buy_orders_ignored(self, strategy):
        """BUY orders should not count toward coverage"""
        position = _make_position('SPY', 100, 450.0)
        orders = [
            {'symbol': 'SPY', 'side': 'BUY', 'order_type': 'Limit',
             'trigger': 'immediate', 'quantity': 50, 'limit_price': 440.0,
             'stop_price': None},
        ]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['signal'] == 'COVER_GAP'
        assert signal['order']['quantity'] == 20

    def test_other_symbol_orders_ignored(self, strategy):
        """Orders for different symbol should not count"""
        position = _make_position('SPY', 100, 450.0)
        orders = [_make_sell_order('AAPL', 50, limit_price=180.0)]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['signal'] == 'COVER_GAP'


class TestGapQuantity:
    """Test gap quantity calculation"""

    def test_gap_from_zero(self, strategy):
        """No orders → gap is 20% of position"""
        position = _make_position('SPY', 200, 450.0)
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, [])

        assert signal['order']['quantity'] == 40  # 20% of 200

    def test_gap_from_partial(self, strategy):
        """Partial coverage → gap fills to 20%"""
        position = _make_position('SPY', 200, 450.0)
        orders = [_make_sell_order('SPY', 15, limit_price=460.0)]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['order']['quantity'] == 25  # 40 - 15

    def test_btc_fractional_quantity(self, strategy):
        """BTC should use 4 decimal places"""
        position = _make_position('BTC', 0.5, 100000.0)
        metrics = {'current_price': 100000.0}

        signal = strategy.analyze_symbol('BTC', metrics, position, [])

        assert signal['order']['quantity'] == 0.1  # 20% of 0.5
        # Verify it's rounded to 4 decimals
        assert round(signal['order']['quantity'], 4) == signal['order']['quantity']

    def test_stock_whole_shares(self, strategy):
        """Stock quantities should be whole numbers"""
        position = _make_position('SPY', 7, 450.0)
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, [])

        assert signal['order']['quantity'] == 1  # int(20% of 7) = int(1.4) = 1
        assert isinstance(signal['order']['quantity'], int)


class TestStopLimitPricing:
    """Test -1.5% stop-limit pricing"""

    def test_stop_price_at_negative_1_5_pct(self, strategy):
        """Stop price should be current_price * 0.985"""
        position = _make_position('SPY', 100, 450.0)
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, [])

        expected_stop = round(450.0 * 0.985, 2)
        assert signal['order']['stop_price'] == expected_stop
        assert signal['order']['limit_price'] == expected_stop

    def test_stop_equals_limit(self, strategy):
        """Stop and limit should be the same price"""
        position = _make_position('BTC', 1.0, 100000.0)
        metrics = {'current_price': 100000.0}

        signal = strategy.analyze_symbol('BTC', metrics, position, [])

        assert signal['order']['stop_price'] == signal['order']['limit_price']


class TestPriceProximity:
    """Test the 0.75% proximity check and resubmit logic"""

    def test_within_proximity_resubmits(self, strategy):
        """Price within 0.75% of existing order → RESUBMIT"""
        position = _make_position('SPY', 100, 450.0)
        # Existing order at 452.0, current price 450.0
        # Distance: |450 - 452| / 452 = 0.44% < 0.75%
        orders = [_make_sell_order('SPY', 10, limit_price=452.0)]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['signal'] == 'RESUBMIT'
        assert signal['order']['action'] == 'limit_sell'
        assert signal['order']['price'] == 452.0  # original order price

    def test_outside_proximity_new_stop_limit(self, strategy):
        """Price beyond 0.75% of existing order → COVER_GAP"""
        position = _make_position('SPY', 100, 450.0)
        # Existing order at 500.0, current price 450.0
        # Distance: |450 - 500| / 500 = 10% > 0.75%
        orders = [_make_sell_order('SPY', 10, limit_price=500.0)]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['signal'] == 'COVER_GAP'
        assert signal['order']['action'] == 'stop_limit_sell'

    def test_resubmit_uses_original_price(self, strategy):
        """RESUBMIT should use the original order price, not current"""
        position = _make_position('SPY', 100, 448.0)
        orders = [_make_sell_order('SPY', 10, limit_price=450.0)]
        metrics = {'current_price': 448.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['signal'] == 'RESUBMIT'
        assert signal['order']['price'] == 450.0
        assert signal['order']['current_price'] == 448.0

    def test_proximity_with_stop_price_order(self, strategy):
        """Proximity check works with stop-price orders too"""
        position = _make_position('SPY', 100, 450.0)
        orders = [_make_sell_order('SPY', 10, stop_price=449.0,
                                   order_type='Stop Loss', trigger='stop')]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        # |450 - 449| / 449 = 0.22% < 0.75% → RESUBMIT
        assert signal['signal'] == 'RESUBMIT'

    def test_proximity_boundary_exact(self, strategy):
        """Price at exactly 0.75% boundary"""
        position = _make_position('SPY', 100, 450.0)
        # 0.75% of 450 = 3.375, so order at 450 + 3.375 = 453.375
        # Distance: |450 - 453.375| / 453.375 = 0.00744... which is < 0.0075
        orders = [_make_sell_order('SPY', 10, limit_price=453.375)]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['signal'] == 'RESUBMIT'

    def test_nearest_order_selected(self, strategy):
        """When multiple orders exist, nearest one is used for proximity"""
        position = _make_position('SPY', 100, 450.0)
        orders = [
            _make_sell_order('SPY', 5, limit_price=500.0),  # far
            _make_sell_order('SPY', 5, limit_price=451.0),  # near
        ]
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, orders)

        assert signal['signal'] == 'RESUBMIT'
        assert signal['order']['price'] == 451.0


class TestEdgeCases:
    """Test edge cases"""

    def test_no_position(self, strategy):
        """No position → NO_POSITION"""
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, None, [])

        assert signal['signal'] == 'NO_POSITION'

    def test_zero_quantity_position(self, strategy):
        """Position with 0 quantity → NO_POSITION"""
        position = _make_position('SPY', 0, 450.0)
        metrics = {'current_price': 450.0}

        signal = strategy.analyze_symbol('SPY', metrics, position, [])

        assert signal['signal'] == 'NO_POSITION'

    def test_no_price_data(self, strategy):
        """No current_price in metrics → NO_DATA"""
        position = _make_position('SPY', 100, 450.0)

        signal = strategy.analyze_symbol('SPY', {}, position, [])

        assert signal['signal'] == 'NO_DATA'

    def test_format_signal_cover_gap(self, strategy):
        """format_signal produces output for COVER_GAP"""
        signal = {
            'signal': 'COVER_GAP',
            'reason': 'test reason',
            'order': {
                'action': 'stop_limit_sell',
                'symbol': 'SPY',
                'quantity': 20,
                'stop_price': 443.25,
                'limit_price': 443.25,
                'current_price': 450.0,
            }
        }

        output = strategy.format_signal('SPY', signal)

        assert 'COVER_GAP' in output
        assert 'Stop Price' in output
        assert '443.25' in output

    def test_format_signal_resubmit(self, strategy):
        """format_signal produces output for RESUBMIT"""
        signal = {
            'signal': 'RESUBMIT',
            'reason': 'test reason',
            'order': {
                'action': 'limit_sell',
                'symbol': 'SPY',
                'quantity': 10,
                'price': 452.0,
                'current_price': 450.0,
            }
        }

        output = strategy.format_signal('SPY', signal)

        assert 'RESUBMIT' in output
        assert 'Limit Price' in output

    def test_format_signal_covered(self, strategy):
        """format_signal produces output for COVERED"""
        signal = {
            'signal': 'COVERED',
            'reason': 'fully covered',
            'order': None,
        }

        output = strategy.format_signal('SPY', signal)

        assert 'COVERED' in output
