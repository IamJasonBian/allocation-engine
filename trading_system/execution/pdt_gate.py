"""
PDT Gate — centralized Pattern Day Trading guard.

EVERY order must pass through this before reaching Robinhood.
Fails CLOSED: if PDT status is unavailable, block the order.
"""

import time
from datetime import datetime, date


class PDTGate:
    """Centralized PDT guard. All orders pass through before execution."""

    def __init__(self, trading_bot=None):
        self._trading_bot = trading_bot
        # Cache of same-day transactions to prevent round trips
        # Format: {symbol: {'buy': date_str, 'sell': date_str}}
        self._same_day_fills = {}

    def can_place_order(self, symbol: str, side: str) -> tuple:
        """Check if an order is safe to place from a PDT perspective.

        Args:
            symbol: Stock symbol
            side: "buy" or "sell"

        Returns:
            (allowed: bool, reason: str)
        """
        if not self._trading_bot:
            return (True, "no trading bot configured — skipping PDT check")

        try:
            time.sleep(0.5)  # Rate limit protection
            pdt_info = self._trading_bot.get_pdt_status()

            if pdt_info is None:
                return (False, "PDT status unavailable — blocking as precaution")

            flagged = pdt_info.get('flagged', False)
            day_trade_count = pdt_info.get('day_trade_count', 0)

            # Step 1: If flagged, only allow sells (position-closing)
            if flagged:
                if side.lower() == 'sell':
                    return (True, "PDT flagged — allowing position-closing sell")
                else:
                    return (False, "PDT FLAGGED — only position-closing sells allowed")

            # Step 2: Check if this would create a day trade
            would_dt = self._would_create_day_trade(symbol, side)

            if would_dt and day_trade_count >= 2:
                return (False,
                        f"PDT risk: {day_trade_count}/3 day trades used, "
                        f"this {side} on {symbol} would create another")

            # Step 3: Warning if approaching limit
            if would_dt and day_trade_count == 1:
                return (True,
                        f"PDT warning: {day_trade_count}/3 day trades used, "
                        f"this {side} on {symbol} may create another (2/3)")

            # Step 4: Would create day trade but we have capacity
            if would_dt and day_trade_count == 0:
                return (True,
                        f"⚠️  WILL CREATE DAY TRADE on {symbol} (opposite side filled today) — "
                        f"allowed ({day_trade_count}/3 used)")

            # Step 5: Safe to proceed
            return (True, f"PDT OK: {day_trade_count}/3 day trades")

        except Exception as e:
            return (False, f"PDT check error — blocking as precaution: {e}")

    def _would_create_day_trade(self, symbol: str, side: str) -> bool:
        """Check if placing this order would create a day trade (round trip).

        Returns True if there's a same-day opposite fill for this symbol.
        Checks both:
        1. Recent filled orders from Robinhood API
        2. Local cache of same-day fills (updated on successful order placement)

        Defensive: returns True on API failure (assume worst case).
        """
        try:
            # Clear old cached fills from previous days
            self._clear_old_fills()

            today = date.today().isoformat()
            opposite_side = 'sell' if side.lower() == 'buy' else 'buy'

            # Check 1: Local cache (updated when orders fill)
            if symbol in self._same_day_fills:
                if opposite_side in self._same_day_fills[symbol]:
                    cached_date = self._same_day_fills[symbol][opposite_side]
                    if cached_date == today:
                        print(f"  [pdt_gate] CACHE HIT: {symbol} {opposite_side.upper()} filled today")
                        return True

            # Check 2: Recent filled stock orders from API (last 1 day)
            recent_orders = self._trading_bot.get_recent_orders(days=1)

            for order in recent_orders:
                if order.get('symbol') != symbol:
                    continue

                # Only check filled/confirmed orders
                order_state = order.get('state', '').lower()
                if order_state not in ('filled', 'confirmed'):
                    continue

                # Check if it's the opposite side
                order_side = order.get('side', '').lower()
                if order_side != opposite_side:
                    continue

                # Check if it FILLED today (not just created)
                # Use last_transaction_at (when filled) instead of created_at
                filled_at = order.get('last_transaction_at') or order.get('updated_at')
                if isinstance(filled_at, str) and filled_at.startswith(today):
                    print(f"  [pdt_gate] API HIT: {symbol} {order_side.upper()} filled today at {filled_at}")
                    # Update cache for future checks
                    self._record_fill(symbol, order_side)
                    return True

            # Check 3: Recent filled option orders (options also count for PDT)
            try:
                recent_option_orders = self._trading_bot.get_recent_option_orders(days=1)

                for order in recent_option_orders:
                    if order.get('state', '').lower() != 'filled':
                        continue

                    # Check each leg for same-day open+close on the same underlying
                    for leg in order.get('legs', []):
                        if leg.get('chain_symbol') != symbol:
                            continue

                        position_effect = leg.get('position_effect', '').lower()
                        leg_side = leg.get('side', '').lower()

                        # Map option position_effect to stock buy/sell equivalent
                        # BUY to open = debit (like buying stock)
                        # SELL to close = credit (like selling stock)
                        option_equiv_side = None
                        if position_effect == 'open':
                            option_equiv_side = 'buy' if leg_side == 'buy' else 'sell'
                        elif position_effect == 'close':
                            option_equiv_side = 'sell' if leg_side == 'sell' else 'buy'

                        if option_equiv_side == opposite_side:
                            filled_at = order.get('updated_at', '')
                            if isinstance(filled_at, str) and filled_at.startswith(today):
                                print(f"  [pdt_gate] OPTION HIT: {symbol} option {position_effect} filled today")
                                self._record_fill(symbol, option_equiv_side)
                                return True
            except Exception as e:
                print(f"  [pdt_gate] Option order check failed (continuing): {e}")

            return False

        except Exception as e:
            print(f"  [pdt_gate] _would_create_day_trade error: {e}")
            return True  # Assume worst case on failure

    def _record_fill(self, symbol: str, side: str):
        """Record that we filled a buy/sell for this symbol today."""
        today = date.today().isoformat()
        if symbol not in self._same_day_fills:
            self._same_day_fills[symbol] = {}
        self._same_day_fills[symbol][side.lower()] = today
        print(f"  [pdt_gate] Recorded {symbol} {side.upper()} fill for today")

    def _clear_old_fills(self):
        """Remove fills from previous days to keep cache fresh."""
        today = date.today().isoformat()
        symbols_to_remove = []

        for symbol in self._same_day_fills:
            sides_to_remove = []
            for side, fill_date in self._same_day_fills[symbol].items():
                if fill_date != today:
                    sides_to_remove.append(side)

            for side in sides_to_remove:
                del self._same_day_fills[symbol][side]

            if not self._same_day_fills[symbol]:
                symbols_to_remove.append(symbol)

        for symbol in symbols_to_remove:
            del self._same_day_fills[symbol]
