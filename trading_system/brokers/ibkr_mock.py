"""
In-memory Interactive Brokers–style client for tests and local simulation.

Mimics common TWS / IB Gateway account tags and order handles without ibapi,
sockets, or credentials. Not for production trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional


Action = Literal["BUY", "SELL"]


@dataclass
class _PositionState:
    quantity: float
    avg_cost: float


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class MockIbkrClient:
    """
    Stand-in for IBKR account queries and order submission.

    - Account tags use the same names many TWS API examples use (string values).
    - Market orders optionally auto-fill at ``last_prices[symbol]`` (default on).
    - Cash and positions update in-memory only.
    """

    def __init__(
        self,
        *,
        initial_cash: float = 1_000_000.0,
        last_prices: Optional[Dict[str, float]] = None,
        auto_fill_market: bool = True,
    ) -> None:
        self._connected = False
        self._cash = float(initial_cash)
        self._positions: Dict[str, _PositionState] = {}
        self._last_prices: Dict[str, float] = dict(last_prices or {"SPY": 450.0, "QQQ": 380.0})
        self._orders: Dict[int, Dict[str, Any]] = {}
        self._next_order_id = 1
        self._auto_fill_market = auto_fill_market

    # --- connection (no-op for mock; matches typical connect/disconnect flow) ---

    def connect(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1) -> bool:
        """Pretend to open a TWS session. Args are accepted for API similarity only."""
        _ = (host, port, client_id)
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # --- quotes (test hooks) ---

    def set_last_price(self, symbol: str, price: float) -> None:
        """Seed or update the last trade price used for marks and market fills."""
        self._last_prices[symbol.upper()] = float(price)

    def get_last_price(self, symbol: str) -> Optional[float]:
        return self._last_prices.get(symbol.upper())

    # --- account / portfolio (IB-style tag -> value strings) ---

    def get_account_summary(self) -> Dict[str, str]:
        """Return selected account values in the style of ``reqAccountSummary`` tags."""
        nl = self._net_liquidation()
        return {
            "NetLiquidation": f"{nl:.2f}",
            "TotalCashValue": f"{self._cash:.2f}",
            "BuyingPower": f"{self._cash:.2f}",
            "GrossPositionValue": f"{self._gross_position_value():.2f}",
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        """One row per symbol, similar to ``position()`` callback fields."""
        rows: List[Dict[str, Any]] = []
        for sym, st in self._positions.items():
            px = self._mark_price(sym)
            mkt = st.quantity * px
            rows.append(
                {
                    "symbol": sym,
                    "position": st.quantity,
                    "avgCost": st.avg_cost,
                    "marketValue": mkt,
                    "unrealizedPNL": mkt - st.quantity * st.avg_cost,
                }
            )
        return rows

    # --- orders ---

    def place_market_order(self, symbol: str, action: Action, quantity: float) -> Dict[str, Any]:
        """Submit a MKT order; may auto-fill if ``auto_fill_market`` is True."""
        oid = self._next_id()
        sym = symbol.upper()
        rec: Dict[str, Any] = {
            "orderId": oid,
            "symbol": sym,
            "action": action,
            "orderType": "MKT",
            "totalQuantity": quantity,
            "status": "Submitted",
            "submittedAt": _utc_now_iso(),
        }
        self._orders[oid] = rec
        if self._auto_fill_market:
            self._fill_market(oid, sym, action, quantity)
        return dict(rec)

    def place_limit_order(
        self,
        symbol: str,
        action: Action,
        quantity: float,
        limit_price: float,
    ) -> Dict[str, Any]:
        """Submit an LMT order (left open until cancelled or you extend the mock)."""
        oid = self._next_id()
        sym = symbol.upper()
        rec: Dict[str, Any] = {
            "orderId": oid,
            "symbol": sym,
            "action": action,
            "orderType": "LMT",
            "lmtPrice": float(limit_price),
            "totalQuantity": quantity,
            "status": "PreSubmitted",
            "submittedAt": _utc_now_iso(),
        }
        self._orders[oid] = rec
        return rec

    def cancel_order(self, order_id: int) -> bool:
        rec = self._orders.get(order_id)
        if not rec:
            return False
        if rec.get("status") in ("Cancelled", "Filled", "ApiCancelled"):
            return False
        rec["status"] = "Cancelled"
        return True

    def get_open_orders(self) -> List[Dict[str, Any]]:
        return [dict(o) for o in self._orders.values() if o.get("status") not in ("Filled", "Cancelled")]

    # --- internal ---

    def _next_id(self) -> int:
        oid = self._next_order_id
        self._next_order_id += 1
        return oid

    def _mark_price(self, symbol: str) -> float:
        return float(self._last_prices.get(symbol, 0.0))

    def _gross_position_value(self) -> float:
        total = 0.0
        for sym, st in self._positions.items():
            total += abs(st.quantity) * self._mark_price(sym)
        return total

    def _net_liquidation(self) -> float:
        return self._cash + sum(
            st.quantity * self._mark_price(sym) for sym, st in self._positions.items()
        )

    def _fill_market(self, order_id: int, symbol: str, action: Action, quantity: float) -> None:
        rec = self._orders[order_id]
        px = self._mark_price(symbol)
        if px <= 0:
            # Avoid bogus fills if no price — leave submitted
            rec["status"] = "Inactive"
            rec["note"] = "no last price for symbol"
            return

        notional = px * quantity
        if action == "BUY":
            if notional > self._cash + 1e-6:
                rec["status"] = "Cancelled"
                rec["note"] = "insufficient cash"
                return
            self._cash -= notional
            self._add_position(symbol, quantity, px)
        else:  # SELL
            pos = self._positions.get(symbol)
            if not pos or pos.quantity + 1e-9 < quantity:
                rec["status"] = "Cancelled"
                rec["note"] = "insufficient position"
                return
            self._cash += notional
            self._add_position(symbol, -quantity, px)

        rec["status"] = "Filled"
        rec["avgFillPrice"] = px
        rec["filledAt"] = _utc_now_iso()

    def _add_position(self, symbol: str, delta_qty: float, trade_price: float) -> None:
        st = self._positions.get(symbol)
        if st is None:
            if abs(delta_qty) < 1e-12:
                return
            self._positions[symbol] = _PositionState(quantity=delta_qty, avg_cost=trade_price)
            return
        old_q, old_a = st.quantity, st.avg_cost
        new_q = old_q + delta_qty
        if abs(new_q) < 1e-9:
            del self._positions[symbol]
            return
        if old_q > 0 and new_q > 0 and new_q < old_q:
            st.quantity, st.avg_cost = new_q, old_a
        elif old_q < 0 and new_q < 0 and new_q > old_q:
            st.quantity, st.avg_cost = new_q, old_a
        elif (old_q > 0 and new_q < 0) or (old_q < 0 and new_q > 0):
            st.quantity, st.avg_cost = new_q, trade_price
        else:
            st.avg_cost = (old_q * old_a + delta_qty * trade_price) / new_q
            st.quantity = new_q
