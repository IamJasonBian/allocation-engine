"""
Trade task classes — Command + Strategy pattern.

TradeTask   : base ABC, defines should_execute()
ScheduledTask: executes on/after execute_date
DeferredTask : blocked by a gate (e.g. PDT), retried next trading day
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


class TradeTask(ABC):
    @abstractmethod
    def should_execute(self) -> tuple[bool, str]: ...


@dataclass
class ScheduledTask(TradeTask):
    symbol: str
    side: str          # "buy" | "sell"
    quantity: float
    price: float
    order_type: str    # "limit" | "stop_limit"
    execute_date: date
    stop_price: float | None = None

    def should_execute(self) -> tuple[bool, str]:
        if date.today() >= self.execute_date:
            return True, "scheduled date reached"
        return False, f"scheduled for {self.execute_date}"


@dataclass
class DeferredTask(TradeTask):
    symbol: str
    side: str
    quantity: float
    price: float
    order_type: str
    execute_date: date
    deferred_reason: str = ""
    stop_price: float | None = None
    retry_count: int = 0

    def should_execute(self) -> tuple[bool, str]:
        if date.today() >= self.execute_date:
            return True, "deferred date reached"
        return False, f"deferred until {self.execute_date}"
