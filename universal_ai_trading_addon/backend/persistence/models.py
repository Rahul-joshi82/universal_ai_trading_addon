"""SQLAlchemy ORM models for order lifecycle, grid state, positions, and risk."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, Float, String, Boolean, DateTime, Text
from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrderDB(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_order_id = Column(String(64), unique=True, index=True, nullable=False)
    exchange_order_id = Column(String(64), nullable=True, index=True)
    product_id = Column(Integer, nullable=True)
    symbol = Column(String(32), index=True, nullable=False)
    side = Column(String(16), nullable=False)
    order_type = Column(String(16), nullable=False)
    price = Column(Float, nullable=True)
    quantity = Column(Integer, nullable=False)
    filled_quantity = Column(Integer, default=0)
    status = Column(String(32), index=True, nullable=False)
    is_exit = Column(Boolean, default=False)
    grid_level_id = Column(Integer, nullable=True)
    lifecycle_history = Column(Text, default="[]")  # JSON encoded list of status events
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    def add_history_event(self, old_status: str, new_status: str, reason: str = ""):
        try:
            history = json.loads(self.lifecycle_history or "[]")
        except Exception:
            history = []
        history.append({
            "from": old_status,
            "to": new_status,
            "reason": reason,
            "timestamp": utcnow().isoformat()
        })
        self.lifecycle_history = json.dumps(history)


class GridConfigDB(Base):
    __tablename__ = "grid_config"

    id = Column(Integer, primary_key=True, default=1)
    instrument = Column(String(32), default="BTCUSD")
    center_price = Column(Float, nullable=False)
    upper_price = Column(Float, nullable=False)
    lower_price = Column(Float, nullable=False)
    grid_levels = Column(Integer, default=10)
    spacing_type = Column(String(16), default="arithmetic")
    quantity_per_level = Column(Integer, default=1)
    max_open_orders = Column(Integer, default=10)
    max_inventory = Column(Integer, default=10)
    stop_loss_price = Column(Float, nullable=True)
    take_profit_price = Column(Float, nullable=True)
    recenter_enabled = Column(Boolean, default=False)
    recenter_threshold_pct = Column(Float, default=0.05)
    max_spread_pct = Column(Float, default=0.005)
    max_data_age_seconds = Column(Float, default=10.0)
    estimated_fee_rate = Column(Float, default=0.0005)
    estimated_slippage_pct = Column(Float, default=0.0002)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class GridLevelDB(Base):
    __tablename__ = "grid_levels"

    level_id = Column(Integer, primary_key=True)
    price = Column(Float, nullable=False)
    target_side = Column(String(16), nullable=False)
    status = Column(String(32), default="VACANT")  # VACANT, ENTRY_OPEN, ENTRY_FILLED, EXIT_OPEN, EXIT_FILLED
    entry_client_order_id = Column(String(64), nullable=True)
    exit_client_order_id = Column(String(64), nullable=True)
    paired_price = Column(Float, nullable=True)
    filled_qty = Column(Integer, default=0)
    cycle_count = Column(Integer, default=0)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class FillDB(Base):
    __tablename__ = "fills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_order_id = Column(String(64), index=True)
    exchange_order_id = Column(String(64), nullable=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(16), nullable=False)
    fill_price = Column(Float, nullable=False)
    fill_quantity = Column(Integer, nullable=False)
    fee = Column(Float, default=0.0)
    realized_pnl = Column(Float, default=0.0)
    timestamp = Column(DateTime, default=utcnow)


class PositionDB(Base):
    __tablename__ = "positions"

    symbol = Column(String(32), primary_key=True)
    size = Column(Integer, default=0)
    entry_price = Column(Float, default=0.0)
    mark_price = Column(Float, default=0.0)
    realized_pnl = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class DailyPnlDB(Base):
    __tablename__ = "daily_pnl"

    date = Column(String(16), primary_key=True)  # YYYY-MM-DD
    realized_pnl = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    fees_paid = Column(Float, default=0.0)
    net_pnl = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class RiskEventDB(Base):
    __tablename__ = "risk_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False)
    severity = Column(String(16), default="WARNING")
    description = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=utcnow)
