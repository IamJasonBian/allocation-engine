class Order:
    def __init__(self, size, price, order_type, created_at=None, order_id=None):
        self.size = size
        self.price = price
        self.order_type = order_type
        self.is_valid = True
        self.created_at = created_at    # datetime or None
        self.order_id = order_id        # str or None

    def mark_invalid(self) -> bool:
        self.is_valid = False

    def get_state(self) -> dict:
        return {
            "size": self.size,
            "price": self.price,
            "order_type": self.order_type,
            "is_valid": self.is_valid
        }

