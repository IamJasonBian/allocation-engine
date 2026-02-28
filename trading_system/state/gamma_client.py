"""
Gamma Runtime Service Client
Minimal HTTP client to fetch the desired order set from the gamma runtime
service for cross-engine visibility and as a price source.
"""

import requests

GAMMA_URL = "https://route-runtime-service.netlify.app/api/orders"


def fetch_gamma_orders(url=None):
    """Fetch the /api/orders JSON snapshot from the gamma runtime service.

    Args:
        url: override URL (defaults to GAMMA_URL)

    Returns:
        dict with snapshot_key, stock_orders, options — or None on failure
    """
    try:
        resp = requests.get(url or GAMMA_URL, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  [gamma] Could not fetch gamma orders: {e}")
        return None


def extract_gamma_prices(data):
    """Extract current prices from gamma order set.

    Args:
        data: dict returned by fetch_gamma_orders()

    Returns:
        dict mapping symbol to price, e.g. {"BTC": 29.47, "SPY": 680.7}
        Returns empty dict on failure.
    """
    if not data:
        return {}

    prices = {}
    try:
        for order in data.get('stock_orders', []):
            symbol = order.get('symbol')
            price = order.get('price') or order.get('limit_price')
            if symbol and price is not None:
                try:
                    prices[symbol] = float(price)
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        print(f"  [gamma] Could not extract prices: {e}")

    return prices
