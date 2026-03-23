"""
Drift Comparator — pushdown layer for conditional market data fetching.

Caches per-symbol price + metrics state. On each cycle, a cheap multi-quote
call checks current prices. Full OHLCV fetches only happen when a symbol's
price has drifted past threshold or data has gone stale.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SymbolState:
    """Cached state for one symbol after a full OHLCV fetch."""
    last_price: float
    last_metrics: Dict
    last_market_data: Dict  # raw {'intraday': [...], 'daily': [...]}
    last_full_fetch_ts: float  # time.time() of last full fetch


class DriftComparator:
    """Determines whether a symbol needs a full market data refresh.

    Usage in the trading loop:
        comparator = DriftComparator()
        quotes = provider.get_multi_quote(symbols)  # 1 API call

        for sym in symbols:
            price = quotes[sym]['price']
            if comparator.should_full_fetch(sym, price, regime):
                data = fetch_market_data(sym)     # 2 API calls
                metrics = calculate_metrics(sym, data)
                comparator.update(sym, price, metrics, data)
            else:
                metrics = comparator.get_cached_metrics(sym, price)
            # ... execute strategy with metrics
    """

    def __init__(
        self,
        price_drift_pct: float = 0.005,
        high_vol_drift_pct: float = 0.002,
        max_stale_minutes: float = 30.0,
    ):
        self.price_drift_pct = price_drift_pct
        self.high_vol_drift_pct = high_vol_drift_pct
        self.max_stale_seconds = max_stale_minutes * 60
        self._state: Dict[str, SymbolState] = {}

    def should_full_fetch(
        self,
        symbol: str,
        current_price: float,
        regime: str = "MEDIUM_VOL",
    ) -> bool:
        """Return True if this symbol needs a fresh OHLCV pull.

        Triggers on:
          1. Symbol has never been fetched (cold start).
          2. Price moved more than drift threshold from last baseline.
          3. Cached data exceeded max staleness window.
        """
        if symbol not in self._state:
            return True

        st = self._state[symbol]

        # Staleness check
        age = time.time() - st.last_full_fetch_ts
        if age >= self.max_stale_seconds:
            return True

        # Price drift check — tighter threshold in high-vol regime
        threshold = (
            self.high_vol_drift_pct
            if regime == "HIGH_VOL"
            else self.price_drift_pct
        )
        if st.last_price > 0:
            pct_change = abs(current_price - st.last_price) / st.last_price
            if pct_change >= threshold:
                return True

        return False

    def update(
        self,
        symbol: str,
        price: float,
        metrics: Dict,
        market_data: Dict,
    ) -> None:
        """Store baseline state after a full OHLCV fetch."""
        self._state[symbol] = SymbolState(
            last_price=price,
            last_metrics=dict(metrics),
            last_market_data=market_data,
            last_full_fetch_ts=time.time(),
        )

    def get_cached_metrics(
        self, symbol: str, current_price: float
    ) -> Optional[Dict]:
        """Return cached metrics with current_price patched in.

        Returns None if the symbol has no cached state.
        """
        if symbol not in self._state:
            return None
        patched = dict(self._state[symbol].last_metrics)
        patched["current_price"] = current_price
        return patched

    def get_symbols_needing_fetch(
        self,
        quotes: Dict[str, Dict],
        regime: str = "MEDIUM_VOL",
    ) -> List[str]:
        """Given a dict of symbol -> quote, return symbols that need full fetch."""
        return [
            sym
            for sym, q in quotes.items()
            if q and q.get("price") and self.should_full_fetch(sym, q["price"], regime)
        ]

    def has_state(self, symbol: str) -> bool:
        return symbol in self._state
