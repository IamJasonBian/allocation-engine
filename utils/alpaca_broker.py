"""
Alpaca Broker Adapter
---------------------
Single-leg equity-options trading via Alpaca's API. Defaults to **paper
trading** (https://paper-api.alpaca.markets) so this is safe to run before
the live account is funded.

Env vars (loaded with python-dotenv, matching utils/rh_auth.py:21):
    ALPACA_API_KEY_ID       - Alpaca API key id
    ALPACA_API_SECRET_KEY   - Alpaca API secret
    ALPACA_PAPER            - "true"/"false", default "true"

Surface intentionally mirrors the new option methods on
``utils.safe_cash_bot.SafeCashBot``: same banner format, same dry_run
gating, same [OK]/[ERR]/[WARN] prefixes.

Single-leg only - spreads / multi-leg are out of scope for this adapter.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv


# --------------------------------------------------------------------------
# OPRA / OCC option symbol encoding
# --------------------------------------------------------------------------
def build_opra_symbol(symbol: str, expiration: str, strike: float, option_type: str) -> str:
    """
    Build an OCC/OPRA-formatted option symbol that Alpaca accepts.

    Format: ROOT (up to 6 chars, padded right) + YYMMDD + C/P + strike*1000
    zero-padded to 8 digits.

    Example:
        build_opra_symbol('XLY', '2026-09-18', 120.0, 'call')
            -> 'XLY260918C00120000'
    """
    option_type = option_type.lower()
    if option_type not in ('put', 'call'):
        raise ValueError(f"option_type must be 'put' or 'call', got {option_type!r}")
    try:
        exp_dt = datetime.strptime(expiration, '%Y-%m-%d')
    except ValueError as e:
        raise ValueError(f"expiration must be YYYY-MM-DD, got {expiration!r}: {e}")

    root = symbol.upper()
    yymmdd = exp_dt.strftime('%y%m%d')
    cp = 'C' if option_type == 'call' else 'P'
    strike_int = int(round(float(strike) * 1000))
    strike_str = f"{strike_int:08d}"
    return f"{root}{yymmdd}{cp}{strike_str}"


# --------------------------------------------------------------------------
# Broker
# --------------------------------------------------------------------------
class AlpacaBroker:
    """
    Thin Alpaca adapter for single-leg equity options.

    Defaults to paper trading. Pass ``paper=False`` (or set
    ``ALPACA_PAPER=false`` in the env) to point at the live endpoint -
    but the user explicitly does NOT want that wired up to a CLI flag
    yet, since the live account is not funded.
    """

    PAPER_URL = "https://paper-api.alpaca.markets"
    LIVE_URL = "https://api.alpaca.markets"

    def __init__(self, paper: Optional[bool] = None):
        load_dotenv()

        # Resolve paper flag: explicit arg > env var > default True
        if paper is None:
            env_paper = os.getenv('ALPACA_PAPER', 'true').strip().lower()
            paper = env_paper not in ('false', '0', 'no')
        self.paper = bool(paper)

        self.api_key = os.getenv('ALPACA_API_KEY_ID')
        self.api_secret = os.getenv('ALPACA_API_SECRET_KEY')
        if not self.api_key or not self.api_secret:
            print("[ERR] ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set in .env")

        # Lazy import keeps unit tests light and avoids hard dep at import time
        from alpaca.trading.client import TradingClient

        self._client = TradingClient(
            api_key=self.api_key,
            secret_key=self.api_secret,
            paper=self.paper,
        )

        mode = "PAPER" if self.paper else "LIVE"
        endpoint = self.PAPER_URL if self.paper else self.LIVE_URL
        print(f"\n{'='*70}")
        print(f"ALPACA BROKER INITIALIZED - {mode}")
        print(f"{'='*70}")
        print(f"   Endpoint: {endpoint}")
        print(f"   API Key:  {(self.api_key or '')[:6]}***")
        print(f"{'='*70}\n")

    # ------------------------------------------------------------------
    # Account / quotes
    # ------------------------------------------------------------------
    def get_account(self) -> dict:
        """Return a small dict view of the Alpaca account."""
        try:
            acct = self._client.get_account()
            data = {
                'account_number': getattr(acct, 'account_number', None),
                'buying_power': float(getattr(acct, 'buying_power', 0) or 0),
                'cash': float(getattr(acct, 'cash', 0) or 0),
                'status': str(getattr(acct, 'status', '')),
            }
            print(f"\n{'='*70}")
            print(f"ALPACA ACCOUNT")
            print(f"{'='*70}")
            for k, v in data.items():
                print(f"   {k}: {v}")
            print(f"{'='*70}\n")
            return data
        except Exception as e:
            print(f"[ERR] Error fetching Alpaca account: {e}")
            return {}

    def get_option_quote(self, symbol, expiration, strike, option_type) -> dict:
        """
        Return latest quote-ish dict for a single option contract.

        Greeks/IV/OI come from the option contract metadata when available;
        bid/ask/mid come from the latest market quote. Anything Alpaca
        can't surface (e.g. on a free data plan) returns None.
        """
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient
            from alpaca.data.requests import OptionLatestQuoteRequest
        except Exception as e:
            print(f"[ERR] alpaca-py data client unavailable: {e}")
            return {}

        opra = build_opra_symbol(symbol, expiration, strike, option_type)

        out = {
            'symbol': opra, 'bid': None, 'ask': None, 'mid': None,
            'iv': None, 'delta': None, 'oi': None, 'spot': None,
        }

        # Bid/ask via market data
        try:
            data_client = OptionHistoricalDataClient(self.api_key, self.api_secret)
            req = OptionLatestQuoteRequest(symbol_or_symbols=opra)
            quotes = data_client.get_option_latest_quote(req)
            q = quotes.get(opra) if isinstance(quotes, dict) else None
            if q is not None:
                bid = float(getattr(q, 'bid_price', 0) or 0)
                ask = float(getattr(q, 'ask_price', 0) or 0)
                out['bid'] = bid
                out['ask'] = ask
                if bid and ask:
                    out['mid'] = round((bid + ask) / 2, 4)
        except Exception as e:
            print(f"   [WARN] Quote fetch failed (continuing): {e}")

        # Greeks / OI via contract metadata
        try:
            contract = self._client.get_option_contract(opra)
            out['iv'] = _safe_float(getattr(contract, 'implied_volatility', None))
            out['delta'] = _safe_float(getattr(contract, 'delta', None))
            out['oi'] = _safe_int(getattr(contract, 'open_interest', None))
            out['spot'] = _safe_float(getattr(contract, 'underlying_price', None))
        except Exception as e:
            print(f"   [WARN] Contract metadata fetch failed (continuing): {e}")

        return out

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------
    def place_option_buy_limit_order(
        self, symbol, expiration, strike, option_type,
        quantity, price, dry_run=True, time_in_force='day',
    ) -> dict:
        """
        Single-leg LIMIT BUY-TO-OPEN on an equity option, via Alpaca.

        Mirrors the banner / dry-run gating style of
        ``SafeCashBot.place_option_buy_limit_order``.
        """
        return self._place_option_limit_order(
            side='buy', position_intent='buy_to_open',
            symbol=symbol, expiration=expiration, strike=strike,
            option_type=option_type, quantity=quantity, price=price,
            dry_run=dry_run, time_in_force=time_in_force,
        )

    def place_option_sell_limit_order(
        self, symbol, expiration, strike, option_type,
        quantity, price, dry_run=True, time_in_force='day',
    ) -> dict:
        """
        Single-leg LIMIT SELL-TO-CLOSE on an equity option, via Alpaca.

        Use this to set a take-profit on an existing long option position.
        """
        return self._place_option_limit_order(
            side='sell', position_intent='sell_to_close',
            symbol=symbol, expiration=expiration, strike=strike,
            option_type=option_type, quantity=quantity, price=price,
            dry_run=dry_run, time_in_force=time_in_force,
        )

    def get_open_option_positions(self) -> list:
        """Return open option positions as a list of dicts."""
        try:
            positions = self._client.get_all_positions()
        except Exception as e:
            print(f"[ERR] Error getting positions: {e}")
            return []

        out = []
        for p in positions or []:
            asset_class = str(getattr(p, 'asset_class', '') or '').lower()
            # alpaca-py returns 'us_option' for option positions
            if 'option' not in asset_class:
                continue
            out.append({
                'symbol': getattr(p, 'symbol', None),
                'qty': _safe_float(getattr(p, 'qty', None)),
                'avg_entry_price': _safe_float(getattr(p, 'avg_entry_price', None)),
                'market_value': _safe_float(getattr(p, 'market_value', None)),
                'unrealized_pl': _safe_float(getattr(p, 'unrealized_pl', None)),
                'side': str(getattr(p, 'side', '')),
                'asset_class': asset_class,
            })
        return out

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _place_option_limit_order(
        self, side, position_intent,
        symbol, expiration, strike, option_type,
        quantity, price, dry_run, time_in_force,
    ) -> dict:
        option_type = option_type.lower()
        if option_type not in ('put', 'call'):
            print(f"[ERR] option_type must be 'put' or 'call', got {option_type!r}")
            return {}

        notional = quantity * price * 100
        opra = build_opra_symbol(symbol, expiration, strike, option_type)

        side_upper = side.upper()
        action = "BUY (OPEN)" if side == 'buy' else "SELL (CLOSE)"
        mode = "DRY RUN" if dry_run else ("PAPER" if self.paper else "LIVE")

        print(f"\n{'='*70}")
        print(f"ALPACA OPTION {action} ORDER - {mode}")
        print(f"{'='*70}")
        print(f"   Endpoint: {self.PAPER_URL if self.paper else self.LIVE_URL}")
        print(f"   Contract: {symbol} {expiration} ${float(strike):.2f} {option_type.upper()}")
        print(f"   OPRA:     {opra}")
        print(f"   Side:     {side_upper} ({position_intent})")
        print(f"   Quantity: {quantity} contract(s) ({quantity * 100} shares)")
        print(f"   Limit:    ${float(price):.2f} per share")
        notional_label = "Total Debit" if side == 'buy' else "Total Credit"
        print(f"   {notional_label}: ${notional:.2f}")
        print(f"   TIF:      {str(time_in_force).upper()}")

        # --- Pre-trade validation ---
        if side == 'buy':
            try:
                acct = self._client.get_account()
                bp = float(getattr(acct, 'options_buying_power', None)
                           or getattr(acct, 'buying_power', 0) or 0)
                print(f"   Buying Power: ${bp:.2f}")
                if bp < notional:
                    print(f"\n[ERR] Insufficient buying power: ${bp:.2f} < ${notional:.2f}")
                    print(f"{'='*70}\n")
                    return {}
                print(f"   Validation: [OK] Buying power sufficient")
            except Exception as e:
                print(f"   [WARN] Buying-power check failed (proceeding): {e}")
        else:
            # Sell-to-close: confirm we hold enough contracts
            try:
                positions = self.get_open_option_positions()
                held = 0
                for pos in positions:
                    if pos.get('symbol') == opra:
                        held = int(abs(pos.get('qty') or 0))
                        break
                print(f"   Held: {held} contract(s)")
                if held < quantity:
                    print(f"\n[ERR] Insufficient open contracts to close: held {held} < sell {quantity}")
                    print(f"{'='*70}\n")
                    return {}
                print(f"   Validation: [OK] Position sufficient")
            except Exception as e:
                print(f"   [WARN] Position check failed (proceeding): {e}")

        if dry_run:
            print("\n[WARN]  DRY RUN MODE - Order not executed")
            print("   To execute, call with dry_run=False")
            print(f"{'='*70}\n")
            return {}

        # --- Submit ---
        try:
            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce, PositionIntent

            tif_map = {
                'day': TimeInForce.DAY, 'gtc': TimeInForce.GTC,
                'ioc': TimeInForce.IOC, 'fok': TimeInForce.FOK,
            }
            tif = tif_map.get(str(time_in_force).lower(), TimeInForce.DAY)

            intent_map = {
                'buy_to_open': PositionIntent.BUY_TO_OPEN,
                'sell_to_close': PositionIntent.SELL_TO_CLOSE,
            }

            req = LimitOrderRequest(
                symbol=opra,
                qty=quantity,
                side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
                time_in_force=tif,
                limit_price=float(price),
                position_intent=intent_map[position_intent],
            )
            print(f"\nExecuting Alpaca option {side_upper} order...")
            order = self._client.submit_order(order_data=req)

            order_id = getattr(order, 'id', None)
            order_state = getattr(order, 'status', None)
            if order_id:
                print(f"[OK] Alpaca option {side} order placed!")
                print(f"   Order ID: {order_id}")
                print(f"   State:    {order_state or 'N/A'}")
                print(f"{'='*70}\n")
                return _order_to_dict(order)

            print(f"[ERR] Alpaca option {side} order returned no id")
            print(f"   Response: {order}")
            print(f"{'='*70}\n")
            return _order_to_dict(order)
        except Exception as e:
            print(f"[ERR] Alpaca option {side} order failed: {e}")
            print(f"{'='*70}\n")
            return {}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _safe_float(x):
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _safe_int(x):
    if x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _order_to_dict(order) -> dict:
    if order is None:
        return {}
    if isinstance(order, dict):
        return order
    # alpaca-py models are pydantic; .model_dump() returns a plain dict
    if hasattr(order, 'model_dump'):
        try:
            return order.model_dump(mode='json')
        except Exception:
            pass
    out = {}
    for attr in ('id', 'status', 'symbol', 'qty', 'filled_qty', 'limit_price',
                 'side', 'time_in_force', 'position_intent', 'submitted_at'):
        val = getattr(order, attr, None)
        if val is not None:
            out[attr] = val if isinstance(val, (str, int, float, bool)) else str(val)
    return out
