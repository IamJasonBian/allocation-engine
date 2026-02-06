"""
Momentum DCA Strategy
Ensures at least 20% of each position is covered by sell orders at all times.
If coverage is below threshold:
  - Price within 0.75% of existing order → resubmit at original price
  - Price moved >0.75% → place stop-limit at -1.5% below current price
"""

from typing import Dict, Optional, List


class MomentumDcaStrategy:
    """
    Momentum Dollar-Cost Averaging Strategy

    Monitors open sell orders against positions and maintains a minimum
    coverage threshold. Places protective orders to fill gaps.
    """

    def __init__(self, symbols: List[str], coverage_threshold: float = 0.20,
                 stop_offset_pct: float = 0.015, proximity_pct: float = 0.0075):
        """
        Args:
            symbols: List of symbols to trade
            coverage_threshold: Minimum coverage ratio (default 20%)
            stop_offset_pct: Stop-limit offset below current price (default 1.5%)
            proximity_pct: Price proximity threshold for resubmit (default 0.75%)
        """
        self.symbols = symbols
        self.coverage_threshold = coverage_threshold
        self.stop_offset_pct = stop_offset_pct
        self.proximity_pct = proximity_pct

        print(f"\n{'='*70}")
        print("MOMENTUM DCA STRATEGY INITIALIZED")
        print(f"{'='*70}")
        print(f"Symbols: {', '.join(symbols)}")
        print(f"Coverage Threshold: {coverage_threshold * 100}%")
        print(f"Stop Offset: {stop_offset_pct * 100}%")
        print(f"Proximity Threshold: {proximity_pct * 100}%")
        print(f"{'='*70}\n")

    def analyze_symbol(self, symbol: str, metrics: Dict,
                       current_position: Optional[Dict] = None,
                       open_orders: Optional[List[Dict]] = None) -> Dict:
        """
        Analyze coverage for a symbol and generate signal

        Args:
            symbol: Stock symbol
            metrics: Current metrics (must include current_price)
            current_position: Current position info (if any)
            open_orders: All open orders (strategy filters to this symbol)

        Returns:
            Signal dict with signal type, reason, and order details
        """
        current_price = metrics.get('current_price', 0)
        if not current_price:
            return {
                'signal': 'NO_DATA',
                'reason': 'No current price available',
                'order': None
            }

        # No position → nothing to protect
        if not current_position or float(current_position.get('quantity', 0)) <= 0:
            return {
                'signal': 'NO_POSITION',
                'reason': f'No position in {symbol} to protect',
                'order': None
            }

        position_qty = float(current_position['quantity'])
        sell_orders = self._get_sell_orders_for_symbol(symbol, open_orders or [])
        covered_qty = self._calculate_coverage(sell_orders)
        coverage_pct = (covered_qty / position_qty) * 100 if position_qty > 0 else 0

        # Already covered
        if coverage_pct >= self.coverage_threshold * 100:
            return {
                'signal': 'COVERED',
                'reason': (f'{symbol}: {coverage_pct:.1f}% covered '
                           f'({covered_qty:.4f}/{position_qty:.4f} shares), '
                           f'threshold {self.coverage_threshold * 100}%'),
                'order': None
            }

        # Under-covered — calculate gap
        gap_qty = (self.coverage_threshold * position_qty) - covered_qty
        gap_qty = self._round_quantity(symbol, gap_qty)

        if gap_qty <= 0:
            return {
                'signal': 'COVERED',
                'reason': f'{symbol}: gap rounds to zero',
                'order': None
            }

        # Check price proximity to existing sell orders
        nearest_order = self._find_nearest_order(current_price, sell_orders)

        if nearest_order:
            order_price = nearest_order.get('limit_price') or nearest_order.get('stop_price')
            if order_price and self._is_within_proximity(current_price, order_price):
                return {
                    'signal': 'RESUBMIT',
                    'reason': (f'{symbol}: {coverage_pct:.1f}% covered, '
                               f'price ${current_price:.2f} within {self.proximity_pct * 100}% '
                               f'of order @ ${order_price:.2f}. '
                               f'Resubmitting {gap_qty} shares at ${order_price:.2f}'),
                    'order': {
                        'action': 'limit_sell',
                        'symbol': symbol,
                        'quantity': gap_qty,
                        'price': order_price,
                        'current_price': current_price,
                    }
                }

        # Price moved too far — new stop-limit
        stop_price = round(current_price * (1 - self.stop_offset_pct), 2)
        limit_price = stop_price

        return {
            'signal': 'COVER_GAP',
            'reason': (f'{symbol}: {coverage_pct:.1f}% covered, '
                       f'need {gap_qty} more shares protected. '
                       f'Placing stop-limit sell @ ${stop_price:.2f} '
                       f'(-{self.stop_offset_pct * 100}%)'),
            'order': {
                'action': 'stop_limit_sell',
                'symbol': symbol,
                'quantity': gap_qty,
                'stop_price': stop_price,
                'limit_price': limit_price,
                'current_price': current_price,
            }
        }

    def _get_sell_orders_for_symbol(self, symbol: str,
                                    orders: List[Dict]) -> List[Dict]:
        """Filter to SELL orders for a given symbol (all types)"""
        return [
            o for o in orders
            if o.get('symbol') == symbol and o.get('side') == 'SELL'
        ]

    def _calculate_coverage(self, sell_orders: List[Dict]) -> float:
        """Sum quantity covered by all sell orders"""
        return sum(float(o.get('quantity', 0)) for o in sell_orders)

    def _find_nearest_order(self, current_price: float,
                            sell_orders: List[Dict]) -> Optional[Dict]:
        """Find the sell order whose price is closest to current price"""
        best = None
        best_dist = float('inf')

        for order in sell_orders:
            price = order.get('limit_price') or order.get('stop_price')
            if price is None:
                continue
            dist = abs(current_price - price) / price
            if dist < best_dist:
                best_dist = dist
                best = order

        return best

    def _is_within_proximity(self, current_price: float,
                             order_price: float) -> bool:
        """Check if current price is within proximity_pct of order price"""
        return abs(current_price - order_price) / order_price <= self.proximity_pct

    def _round_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity appropriately for the asset type"""
        if symbol == 'BTC':
            return round(quantity, 4)
        return int(quantity)

    def format_signal(self, symbol: str, signal_data: Dict) -> str:
        """Format signal for display"""
        lines = [
            f"\n{'='*70}",
            f"MOMENTUM DCA: {symbol}",
            f"{'='*70}",
            f"Signal: {signal_data['signal']}",
            f"Reason: {signal_data['reason']}",
        ]

        if signal_data['order']:
            order = signal_data['order']
            lines.append("")
            lines.append("Order Details:")
            lines.append(f"  Action: {order['action'].upper()}")
            lines.append(f"  Quantity: {order['quantity']}")
            lines.append(f"  Current Price: ${order['current_price']:,.2f}")

            if order['action'] == 'stop_limit_sell':
                lines.append(f"  Stop Price: ${order['stop_price']:,.2f}")
                lines.append(f"  Limit Price: ${order['limit_price']:,.2f}")
            elif order['action'] == 'limit_sell':
                lines.append(f"  Limit Price: ${order['price']:,.2f}")

        lines.append(f"{'='*70}\n")
        return '\n'.join(lines)

    def calculate_position_size(self, symbol: str, price: float,
                                available_cash: float) -> float:
        """Calculate position size (not primary for this strategy but kept for interface)"""
        if symbol == 'BTC':
            return round(available_cash * 0.25 / price, 4)
        return int(available_cash * 0.25 / price)
