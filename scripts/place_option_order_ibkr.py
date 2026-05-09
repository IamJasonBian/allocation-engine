"""
Place a single-leg option order against IBKR via TWS / IB Gateway.

Dry runs work fully offline - no TWS connection is attempted. Live orders require
TWS or IB Gateway running locally with the API socket enabled (default port 7497
for paper). See `TRADING_SYSTEM_GUIDE.md` ("IBKR setup") for the one-time setup.

Usage:
    # Dry run (no TWS connection)
    python scripts/place_option_order_ibkr.py XLY 2026-09-18 120 call 1 4.50 buy

    # Live (requires TWS/IB Gateway running on 127.0.0.1:7497)
    python scripts/place_option_order_ibkr.py XLY 2026-09-18 120 call 1 4.50 buy --live
    python scripts/place_option_order_ibkr.py XLY 2026-09-18 120 call 1 6.75 sell --live
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.ibkr_broker import IBKRBroker  # noqa: E402


USAGE = """\
Usage:
  python scripts/place_option_order_ibkr.py SYMBOL EXPIRATION STRIKE TYPE QTY PRICE SIDE [--live]

  SYMBOL      Underlying ticker (e.g. XLY)
  EXPIRATION  YYYY-MM-DD (e.g. 2026-09-18)
  STRIKE      Strike price (e.g. 120)
  TYPE        put | call
  QTY         Number of contracts (1 = 100 shares)
  PRICE       Limit price per contract share (e.g. 4.50)
  SIDE        buy  -> buy-to-open
              sell -> sell-to-close (must already hold the contract)
  --live      Send to broker. Default is DRY RUN (no TWS connection).

Examples:
  Dry-run open:    python scripts/place_option_order_ibkr.py XLY 2026-09-18 120 call 1 4.50 buy
  Live open:       python scripts/place_option_order_ibkr.py XLY 2026-09-18 120 call 1 4.50 buy --live
  Live close:      python scripts/place_option_order_ibkr.py XLY 2026-09-18 120 call 1 6.75 sell --live

Live orders require TWS or IB Gateway running locally with API access enabled.
Default paper port is 7497 (live is 7496). Override via IBKR_HOST / IBKR_PORT.
"""


def main():
    if len(sys.argv) < 8:
        print(USAGE)
        sys.exit(1)
    try:
        symbol = sys.argv[1].upper()
        expiration = sys.argv[2]
        strike = float(sys.argv[3])
        option_type = sys.argv[4].lower()
        quantity = int(sys.argv[5])
        price = float(sys.argv[6])
        side = sys.argv[7].lower()
        dry_run = '--live' not in sys.argv
    except (ValueError, IndexError) as e:
        print(f"[ERR] Could not parse args: {e}\n")
        print(USAGE)
        sys.exit(1)

    if option_type not in ('put', 'call'):
        print(f"[ERR] TYPE must be 'put' or 'call', got {option_type!r}")
        sys.exit(1)
    if side not in ('buy', 'sell'):
        print(f"[ERR] SIDE must be 'buy' or 'sell', got {side!r}")
        sys.exit(1)
    if quantity <= 0:
        print("[ERR] QTY must be positive")
        sys.exit(1)
    if price <= 0:
        print("[ERR] PRICE must be positive")
        sys.exit(1)

    broker = IBKRBroker()
    try:
        try:
            if side == 'buy':
                order = broker.place_option_buy_limit_order(
                    symbol=symbol,
                    expiration=expiration,
                    strike=strike,
                    option_type=option_type,
                    quantity=quantity,
                    price=price,
                    dry_run=dry_run,
                )
            else:
                order = broker.place_option_sell_limit_order(
                    symbol=symbol,
                    expiration=expiration,
                    strike=strike,
                    option_type=option_type,
                    quantity=quantity,
                    price=price,
                    dry_run=dry_run,
                )
        except ImportError as e:
            print(f"\n[ERR] {e}\n")
            sys.exit(2)

        if isinstance(order, dict):
            status = order.get('status')
            if status == 'submitted':
                print(f"\n[OK] Order ID: {order.get('order_id')}  state={order.get('order_status', 'N/A')}\n")
            elif status == 'dry_run':
                print("\n[OK] Dry run completed - no real order placed (no TWS connection attempted)\n")
            elif status == 'error':
                print(f"\n[ERR] Order failed: {order.get('reason')}\n")
                sys.exit(2)
            elif status == 'rejected':
                print(f"\n[ERR] Order rejected: {order.get('reason')}\n")
                sys.exit(2)
            else:
                print(f"\n[ERR] Unknown order status: {order}\n")
                sys.exit(2)
        else:
            print(f"\n[ERR] Unexpected response: {order}\n")
            sys.exit(2)
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
