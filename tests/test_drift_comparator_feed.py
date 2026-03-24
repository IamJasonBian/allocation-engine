"""
Integration test: DriftComparator against allocation-engine-2.0 market data feed.

Tests that the comparator can consume quotes from the Netlify blob stores
(market-quotes, options-chain) used by allocation-engine-2.0's Alpaca pipeline.

Runs in two modes:
  1. Offline (default): Uses realistic mock data matching the blob format.
  2. Live (NETLIFY_API_TOKEN + NETLIFY_SITE_ID set): Reads actual blobs.
"""

import json
import os
import time
from unittest.mock import patch

import pytest
import requests

from trading_system.drift_comparator import DriftComparator

# ---------------------------------------------------------------------------
# Blob store client (reusable outside tests)
# ---------------------------------------------------------------------------

NETLIFY_BLOBS_URL = "https://api.netlify.com/api/v1/blobs"


class AllocationFeedClient:
    """Reads from allocation-engine-2.0 Netlify blob stores."""

    def __init__(self, token=None, site_id=None):
        self.token = token or os.getenv("NETLIFY_API_TOKEN")
        self.site_id = site_id or os.getenv("NETLIFY_SITE_ID")

    @property
    def available(self):
        return bool(self.token and self.site_id)

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def list_blobs(self, store_name):
        """List blob keys in a store."""
        url = f"{NETLIFY_BLOBS_URL}/{self.site_id}/{store_name}"
        resp = requests.get(url, headers=self._headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return [b["key"] for b in data.get("blobs", [])]

    def get_blob(self, store_name, key):
        """Fetch a single blob by key."""
        url = f"{NETLIFY_BLOBS_URL}/{self.site_id}/{store_name}/{key}"
        resp = requests.get(url, headers=self._headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_latest_blob(self, store_name):
        """Fetch the most recent blob (keys are ISO timestamps, lexicographic sort)."""
        keys = self.list_blobs(store_name)
        if not keys:
            return None
        latest_key = sorted(keys)[-1]
        return self.get_blob(store_name, latest_key)

    def get_market_quotes(self):
        """Get latest market quotes from the market-quotes store.

        Returns dict of {symbol: {price, bid, ask, spread_bps, ...}}.
        """
        blob = self.get_latest_blob("market-quotes")
        if not blob:
            return {}
        return blob.get("latest_quotes", blob)

    def get_options_chain(self, symbol=None):
        """Get latest options chain data.

        Blob format: {timestamp, underlying, blob_key, latest_chain: {contract_symbol: {...}}}
        If symbol provided, fetches blobs keyed under that underlying.
        """
        if symbol:
            # Options chain blobs are keyed as "SYMBOL/timestamp"
            keys = self.list_blobs("options-chain")
            symbol_keys = sorted([k for k in keys if k.startswith(f"{symbol}/")])
            if not symbol_keys:
                return {}
            return self.get_blob("options-chain", symbol_keys[-1])
        blob = self.get_latest_blob("options-chain")
        if not blob:
            return {}
        return blob


def normalize_feed_quotes(feed_quotes):
    """Convert allocation-engine-2.0 quote format to drift comparator format.

    Feed format (Alpaca via Redis / Netlify blobs):
        {symbol: {bid, ask, mid, spread_bps, timestamp, ...}}
        — OR legacy —
        {symbol: {bid_price, ask_price, midpoint_price, ...}}

    Comparator format:
        {symbol: {price: float}}
    """
    normalized = {}
    for symbol, q in feed_quotes.items():
        if not q or not isinstance(q, dict):
            continue
        if symbol == "_meta":
            continue
        # Prefer mid/midpoint_price, fall back to bid/ask average, then price
        price = (
            q.get("mid")
            or q.get("midpoint_price")
            or q.get("price")
            or (
                (q.get("bid", 0) + q.get("ask", 0)) / 2
                if q.get("bid") and q.get("ask")
                else None
            )
            or (
                (q.get("bid_price", 0) + q.get("ask_price", 0)) / 2
                if q.get("bid_price") and q.get("ask_price")
                else None
            )
        )
        if price and float(price) > 0:
            normalized[symbol] = {"price": float(price)}
    return normalized


# ---------------------------------------------------------------------------
# Realistic mock data matching allocation-engine-2.0 blob format
# ---------------------------------------------------------------------------

MOCK_MARKET_QUOTES = {
    "timestamp": "2026-03-23T14:30:00",
    "blob_key": "2026-03-23T14-30-00",
    "latest_quotes": {
        "BTC": {
            "bid_price": 67450.00,
            "ask_price": 67460.00,
            "midpoint_price": 67455.00,
            "bid_size": 0.5,
            "ask_size": 0.3,
            "spread_bps": 1.48,
            "timestamp": "2026-03-23T14:29:58Z",
        },
        "SPY": {
            "bid_price": 520.10,
            "ask_price": 520.15,
            "midpoint_price": 520.125,
            "bid_size": 100,
            "ask_size": 200,
            "spread_bps": 0.96,
            "timestamp": "2026-03-23T14:29:59Z",
        },
        "QQQ": {
            "bid_price": 440.50,
            "ask_price": 440.55,
            "midpoint_price": 440.525,
            "bid_size": 50,
            "ask_size": 75,
            "spread_bps": 1.13,
            "timestamp": "2026-03-23T14:29:59Z",
        },
        "IWM": {
            "bid_price": 205.30,
            "ask_price": 205.35,
            "midpoint_price": 205.325,
            "bid_size": 150,
            "ask_size": 120,
            "spread_bps": 2.43,
            "timestamp": "2026-03-23T14:29:58Z",
        },
    },
    "history_count": 48,
}

MOCK_OPTIONS_CHAIN = {
    "timestamp": "2026-03-23T14:30:05",
    "IWM": {
        "contracts": [
            {
                "symbol": "IWM260417C00210000",
                "type": "call",
                "strike": 210.0,
                "expiration": "2026-04-17",
                "last_trade": {"price": 3.45, "size": 10},
                "quote": {"bid": 3.40, "ask": 3.50, "midpoint": 3.45},
                "greeks": {
                    "delta": 0.42,
                    "gamma": 0.03,
                    "theta": -0.08,
                    "vega": 0.15,
                    "rho": 0.02,
                    "iv": 0.22,
                },
            },
            {
                "symbol": "IWM260417P00200000",
                "type": "put",
                "strike": 200.0,
                "expiration": "2026-04-17",
                "last_trade": {"price": 2.10, "size": 5},
                "quote": {"bid": 2.05, "ask": 2.15, "midpoint": 2.10},
                "greeks": {
                    "delta": -0.35,
                    "gamma": 0.028,
                    "theta": -0.07,
                    "vega": 0.14,
                    "rho": -0.015,
                    "iv": 0.24,
                },
            },
        ]
    },
}


# ---------------------------------------------------------------------------
# Tests: Offline (mock data format validation)
# ---------------------------------------------------------------------------


class TestNormalizeQuotes:
    """Verify quote normalization handles all allocation-feed formats."""

    def test_midpoint_preferred(self):
        quotes = normalize_feed_quotes(MOCK_MARKET_QUOTES["latest_quotes"])
        assert quotes["BTC"]["price"] == 67455.00
        assert quotes["SPY"]["price"] == 520.125

    def test_fallback_to_bid_ask_average(self):
        raw = {"AAPL": {"bid_price": 180.0, "ask_price": 182.0}}
        quotes = normalize_feed_quotes(raw)
        assert quotes["AAPL"]["price"] == 181.0

    def test_fallback_to_price_field(self):
        raw = {"AAPL": {"price": 180.5}}
        quotes = normalize_feed_quotes(raw)
        assert quotes["AAPL"]["price"] == 180.5

    def test_skips_none_entries(self):
        raw = {"AAPL": None, "BTC": {"midpoint_price": 67000}}
        quotes = normalize_feed_quotes(raw)
        assert "AAPL" not in quotes
        assert quotes["BTC"]["price"] == 67000


class TestComparatorWithFeedFormat:
    """Drift comparator works with allocation-engine-2.0 quote data."""

    def test_cold_start_all_symbols_need_fetch(self):
        comp = DriftComparator()
        quotes = normalize_feed_quotes(MOCK_MARKET_QUOTES["latest_quotes"])
        needs = comp.get_symbols_needing_fetch(quotes)
        assert set(needs) == {"BTC", "SPY", "QQQ", "IWM"}

    def test_no_drift_after_update(self):
        comp = DriftComparator(price_drift_pct=0.005)
        quotes = normalize_feed_quotes(MOCK_MARKET_QUOTES["latest_quotes"])

        # Simulate first cycle — update all symbols
        for sym, q in quotes.items():
            comp.update(sym, q["price"], {"current_price": q["price"]}, {})

        # Same quotes again — no drift
        needs = comp.get_symbols_needing_fetch(quotes)
        assert needs == []

    def test_detects_drift_on_price_move(self):
        comp = DriftComparator(price_drift_pct=0.005)
        quotes = normalize_feed_quotes(MOCK_MARKET_QUOTES["latest_quotes"])

        for sym, q in quotes.items():
            comp.update(sym, q["price"], {"current_price": q["price"]}, {})

        # BTC jumps 1%, others flat
        quotes["BTC"]["price"] = 67455.00 * 1.01  # +1%
        needs = comp.get_symbols_needing_fetch(quotes)
        assert needs == ["BTC"]

    def test_cached_metrics_patch_price_from_feed(self):
        comp = DriftComparator()
        metrics = {"current_price": 67455.0, "30d_high": 70000.0, "vol_30d": 0.45}
        comp.update("BTC", 67455.0, metrics, {})

        # New feed price, but no drift trigger
        patched = comp.get_cached_metrics("BTC", 67480.0)
        assert patched["current_price"] == 67480.0
        assert patched["30d_high"] == 70000.0  # preserved

    def test_high_vol_regime_tighter_threshold(self):
        comp = DriftComparator(price_drift_pct=0.005, high_vol_drift_pct=0.002)
        quotes = normalize_feed_quotes(MOCK_MARKET_QUOTES["latest_quotes"])

        for sym, q in quotes.items():
            comp.update(sym, q["price"], {"current_price": q["price"]}, {})

        # SPY moves 0.3% — below default, above high-vol
        quotes["SPY"]["price"] = 520.125 * 1.003
        assert comp.get_symbols_needing_fetch(quotes, regime="MEDIUM_VOL") == []
        assert comp.get_symbols_needing_fetch(quotes, regime="HIGH_VOL") == ["SPY"]


class TestOptionsChainIntegration:
    """Verify options chain data can enrich comparator decisions."""

    def test_iv_from_options_can_inform_regime(self):
        """Options IV could drive regime selection for drift thresholds."""
        chain = MOCK_OPTIONS_CHAIN["IWM"]["contracts"]
        avg_iv = sum(c["greeks"]["iv"] for c in chain) / len(chain)
        # avg_iv = 0.23 — moderate
        regime = "HIGH_VOL" if avg_iv > 0.30 else "MEDIUM_VOL"
        assert regime == "MEDIUM_VOL"
        assert avg_iv == pytest.approx(0.23, abs=0.01)

    def test_high_iv_triggers_tighter_thresholds(self):
        """When options IV is elevated, comparator uses tighter thresholds."""
        comp = DriftComparator(price_drift_pct=0.005, high_vol_drift_pct=0.002)
        comp.update("IWM", 205.325, {"current_price": 205.325}, {})

        # Simulate elevated IV from options chain
        mock_iv = 0.35  # elevated
        regime = "HIGH_VOL" if mock_iv > 0.30 else "MEDIUM_VOL"

        # 0.3% move — only triggers with HIGH_VOL regime
        new_price = 205.325 * 1.003
        assert comp.should_full_fetch("IWM", new_price, regime="MEDIUM_VOL") is False
        assert comp.should_full_fetch("IWM", new_price, regime=regime) is True


class TestRefreshOpenOrders:
    """Test pattern for refreshing metrics on symbols with open orders."""

    def test_force_fetch_for_open_order_symbols(self):
        comp = DriftComparator(price_drift_pct=0.005)
        quotes = normalize_feed_quotes(MOCK_MARKET_QUOTES["latest_quotes"])

        # Warm up all symbols
        for sym, q in quotes.items():
            comp.update(sym, q["price"], {"current_price": q["price"]}, {})

        # No drift, but IWM has an open order — force it into the fetch set
        open_order_symbols = {"IWM"}
        needs = comp.get_symbols_needing_fetch(quotes)
        fetch_set = set(needs) | open_order_symbols  # union with open orders

        assert "IWM" in fetch_set
        assert "BTC" not in fetch_set


# ---------------------------------------------------------------------------
# Tests: Live (only runs with Netlify credentials)
# ---------------------------------------------------------------------------

needs_netlify = pytest.mark.skipif(
    not (os.getenv("NETLIFY_API_TOKEN") and os.getenv("NETLIFY_SITE_ID")),
    reason="NETLIFY_API_TOKEN and NETLIFY_SITE_ID required",
)


@needs_netlify
class TestLiveFeed:
    """Integration tests against real Netlify blob stores."""

    def setup_method(self):
        self.client = AllocationFeedClient()
        self.comp = DriftComparator()

    def test_market_quotes_blob_readable(self):
        quotes = self.client.get_market_quotes()
        assert len(quotes) > 0, "market-quotes store is empty"
        # Filter out _meta key
        symbol_quotes = {k: v for k, v in quotes.items() if k != "_meta"}
        assert len(symbol_quotes) > 0, "No symbol quotes found"
        sample = next(iter(symbol_quotes.values()))
        assert any(
            k in sample
            for k in ("mid", "midpoint_price", "price", "bid", "bid_price")
        ), f"Unexpected quote format: {list(sample.keys())}"

    def test_comparator_with_live_quotes(self):
        raw = self.client.get_market_quotes()
        quotes = normalize_feed_quotes(raw)
        assert len(quotes) > 0

        # Cold start — all should need fetch
        needs = self.comp.get_symbols_needing_fetch(quotes)
        assert len(needs) == len(quotes)

        # Update all
        for sym, q in quotes.items():
            self.comp.update(sym, q["price"], {"current_price": q["price"]}, {})

        # Same quotes — none should need fetch
        needs = self.comp.get_symbols_needing_fetch(quotes)
        assert needs == []

    def test_options_chain_blob_readable(self):
        chain = self.client.get_options_chain()
        assert chain is not None, "options-chain store is empty"

    def test_list_available_stores(self):
        """Informational: show what's in each blob store."""
        for store in ("market-quotes", "options-chain", "order-book"):
            try:
                keys = self.client.list_blobs(store)
                print(f"\n  [{store}] {len(keys)} blobs, latest: {sorted(keys)[-1] if keys else 'none'}")
            except Exception as e:
                print(f"\n  [{store}] Error: {e}")
