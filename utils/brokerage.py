"""
Brokerage — abstract interface for trading platforms.

The TradeExecutor depends on this interface, not on any concrete broker.
Swap RobinhoodClient for AlpacaClient or any other implementation without
touching the executor.
"""

from abc import ABC, abstractmethod


class Brokerage(ABC):

    @abstractmethod
    def place_limit_buy(self, symbol: str, quantity: float, price: float) -> dict | None:
        """Place a limit buy order. Returns broker response dict or None on failure."""
        ...

    @abstractmethod
    def place_limit_sell(self, symbol: str, quantity: float, price: float) -> dict | None:
        """Place a limit sell order."""
        ...

    @abstractmethod
    def place_stop_limit_sell(self, symbol: str, quantity: float,
                              stop_price: float, limit_price: float) -> dict | None:
        """Place a stop-limit sell order."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID. Returns True if cancelled."""
        ...

    @abstractmethod
    def get_open_orders(self) -> list:
        """Return list of open orders."""
        ...

    @abstractmethod
    def get_positions(self) -> list:
        """Return list of current positions."""
        ...

    @abstractmethod
    def get_cash_balance(self) -> dict | None:
        """Return cash balance dict with at least 'tradeable_cash' and 'buying_power'."""
        ...

    @abstractmethod
    def get_pdt_status(self) -> dict | None:
        """Return PDT status dict with 'day_trade_count' and 'flagged'."""
        ...
