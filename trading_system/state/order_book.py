"""
Order Book Tracker
Tracks order lifecycle (submitted → filled/cancelled) across ticks to compute
fill rates and maintain an execution log.
"""

from datetime import datetime


class OrderBook:
    """Tracks open order IDs across ticks, diffs to detect fills/cancellations."""

    def __init__(self):
        self._prev_open_ids = set()
        self._seeded = False
        self._total_submitted = 0
        self._total_filled = 0
        self._total_cancelled = 0
        self._by_symbol = {}  # symbol -> {submitted, filled, cancelled}
        self.execution_log = []  # chronological list of events

    def seed_from_history(self, recent_orders):
        """Seed counters from 7-day order history so fill rate is immediately meaningful.

        Args:
            recent_orders: list of order dicts from get_recent_orders(days=7).
                Each has keys: order_id, symbol, state, average_price, filled_quantity, etc.
        """
        if self._seeded:
            return

        for order in (recent_orders or []):
            state = order.get('state', '')
            symbol = order.get('symbol', 'N/A')

            self._ensure_symbol(symbol)
            self._total_submitted += 1
            self._by_symbol[symbol]['submitted'] += 1

            if state == 'filled':
                self._total_filled += 1
                self._by_symbol[symbol]['filled'] += 1
                self.execution_log.append({
                    'event': 'filled',
                    'order_id': order.get('order_id'),
                    'symbol': symbol,
                    'side': order.get('side'),
                    'quantity': order.get('quantity'),
                    'average_price': order.get('average_price'),
                    'filled_quantity': order.get('filled_quantity'),
                    'timestamp': order.get('updated_at', order.get('created_at')),
                    'source': 'seed',
                })
            elif state in ('cancelled', 'failed', 'rejected'):
                self._total_cancelled += 1
                self._by_symbol[symbol]['cancelled'] += 1
                self.execution_log.append({
                    'event': 'cancelled',
                    'order_id': order.get('order_id'),
                    'symbol': symbol,
                    'side': order.get('side'),
                    'quantity': order.get('quantity'),
                    'timestamp': order.get('updated_at', order.get('created_at')),
                    'source': 'seed',
                })

        self._seeded = True

    def update(self, open_orders, recent_orders):
        """Diff current open orders against previous tick to detect fills/cancellations.

        Args:
            open_orders: list of currently open order dicts (from get_open_orders())
            recent_orders: list of recent historical orders (from get_recent_orders())

        Returns:
            dict with 'new_orders', 'fills', 'cancellations' detected this tick
        """
        current_ids = {o['order_id'] for o in (open_orders or []) if o.get('order_id')}

        # Build lookup of current open orders by ID
        open_by_id = {o['order_id']: o for o in (open_orders or []) if o.get('order_id')}

        # Build lookup of recent orders by ID for resolving disappeared orders
        recent_by_id = {o['order_id']: o for o in (recent_orders or []) if o.get('order_id')}

        # New orders = IDs in current but not in previous
        new_ids = current_ids - self._prev_open_ids

        # Disappeared orders = IDs in previous but not in current
        disappeared_ids = self._prev_open_ids - current_ids

        tick_summary = {
            'new_orders': [],
            'fills': [],
            'cancellations': [],
        }

        now = datetime.now().isoformat()

        # Track new submissions
        for oid in new_ids:
            order = open_by_id.get(oid, {})
            symbol = order.get('symbol', 'N/A')
            self._ensure_symbol(symbol)
            self._total_submitted += 1
            self._by_symbol[symbol]['submitted'] += 1

            entry = {
                'event': 'submitted',
                'order_id': oid,
                'symbol': symbol,
                'side': order.get('side'),
                'quantity': order.get('quantity'),
                'limit_price': order.get('limit_price'),
                'stop_price': order.get('stop_price'),
                'timestamp': now,
            }
            self.execution_log.append(entry)
            tick_summary['new_orders'].append(entry)

        # Resolve disappeared orders: check recent_orders to distinguish fill vs cancel
        for oid in disappeared_ids:
            resolved = recent_by_id.get(oid, {})
            state = resolved.get('state', '')
            symbol = resolved.get('symbol', 'N/A')
            self._ensure_symbol(symbol)

            if state == 'filled':
                self._total_filled += 1
                self._by_symbol[symbol]['filled'] += 1
                entry = {
                    'event': 'filled',
                    'order_id': oid,
                    'symbol': symbol,
                    'side': resolved.get('side'),
                    'quantity': resolved.get('quantity'),
                    'average_price': resolved.get('average_price'),
                    'filled_quantity': resolved.get('filled_quantity'),
                    'timestamp': now,
                }
                self.execution_log.append(entry)
                tick_summary['fills'].append(entry)
            else:
                # Treat as cancelled (includes cancelled, failed, rejected, or unknown)
                self._total_cancelled += 1
                self._by_symbol[symbol]['cancelled'] += 1
                entry = {
                    'event': 'cancelled',
                    'order_id': oid,
                    'symbol': symbol,
                    'side': resolved.get('side'),
                    'quantity': resolved.get('quantity'),
                    'state': state or 'disappeared',
                    'timestamp': now,
                }
                self.execution_log.append(entry)
                tick_summary['cancellations'].append(entry)

        self._prev_open_ids = current_ids
        return tick_summary

    def get_fill_rate(self):
        """Compute fill rate statistics.

        Returns:
            dict with total_submitted, total_filled, total_cancelled,
            fill_rate_pct, and by_symbol breakdown
        """
        rate = 0.0
        if self._total_submitted > 0:
            rate = (self._total_filled / self._total_submitted) * 100

        return {
            'total_submitted': self._total_submitted,
            'total_filled': self._total_filled,
            'total_cancelled': self._total_cancelled,
            'fill_rate_pct': round(rate, 2),
            'by_symbol': dict(self._by_symbol),
        }

    def get_execution_log(self, limit=50):
        """Return the most recent execution log entries.

        Args:
            limit: max number of entries to return (most recent first)

        Returns:
            list of event dicts, newest first
        """
        return list(reversed(self.execution_log[-limit:]))

    def to_dict(self):
        """Serialize full state for blob storage."""
        return {
            'fill_rate': self.get_fill_rate(),
            'execution_log': self.execution_log,
            'prev_open_ids': list(self._prev_open_ids),
            'seeded': self._seeded,
        }

    def _ensure_symbol(self, symbol):
        if symbol not in self._by_symbol:
            self._by_symbol[symbol] = {'submitted': 0, 'filled': 0, 'cancelled': 0}
