"""
Fill Monitor — detects fill status and crossover events.

A crossover occurs when the market price moves through an order's limit price
without filling. This typically indicates the order missed the market.
"""

from datetime import datetime


class FillMonitor:
    """Detects fills, cancellations, and crossover events for pending orders."""

    # Price must exceed limit by this fraction to count as a crossover.
    # Avoids false alerts from bid-ask noise.
    CROSSOVER_THRESHOLD_PCT = 0.001  # 0.1%

    def __init__(self, price_discovery):
        """
        Args:
            price_discovery: PriceDiscovery instance for current market prices.
        """
        self.price_discovery = price_discovery

    def check_order(self, ctx, open_order_ids: set, recent_orders: list = None) -> str:
        """Determine the current status of an order.

        Args:
            ctx: ExecutionContext for the order.
            open_order_ids: Set of order_id strings currently open on the broker.
            recent_orders: Optional list of recent order dicts for fill lookup.

        Returns:
            'filled' | 'open' | 'crossover' | 'cancelled'
        """
        order_id = ctx.order_id
        if order_id is None:
            return 'open'

        still_open = order_id in open_order_ids

        if not still_open:
            # Disappeared from open orders — check recent for state
            if recent_orders:
                for ro in recent_orders:
                    if ro.get('order_id') == order_id:
                        state = ro.get('state', '')
                        if state == 'filled':
                            return 'filled'
                        if state in ('cancelled', 'failed', 'rejected'):
                            return 'cancelled'
            # Disappeared but not in recent — assume filled
            return 'filled'

        # Still open — check for crossover
        price_data = self.price_discovery.get_price(ctx.symbol)
        current_price = price_data['price']
        if current_price > 0 and self.detect_crossover(ctx, current_price):
            return 'crossover'

        return 'open'

    def detect_crossover(self, ctx, current_price: float) -> bool:
        """Detect whether the market has crossed through the order's limit.

        Sell limit at $30: crossover if market > $30 (buyers paying more than our ask).
        Buy limit at $30: crossover if market < $30 (sellers accepting less than our bid).
        Stop-limit sell (stop=$29, limit=$28.50): crossover if market < $28.50.

        Returns True if market has crossed through limit beyond the threshold.
        """
        limit_price = ctx.current_limit_price
        if limit_price is None or limit_price <= 0 or current_price <= 0:
            return False

        action = ctx.action
        threshold = limit_price * self.CROSSOVER_THRESHOLD_PCT

        if action in ('stop_limit_sell',):
            # Stop-limit sell: crossover if market dropped below limit
            return current_price < (limit_price - threshold)
        elif action in ('sell', 'limit_sell'):
            # Sell limit: crossover if market rose above limit
            return current_price > (limit_price + threshold)
        elif action in ('buy', 'limit_buy'):
            # Buy limit: crossover if market dropped below limit
            return current_price < (limit_price - threshold)

        return False

    def build_crossover_alert(self, ctx, current_price: float) -> str:
        """Build a Slack crossover alert message.

        Includes @Jason Bian mention for escalation.
        """
        elapsed = ""
        if ctx.submitted_at:
            delta = datetime.now() - ctx.submitted_at
            minutes = int(delta.total_seconds() / 60)
            elapsed = f", open {minutes}m"

        reprice_note = ""
        if ctx.reprice_attempt > 0:
            reprice_note = f", reprice attempt {ctx.reprice_attempt}"

        direction = "above" if ctx.action in ('sell', 'limit_sell') else "below"

        return (
            f"Crossover detected: {ctx.symbol} {ctx.action} "
            f"limit=${ctx.current_limit_price:.2f} but market=${current_price:.2f} "
            f"({direction} limit{elapsed}{reprice_note})"
        )
