"""Read-only: list open stock orders for the configured account as a table.

Places nothing. Account-scoped: logs in via RobinhoodAuth (RH_ACTIVE_ACCOUNT)
and queries open stock orders for RH_AUTOMATED_ACCOUNT_NUMBER.

Self-locating: chdir/​sys.path to its own dir so it runs from any cwd
(the Telegram bot runs Claude with cwd=/).
"""
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)                 # so load_dotenv() finds .env
sys.path.insert(0, HERE)       # so `from utils...` resolves

import robin_stocks.robinhood as r  # noqa: E402
from utils.rh_auth import RobinhoodAuth  # noqa: E402


def _fmt_price(v):
    return f"${float(v):,.2f}" if v not in (None, "", "N/A") else "—"


def _fmt_ts(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)[:16]


def fetch_open_orders(account_number):
    """Return parsed open stock orders for the given account."""
    raw = r.orders.get_all_open_stock_orders(account_number=account_number) or []
    out = []
    for o in raw:
        symbol = o.get("symbol", "N/A")
        if symbol == "N/A" and o.get("instrument_id"):
            try:
                inst = r.stocks.get_instrument_by_url(
                    f"https://api.robinhood.com/instruments/{o['instrument_id']}/"
                )
                symbol = (inst or {}).get("symbol", "N/A")
            except Exception:
                pass
        order_type = o.get("type", "N/A")
        trigger = o.get("trigger", "immediate")
        if trigger == "stop" and order_type == "limit":
            desc = "Stop Limit"
        elif trigger == "stop":
            desc = "Stop Loss"
        elif order_type == "limit":
            desc = "Limit"
        else:
            desc = "Market"
        side = o.get("side", "N/A")
        out.append({
            "symbol": symbol,
            "side": side.upper() if side != "N/A" else "N/A",
            "order_type": desc,
            "quantity": float(o.get("quantity", 0)),
            "limit_price": o.get("price"),
            "stop_price": o.get("stop_price"),
            "created_at": _fmt_ts(o.get("created_at", "N/A")),
        })
    return out


def main():
    RobinhoodAuth().login()  # cached session
    account_number = os.getenv("RH_AUTOMATED_ACCOUNT_NUMBER")
    orders = fetch_open_orders(account_number)

    print(f"\nOpen orders — account {account_number} — DRY RUN (read-only)\n")
    if not orders:
        print("  (no open orders)")
        return

    rows = [(
        o["symbol"], o["side"], o["order_type"], f"{o['quantity']:g}",
        _fmt_price(o["limit_price"]), _fmt_price(o["stop_price"]), o["created_at"],
    ) for o in orders]

    headers = ("SYMBOL", "SIDE", "TYPE", "QTY", "LIMIT", "STOP", "PLACED")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

    def line(cells):
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    print(line(headers))
    print(line(["-" * w for w in widths]))
    for row in rows:
        print(line(row))
    print(f"\n{len(orders)} open order(s).")


if __name__ == "__main__":
    main()
