from trading_system.entities.Order import Order
from trading_system.entities.OrderType import OrderType, OrderSource
from trading_system.entities.Ticker import Ticker

def make_orders():
    return [
        Order(100, 50.0, OrderType.MARKET),
        Order(200, 75.0, OrderType.LIMIT),
        Order(150, 60.0, OrderType.STOP),
    ]

class TestGetOpenOrders:
    def test_returns_all(self):
        orders = make_orders()
        ticker = Ticker(orders)
        assert ticker.get_open_orders() == orders

    def test_empty(self):
        ticker = Ticker([])
        assert ticker.get_open_orders() == []

class TestGetSignalOrders:
    def test_initially_all_valid(self):
        orders = make_orders()
        ticker = Ticker(orders)
        assert len(ticker.get_signal_orders()) == 3
        assert ticker.get_signal_orders() == orders

    def test_filters_invalid(self):
        orders = make_orders()
        ticker = Ticker(orders)
        orders[1].mark_invalid()
        valid = ticker.get_signal_orders()
        assert len(valid) == 2
        assert orders[1] not in valid
        assert orders[0] in valid
        assert orders[2] in valid

class TestGetEngineOrders:
    def test_returns_engine_orders(self):
        orders = [
            Order(100, 50.0, OrderType.MARKET, source=OrderSource.ENGINE),
            Order(200, 75.0, OrderType.LIMIT, source=OrderSource.EXTERNAL),
            Order(150, 60.0, OrderType.STOP, source=OrderSource.ENGINE),
        ]
        ticker = Ticker(orders)
        engine = ticker.get_engine_orders()
        assert len(engine) == 2
        assert all(o.source == OrderSource.ENGINE for o in engine)

    def test_empty_when_no_engine_orders(self):
        orders = [
            Order(100, 50.0, OrderType.MARKET, source=OrderSource.EXTERNAL),
        ]
        ticker = Ticker(orders)
        assert ticker.get_engine_orders() == []

    def test_excludes_none_source(self):
        orders = [
            Order(100, 50.0, OrderType.MARKET),
            Order(200, 75.0, OrderType.LIMIT, source=OrderSource.ENGINE),
        ]
        ticker = Ticker(orders)
        assert len(ticker.get_engine_orders()) == 1


class TestGetExternalOrders:
    def test_returns_external_orders(self):
        orders = [
            Order(100, 50.0, OrderType.MARKET, source=OrderSource.ENGINE),
            Order(200, 75.0, OrderType.LIMIT, source=OrderSource.EXTERNAL),
            Order(150, 60.0, OrderType.STOP, source=OrderSource.EXTERNAL),
        ]
        ticker = Ticker(orders)
        external = ticker.get_external_orders()
        assert len(external) == 2
        assert all(o.source == OrderSource.EXTERNAL for o in external)

    def test_empty_when_no_external_orders(self):
        orders = [
            Order(100, 50.0, OrderType.MARKET, source=OrderSource.ENGINE),
        ]
        ticker = Ticker(orders)
        assert ticker.get_external_orders() == []


class TestOrderValidity:
    def test_orders_start_valid(self):
        orders = make_orders()
        ticker = Ticker(orders)
        assert all(order.is_valid for order in orders)

    def test_mark_invalid(self):
        orders = make_orders()
        ticker = Ticker(orders)
        orders[0].mark_invalid()
        assert orders[0].is_valid is False
        assert len(ticker.get_signal_orders()) == 2