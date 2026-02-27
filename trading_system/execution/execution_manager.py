"""
Execution Manager — orchestrates order lifecycle.

Sits between strategy signals and the broker:
  1. Cross-checks price before placement
  2. Registers orders for fill monitoring
  3. Detects crossovers and reprices within slippage budget
  4. Escalates via Slack when budget is exhausted
"""

from datetime import datetime
from typing import Dict, Optional

from trading_system.utils.slack import send_crossover_alert


class ExecutionContext:
    """Tracks one order through its lifecycle."""

    def __init__(self, symbol: str, order_dict: dict, order_id: Optional[str] = None):
        self.symbol = symbol
        self.action = order_dict.get('action', '')
        self.original_order = dict(order_dict)
        self.order_id = order_id

        # Limit price tracking
        limit = (order_dict.get('limit_price')
                 or order_dict.get('price')
                 or order_dict.get('current_price')
                 or 0.0)
        self.original_limit_price = float(limit)
        self.current_limit_price = float(limit)

        # Stop price (for stop-limit orders)
        self.stop_price = float(order_dict.get('stop_price', 0))

        # Repricing state
        self.reprice_attempt = 0
        self.reprice_history = []

        # Timestamps
        self.submitted_at = datetime.now()
        self.crossover_detected_at = None

        # Status: pending | filled | cancelled | escalated
        self.status = 'pending'

    def to_dict(self) -> dict:
        """Serialize for blob logging."""
        return {
            'symbol': self.symbol,
            'action': self.action,
            'order_id': self.order_id,
            'original_limit_price': self.original_limit_price,
            'current_limit_price': self.current_limit_price,
            'stop_price': self.stop_price,
            'reprice_attempt': self.reprice_attempt,
            'reprice_history': self.reprice_history,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'crossover_detected_at': (
                self.crossover_detected_at.isoformat()
                if self.crossover_detected_at else None
            ),
            'status': self.status,
        }


class ExecutionManager:
    """Orchestrates order placement, monitoring, repricing, and escalation."""

    def __init__(self, trading_bot, price_discovery, slippage_controller,
                 fill_monitor, dry_run: bool = True):
        """
        Args:
            trading_bot: SafeCashBot instance.
            price_discovery: PriceDiscovery instance.
            slippage_controller: SlippageController instance.
            fill_monitor: FillMonitor instance.
            dry_run: If True, skip live repricing / cancellation.
        """
        self.trading_bot = trading_bot
        self.price_discovery = price_discovery
        self.slippage_controller = slippage_controller
        self.fill_monitor = fill_monitor
        self.dry_run = dry_run
        self._pending: Dict[str, ExecutionContext] = {}  # order_id -> ctx

    def submit(self, symbol: str, order_dict: dict, paired_buy: dict = None):
        """Register an order for lifecycle tracking.

        Call this *after* the order has been placed via the existing
        _execute_* methods.  The order_id is extracted from state_manager
        or passed in the order_dict.

        Also performs a pre-flight price cross-check and logs divergence.

        Args:
            symbol: Trading symbol.
            order_dict: Order details dict (action, price, quantity, etc.).
            paired_buy: Optional paired buy dict (tracked separately).
        """
        # Pre-flight price cross-check
        expected_price = (order_dict.get('limit_price')
                          or order_dict.get('price')
                          or order_dict.get('current_price'))
        if expected_price:
            check = self.price_discovery.cross_check(symbol, float(expected_price))
            if check['alert']:
                print(f"  [execution] Price divergence warning for {symbol}: "
                      f"{check['divergence_pct']:.2f}% from consensus "
                      f"(expected=${expected_price}, consensus=${check['consensus_price']:.2f})")

        # Register context — order_id may be set later by _execute_* via register_order_id
        ctx = ExecutionContext(symbol, order_dict)
        # Use a placeholder key until we have a real order_id
        placeholder_key = f"{symbol}_{ctx.action}_{ctx.submitted_at.isoformat()}"
        self._pending[placeholder_key] = ctx

        # Track paired buy separately
        if paired_buy:
            pb_ctx = ExecutionContext(symbol, paired_buy)
            pb_key = f"{symbol}_{pb_ctx.action}_{pb_ctx.submitted_at.isoformat()}"
            self._pending[pb_key] = pb_ctx

    def register_order_id(self, symbol: str, action: str, order_id: str):
        """Attach a broker order_id to a pending context.

        Called after SafeCashBot returns the order_id from placement.
        Finds the most recent unassigned context matching symbol+action.
        """
        if not order_id:
            return
        for key, ctx in reversed(list(self._pending.items())):
            if (ctx.symbol == symbol
                    and ctx.action == action
                    and ctx.order_id is None
                    and ctx.status == 'pending'):
                ctx.order_id = order_id
                # Re-key by order_id
                del self._pending[key]
                self._pending[order_id] = ctx
                return

    def check_pending_orders(self, open_orders: list, recent_orders: list):
        """Check all pending orders for fills, crossovers, and repricing.

        Called once per tick from run_once(), after order_book.update().

        Args:
            open_orders: List of currently open order dicts from broker.
            recent_orders: List of recent order dicts (7-day history).
        """
        if not self._pending:
            return

        open_ids = {o.get('order_id') for o in (open_orders or []) if o.get('order_id')}
        completed = []

        for key, ctx in list(self._pending.items()):
            if ctx.status != 'pending':
                continue

            status = self.fill_monitor.check_order(ctx, open_ids, recent_orders)

            if status == 'filled':
                ctx.status = 'filled'
                completed.append(key)
                print(f"  [execution] Filled: {ctx.symbol} {ctx.action} "
                      f"(limit=${ctx.current_limit_price:.2f})")

            elif status == 'cancelled':
                ctx.status = 'cancelled'
                completed.append(key)
                print(f"  [execution] Cancelled: {ctx.symbol} {ctx.action}")

            elif status == 'crossover':
                if ctx.crossover_detected_at is None:
                    ctx.crossover_detected_at = datetime.now()

                if self.slippage_controller.can_reprice(ctx):
                    self._reprice_order(ctx, open_orders)
                else:
                    self._escalate(ctx)
                    completed.append(key)

        # Clean up completed
        for key in completed:
            if key in self._pending:
                del self._pending[key]

    def _reprice_order(self, ctx: ExecutionContext, open_orders: list):
        """Cancel and replace an order at a repriced limit."""
        price_data = self.price_discovery.get_price(ctx.symbol)
        current_market = price_data['price']
        if current_market <= 0:
            return

        new_price = self.slippage_controller.next_price(ctx, current_market)
        old_price = ctx.current_limit_price

        print(f"  [execution] Repricing {ctx.symbol} {ctx.action}: "
              f"${old_price:.2f} -> ${new_price:.2f} "
              f"(attempt {ctx.reprice_attempt + 1}/{self.slippage_controller.max_attempts})")

        ctx.reprice_history.append({
            'from': old_price,
            'to': new_price,
            'market': current_market,
            'timestamp': datetime.now().isoformat(),
        })
        self.slippage_controller.record_reprice(ctx, new_price)

        if not self.dry_run and ctx.order_id:
            # Cancel old order
            self.trading_bot.cancel_order_by_id(ctx.order_id)

            # Place replacement
            result = None
            if ctx.action in ('stop_limit_sell',):
                new_stop = round(new_price * (ctx.stop_price / ctx.original_limit_price), 2) \
                    if ctx.original_limit_price > 0 else new_price
                result = self.trading_bot.place_stop_limit_sell_order(
                    ctx.symbol, ctx.original_order.get('quantity', 0),
                    new_stop, new_price, dry_run=False,
                )
            elif ctx.action in ('sell', 'limit_sell'):
                result = self.trading_bot.place_sell_order(
                    ctx.symbol, ctx.original_order.get('quantity', 0),
                    new_price, dry_run=False,
                )
            elif ctx.action in ('buy', 'limit_buy'):
                result = self.trading_bot.place_cash_buy_order(
                    ctx.symbol, ctx.original_order.get('quantity', 0),
                    new_price, dry_run=False,
                )

            if result and isinstance(result, dict):
                new_id = result.get('id')
                if new_id:
                    old_key = ctx.order_id
                    ctx.order_id = new_id
                    if old_key in self._pending:
                        del self._pending[old_key]
                    self._pending[new_id] = ctx

    def _escalate(self, ctx: ExecutionContext):
        """Escalate an unrepriced crossover to Slack."""
        ctx.status = 'escalated'
        price_data = self.price_discovery.get_price(ctx.symbol)
        current_price = price_data['price']
        message = self.fill_monitor.build_crossover_alert(ctx, current_price)
        print(f"  [execution] ESCALATING: {message}")
        send_crossover_alert(message)

    def get_pending_summary(self) -> dict:
        """Summary dict for blob logging."""
        contexts = []
        for ctx in self._pending.values():
            contexts.append(ctx.to_dict())
        return {
            'pending_count': len(self._pending),
            'contexts': contexts,
        }
