"""
Executor — submission gateway for TradeTask objects.

Responsibilities:
  - Run PDT gate before every order
  - PDT-blocked tasks are deferred to the next trading day
  - drain_deferred() is called at the top of each engine cycle
"""
from __future__ import annotations

from datetime import date, timedelta

from trading_system.execution.trade_task import TradeTask, ScheduledTask, DeferredTask
from trading_system.execution.pdt_gate import PDTGate


def _next_trading_day(today: date) -> date:
    """Return the next weekday after today."""
    delta = 1
    while True:
        candidate = today + timedelta(days=delta)
        if candidate.weekday() < 5:  # Mon–Fri
            return candidate
        delta += 1


class Executor:
    """Submits TradeTask objects through the PDT gate; defers on block."""

    def __init__(self, trading_bot):
        self._bot = trading_bot
        self._pdt_gate = PDTGate(trading_bot=trading_bot)
        self._deferred: list[DeferredTask] = []

    def submit(self, task: TradeTask) -> dict | None:
        """Gate the task through PDT, then dispatch or defer."""
        ready, reason = task.should_execute()
        if not ready:
            print(f"  [executor] task not ready: {reason}")
            return None

        allowed, pdt_reason = self._pdt_gate.can_place_order(task.symbol, task.side)
        if not allowed:
            print(f"  [executor] PDT blocked ({pdt_reason}) — deferring {task.symbol} {task.side}")
            self._enqueue_deferred(task, pdt_reason)
            return None

        return self._dispatch(task)

    def drain_deferred(self) -> None:
        """Re-attempt deferred tasks whose execute_date has arrived."""
        if not self._deferred:
            return

        remaining = []
        for task in self._deferred:
            ready, _ = task.should_execute()
            if ready:
                print(f"  [executor] retrying deferred {task.symbol} {task.side} "
                      f"(attempt {task.retry_count + 1})")
                result = self.submit(task)
                if result is None:
                    task.retry_count += 1
                    remaining.append(task)
            else:
                remaining.append(task)
        self._deferred = remaining

    def _dispatch(self, task: TradeTask) -> dict | None:
        """Call the appropriate broker method based on task type."""
        try:
            if task.order_type == "stop_limit":
                return self._bot.place_stop_limit_sell(
                    task.symbol, task.quantity, task.stop_price, task.price
                )
            elif task.side == "buy":
                return self._bot.place_limit_buy(task.symbol, task.quantity, task.price)
            else:
                return self._bot.place_limit_sell(task.symbol, task.quantity, task.price)
        except Exception as e:
            print(f"  [executor] dispatch error for {task.symbol}: {e}")
            return None

    def _enqueue_deferred(self, task: TradeTask, reason: str) -> None:
        deferred = DeferredTask(
            symbol=task.symbol,
            side=task.side,
            quantity=task.quantity,
            price=task.price,
            order_type=task.order_type,
            execute_date=_next_trading_day(date.today()),
            deferred_reason=reason,
            stop_price=getattr(task, "stop_price", None),
        )
        self._deferred.append(deferred)
