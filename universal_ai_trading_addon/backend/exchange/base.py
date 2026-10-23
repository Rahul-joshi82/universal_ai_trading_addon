"""Abstract exchange adapter interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from ..models import OrderRequest, MarketDataSnapshot


class ExchangeAdapter(ABC):
    """Normalized asynchronous broker/exchange adapter."""

    @abstractmethod
    async def get_products(self) -> List[Dict[str, Any]]:
        """Retrieve tradable products / instruments metadata."""
        ...

    @abstractmethod
    async def get_ticker(self, symbol: str) -> MarketDataSnapshot:
        """Fetch current ticker / snapshot for symbol."""
        ...

    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 10) -> Dict[str, Any]:
        """Fetch L2 orderbook."""
        ...

    @abstractmethod
    async def get_account(self) -> Dict[str, Any]:
        """Fetch account balances and equity."""
        ...

    @abstractmethod
    async def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch open positions."""
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch open orders."""
        ...

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> Dict[str, Any]:
        """Submit a new order to the exchange."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str, product_id: Optional[int] = None) -> Dict[str, Any]:
        """Cancel an open order."""
        ...

    @abstractmethod
    async def cancel_all_orders(self, product_id: Optional[int] = None) -> Dict[str, Any]:
        """Cancel all open orders."""
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Query single order status."""
        ...
