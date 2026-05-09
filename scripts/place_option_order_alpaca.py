"""
Place a single-leg option order via Alpaca (paper-trading by default).

Defaults:
    - Paper endpoint (https://paper-api.alpaca.markets)
    - Dry run (no order is sent at all)

Usage:
    # Dry run against the paper endpoint (no order is placed)
    python scripts/place_option_order_alpaca.py XLY 2026-09-18 120 call 1 4.50 buy

    # Actually send the order to the paper endpoint
    python scripts/place_option_order_alpaca.py XLY 2026-09-18 120 call 1 4.50 buy --live

    # Take-profit close on a paper position
    python scripts/place_option_order_alpaca.py XLY 2026-09-18 120 call 1 6.75 sell --live

NOTE on flag semantics:
    --live          Send to the *paper* endpoint (i.e. actually call submit_order).
                    The Alpaca account is not yet funded, so live-money trading is
                    intentionally NOT wired up to a CLI flag.

    # TODO(once funded): add a --real-money flag that sets ALPACA_PAPER=false /
    # AlpacaBroker(paper=False) so this same CLI can hit the live endpoint.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.alpaca_broker import AlpacaBroker  # noqa: E402


USAGE = """\
Usage:
  python scripts/place_option_order_alpaca.py SYMBOL EXPIRATION STRIKE TYPE QTY PRICE SIDE [--live]

  SYMBOL      Underlying ticker (e.g. XLY)
  EXPIRATION  YYYY-MM-DD (e.g. 2026-09-18)
  STRIKE      Strike price (e.g. 120)
  TYPE        put | call
  QTY         Number of contracts (1 = 100 shares)
  PRICE       Limit price per contract share (e.g. 4.50)
  SIDE        buy  -> buy-to-open
              sell -> sell-to-close (must already hold the contract)
  --live      Actually send the order to Alpaca's PAPER endpoint.
              Default is DRY RUN (no order sent).

Examples:
  Dry-run open:     python scripts/place_option_order_alpaca.py XLY 2026-09-18 120 call 1 4.50 buy
  Paper open:       python scripts/place_option_order_alpaca.py XLY 2026-09-18 120 call 1 4.50 buy --live
  Paper take-profit: python scripts/place_option_order_alpaca.py XLY 2026-09-18 120 call 1 6.75 sell --live
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

    # paper=None lets AlpacaBroker resolve from the ALPACA_PAPER env var,
    # which defaults to true. There is intentionally no --real-money flag yet.
    broker = AlpacaBroker(paper=None)

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

    if order and isinstance(order, dict) and order.get('id'):
        print(f"\n[OK] Order ID: {order['id']}  state={order.get('status', 'N/A')}\n")
    elif dry_run:
        print("\n[OK] Dry run completed - no real order placed\n")
    else:
        print("\n[ERR] Order failed - see error above\n")


if __name__ == "__main__":
    main()
