"""
Smart Order Types — TWAP stub for future use.
"""

from enum import Enum


class SmartOrderType(Enum):
    IMMEDIATE = "immediate"  # Default: single limit order
    TWAP = "twap"            # Future: time-split across ticks


def should_use_twap(quantity, lot_size=250) -> bool:
    """True if quantity > 2x lot_size (future use, returns False for now)."""
    return False
