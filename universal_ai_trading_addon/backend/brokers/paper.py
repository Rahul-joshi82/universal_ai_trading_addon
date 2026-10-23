from uuid import uuid4
from ..models import OrderRequest, PaperOrder, Side
from ..risk import validate_order

class PaperBroker:
    def __init__(self):
        self.cash = 100_000.0
        self.positions: dict[str, int] = {}
        self.orders: dict[str, dict] = {}
        self.realized_pnl = 0.0

    async def get_account(self) -> dict:
        return {"mode": "PAPER", "cash": round(self.cash, 2), "realized_pnl": round(self.realized_pnl, 2)}

    async def get_positions(self) -> dict[str, int]:
        return dict(self.positions)

    async def get_open_orders(self) -> list[dict]:
        return [o for o in self.orders.values() if o["status"] == "OPEN"]

    async def place_order(self, order: OrderRequest, reference_price: float) -> PaperOrder:
        position = self.positions.get(order.instrument, 0)
        validate_order(order, position)
        fill_price = order.limit_price if order.order_type.value == "LIMIT" else reference_price
        if fill_price is None:
            raise ValueError("Unable to determine fill price")
        signed_qty = order.quantity if order.side == Side.BUY else -order.quantity
        # Educational simulator: cash and position bookkeeping only; excludes fees, margin and realistic slippage.
        self.cash -= signed_qty * fill_price
        self.positions[order.instrument] = position + signed_qty
        order_id = str(uuid4())
        record = {
            "order_id": order_id, "instrument": order.instrument, "side": order.side.value,
            "quantity": order.quantity, "order_type": order.order_type.value,
            "status": "FILLED", "fill_price": round(fill_price, 2)
        }
        self.orders[order_id] = record
        return PaperOrder(**record, message="Simulated fill only; no live broker order was sent.")

    async def get_order_status(self, order_id: str) -> dict:
        if order_id not in self.orders:
            return {"order_id": order_id, "status": "NOT_FOUND"}
        return self.orders[order_id]
