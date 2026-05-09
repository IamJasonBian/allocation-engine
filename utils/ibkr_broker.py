"""
Interactive Brokers (IBKR) broker for single-leg equity option orders.

Wraps `ib_insync` to mirror the surface of `SafeCashBot.place_option_buy_limit_order` /
`place_option_sell_limit_order` so the rest of the engine can swap brokers without
changing call sites.

EXECUTION REQUIRES TWS / IB GATEWAY
-----------------------------------
IBKR has no public REST endpoint for order placement; routing goes through the
Trader Workstation (TWS) or IB Gateway desktop process listening on a local
socket. So `dry_run=True` works fully offline (no `connect()` call), but
`dry_run=False` requires TWS or IB Gateway running locally with API access
enabled. See `TRADING_SYSTEM_GUIDE.md` ("IBKR setup") for the one-time
configuration.

Defaults to PAPER trading (port 7497). Set `IBKR_PAPER=false` and use port 7496
for live.
"""

import os
import sys
from datetime import datetime

from dotenv import load_dotenv


def _load_ib_insync():
    """Lazy import so dry-run paths don't require ib_insync at all."""
    try:
        import ib_insync  # type: ignore
    except ImportError as e:
        raise ImportError(
            "ib_insync is required for IBKR live execution. "
            "Install with: pip install ib_insync"
        ) from e
    return ib_insync


class IBKRBroker:
    """
    IBKR broker wrapper for single-leg equity option orders via TWS / IB Gateway.

    Logging style mirrors SafeCashBot:
      - section dividers '=' * 70
      - [OK] / [ERR] / [WARN] prefixes
      - banner shows DRY RUN vs LIVE and PAPER vs LIVE account
    """

    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 7497,
        client_id: int = 1,
        paper: bool = True,
    ):
        load_dotenv()

        # Allow env vars to override constructor defaults so a single
        # IBKRBroker() invocation respects deployment config.
        self.host = os.getenv('IBKR_HOST', host)
        self.port = int(os.getenv('IBKR_PORT', port))
        self.client_id = int(os.getenv('IBKR_CLIENT_ID', client_id))

        env_paper = os.getenv('IBKR_PAPER')
        if env_paper is not None:
            self.paper = env_paper.strip().lower() in ('1', 'true', 'yes', 'on')
        else:
            self.paper = paper

        self._ib = None  # ib_insync.IB instance (lazy)

        print(f"\n{'=' * 70}")
        print(f"[LOCKED] IBKR BROKER ({'PAPER' if self.paper else 'LIVE'})")
        print(f"{'=' * 70}")
        print(f"   Host:      {self.host}")
        print(f"   Port:      {self.port}")
        print(f"   Client ID: {self.client_id}")
        print(f"{'=' * 70}\n")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open a TWS / IB Gateway socket connection. Errors with a clear hint."""
        ib_insync = _load_ib_insync()
        if self._ib is None:
            self._ib = ib_insync.IB()
        if self._ib.isConnected():
            return
        try:
            self._ib.connect(self.host, self.port, clientId=self.client_id)
            print(f"[OK] Connected to TWS at {self.host}:{self.port} (clientId={self.client_id})")
        except Exception as e:
            raise ConnectionError(
                f"Could not connect to TWS at {self.host}:{self.port}. "
                f"Start TWS in Paper Trading mode and ensure 'Enable ActiveX and Socket Clients' "
                f"is on under API Settings. Underlying error: {e}"
            ) from e

    def disconnect(self) -> None:
        """Close the TWS connection if open."""
        if self._ib is not None and self._ib.isConnected():
            try:
                self._ib.disconnect()
                print("[OK] Disconnected from TWS")
            except Exception as e:
                print(f"[WARN] Disconnect failed: {e}")

    # ------------------------------------------------------------------
    # Account / quote helpers
    # ------------------------------------------------------------------

    def get_account(self) -> dict:
        """Return account summary tags from TWS as a {tag: value} dict."""
        self.connect()
        try:
            summary = self._ib.accountSummary()
        except Exception as e:
            print(f"[ERR] accountSummary() failed: {e}")
            return {}
        out = {}
        for row in summary or []:
            tag = getattr(row, 'tag', None)
            value = getattr(row, 'value', None)
            if tag is not None:
                out[tag] = value
        return out

    def get_option_quote(self, symbol, expiration, strike, option_type) -> dict:
        """
        Snapshot bid/ask/last for a single equity option contract.

        expiration: 'YYYY-MM-DD'
        option_type: 'put' or 'call'
        """
        ib_insync = _load_ib_insync()
        right = self._right(option_type)
        if right is None:
            return {}

        self.connect()
        # IBKR option contracts use YYYYMMDD (no dashes) for lastTradeDateOrContractMonth.
        ib_expiry = expiration.replace('-', '')
        contract = ib_insync.Option(symbol, ib_expiry, float(strike), right, exchange='SMART', currency='USD')
        try:
            self._ib.qualifyContracts(contract)
            ticker = self._ib.reqMktData(contract, '', False, False)
            self._ib.sleep(1.0)  # let the snapshot populate
            quote = {
                'symbol': symbol,
                'expiration': expiration,
                'strike': float(strike),
                'option_type': option_type.lower(),
                'bid': getattr(ticker, 'bid', None),
                'ask': getattr(ticker, 'ask', None),
                'last': getattr(ticker, 'last', None),
            }
            self._ib.cancelMktData(contract)
            return quote
        except Exception as e:
            print(f"[ERR] get_option_quote failed: {e}")
            return {}

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_option_buy_limit_order(
        self,
        symbol,
        expiration,
        strike,
        option_type,
        quantity,
        price,
        dry_run: bool = True,
        time_in_force: str = 'GTC',
    ) -> dict:
        """Single-leg limit BUY-TO-OPEN on an equity option via IBKR."""
        return self._place_option_order(
            action='BUY',
            symbol=symbol,
            expiration=expiration,
            strike=strike,
            option_type=option_type,
            quantity=quantity,
            price=price,
            dry_run=dry_run,
            time_in_force=time_in_force,
            label='OPTION BUY ORDER',
            money_label='Total Debit',
        )

    def place_option_sell_limit_order(
        self,
        symbol,
        expiration,
        strike,
        option_type,
        quantity,
        price,
        dry_run: bool = True,
        time_in_force: str = 'GTC',
    ) -> dict:
        """Single-leg limit SELL-TO-CLOSE on an equity option via IBKR."""
        return self._place_option_order(
            action='SELL',
            symbol=symbol,
            expiration=expiration,
            strike=strike,
            option_type=option_type,
            quantity=quantity,
            price=price,
            dry_run=dry_run,
            time_in_force=time_in_force,
            label='OPTION SELL (CLOSE) ORDER',
            money_label='Total Credit',
        )

    def _place_option_order(
        self,
        *,
        action: str,
        symbol,
        expiration,
        strike,
        option_type,
        quantity,
        price,
        dry_run: bool,
        time_in_force: str,
        label: str,
        money_label: str,
    ) -> dict:
        option_type = option_type.lower()
        right = self._right(option_type)
        if right is None:
            return {'status': 'rejected', 'reason': f"option_type must be 'put' or 'call', got {option_type!r}"}

        if quantity is None or int(quantity) <= 0:
            return {'status': 'rejected', 'reason': f'quantity must be positive, got {quantity!r}'}
        if price is None or float(price) <= 0:
            return {'status': 'rejected', 'reason': f'price must be positive, got {price!r}'}

        notional = int(quantity) * float(price) * 100
        mode = 'DRY RUN' if dry_run else ('LIVE-PAPER' if self.paper else 'LIVE-REAL')

        print(f"\n{'=' * 70}")
        print(f"{label} - {mode}  (IBKR)")
        print(f"{'=' * 70}")
        print(f"   Broker:   IBKR ({'paper' if self.paper else 'live'}) {self.host}:{self.port}")
        print(f"   Contract: {symbol} {expiration} ${float(strike):.2f} {option_type.upper()}")
        print(f"   Quantity: {int(quantity)} contract(s) ({int(quantity) * 100} shares)")
        print(f"   Limit Price: ${float(price):.2f} per share")
        print(f"   {money_label}: ${notional:.2f}")
        print(f"   TIF: {time_in_force.upper()}")

        if dry_run:
            print("\n[WARN]  DRY RUN MODE - Order not executed (no TWS connection attempted)")
            print("   To execute real orders, call with dry_run=False (requires TWS / IB Gateway running)")
            print(f"{'=' * 70}\n")
            return {
                'status': 'dry_run',
                'broker': 'ibkr',
                'action': action,
                'symbol': symbol,
                'expiration': expiration,
                'strike': float(strike),
                'option_type': option_type,
                'quantity': int(quantity),
                'price': float(price),
                'time_in_force': time_in_force.upper(),
                'paper': self.paper,
            }

        ib_insync = _load_ib_insync()
        try:
            self.connect()
        except ConnectionError as e:
            print(f"\n[ERR] {e}")
            print(f"{'=' * 70}\n")
            return {'status': 'error', 'reason': str(e)}

        ib_expiry = expiration.replace('-', '')
        contract = ib_insync.Option(symbol, ib_expiry, float(strike), right, exchange='SMART', currency='USD')
        try:
            self._ib.qualifyContracts(contract)
        except Exception as e:
            print(f"[WARN] qualifyContracts failed (proceeding): {e}")

        order = ib_insync.LimitOrder(action, int(quantity), float(price))
        order.tif = time_in_force.upper()

        try:
            print(f"\nExecuting option {action} order on IBKR...")
            trade = self._ib.placeOrder(contract, order)
            order_id = getattr(getattr(trade, 'order', None), 'orderId', None)
            order_status = getattr(getattr(trade, 'orderStatus', None), 'status', None)
            print("[OK] IBKR option order placed!")
            print(f"   Order ID: {order_id}")
            print(f"   State:    {order_status or 'N/A'}")
            print(f"{'=' * 70}\n")
            return {
                'status': 'submitted',
                'broker': 'ibkr',
                'order_id': order_id,
                'order_status': order_status,
                'action': action,
                'symbol': symbol,
                'expiration': expiration,
                'strike': float(strike),
                'option_type': option_type,
                'quantity': int(quantity),
                'price': float(price),
                'time_in_force': time_in_force.upper(),
                'paper': self.paper,
            }
        except Exception as e:
            print(f"[ERR] IBKR option {action} order failed: {e}")
            print(f"{'=' * 70}\n")
            return {'status': 'error', 'reason': str(e)}

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_open_option_positions(self) -> list:
        """Return open option positions reported by TWS as a list of dicts."""
        self.connect()
        try:
            positions = self._ib.positions()
        except Exception as e:
            print(f"[ERR] positions() failed: {e}")
            return []

        out = []
        for p in positions or []:
            contract = getattr(p, 'contract', None)
            sec_type = getattr(contract, 'secType', None) if contract else None
            if sec_type != 'OPT':
                continue
            out.append({
                'symbol': getattr(contract, 'symbol', None),
                'expiration': getattr(contract, 'lastTradeDateOrContractMonth', None),
                'strike': getattr(contract, 'strike', None),
                'right': getattr(contract, 'right', None),
                'quantity': getattr(p, 'position', None),
                'avg_cost': getattr(p, 'avgCost', None),
                'account': getattr(p, 'account', None),
            })
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _right(option_type) -> str:
        ot = (option_type or '').lower()
        if ot == 'call':
            return 'C'
        if ot == 'put':
            return 'P'
        print(f"[ERR] option_type must be 'put' or 'call', got {option_type!r}")
        return None


def main():
    """Smoke entrypoint: print the banner only, no TWS connection."""
    print("\nIBKR Broker (utils/ibkr_broker.py)")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    IBKRBroker()
    print("Note: dry-run paths require no TWS. Use --live in scripts to attempt a real connect.\n")


if __name__ == "__main__":
    main()
