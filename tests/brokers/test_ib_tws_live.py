"""
Optional live TWS integration (paper by default on port 7496).

Set environment variable ``TWS_INTEGRATION=1`` to run. Read-only tests are always
safe. Set ``TWS_PLACE_ORDER=1`` only on **paper** to submit and cancel a silly
limit order (verify you are not on live).

Example::

    TWS_INTEGRATION=1 python3 -m pytest tests/brokers/test_ib_tws_live.py -v
    TWS_INTEGRATION=1 TWS_PLACE_ORDER=1 python3 -m pytest tests/brokers/test_ib_tws_live.py -v -k place
"""

from __future__ import annotations

import os

import pytest

from trading_system.brokers.ib_tws_client import IbTwsClient, TwsClientConfig


pytestmark = pytest.mark.integration


def _skip_if_disabled() -> None:
    if os.environ.get("TWS_INTEGRATION") != "1":
        pytest.skip("set TWS_INTEGRATION=1 and run TWS with API on 7496 (paper)")


def test_live_account_and_positions() -> None:
    _skip_if_disabled()
    port = int(os.environ.get("TWS_PORT", "7496"))
    client_id = int(os.environ.get("TWS_CLIENT_ID", "7"))
    c = IbTwsClient(TwsClientConfig(port=port, client_id=client_id))
    assert c.connect() is True
    try:
        vals = c.get_account_values()
        assert isinstance(vals, list)
        if vals:
            assert "tag" in vals[0]
        sm = c.get_account_summary_map()
        assert isinstance(sm, dict)
        pos = c.get_positions()
        assert isinstance(pos, list)
    finally:
        c.disconnect()


def test_live_place_limit_and_cancel_spy() -> None:
    """Submit a deep OTM limit that should not fill, then cancel."""
    _skip_if_disabled()
    if os.environ.get("TWS_PLACE_ORDER") != "1":
        pytest.skip("set TWS_PLACE_ORDER=1 to test order submit+cancel (paper only)")

    port = int(os.environ.get("TWS_PORT", "7496"))
    if port == 7497:
        pytest.fail("Refusing to place test order on live port 7497; use paper 7496")

    client_id = int(os.environ.get("TWS_CLIENT_ID", "8"))
    c = IbTwsClient(TwsClientConfig(port=port, client_id=client_id))
    assert c.connect() is True
    try:
        # Far below market — still not risk-free; paper only
        oid = c.place_limit_order("SPY", "BUY", 1, 1.0)
        assert oid > 0
        assert c.cancel_order(oid) is True
    finally:
        c.disconnect()
