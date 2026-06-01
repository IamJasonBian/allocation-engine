"""
Explore IWN put options chain for 2-3 week expirations
and check recent option order history.
"""

import sys
import os
import json
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import robin_stocks.robinhood as r
from utils.rh_auth import RobinhoodAuth


def explore_iwn_puts():
    auth = RobinhoodAuth()
    auth.login()

    symbol = "IWN"

    # ── Current price ───────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  {symbol} OPTIONS EXPLORER")
    print(f"{'='*70}")

    price_data = r.stocks.get_latest_price(symbol)
    current_price = float(price_data[0]) if price_data and price_data[0] else None
    print(f"\n  Current {symbol} price: ${current_price:.2f}" if current_price else f"\n  Could not get {symbol} price")

    # ── Find expiration dates 2-3 weeks out ─────────────────────────
    today = date.today()
    two_weeks = today + timedelta(weeks=2)
    three_weeks = today + timedelta(weeks=3)

    # Get available expiration dates
    print(f"\n  Looking for expirations between {two_weeks} and {three_weeks}...")

    try:
        chains = r.options.get_chains(symbol)
        if chains:
            exp_dates = chains.get('expiration_dates', [])
            print(f"  Available expirations: {exp_dates[:10]}...")

            # Filter to 2-3 week window
            target_exps = []
            for exp in exp_dates:
                exp_date = datetime.strptime(exp, '%Y-%m-%d').date()
                days_out = (exp_date - today).days
                if 10 <= days_out <= 28:  # ~2-4 weeks window
                    target_exps.append(exp)

            if not target_exps:
                # Fallback: grab the nearest expirations
                print("  No expirations in exact 2-3 week window, showing nearest...")
                target_exps = exp_dates[:3]

            print(f"  Target expirations: {target_exps}")
        else:
            print(f"  [WARN] No options chain found for {symbol}")
            auth.logout()
            return
    except Exception as e:
        print(f"  [ERR] Error fetching chain: {e}")
        auth.logout()
        return

    # ── Fetch puts for each target expiration ───────────────────────
    for exp_date in target_exps:
        print(f"\n{'='*70}")
        days_out = (datetime.strptime(exp_date, '%Y-%m-%d').date() - today).days
        print(f"  PUTS expiring {exp_date} ({days_out} days out)")
        print(f"{'='*70}")

        try:
            options = r.options.find_options_by_expiration(
                symbol,
                expirationDate=exp_date,
                optionType="put"
            )

            if not options:
                print("  No puts found for this expiration")
                continue

            # Filter to strikes near the money (within ~10%)
            relevant = []
            for opt in options:
                strike = float(opt.get('strike_price', 0))
                if current_price and abs(strike - current_price) / current_price <= 0.10:
                    bid = float(opt.get('bid_price', 0) or 0)
                    ask = float(opt.get('ask_price', 0) or 0)
                    mark = float(opt.get('mark_price', 0) or 0) if opt.get('mark_price') else (bid + ask) / 2
                    volume = int(opt.get('volume', 0) or 0)
                    oi = int(opt.get('open_interest', 0) or 0)
                    iv = opt.get('implied_volatility')
                    iv_str = f"{float(iv)*100:.1f}%" if iv else "N/A"
                    delta = opt.get('delta')
                    delta_str = f"{float(delta):.3f}" if delta else "N/A"

                    relevant.append({
                        'strike': strike,
                        'bid': bid,
                        'ask': ask,
                        'mark': mark,
                        'volume': volume,
                        'oi': oi,
                        'iv': iv_str,
                        'delta': delta_str,
                    })

            # Sort by strike descending (ATM first)
            relevant.sort(key=lambda x: x['strike'], reverse=True)

            print(f"  {'Strike':>8}  {'Bid':>7}  {'Ask':>7}  {'Mark':>7}  {'Vol':>6}  {'OI':>6}  {'IV':>7}  {'Delta':>7}")
            print(f"  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*7}")
            for o in relevant:
                print(f"  ${o['strike']:>7.2f}  ${o['bid']:>6.2f}  ${o['ask']:>6.2f}  ${o['mark']:>6.2f}  {o['volume']:>6}  {o['oi']:>6}  {o['iv']:>7}  {o['delta']:>7}")

            if not relevant:
                print(f"  No puts within 10% of ${current_price:.2f}")

        except Exception as e:
            print(f"  [ERR] Error fetching puts: {e}")

    # ── Recent option order history ─────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  RECENT OPTION ORDERS (all symbols)")
    print(f"{'='*70}")

    try:
        all_option_orders = r.orders.get_all_option_orders(info=None)

        if all_option_orders:
            # Show last 15 orders
            count = 0
            for order in all_option_orders:
                if count >= 15:
                    break

                state = order.get('state', 'N/A')
                quantity = order.get('quantity', 'N/A')
                price = order.get('price', 'N/A')
                premium = order.get('premium', 'N/A')
                direction = order.get('direction', 'N/A')
                order_type = order.get('type', 'N/A')
                created = order.get('created_at', 'N/A')
                strategy = order.get('opening_strategy') or order.get('closing_strategy') or 'N/A'

                # Parse timestamp
                try:
                    if created != 'N/A':
                        dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                        created = dt.strftime('%Y-%m-%d %H:%M')
                except Exception:
                    pass

                # Get leg details
                legs_info = []
                for leg in order.get('legs', []):
                    option_url = leg.get('option', '')
                    option_id = option_url.rstrip('/').split('/')[-1] if option_url else None
                    side = leg.get('side', '?')
                    pos_effect = leg.get('position_effect', '?')

                    instrument = {}
                    if option_id:
                        try:
                            instrument = r.options.get_option_instrument_data_by_id(option_id) or {}
                        except Exception:
                            pass

                    chain_sym = instrument.get('chain_symbol', '?')
                    strike = instrument.get('strike_price', '?')
                    exp = instrument.get('expiration_date', '?')
                    opt_type = instrument.get('type', '?')

                    legs_info.append(f"{side.upper()} {chain_sym} {strike} {opt_type} {exp}")

                legs_str = " | ".join(legs_info) if legs_info else "N/A"

                print(f"\n  [{state.upper():>10}] {created}")
                print(f"    {direction} {order_type} x{quantity} @ ${price}")
                print(f"    {legs_str}")
                print(f"    strategy: {strategy}  premium: ${premium}")

                count += 1
        else:
            print("  No option orders found")

    except Exception as e:
        print(f"  [ERR] Error fetching option orders: {e}")

    auth.logout()
    print(f"\n{'='*70}")
    print("  Done.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    explore_iwn_puts()
