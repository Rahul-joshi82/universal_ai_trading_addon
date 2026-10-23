"""Base quantitative strategy interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List
from ..models import MarketDataSnapshot


class BaseStrategy(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        """Set up strategy levels, load state, and validate parameters."""
        ...

    @abstractmethod
    async def on_market_update(self, snapshot: MarketDataSnapshot) -> None:
        """Handle incoming ticker or candle update."""
        ...

    @abstractmethod
    async def pause(self) -> None:
        """Pause strategy entries."""
        ...

    @abstractmethod
    async def resume(self) -> None:
        """Resume strategy execution."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop strategy execution and cleanup."""
        ...
