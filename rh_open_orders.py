"""Read-only: list open stock orders across ALL accounts (cash + margin) as a table.

Places nothing. Account-aware: logs in via RobinhoodAuth, discovers each account on
the login, queries open stock orders per account, and labels every row with the
account it lives on (so margin orders are included, not hidden behind a cash filter).

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


def discover_accounts():
    """Return {account_number: type} for every account on this login.

    Seeds with the configured trade account (RH_AUTOMATED_ACCOUNT_NUMBER, which the
    /accounts/ endpoint may omit), then merges anything the endpoint reports.
    """
    accts = {}
    cfg = os.getenv("RH_AUTOMATED_ACCOUNT_NUMBER")
    if cfg:
        accts[cfg] = None
    try:
        data = r.helper.request_get("https://api.robinhood.com/accounts/", "regular")
        results = (data or {}).get("results", []) if isinstance(data, dict) else (data or [])
        for a in results:
            num = a.get("account_number")
            if num:
                accts[num] = a.get("type")
    except Exception:
        pass
    for num in list(accts):
        if accts[num] is None:
            try:
                prof = r.profiles.load_account_profile(account_number=num)
                accts[num] = (prof or {}).get("type", "?")
            except Exception:
                accts[num] = "?"
    return accts


def fetch_open_orders(account_number, acct_label):
    """Return parsed open stock orders for one account, each tagged with acct_label."""
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
            "account": acct_label,
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
    accounts = discover_accounts()

    orders = []
    for num, acct_type in accounts.items():
        label = f"{num}/{acct_type}"
        orders.extend(fetch_open_orders(num, label))

    scanned = ", ".join(f"{n}/{t}" for n, t in accounts.items())
    print(f"\nOpen orders — accounts: {scanned} — DRY RUN (read-only)\n")
    if not orders:
        print("  (no open orders)")
        return

    rows = [(
        o["account"], o["symbol"], o["side"], o["order_type"], f"{o['quantity']:g}",
        _fmt_price(o["limit_price"]), _fmt_price(o["stop_price"]), o["created_at"],
    ) for o in orders]

    headers = ("ACCOUNT", "SYMBOL", "SIDE", "TYPE", "QTY", "LIMIT", "STOP", "PLACED")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

    def line(cells):
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    print(line(headers))
    print(line(["-" * w for w in widths]))
    for row in rows:
        print(line(row))
    print(f"\n{len(orders)} open order(s) across {len(accounts)} account(s).")


if __name__ == "__main__":
    main()
