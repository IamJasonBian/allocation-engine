"""Broker adapters (mock and future live clients)."""

from __future__ import annotations

import typing

from trading_system.brokers.ibkr_mock import MockIbkrClient

# Lazy: importing ib_tws_client pulls ib_insync (heavy); keep MockIbkrClient import cheap.
if typing.TYPE_CHECKING:
    from trading_system.brokers.ib_tws_client import IbTwsClient as IbTwsClient
    from trading_system.brokers.ib_tws_client import TwsClientConfig as TwsClientConfig

__all__ = ["IbTwsClient", "TwsClientConfig", "MockIbkrClient"]


def __getattr__(name: str):
    if name in ("IbTwsClient", "TwsClientConfig"):
        from trading_system.brokers import ib_tws_client as _tws

        return getattr(_tws, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
