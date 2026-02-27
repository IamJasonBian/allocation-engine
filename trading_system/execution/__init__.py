"""
Execution Layer — Order Lifecycle Management

Handles multi-source price discovery, fill monitoring, staggered repricing
within a slippage budget, and Slack escalation on crossover events.
"""

from trading_system.execution.execution_manager import ExecutionManager, ExecutionContext
from trading_system.execution.price_discovery import PriceDiscovery
from trading_system.execution.fill_monitor import FillMonitor
from trading_system.execution.slippage_controller import SlippageController
from trading_system.execution.order_types import SmartOrderType, should_use_twap

__all__ = [
    "ExecutionManager",
    "ExecutionContext",
    "PriceDiscovery",
    "FillMonitor",
    "SlippageController",
    "SmartOrderType",
    "should_use_twap",
]
