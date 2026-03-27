"""
RobinhoodClient — concrete Brokerage implementation for Robinhood.

Thin wrapper around robin_stocks. Only responsible for:
  - Account locking and verification
  - Reading account state (positions, orders, balances, PDT)
  - Placing and cancelling orders via robin_stocks

Execution quality logic (PDT gate, spread check, deferred queue) lives in
TradeExecutor, which injects this class via the Brokerage interface.
"""

import math
import os
import sys
from datetime import datetime, date

import robin_stocks.robinhood as r
from dotenv import load_dotenv

from .rh_auth import RobinhoodAuth
from .brokerage import Brokerage


class RobinhoodClient(Brokerage):
    """Robinhood broker implementation. Cash-only, locked to one account."""

    def __init__(self):
        load_dotenv()

        self.account_number = os.getenv('RH_AUTOMATED_ACCOUNT_NUMBER')

        if not self.account_number:
            print("[ERR] ERROR: RH_AUTOMATED_ACCOUNT_NUMBER not set in .env")
            sys.exit(1)

        if self.account_number != "490706777":
            print(f"[WARN] WARNING: Expected account 490706777, got {self.account_number}")
            if sys.stdin.isatty():
                response = input("Continue anyway? (yes/no): ")
                if response.lower() != 'yes':
                    sys.exit(1)
            else:
                print("   Non-interactive mode: proceeding with configured account")

        self.auth = RobinhoodAuth()
        self.auth.login()
        self._verify_account()

    # ------------------------------------------------------------------
    # Brokerage interface — order placement
    # ------------------------------------------------------------------

    def place_limit_buy(self, symbol: str, quantity: float, price: float) -> dict | None:
        """Place a limit buy order. Validates buying power first."""
        print(f"\n{'='*70}")
        print(f"BUY ORDER - LIVE")
        print(f"{'='*70}")
        print(f"   Account: {self.account_number}")
        print(f"   Symbol: {symbol}  Qty: {quantity}  Limit: ${price:.2f}")
        print(f"   Total Cost: ${quantity * price:.2f}")

        is_valid, reason = self.validate_buy_order(symbol, quantity, price)
        print(f"   Validation: {'[OK] ' + reason if is_valid else '[ERR] ' + reason}")
        if not is_valid:
            print(f"{'='*70}\n")
            return None

        try:
            print("\n   Executing order...")
            order = r.orders.order_buy_limit(
                symbol=symbol,
                quantity=quantity,
                limitPrice=price,
                account_number=self.account_number,
            )
            order_id = order.get('id') if isinstance(order, dict) else None
            if order_id:
                print(f"   [OK] Order placed: {order_id} | state: {order.get('state', 'N/A')}")
                print(f"{'='*70}\n")
                return order

            # Broker rejected
            detail = None
            if isinstance(order, dict):
                detail = order.get('detail') or order.get('non_field_errors') or order.get('message')
            print(f"   [ERR] Buy order failed: {detail or order}")

            # PDT retry: cancel conflicting buy and resubmit once
            if isinstance(detail, str) and 'pdt' in detail.lower():
                print(f"   PDT hit — cancelling existing buy(s) for {symbol} and retrying...")
                cancelled_qty = self._cancel_existing_orders(symbol, 'buy')
                if cancelled_qty:
                    retry = r.orders.order_buy_limit(
                        symbol=symbol,
                        quantity=quantity,
                        limitPrice=price,
                        account_number=self.account_number,
                    )
                    retry_id = retry.get('id') if isinstance(retry, dict) else None
                    if retry_id:
                        print(f"   [OK] Retry placed: {retry_id}")
                        print(f"{'='*70}\n")
                        return retry
                    retry_detail = None
                    if isinstance(retry, dict):
                        retry_detail = retry.get('detail') or retry.get('non_field_errors')
                    print(f"   Retry failed: {retry_detail or retry}")

            print(f"{'='*70}\n")
            return order

        except Exception as e:
            print(f"   [ERR] Order failed: {e}")
            print(f"{'='*70}\n")
            return None

    def place_limit_sell(self, symbol: str, quantity: float, price: float) -> dict | None:
        """Place a limit sell order. Validates position first."""
        print(f"\n{'='*70}")
        print(f"SELL ORDER - LIVE")
        print(f"{'='*70}")
        print(f"   Account: {self.account_number}")
        print(f"   Symbol: {symbol}  Qty: {quantity}  Limit: ${price:.2f}")

        positions = self.get_positions()
        position = next((p for p in positions if p['symbol'] == symbol), None)
        if not position:
            print(f"   [ERR] No position in {symbol}")
            print(f"{'='*70}\n")
            return None
        if quantity > position['quantity']:
            print(f"   [ERR] Insufficient shares (have {position['quantity']})")
            print(f"{'='*70}\n")
            return None

        try:
            print("\n   Executing order...")
            order = r.orders.order_sell_limit(
                symbol=symbol,
                quantity=quantity,
                limitPrice=price,
                account_number=self.account_number,
            )
            order_id = order.get('id', 'N/A') if isinstance(order, dict) else 'N/A'
            print(f"   [OK] Order placed: {order_id} | state: {order.get('state', 'N/A') if isinstance(order, dict) else 'N/A'}")
            print(f"{'='*70}\n")
            return order

        except Exception as e:
            print(f"   [ERR] Order failed: {e}")
            print(f"{'='*70}\n")
            return None

    def place_stop_limit_sell(self, symbol: str, quantity: float,
                              stop_price: float, limit_price: float) -> dict | None:
        """Place a stop-limit sell order. Validates position first."""
        print(f"\n{'='*70}")
        print(f"STOP-LIMIT SELL ORDER - LIVE")
        print(f"{'='*70}")
        print(f"   Account: {self.account_number}")
        print(f"   Symbol: {symbol}  Qty: {quantity}  Stop: ${stop_price:.2f}  Limit: ${limit_price:.2f}")

        positions = self.get_positions()
        position = next((p for p in positions if p['symbol'] == symbol), None)
        if not position:
            print(f"   [ERR] No position in {symbol}")
            print(f"{'='*70}\n")
            return None
        if quantity > position['quantity']:
            print(f"   [ERR] Insufficient shares (have {position['quantity']})")
            print(f"{'='*70}\n")
            return None

        # Prevent zero-fills on gap-through: ensure stop != limit
        if abs(stop_price - limit_price) < 0.01:
            limit_price = round(stop_price * 0.995, 2)
            print(f"   Stop=Limit buffer applied: limit adjusted to ${limit_price:.2f}")

        try:
            print("\n   Executing order...")
            order = r.orders.order_sell_stop_limit(
                symbol=symbol,
                quantity=quantity,
                limitPrice=limit_price,
                stopPrice=stop_price,
                account_number=self.account_number,
                timeInForce='gtc',
            )
            order_id = order.get('id') if isinstance(order, dict) else None
            if order_id:
                print(f"   [OK] Order placed: {order_id} | state: {order.get('state', 'N/A')}")
                print(f"{'='*70}\n")
                return order

            detail = None
            if isinstance(order, dict):
                detail = order.get('detail') or order.get('non_field_errors')
            print(f"   [ERR] Stop-limit failed: {detail or order}")
            print(f"{'='*70}\n")
            return order

        except Exception as e:
            print(f"   [ERR] Order failed: {e}")
            print(f"{'='*70}\n")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID. Only cancels if still in a cancellable state."""
        try:
            order_info = r.orders.get_stock_order_info(order_id)
            if not order_info or not isinstance(order_info, dict):
                print(f"   cancel_order: order {order_id} not found")
                return False
            state = order_info.get('state', '')
            if state not in ('queued', 'unconfirmed', 'confirmed'):
                print(f"   cancel_order: order {order_id} in state '{state}', cannot cancel")
                return False
            r.orders.cancel_stock_order(order_id)
            print(f"   cancel_order: cancelled {order_id}")
            return True
        except Exception as e:
            print(f"   cancel_order: error cancelling {order_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Brokerage interface — account reads
    # ------------------------------------------------------------------

    def get_open_orders(self) -> list:
        """Get all open orders for this account."""
        try:
            open_orders = r.orders.get_all_open_stock_orders()
            orders = []
            if open_orders:
                for order in open_orders:
                    order_id = order.get('id', 'N/A')
                    symbol = order.get('symbol', 'N/A')
                    if symbol == 'N/A':
                        instrument_id = order.get('instrument_id')
                        if instrument_id:
                            try:
                                instrument = r.stocks.get_instrument_by_url(
                                    f"https://api.robinhood.com/instruments/{instrument_id}/"
                                )
                                if instrument:
                                    symbol = instrument.get('symbol', 'N/A')
                            except Exception:
                                pass
                    side = order.get('side', 'N/A')
                    order_type = order.get('type', 'N/A')
                    trigger = order.get('trigger', 'immediate')
                    state = order.get('state', 'N/A')
                    quantity = float(order.get('quantity', 0))
                    limit_price = order.get('price')
                    stop_price = order.get('stop_price')
                    created_at = order.get('created_at', 'N/A')
                    updated_at = order.get('updated_at', 'N/A')
                    try:
                        if created_at != 'N/A':
                            created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            created_at = created_dt.strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        pass
                    if trigger == 'stop' and order_type == 'limit':
                        order_desc = 'Stop Limit'
                    elif trigger == 'stop':
                        order_desc = 'Stop Loss'
                    elif order_type == 'limit':
                        order_desc = 'Limit'
                    else:
                        order_desc = 'Market'
                    orders.append({
                        'order_id': order_id,
                        'symbol': symbol,
                        'side': side.upper() if side != 'N/A' else 'N/A',
                        'order_type': order_desc,
                        'trigger': trigger,
                        'state': state,
                        'quantity': quantity,
                        'limit_price': float(limit_price) if limit_price else None,
                        'stop_price': float(stop_price) if stop_price else None,
                        'created_at': created_at,
                        'updated_at': updated_at,
                    })
            return orders
        except Exception as e:
            print(f"[ERR] Error getting open orders: {e}")
            return []

    def get_positions(self) -> list:
        """Get current equity positions."""
        try:
            holdings = r.account.build_holdings()
            positions = []
            if holdings:
                for symbol, data in holdings.items():
                    quantity = float(data.get('quantity', 0))
                    if quantity > 0:
                        avg_price = float(data.get('average_buy_price', 0))
                        current_price = float(data.get('price', 0))
                        equity = float(data.get('equity', 0))
                        profit_loss = (current_price - avg_price) * quantity
                        profit_loss_pct = ((current_price - avg_price) / avg_price * 100
                                          if avg_price > 0 else 0)
                        positions.append({
                            'symbol': symbol,
                            'name': data.get('name', ''),
                            'type': data.get('type', ''),
                            'quantity': quantity,
                            'avg_buy_price': avg_price,
                            'current_price': current_price,
                            'equity': equity,
                            'profit_loss': profit_loss,
                            'profit_loss_pct': profit_loss_pct,
                            'percent_change': self._safe_float(data.get('percent_change')),
                            'equity_change': self._safe_float(data.get('equity_change')),
                            'pe_ratio': self._safe_float(data.get('pe_ratio')),
                            'percentage': self._safe_float(data.get('percentage')),
                        })
            return positions
        except Exception as e:
            print(f"[ERR] Error getting positions: {e}")
            return []

    def get_cash_balance(self) -> dict | None:
        """Get available cash balance."""
        try:
            account = r.profiles.load_account_profile(account_number=self.account_number)
            cash = float(account.get('cash', 0))
            cash_available_for_withdrawal = float(account.get('cash_available_for_withdrawal', 0))
            buying_power = float(account.get('buying_power', 0))
            return {
                'cash': cash,
                'cash_available_for_withdrawal': cash_available_for_withdrawal,
                'buying_power': buying_power,
                'tradeable_cash': cash,
            }
        except Exception as e:
            print(f"[ERR] Error getting cash balance: {e}")
            return None

    def get_pdt_status(self) -> dict | None:
        """Get Pattern Day Trading status."""
        try:
            import time
            time.sleep(0.5)
            account = r.profiles.load_account_profile(account_number=self.account_number)
            day_trade_count = int(account.get('day_trade_count') or 0)
            flagged = account.get('pattern_day_trader', False)
            trades = []
            day_trade_info = account.get('day_trades', [])
            for dt in (day_trade_info or []):
                opened = dt.get('opened_at', 'N/A')
                closed = dt.get('closed_at', 'N/A')
                try:
                    if opened != 'N/A':
                        opened = datetime.fromisoformat(
                            opened.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
                    if closed != 'N/A':
                        closed = datetime.fromisoformat(
                            closed.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    pass
                trades.append(f"opened {opened} → closed {closed}")
            return {'day_trade_count': day_trade_count, 'flagged': flagged, 'trades': trades}
        except Exception as e:
            print(f"   Could not fetch PDT status: {e}")
            return None

    # ------------------------------------------------------------------
    # Extended reads (not part of Brokerage ABC, used by main.py)
    # ------------------------------------------------------------------

    def get_portfolio_summary(self, symbols=None):
        """Full portfolio summary — positions, cash, orders, PDT, options."""
        try:
            r.profiles.load_account_profile(account_number=self.account_number)
            portfolio = r.profiles.load_portfolio_profile(account_number=self.account_number)
            equity = float(portfolio.get('equity', 0))
            market_value = float(portfolio.get('market_value', 0))
            cash_info = self.get_cash_balance()
            positions = self.get_positions()
            open_orders = self.get_open_orders()
            option_positions = self.get_option_positions()

            if symbols:
                positions = [p for p in positions if p['symbol'] in symbols]
                open_orders = [o for o in open_orders if o['symbol'] in symbols]

            total_position_value = sum(pos['equity'] for pos in positions)

            return {
                'equity': equity,
                'market_value': market_value,
                'cash': cash_info,
                'positions': positions,
                'open_orders': open_orders,
                'options': option_positions,
                'total_position_value': total_position_value,
            }
        except Exception as e:
            print(f"[ERR] Error getting portfolio summary: {e}")
            return None

    def get_recent_orders(self, days=7) -> list:
        """Get recently filled/cancelled orders."""
        try:
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(days=days)
            cutoff_str = cutoff.strftime('%Y-%m-%dT00:00:00Z')
            all_orders = r.orders.get_all_stock_orders(info=None)
            orders = []
            if not all_orders:
                return orders
            for order in all_orders:
                state = order.get('state', '')
                if state not in ('filled', 'cancelled', 'failed', 'rejected'):
                    continue
                if order.get('updated_at', '') < cutoff_str:
                    continue
                symbol = order.get('symbol', 'N/A')
                if symbol == 'N/A':
                    instrument_id = order.get('instrument_id')
                    if instrument_id:
                        try:
                            inst = r.stocks.get_instrument_by_url(
                                f"https://api.robinhood.com/instruments/{instrument_id}/")
                            if inst:
                                symbol = inst.get('symbol', 'N/A')
                        except Exception:
                            pass
                trigger = order.get('trigger', 'immediate')
                order_type = order.get('type', 'N/A')
                if trigger == 'stop' and order_type == 'limit':
                    order_desc = 'Stop Limit'
                elif trigger == 'stop':
                    order_desc = 'Stop Loss'
                elif order_type == 'limit':
                    order_desc = 'Limit'
                else:
                    order_desc = 'Market'
                created_at = order.get('created_at', 'N/A')
                try:
                    if created_at != 'N/A':
                        created_at = datetime.fromisoformat(
                            created_at.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    pass
                limit_price = order.get('price')
                stop_price = order.get('stop_price')
                average_price = order.get('average_price')
                cumulative_quantity = order.get('cumulative_quantity')
                orders.append({
                    'order_id': order.get('id', 'N/A'),
                    'symbol': symbol,
                    'side': order.get('side', 'N/A').upper(),
                    'order_type': order_desc,
                    'trigger': trigger,
                    'state': state,
                    'quantity': float(order.get('quantity', 0)),
                    'limit_price': float(limit_price) if limit_price else None,
                    'stop_price': float(stop_price) if stop_price else None,
                    'average_price': float(average_price) if average_price else None,
                    'filled_quantity': float(cumulative_quantity) if cumulative_quantity else None,
                    'created_at': created_at,
                    'updated_at': order.get('updated_at', 'N/A'),
                })
            return orders
        except Exception as e:
            print(f"[ERR] Error getting recent orders: {e}")
            return []

    def get_recent_option_orders(self, days=7) -> list:
        """Get recently filled/cancelled option orders."""
        try:
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(days=days)
            cutoff_str = cutoff.strftime('%Y-%m-%dT00:00:00Z')
            raw_orders = r.orders.get_all_option_orders()
            if not raw_orders:
                return []
            orders = []
            for order in raw_orders:
                state = order.get('state', '')
                if state not in ('filled', 'cancelled', 'failed', 'rejected'):
                    continue
                if order.get('updated_at', '') < cutoff_str:
                    continue
                orders.append({
                    'order_id': order.get('id', 'N/A'),
                    'state': state,
                    'quantity': float(order.get('quantity', 0)),
                    'price': float(order.get('price', 0) or 0),
                    'direction': order.get('direction', 'N/A'),
                    'order_type': order.get('type', 'N/A'),
                    'created_at': order.get('created_at', 'N/A'),
                    'updated_at': order.get('updated_at', 'N/A'),
                })
            return orders
        except Exception as e:
            print(f"[ERR] Error getting recent option orders: {e}")
            return []

    def get_open_option_orders(self) -> list:
        """Get all open option orders."""
        try:
            raw_orders = r.orders.get_all_open_option_orders(
                account_number=self.account_number)
            if not raw_orders:
                return []
            return raw_orders
        except Exception as e:
            print(f"[ERR] Error getting open option orders: {e}")
            return []

    def get_option_positions(self) -> list:
        """Get open option positions with greeks and analytics."""
        try:
            raw_positions = r.options.get_open_option_positions(
                account_number=self.account_number)
            if not raw_positions:
                return []
            positions = []
            underlying_symbols = set()
            for pos in raw_positions:
                quantity = float(pos.get('quantity', 0))
                if quantity == 0:
                    continue
                chain_symbol = pos.get('chain_symbol', 'N/A')
                underlying_symbols.add(chain_symbol)
                avg_price = float(pos.get('average_price', 0)) / 100
                pos_type = pos.get('type', 'long')
                multiplier = float(pos.get('trade_value_multiplier', '100'))
                option_url = pos.get('option', '')
                option_id = option_url.rstrip('/').split('/')[-1] if option_url else None
                instrument = {}
                if option_id:
                    try:
                        instrument = r.options.get_option_instrument_data_by_id(option_id) or {}
                    except Exception:
                        pass
                strike = float(instrument.get('strike_price', 0))
                expiration = instrument.get('expiration_date', 'N/A')
                option_type = instrument.get('type', 'N/A')
                market_data = {}
                if option_id:
                    try:
                        md = r.options.get_option_market_data_by_id(option_id)
                        if md and isinstance(md, list) and len(md) > 0:
                            market_data = md[0]
                        elif md and isinstance(md, dict):
                            market_data = md
                    except Exception:
                        pass
                delta = self._safe_float(market_data.get('delta'))
                gamma = self._safe_float(market_data.get('gamma'))
                theta = self._safe_float(market_data.get('theta'))
                vega = self._safe_float(market_data.get('vega'))
                rho = self._safe_float(market_data.get('rho'))
                iv = self._safe_float(market_data.get('implied_volatility'))
                mark_price = self._safe_float(market_data.get('adjusted_mark_price'))
                chance_profit_long = self._safe_float(market_data.get('chance_of_profit_long'))
                chance_profit_short = self._safe_float(market_data.get('chance_of_profit_short'))
                break_even = self._safe_float(market_data.get('break_even_price'))
                underlying_price = None
                try:
                    price_data = r.stocks.get_latest_price(chain_symbol)
                    if price_data and price_data[0]:
                        underlying_price = float(price_data[0])
                except Exception:
                    pass
                dte = None
                if expiration and expiration != 'N/A':
                    try:
                        exp_date = datetime.strptime(expiration, '%Y-%m-%d').date()
                        dte = (exp_date - date.today()).days
                    except Exception:
                        pass
                current_value = (mark_price or 0) * quantity * multiplier
                recommendation = self._recommend_option_action(
                    option_type, pos_type, delta, theta, iv, dte, mark_price,
                    avg_price, underlying_price, strike, chance_profit_long,
                    chance_profit_short)
                expected_pnl = self._calculate_expected_pnl(
                    delta, gamma, underlying_price, quantity, multiplier, pos_type)
                greeks = {'delta': delta, 'gamma': gamma, 'theta': theta,
                          'vega': vega, 'rho': rho, 'iv': iv}
                positions.append({
                    'chain_symbol': chain_symbol,
                    'option_type': option_type,
                    'strike': strike,
                    'expiration': expiration,
                    'quantity': quantity,
                    'avg_price': avg_price,
                    'mark_price': mark_price,
                    'current_value': current_value,
                    'position_type': pos_type,
                    'dte': dte,
                    'underlying_price': underlying_price,
                    'greeks': greeks,
                    'break_even': break_even,
                    'chance_profit_long': chance_profit_long,
                    'chance_profit_short': chance_profit_short,
                    'expected_pnl': expected_pnl,
                    'recommendation': recommendation,
                })
            return positions
        except Exception as e:
            print(f"[ERR] Error getting option positions: {e}")
            return []

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def validate_buy_order(self, symbol, quantity, price):
        """Returns (is_valid, reason). Checks buying power and symbol validity."""
        cash_info = self.get_cash_balance()
        if not cash_info:
            return False, "Cannot retrieve cash balance"
        total_cost_with_buffer = quantity * price * 1.01
        if total_cost_with_buffer > cash_info['buying_power']:
            return False, (f"Insufficient buying power: need "
                           f"${total_cost_with_buffer:,.2f}, "
                           f"have ${cash_info['buying_power']:,.2f}")
        try:
            quote = r.stocks.get_quotes(symbol)
            if not quote or len(quote) == 0:
                return False, f"Invalid symbol: {symbol}"
        except Exception:
            return False, f"Cannot get quote for {symbol}"
        return True, "Order validated"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _verify_account(self):
        try:
            account = r.profiles.load_account_profile(account_number=self.account_number)
            account_type = account.get('type', 'unknown')
            print(f"\n{'='*70}")
            print(f"[LOCKED] LOCKED TO ACCOUNT: {self.account_number}")
            print(f"{'='*70}")
            print(f"   Account Type: {account_type}")
            if account_type != 'cash':
                print(f"   [WARN] Account type is '{account_type}', not 'cash'")
            print(f"{'='*70}\n")
        except Exception as e:
            print(f"[ERR] Cannot access account {self.account_number}: {e}")
            sys.exit(1)

    def _cancel_existing_orders(self, symbol, side) -> int:
        """Cancel all open orders for symbol on the given side. Returns cancelled qty."""
        target_instrument_url = None
        try:
            instruments = r.stocks.get_instruments_by_symbols(symbol, info='url')
            if instruments:
                target_instrument_url = instruments[0]
        except Exception as e:
            print(f"   Failed to resolve instrument for {symbol}: {e}")
            return 0
        if not target_instrument_url:
            return 0
        open_orders = r.orders.get_all_open_stock_orders(account_number=self.account_number)
        cancelled_qty = 0
        if open_orders:
            for order in open_orders:
                if (order.get('instrument', '') == target_instrument_url
                        and order.get('side', '') == side):
                    existing_id = order.get('id')
                    r.orders.cancel_stock_order(existing_id)
                    try:
                        cancelled_qty += int(float(order.get('quantity', '0')))
                    except (ValueError, TypeError):
                        pass
        return cancelled_qty

    @staticmethod
    def _safe_float(value):
        try:
            return float(value) if value is not None else None
        except (ValueError, TypeError):
            return None

    def _calculate_expected_pnl(self, delta, gamma, underlying_price,
                                quantity, multiplier, pos_type):
        if not underlying_price or delta is None:
            return None
        sign = 1.0 if pos_type == 'long' else -1.0
        scenarios = {}
        for pct_label, pct in [('-5%', -0.05), ('-1%', -0.01), ('+1%', 0.01), ('+5%', 0.05)]:
            dollar_move = underlying_price * pct
            option_delta_price = (delta or 0) * dollar_move
            if gamma:
                option_delta_price += 0.5 * gamma * dollar_move ** 2
            scenarios[pct_label] = round(sign * option_delta_price * quantity * multiplier, 2)
        if delta is not None:
            scenarios['theta_daily'] = round(sign * (delta or 0) * quantity * multiplier, 2)
        return scenarios

    def _recommend_option_action(self, option_type, pos_type, delta, theta, iv,
                                 dte, mark_price, avg_price, underlying_price,
                                 strike, chance_profit_long, chance_profit_short):
        reasons = []
        action = 'HOLD'
        if dte is not None and dte <= 0:
            return {'action': 'CLOSE', 'reasons': ['Expired or expiring today']}
        chance_of_profit = chance_profit_long if pos_type == 'long' else chance_profit_short
        if pos_type == 'long':
            if mark_price and avg_price and avg_price > 0:
                gain_pct = (mark_price - avg_price) / avg_price * 100
                if gain_pct >= 100:
                    action = 'CLOSE'
                    reasons.append(f'Up {gain_pct:.0f}% — take profit')
                elif gain_pct >= 50:
                    reasons.append(f'Up {gain_pct:.0f}% — consider partial close')
            if dte is not None and dte <= 7 and theta is not None and theta < -0.03:
                action = 'CLOSE'
                reasons.append(f'DTE={dte}, heavy theta decay (${theta:.3f}/day)')
            elif dte is not None and dte <= 14:
                reasons.append(f'DTE={dte} — monitor theta decay')
            if chance_of_profit is not None and chance_of_profit < 0.20:
                action = 'CLOSE'
                reasons.append(f'Low probability of profit ({chance_of_profit:.0%})')
            if underlying_price and strike and option_type in ('call', 'put'):
                if option_type == 'call' and underlying_price < strike * 0.90:
                    reasons.append('Deep OTM call')
                elif option_type == 'put' and underlying_price > strike * 1.10:
                    reasons.append('Deep OTM put')
        else:
            if mark_price and avg_price and avg_price > 0:
                decay_pct = (avg_price - mark_price) / avg_price * 100
                if decay_pct >= 80:
                    action = 'CLOSE'
                    reasons.append(f'Captured {decay_pct:.0f}% of premium — close to lock in')
                elif decay_pct >= 50:
                    reasons.append(f'Captured {decay_pct:.0f}% of premium — consider closing')
            if iv is not None and iv > 0.80:
                reasons.append(f'High IV ({iv:.0%}) — increased risk')
            if dte is not None and dte <= 3 and underlying_price and strike:
                if option_type == 'call' and underlying_price >= strike:
                    action = 'CLOSE'
                    reasons.append('ITM near expiration — assignment risk')
                elif option_type == 'put' and underlying_price <= strike:
                    action = 'CLOSE'
                    reasons.append('ITM near expiration — assignment risk')
        if not reasons:
            reasons.append('No immediate signals')
        return {'action': action, 'reasons': reasons}

    def _compute_btc_correlations(self, symbols):
        correlations = {}
        if not symbols:
            return correlations
        btc_returns = self._get_daily_returns('BTC')
        if not btc_returns:
            return correlations
        for sym in symbols:
            if sym == 'BTC':
                correlations[sym] = 1.0
                continue
            sym_returns = self._get_daily_returns(sym)
            if not sym_returns:
                correlations[sym] = None
                continue
            corr = self._pearson_from_return_dicts(btc_returns, sym_returns)
            correlations[sym] = round(corr, 3) if corr is not None else None
        return correlations

    def _get_daily_returns(self, symbol):
        try:
            historicals = r.stocks.get_stock_historicals(
                symbol, interval='day', span='3month')
            if not historicals or len(historicals) < 5:
                return None
            returns = {}
            for i in range(1, len(historicals)):
                prev_close = float(historicals[i - 1].get('close_price', 0))
                curr_close = float(historicals[i].get('close_price', 0))
                if prev_close > 0 and curr_close > 0:
                    dt = historicals[i].get('begins_at', '')[:10]
                    returns[dt] = math.log(curr_close / prev_close)
            return returns
        except Exception:
            return None

    @staticmethod
    def _pearson_from_return_dicts(a_dict, b_dict):
        common = sorted(set(a_dict.keys()) & set(b_dict.keys()))
        common = common[-30:] if len(common) > 30 else common
        if len(common) < 5:
            return None
        a = [a_dict[d] for d in common]
        b = [b_dict[d] for d in common]
        n = len(a)
        ma = sum(a) / n
        mb = sum(b) / n
        cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)
        sa = math.sqrt(sum((x - ma) ** 2 for x in a) / (n - 1))
        sb = math.sqrt(sum((x - mb) ** 2 for x in b) / (n - 1))
        if sa == 0 or sb == 0:
            return None
        return cov / (sa * sb)
