"""
Tests for StopLossAuditor class
"""
import pytest
from unittest.mock import Mock, patch
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_system.audit import StopLossAuditor


@pytest.fixture
def mock_auditor():
    """Create a mock StopLossAuditor instance"""
    with patch('trading_system.audit.SafeCashBot') as mock_bot_class:
        mock_bot = Mock()
        mock_bot_class.return_value = mock_bot
        auditor = StopLossAuditor()
        auditor.bot = mock_bot
        yield auditor


class TestStopLossAuditor:
    """Test suite for StopLossAuditor class"""

    def test_find_orders_for_position(self, mock_auditor):
        """Test finding and categorizing orders by type"""
        orders = [
            {'symbol': 'AAPL', 'side': 'SELL', 'order_type': 'Limit', 'trigger': 'immediate', 'quantity': 100},
            {'symbol': 'AAPL', 'side': 'SELL', 'order_type': 'Stop Limit', 'trigger': 'stop', 'quantity': 100},
            {'symbol': 'AAPL', 'side': 'SELL', 'order_type': 'Stop Loss', 'trigger': 'stop', 'quantity': 50},
            {'symbol': 'AAPL', 'side': 'BUY', 'order_type': 'Limit', 'trigger': 'immediate', 'quantity': 200},  # Ignored
            {'symbol': 'TSLA', 'side': 'SELL', 'order_type': 'Limit', 'trigger': 'immediate', 'quantity': 50},  # Wrong symbol
        ]

        order_map = mock_auditor.find_orders_for_position('AAPL', orders)

        assert len(order_map['limit']) == 1
        assert len(order_map['stop_limit']) == 1
        assert len(order_map['stop']) == 1
        assert order_map['limit'][0]['quantity'] == 100
        assert order_map['stop_limit'][0]['quantity'] == 100

    def test_check_coverage_calculations(self, mock_auditor):
        """Test coverage calculations with mixed protection levels"""
        positions = [
            {'symbol': 'AAPL', 'quantity': 100, 'current_price': 150.0, 'equity': 15000.0},
            {'symbol': 'BTC', 'quantity': 500, 'current_price': 30.0, 'equity': 15000.0},
            {'symbol': 'TSLA', 'quantity': 50, 'current_price': 200.0, 'equity': 10000.0},
        ]
        orders = [
            {'symbol': 'AAPL', 'side': 'SELL', 'order_type': 'Limit', 'trigger': 'immediate',
             'quantity': 100, 'limit_price': 160.0, 'stop_price': None},
            {'symbol': 'AAPL', 'side': 'SELL', 'order_type': 'Stop Limit', 'trigger': 'stop',
             'quantity': 100, 'limit_price': 140.0, 'stop_price': 140.0},
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Limit', 'trigger': 'immediate',
             'quantity': 300, 'limit_price': 35.0, 'stop_price': None},
        ]

        mock_auditor.bot.get_positions.return_value = positions
        mock_auditor.bot.get_open_orders.return_value = orders

        coverage = mock_auditor.check_coverage()

        # Verify totals
        assert coverage['total_positions'] == 3
        assert coverage['total_equity'] == 40000.0

        # Verify coverage by type (proportional coverage)
        # AAPL: 100% limit coverage = 15000, 100% stop_limit = 15000
        # BTC: 60% limit coverage (300/500) = 9000
        # Total limit equity: 15000 + 9000 = 24000
        assert coverage['coverage_by_type']['any_protection']['positions'] == 2  # AAPL, BTC
        assert pytest.approx(coverage['coverage_by_type']['limit']['equity']) == 24000.0
        assert pytest.approx(coverage['coverage_by_type']['stop_limit']['equity']) == 15000.0

        # Verify largest uncovered
        assert coverage['largest_uncovered']['symbol'] == 'TSLA'
        assert coverage['largest_uncovered']['equity'] == 10000.0

        # Verify position details sorted by equity
        assert coverage['details'][0]['equity'] >= coverage['details'][1]['equity']

    def test_check_coverage_with_symbol_filter(self, mock_auditor):
        """Test filtering by specific symbol"""
        positions = [
            {'symbol': 'AAPL', 'quantity': 100, 'current_price': 150.0, 'equity': 15000.0},
            {'symbol': 'BTC', 'quantity': 500, 'current_price': 30.0, 'equity': 15000.0},
        ]
        orders = [
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Limit', 'trigger': 'immediate',
             'quantity': 300, 'limit_price': 35.0, 'stop_price': None},
        ]

        mock_auditor.bot.get_positions.return_value = positions
        mock_auditor.bot.get_open_orders.return_value = orders

        coverage = mock_auditor.check_coverage(filter_symbol='BTC')

        assert coverage['total_positions'] == 1
        assert coverage['details'][0]['symbol'] == 'BTC'
        assert coverage['total_equity'] == 15000.0

        # Test non-existent symbol
        coverage = mock_auditor.check_coverage(filter_symbol='NONEXISTENT')
        assert coverage['total_positions'] == 0

    def test_run_audit_exit_codes(self, mock_auditor):
        """Test that run_audit returns correct exit codes"""
        mock_auditor.bot.auth.logout = Mock()

        # Test with uncovered position (should return 1)
        positions = [
            {'symbol': 'AAPL', 'quantity': 100, 'current_price': 150.0, 'equity': 15000.0},
        ]
        mock_auditor.bot.get_positions.return_value = positions
        mock_auditor.bot.get_open_orders.return_value = []

        exit_code = mock_auditor.run_audit()
        assert exit_code == 1

        # Test with all protected (should return 0)
        orders = [
            {'symbol': 'AAPL', 'side': 'SELL', 'order_type': 'Limit', 'trigger': 'immediate',
             'quantity': 100, 'limit_price': 160.0, 'stop_price': None},
        ]
        mock_auditor.bot.get_open_orders.return_value = orders

        exit_code = mock_auditor.run_audit()
        assert exit_code == 0

        # Test exception handling
        mock_auditor.bot.get_positions.side_effect = Exception("API Error")
        exit_code = mock_auditor.run_audit()
        assert exit_code == 1

    def test_multiple_orders_same_position(self, mock_auditor):
        """Test summing quantities for multiple orders of same type"""
        positions = [
            {'symbol': 'BTC', 'quantity': 1000, 'current_price': 30.0, 'equity': 30000.0},
        ]
        orders = [
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Limit', 'trigger': 'immediate',
             'quantity': 300, 'limit_price': 35.0, 'stop_price': None},
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Limit', 'trigger': 'immediate',
             'quantity': 400, 'limit_price': 40.0, 'stop_price': None},
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Stop Limit', 'trigger': 'stop',
             'quantity': 200, 'limit_price': 28.0, 'stop_price': 28.0},
        ]

        mock_auditor.bot.get_positions.return_value = positions
        mock_auditor.bot.get_open_orders.return_value = orders

        coverage = mock_auditor.check_coverage()

        detail = coverage['details'][0]
        assert detail['order_coverage']['limit']['quantity'] == 700  # 300 + 400
        assert detail['order_coverage']['stop_limit']['quantity'] == 200
        assert pytest.approx(detail['order_coverage']['limit']['pct']) == 70.0

    def test_proportional_coverage_btc_scenario(self, mock_auditor):
        """Test proportional equity coverage for BTC scenario with partial orders"""
        positions = [
            {'symbol': 'BTC', 'quantity': 3562, 'current_price': 31.54, 'equity': 112350.40},
        ]
        orders = [
            # Limit order covering 2150 shares (60.4%)
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Limit', 'trigger': 'immediate',
             'quantity': 2150, 'limit_price': 37.99, 'stop_price': None},
            # Stop Limit order covering 1050 shares (29.5%)
            {'symbol': 'BTC', 'side': 'SELL', 'order_type': 'Stop Limit', 'trigger': 'stop',
             'quantity': 1050, 'limit_price': 28.0, 'stop_price': 28.0},
        ]

        mock_auditor.bot.get_positions.return_value = positions
        mock_auditor.bot.get_open_orders.return_value = orders

        coverage = mock_auditor.check_coverage()

        # Verify equity coverage is proportional
        # Limit: 2150/3562 * 112350.40 = ~67,852
        # Stop Limit: 1050/3562 * 112350.40 = ~33,121
        # Any protection: max(2150, 1050) / 3562 * 112350.40 = ~67,852

        assert pytest.approx(coverage['coverage_by_type']['limit']['equity'], rel=0.01) == 67852.0
        assert pytest.approx(coverage['coverage_by_type']['stop_limit']['equity'], rel=0.01) == 33121.0
        assert pytest.approx(coverage['coverage_by_type']['any_protection']['equity'], rel=0.01) == 67852.0

        # Verify percentages
        # Limit: 67852 / 112350.40 = ~60.4%
        # Stop Limit: 33121 / 112350.40 = ~29.5%
        assert pytest.approx(coverage['coverage_by_type']['limit']['pct_equity'], abs=0.5) == 60.4
        assert pytest.approx(coverage['coverage_by_type']['stop_limit']['pct_equity'], abs=0.5) == 29.5

        # Verify position is marked as protected
        detail = coverage['details'][0]
        assert detail['has_any_protection'] is True
        assert pytest.approx(detail['order_coverage']['limit']['pct'], abs=0.1) == 60.4
        assert pytest.approx(detail['order_coverage']['stop_limit']['pct'], abs=0.1) == 29.5
