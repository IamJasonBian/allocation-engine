"""
Test PDT Gate with current live positions and fills
"""

import sys
import os
from datetime import date

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.safe_cash_bot import SafeCashBot
from trading_system.execution.pdt_gate import PDTGate


def test_pdt_guard():
    """Test PDT guard against current live positions and fills."""
    print("\n" + "="*70)
    print("PDT GATE TEST - LIVE DATA")
    print("="*70 + "\n")

    # Initialize bot and PDT gate
    bot = SafeCashBot()
    pdt_gate = PDTGate(trading_bot=bot)

    # Get recent fills (stock and options)
    print("Fetching recent fills (last 1 day)...\n")
    recent_stock_orders = bot.get_recent_orders(days=1)
    recent_option_orders = bot.get_recent_option_orders(days=1)

    stock_fills = [o for o in recent_stock_orders if o['state'] in ('filled', 'confirmed')]
    option_fills = [o for o in recent_option_orders if o['state'] == 'filled']

    print(f"Stock fills today: {len(stock_fills)}")
    print(f"Option fills today: {len(option_fills)}\n")

    # Display stock fills
    if stock_fills:
        print("STOCK FILLS TODAY:")
        for fill in stock_fills:
            print(f"  {fill['side']} {fill['symbol']} x{fill['quantity']:.0f} @ ${fill.get('average_price', 0):.2f}")
            print(f"    Filled: {fill.get('last_transaction_at', 'N/A')}")
        print()

    # Display option fills
    if option_fills:
        print("OPTION FILLS TODAY:")
        for fill in option_fills:
            for leg in fill.get('legs', []):
                print(f"  {leg['side']} {leg['position_effect']} {leg['chain_symbol']} "
                      f"${leg['strike']:.2f} {leg['option_type'].upper()} exp {leg['expiration']}")
            print(f"    Premium: ${fill['processed_premium']:.2f}")
            print(f"    Updated: {fill['updated_at']}")
        print()

    # Test PDT checks for various scenarios
    print("="*70)
    print("PDT CHECK SCENARIOS")
    print("="*70 + "\n")

    # Collect unique symbols from fills
    test_symbols = set()
    for fill in stock_fills:
        test_symbols.add(fill['symbol'])
    for fill in option_fills:
        for leg in fill.get('legs', []):
            test_symbols.add(leg['chain_symbol'])

    if not test_symbols:
        print("No fills today - testing with common symbols: BTC, SPY, QQQ\n")
        test_symbols = {'BTC', 'SPY', 'QQQ'}

    # Test each symbol for both buy and sell
    for symbol in sorted(test_symbols):
        print(f"Testing {symbol}:")

        # Test BUY
        can_buy, buy_reason = pdt_gate.can_place_order(symbol, 'buy')
        status = "✅ ALLOWED" if can_buy else "🚫 BLOCKED"
        print(f"  BUY:  {status}")
        print(f"        {buy_reason}")

        # Test SELL
        can_sell, sell_reason = pdt_gate.can_place_order(symbol, 'sell')
        status = "✅ ALLOWED" if can_sell else "🚫 BLOCKED"
        print(f"  SELL: {status}")
        print(f"        {sell_reason}")
        print()

    # Summary
    print("="*70)
    print("PDT GUARD STATUS")
    print("="*70 + "\n")

    pdt_info = bot.get_pdt_status()
    if pdt_info:
        count = pdt_info.get('day_trade_count', 0)
        flagged = pdt_info.get('flagged', False)

        print(f"Day Trades Used: {count}/3")
        print(f"PDT Flagged: {'YES ⚠️' if flagged else 'NO ✅'}")
        print(f"Remaining Day Trades: {3 - count}")

        if flagged:
            print("\n⚠️  ACCOUNT IS PDT FLAGGED")
            print("   Only position-closing sells are allowed")
        elif count >= 2:
            print("\n⚠️  WARNING: Only 1 day trade remaining")
        else:
            print(f"\n✅ Safe to trade ({3 - count} day trades remaining)")
    else:
        print("❌ Could not fetch PDT status")

    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    test_pdt_guard()
