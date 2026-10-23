
"""In-memory paper broker with deterministic risk checks."""
from __future__ import annotations

import math
from dataclasses import dataclass

from llm_analyst import TradingSignal


@dataclass
class Position:
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: float


class MockBroker:
    """Paper-only broker. No live orders are sent; state resets on restart."""

    def __init__(self, initial_cash: float):
        if not math.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("Initial cash must be positive and finite")
        self.cash = initial_cash
        self.position: Position | None = None
        self.realized_pnl = 0.0

    def buy(self, quantity: int, price: float, stop: float, target: float) -> str:
        if quantity < 1 or quantity * price > self.cash:
            return "REJECTED: invalid quantity or insufficient cash"
        if self.position is not None:
            return "REJECTED: position already open"
        if not (0 < stop < price < target):
            return "REJECTED: invalid stop/entry/target"
        self.cash -= quantity * price
        self.position = Position(quantity, price, stop, target)
        return f"PAPER BUY {quantity} units @ {price:.4f}"

    def sell(self, price: float, reason: str) -> str:
        if self.position is None:
            return "NO TRADE: no open long position"
        p = self.position
        self.cash += p.quantity * price
        trade_pnl = (price - p.entry_price) * p.quantity
        self.realized_pnl += trade_pnl
        self.position = None
        return (
            f"PAPER SELL {p.quantity} units @ {price:.4f}; "
            f"reason={reason}; trade_pnl={trade_pnl:.2f}; "
            f"total_realized_pnl={self.realized_pnl:.2f}"
        )


class RiskManagedExecutor:
    def __init__(
        self,
        broker: MockBroker,
        min_confidence: float = 0.70,
        max_position_fraction: float = 0.10,
        default_stop_fraction: float = 0.03,
        take_profit_fraction: float = 0.06,
    ):
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be in [0, 1]")
        if not 0 < max_position_fraction <= 1:
            raise ValueError("max_position_fraction must be in (0, 1]")
        if not 0 < default_stop_fraction < 1 or take_profit_fraction <= 0:
            raise ValueError("Invalid stop-loss or take-profit configuration")
        self.broker = broker
        self.min_confidence = min_confidence
        self.max_position_fraction = max_position_fraction
        self.default_stop_fraction = default_stop_fraction
        self.take_profit_fraction = take_profit_fraction

    def check_protective_exits(self, current_price: float) -> str | None:
        """Check the latest observed price against paper stop/target levels."""
        if not math.isfinite(current_price) or current_price <= 0:
            return "REJECTED: invalid market price"
        p = self.broker.position
        if p is None:
            return None
        if current_price <= p.stop_loss:
            return self.broker.sell(current_price, "stop_loss")
        if current_price >= p.take_profit:
            return self.broker.sell(current_price, "take_profit")
        return None

    def execute(self, signal: TradingSignal, current_price: float) -> str:
        if not math.isfinite(current_price) or current_price <= 0:
            return "REJECTED: invalid market price"

        exit_action = self.check_protective_exits(current_price)
        if exit_action:
            return exit_action

        if signal.confidence_score < self.min_confidence:
            return "NO TRADE: confidence below threshold"
        if signal.signal == "HOLD":
            return "NO TRADE: HOLD signal"
        if signal.signal == "SELL":
            return self.broker.sell(current_price, "LLM_SELL")
        if self.broker.position is not None:
            return "NO TRADE: position already open"

        proposed = signal.recommended_stop_loss
        stop = (
            current_price * (1 - self.default_stop_fraction)
            if proposed is None else proposed
        )
        if not math.isfinite(stop) or not 0 < stop < current_price:
            return "REJECTED: invalid BUY stop loss"
        if current_price - stop > current_price * self.default_stop_fraction:
            return "REJECTED: stop loss exceeds configured maximum distance"

        budget = self.broker.cash * self.max_position_fraction
        quantity = math.floor(budget / current_price)
        if quantity < 1:
            return "NO TRADE: insufficient cash for one unit"
        target = current_price * (1 + self.take_profit_fraction)
        return self.broker.buy(quantity, current_price, stop, target)
