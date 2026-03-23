"""Tests for DriftComparator pushdown logic."""

import time
from unittest.mock import patch

from trading_system.drift_comparator import DriftComparator


class TestShouldFullFetch:
    def test_cold_start_always_fetches(self):
        comp = DriftComparator()
        assert comp.should_full_fetch("BTC", 100.0) is True

    def test_no_fetch_when_price_flat(self):
        comp = DriftComparator(price_drift_pct=0.005)
        comp.update("BTC", 100.0, {"current_price": 100.0}, {})
        # 0.1% move — below 0.5% threshold
        assert comp.should_full_fetch("BTC", 100.1) is False

    def test_fetch_when_price_drifts(self):
        comp = DriftComparator(price_drift_pct=0.005)
        comp.update("BTC", 100.0, {"current_price": 100.0}, {})
        # 1% move — above 0.5% threshold
        assert comp.should_full_fetch("BTC", 101.0) is True

    def test_fetch_on_price_drop(self):
        comp = DriftComparator(price_drift_pct=0.005)
        comp.update("BTC", 100.0, {"current_price": 100.0}, {})
        assert comp.should_full_fetch("BTC", 99.0) is True

    def test_high_vol_tighter_threshold(self):
        comp = DriftComparator(price_drift_pct=0.005, high_vol_drift_pct=0.002)
        comp.update("BTC", 100.0, {"current_price": 100.0}, {})
        # 0.3% move — below default but above high_vol threshold
        assert comp.should_full_fetch("BTC", 100.3, regime="MEDIUM_VOL") is False
        assert comp.should_full_fetch("BTC", 100.3, regime="HIGH_VOL") is True

    def test_staleness_forces_fetch(self):
        comp = DriftComparator(max_stale_minutes=0.01)  # ~0.6 seconds
        comp.update("BTC", 100.0, {"current_price": 100.0}, {})
        time.sleep(0.7)
        # Price hasn't moved but data is stale
        assert comp.should_full_fetch("BTC", 100.0) is True


class TestCachedMetrics:
    def test_returns_none_for_unknown_symbol(self):
        comp = DriftComparator()
        assert comp.get_cached_metrics("BTC", 100.0) is None

    def test_patches_current_price(self):
        comp = DriftComparator()
        original = {"current_price": 100.0, "30d_high": 110.0, "30d_low": 90.0}
        comp.update("BTC", 100.0, original, {})
        patched = comp.get_cached_metrics("BTC", 105.0)
        assert patched["current_price"] == 105.0
        assert patched["30d_high"] == 110.0
        assert patched["30d_low"] == 90.0

    def test_does_not_mutate_stored_metrics(self):
        comp = DriftComparator()
        original = {"current_price": 100.0}
        comp.update("BTC", 100.0, original, {})
        comp.get_cached_metrics("BTC", 999.0)
        # Original stored state should be unchanged
        assert comp._state["BTC"].last_metrics["current_price"] == 100.0


class TestGetSymbolsNeedingFetch:
    def test_returns_all_on_cold_start(self):
        comp = DriftComparator()
        quotes = {
            "BTC": {"price": 100.0},
            "SPY": {"price": 500.0},
        }
        result = comp.get_symbols_needing_fetch(quotes)
        assert set(result) == {"BTC", "SPY"}

    def test_filters_to_drifted_only(self):
        comp = DriftComparator(price_drift_pct=0.005)
        comp.update("BTC", 100.0, {}, {})
        comp.update("SPY", 500.0, {}, {})
        quotes = {
            "BTC": {"price": 100.01},  # 0.01% — no drift
            "SPY": {"price": 510.0},   # 2% — drifted
        }
        result = comp.get_symbols_needing_fetch(quotes)
        assert result == ["SPY"]

    def test_skips_missing_quotes(self):
        comp = DriftComparator()
        quotes = {
            "BTC": {"price": 100.0},
            "SPY": None,
        }
        result = comp.get_symbols_needing_fetch(quotes)
        assert "SPY" not in result


class TestHasState:
    def test_false_before_update(self):
        comp = DriftComparator()
        assert comp.has_state("BTC") is False

    def test_true_after_update(self):
        comp = DriftComparator()
        comp.update("BTC", 100.0, {}, {})
        assert comp.has_state("BTC") is True
