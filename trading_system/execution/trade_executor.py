"""
Executor — execution quality layer + deferred order queue.

Sits between the strategy (which creates TradeTasks) and the Brokerage
(which talks to the broker API). Owns:
  - PDT gate, spread check, price optimizer, fill logger
  - In-memory deferred queue for PDT-blocked orders

The Brokerage is injected (Dependency Inversion), so the Executor never
imports robin_stocks or any platform-specific code.
"""

from datetime import date, timedelta
from typing import Optional

from trading_system.execution.trade_task import TradeTask, DeferredTask


def _next_trading_day(from_date: date) -> date:
    d = from_date + timedelta(days=1)
    while d.weekday() >= 5:  # skip Saturday=5, Sunday=6
        d += timedelta(days=1)
    return d


class Executor:
    """
    Processes TradeTasks. Applies pre-flight checks, dispatches to the
    Brokerage, and defers PDT-blocked orders to the next trading day.
    """

    def __init__(self, brokerage, pdt_gate=None, spread_checker=None,
                 price_optimizer=None, fill_logger=None, fill_auditor=None):
        self._brokerage = brokerage
        self._pdt_gate = pdt_gate
        self._spread_checker = spread_checker
        self._price_optimizer = price_optimizer
        self._fill_logger = fill_logger
        self._fill_auditor = fill_auditor
        self._deferred_queue: list[DeferredTask] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(self, task: TradeTask) -> Optional[dict]:
        """
        Try to execute a task now.
        Returns broker response on success, None if blocked or deferred.
        """
        can_execute, reason = task.should_execute()
        if not can_execute:
            self._enqueue_deferred(task, reason)
            return None

        if self._pdt_gate:
            allowed, pdt_reason = self._pdt_gate.can_place_order(task.symbol, task.side)
            print(f"   PDT Gate: {pdt_reason}")
            if not allowed:
                self._enqueue_deferred(task, pdt_reason)
                return None

        if self._spread_checker:
            spread_info = self._spread_checker.check_spread(task.symbol)
            if spread_info and not spread_info.get('is_acceptable', True):
                print(f"   Spread Check: BLOCKED — {spread_info.get('reason', '')}")
                return None
            if spread_info and spread_info.get('should_wait'):
                print(f"   Spread Check: WARNING — {spread_info.get('reason', '')}")

        return self._dispatch(task)

    def drain_deferred(self):
        """
        Re-attempt deferred tasks whose execute_date has been reached.
        Call at the top of each engine cycle.
        """
        ready = [t for t in self._deferred_queue if t.should_execute()[0]]
        submitted = []
        for task in ready:
            print(f"   [executor] Retrying deferred {task.symbol} {task.side} "
                  f"(was: {task.deferred_reason})")
            result = self.submit(task)
            if result:
                submitted.append(task)
        for task in submitted:
            self._deferred_queue.remove(task)

    def get_deferred(self) -> list[DeferredTask]:
        """Read-only view of the current deferred queue."""
        return list(self._deferred_queue)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _dispatch(self, task: TradeTask) -> Optional[dict]:
        """Send the task to the brokerage after pre-flight passes."""
        submission_id = None
        bid, ask = None, None

        if self._fill_auditor:
            nbbo = self._fill_auditor.get_nbbo_now(task.symbol)
            if nbbo:
                bid = nbbo.get('bid')
                ask = nbbo.get('ask')
                mid = nbbo.get('mid')
                if mid and mid > 0:
                    print(f"   NBBO: bid=${bid:.2f} ask=${ask:.2f} mid=${mid:.2f}")

        if self._fill_logger:
            submission_id = self._fill_logger.log_submission(
                task.symbol, task.side, task.price, bid, ask)

        try:
            if task.side == 'buy':
                result = self._brokerage.place_limit_buy(
                    task.symbol, task.quantity, task.price)
            elif task.order_type == 'stop_limit':
                result = self._brokerage.place_stop_limit_sell(
                    task.symbol, task.quantity, task.stop_price, task.price)
            else:
                result = self._brokerage.place_limit_sell(
                    task.symbol, task.quantity, task.price)
        except Exception as e:
            if self._fill_logger and submission_id:
                self._fill_logger.log_cancel(submission_id, reason=str(e))
            raise

        if result and self._fill_logger and submission_id:
            order_id = result.get('id') if isinstance(result, dict) else None
            if order_id:
                self._fill_logger.log_fill(submission_id, task.price)

        return result

    def _enqueue_deferred(self, task: TradeTask, reason: str):
        next_day = _next_trading_day(date.today())
        retry_count = getattr(task, 'retry_count', 0) + 1
        deferred = DeferredTask(
            symbol=task.symbol,
            side=task.side,
            quantity=task.quantity,
            price=task.price,
            order_type=task.order_type,
            stop_price=getattr(task, 'stop_price', None),
            execute_date=next_day,
            checks=["pdt_gate"],
            deferred_reason=reason,
            retry_count=retry_count,
        )
        print(f"   [executor] Deferred {task.symbol} {task.side} → {next_day} | {reason}")
        self._deferred_queue.append(deferred)
