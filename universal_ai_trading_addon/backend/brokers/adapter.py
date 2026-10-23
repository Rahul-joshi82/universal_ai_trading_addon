from abc import ABC, abstractmethod
from typing import Any
from ..models import OrderRequest, PaperOrder

class BrokerAdapter(ABC):
    """Normalized broker interface. Real implementations must use official APIs."""
    @abstractmethod
    async def get_account(self) -> dict[str, Any]: ...

    @abstractmethod
    async def get_positions(self) -> dict[str, int]: ...

    @abstractmethod
    async def get_open_orders(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def place_order(self, order: OrderRequest, reference_price: float) -> PaperOrder: ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> dict[str, Any]: ...
