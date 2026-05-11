"""
Live Interactive Brokers TWS / IB Gateway client via **ib_insync**

Requires TWS or Gateway with **API** enabled: ``127.0.0.1``, port **7496** (paper) or
**7497** (live). This module does not start TWS; it only opens a socket.

Use :class:`MockIbkrClient` for offline unit tests; use this class for real read/write
against a running session.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

# ib_insync / eventkit expect a main-thread event loop; Python 3.10+ may not
# create one on import, which breaks their module init.
def _ensure_event_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)


_ensure_event_loop()

try:
    from ib_insync import IB, LimitOrder, Stock, Trade
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "ib_insync is required for IbTwsClient. Install with: pip install ib_insync"
    ) from e

Action = Literal["BUY", "SELL"]


@dataclass
class TwsClientConfig:
    host: str = "127.0.0.1"
    port: int = 7496  # paper; use 7497 for live
    client_id: int = 1
    """Must be unique per concurrent connection to the same TWS."""


class IbTwsClient:
    """
    Thin sync wrapper: account values, positions, limit orders, cancel by order id.
    Call :meth:`connect` before other methods, :meth:`disconnect` when done.
    """

    def __init__(self, config: Optional[TwsClientConfig] = None) -> None:
        self._cfg = config or TwsClientConfig()
        self._ib: Optional[IB] = None

    def connect(self) -> bool:
        """Connect to TWS. Returns True on success."""
        if self._ib is not None and self._ib.isConnected():
            return True
        ib = IB()
        ib.connect(
            self._cfg.host,
            self._cfg.port,
            clientId=self._cfg.client_id,
        )
        self._ib = ib
        return bool(ib.isConnected())

    def disconnect(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()
        self._ib = None

    @property
    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    def get_account_values(self) -> List[Dict[str, str]]:
        """``reqAccountSummary``-style rows as plain dicts (tag, value, currency, account)."""
        if not self._ib or not self._ib.isConnected():
            raise RuntimeError("not connected; call connect() first")
        out: List[Dict[str, str]] = []
        for av in self._ib.accountValues():
            out.append(
                {
                    "tag": av.tag,
                    "value": av.value,
                    "currency": av.currency,
                    "account": av.account,
                }
            )
        return out

    def get_account_summary_map(self) -> Dict[str, str]:
        """
        Map common TWS tags (e.g. ``NetLiquidation``) to value strings, first currency match.
        """
        m: Dict[str, str] = {}
        for row in self.get_account_values():
            if row.get("tag") and row.get("value") is not None and row.get("tag") not in m:
                m[str(row["tag"])] = str(row["value"])
        return m

    def get_positions(self) -> List[Dict[str, Any]]:
        """Open positions (symbol, position, avgCost, conId, exchange)."""
        if not self._ib or not self._ib.isConnected():
            raise RuntimeError("not connected; call connect() first")
        out: List[Dict[str, Any]] = []
        for p in self._ib.positions():
            c = p.contract
            out.append(
                {
                    "symbol": getattr(c, "symbol", "") or str(c),
                    "position": float(p.position),
                    "avgCost": float(p.avgCost) if p.avgCost else 0.0,
                    "conId": int(getattr(c, "conId", 0) or 0),
                    "exchange": getattr(c, "exchange", "") or "",
                    "currency": getattr(c, "currency", "") or "",
                }
            )
        return out

    def place_limit_order(
        self,
        symbol: str,
        action: Action,
        quantity: float,
        limit_price: float,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> int:
        """
        Submit a stock limit order (US equity via SMART by default). Returns **orderId**.
        For paper, use a price safely away from market if you will cancel, or you risk a fill.
        """
        if not self._ib or not self._ib.isConnected():
            raise RuntimeError("not connected; call connect() first")
        contract = Stock(symbol, exchange, currency)
        self._ib.qualifyContracts(contract)
        order = LimitOrder(str(action), float(quantity), float(limit_price))
        trade: Trade = self._ib.placeOrder(contract, order)
        return int(trade.order.orderId)

    def cancel_order(self, order_id: int) -> bool:
        """Cancel a working order by id (scans :meth:`ib.openTrades`)."""
        if not self._ib or not self._ib.isConnected():
            raise RuntimeError("not connected; call connect() first")
        for t in self._ib.openTrades():
            if t.order.orderId == order_id:
                self._ib.cancelOrder(t.contract, t.order)
                return True
        return False
