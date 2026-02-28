"""
Price Discovery — aggregates prices from multiple sources.

Sources:
  1. TwelveDataProvider.get_quote()
  2. Gamma extract_gamma_prices()
  3. Alpaca latest quote (price feed only, not a broker)
  4. SafeCashBot broker quote
"""

import os
import statistics
from typing import Dict, Optional


def _fetch_alpaca_quote(symbol: str) -> Optional[float]:
    """Fetch latest mid-price from Alpaca market data.

    Requires ALPACA_API_KEY and ALPACA_SECRET_KEY env vars.
    Returns None if not configured or on any error.
    """
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        return None

    try:
        from alpaca.data.requests import StockLatestQuoteRequest
        from alpaca.data.historical import StockHistoricalDataClient

        client = StockHistoricalDataClient(api_key, secret_key)
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = client.get_stock_latest_quote(request)
        quote = quotes.get(symbol)
        if quote and quote.ask_price and quote.bid_price:
            return round((quote.ask_price + quote.bid_price) / 2, 4)
        return None
    except Exception:
        return None


class PriceDiscovery:
    """Aggregates prices from Twelve Data, Gamma, Alpaca, and broker."""

    def __init__(self, data_provider, trading_bot):
        """
        Args:
            data_provider: TwelveDataProvider instance
            trading_bot: SafeCashBot instance
        """
        self.data_provider = data_provider
        self.trading_bot = trading_bot
        self._gamma_prices: Dict[str, float] = {}

    def update_gamma_prices(self, gamma_data):
        """Refresh cached gamma prices from a gamma snapshot.

        Called once per tick with the result of fetch_gamma_orders().
        """
        if gamma_data is None:
            return
        try:
            from trading_system.state.gamma_client import extract_gamma_prices
            self._gamma_prices = extract_gamma_prices(gamma_data) or {}
        except Exception:
            self._gamma_prices = {}

    def get_price(self, symbol: str) -> dict:
        """Get consensus price from all available sources.

        Returns:
            {
                'price': float,          # median consensus
                'sources': {
                    'twelve_data': float | None,
                    'gamma': float | None,
                    'alpaca': float | None,
                    'broker': float | None,
                },
                'spread': float,         # max - min of available sources
                'confidence': str,       # 'high' (3+ sources) | 'medium' (2) | 'low' (1) | 'none'
                'outliers': list,        # source names >1% from median
            }
        """
        sources: Dict[str, Optional[float]] = {
            'twelve_data': None,
            'gamma': None,
            'alpaca': None,
            'broker': None,
        }

        # 1. Twelve Data
        try:
            quote = self.data_provider.get_quote(symbol)
            if quote and quote.get('price'):
                sources['twelve_data'] = float(quote['price'])
        except Exception:
            pass

        # 2. Gamma
        gamma_price = self._gamma_prices.get(symbol)
        if gamma_price:
            sources['gamma'] = float(gamma_price)

        # 3. Alpaca
        sources['alpaca'] = _fetch_alpaca_quote(symbol)

        # 4. Broker
        try:
            broker_quote = self.trading_bot.get_quote(symbol)
            if broker_quote:
                price = broker_quote.get('last_trade_price') or broker_quote.get('price')
                if price:
                    sources['broker'] = float(price)
        except Exception:
            pass

        # Consensus: median of available
        available = [v for v in sources.values() if v is not None]

        if not available:
            return {
                'price': 0.0,
                'sources': sources,
                'spread': 0.0,
                'confidence': 'none',
                'outliers': [],
            }

        median_price = statistics.median(available)
        spread = max(available) - min(available)

        # Flag outliers: >1% from median
        outliers = []
        for name, val in sources.items():
            if val is not None and median_price > 0:
                divergence = abs(val - median_price) / median_price
                if divergence > 0.01:
                    outliers.append(name)

        n = len(available)
        if n >= 3:
            confidence = 'high'
        elif n == 2:
            confidence = 'medium'
        else:
            confidence = 'low'

        return {
            'price': round(median_price, 4),
            'sources': sources,
            'spread': round(spread, 4),
            'confidence': confidence,
            'outliers': outliers,
        }

    def cross_check(self, symbol: str, expected_price: float) -> dict:
        """Cross-check an expected price against consensus.

        Returns:
            {
                'divergence_pct': float,
                'consensus_price': float,
                'alert': bool,           # True if >1% divergence
            }
        """
        result = self.get_price(symbol)
        consensus = result['price']
        if consensus == 0 or expected_price == 0:
            return {
                'divergence_pct': 0.0,
                'consensus_price': consensus,
                'alert': False,
            }

        divergence_pct = abs(expected_price - consensus) / consensus * 100
        return {
            'divergence_pct': round(divergence_pct, 4),
            'consensus_price': consensus,
            'alert': divergence_pct > 1.0,
        }
