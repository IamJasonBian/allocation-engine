"""
TradeTask — Command Pattern base for deferred and scheduled orders.

Each subclass implements should_execute() (Strategy Pattern) so the
Executor can ask "is this task ready?" without knowing which type
it holds.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


class TradeTask(ABC):
    """Base command. Holds trade data; does not execute itself."""

    @abstractmethod
    def should_execute(self) -> tuple[bool, str]:
        """Returns (can_execute, reason)."""
        ...


@dataclass
class ScheduledTask(TradeTask):
    """Executes on or after execute_date."""

    symbol: str
    side: str           # "buy" | "sell"
    quantity: float
    price: float
    order_type: str     # "limit" | "stop_limit" | "market"
    execute_date: date
    stop_price: float | None = None

    def should_execute(self) -> tuple[bool, str]:
        if date.today() >= self.execute_date:
            return True, "scheduled date reached"
        return False, f"scheduled for {self.execute_date}"


@dataclass
class DeferredTask(TradeTask):
    """Blocked by PDT or another gate. Retried each cycle once execute_date is reached."""

    symbol: str
    side: str
    quantity: float
    price: float
    order_type: str
    execute_date: date
    checks: list[str]       # gates to re-run: ["pdt_gate", "spread_check"]
    deferred_reason: str = ""
    stop_price: float | None = None
    retry_count: int = 0

    def should_execute(self) -> tuple[bool, str]:
        if date.today() >= self.execute_date:
            return True, "deferred date reached"
        return False, f"deferred until {self.execute_date}"
