"""Database repository layer providing thread-safe CRUD operations."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import OrderDB, GridConfigDB, GridLevelDB, FillDB, PositionDB, DailyPnlDB, RiskEventDB


def get_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Repository:
    @staticmethod
    def save_order(
        client_order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        price: Optional[float],
        quantity: int,
        status: str,
        product_id: Optional[int] = None,
        is_exit: bool = False,
        grid_level_id: Optional[int] = None,
        exchange_order_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> OrderDB:
        with SessionLocal() as db:
            order = db.query(OrderDB).filter(OrderDB.client_order_id == client_order_id).first()
            if not order:
                order = OrderDB(
                    client_order_id=client_order_id,
                    exchange_order_id=exchange_order_id,
                    product_id=product_id,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    price=price,
                    quantity=quantity,
                    status=status,
                    is_exit=is_exit,
                    grid_level_id=grid_level_id,
                    error_message=error_message,
                )
                order.add_history_event("INIT", status, "Order created")
                db.add(order)
            else:
                old_status = order.status
                order.status = status
                if exchange_order_id:
                    order.exchange_order_id = exchange_order_id
                if error_message:
                    order.error_message = error_message
                order.add_history_event(old_status, status, "Order updated")
            db.commit()
            db.refresh(order)
            return order

    @staticmethod
    def get_order_by_client_id(client_order_id: str) -> Optional[OrderDB]:
        with SessionLocal() as db:
            return db.query(OrderDB).filter(OrderDB.client_order_id == client_order_id).first()

    @staticmethod
    def get_order_by_exchange_id(exchange_order_id: str) -> Optional[OrderDB]:
        with SessionLocal() as db:
            return db.query(OrderDB).filter(OrderDB.exchange_order_id == exchange_order_id).first()

    @staticmethod
    def get_open_orders() -> List[OrderDB]:
        with SessionLocal() as db:
            return db.query(OrderDB).filter(OrderDB.status.in_(["SUBMITTED", "OPEN", "PARTIALLY_FILLED"])).all()

    @staticmethod
    def get_all_orders(limit: int = 100) -> List[OrderDB]:
        with SessionLocal() as db:
            return db.query(OrderDB).order_by(OrderDB.created_at.desc()).limit(limit).all()

    @staticmethod
    def record_fill(
        client_order_id: str,
        symbol: str,
        side: str,
        fill_price: Optional[float] = None,
        fill_quantity: Optional[int] = None,
        fee: float = 0.0,
        realized_pnl: float = 0.0,
        exchange_order_id: Optional[str] = None,
        **kwargs: Any
    ) -> FillDB:
        actual_price = float(fill_price if fill_price is not None else kwargs.get("price", 0.0))
        actual_qty = int(fill_quantity if fill_quantity is not None else kwargs.get("quantity", 1))
        with SessionLocal() as db:
            fill = FillDB(
                client_order_id=client_order_id,
                exchange_order_id=exchange_order_id,
                symbol=symbol,
                side=side,
                fill_price=actual_price,
                fill_quantity=actual_qty,
                fee=fee,
                realized_pnl=realized_pnl
            )
            db.add(fill)

            # Update order record
            order = db.query(OrderDB).filter(OrderDB.client_order_id == client_order_id).first()
            if order:
                order.filled_quantity = (order.filled_quantity or 0) + actual_qty
                old_status = order.status
                if order.filled_quantity >= order.quantity:
                    order.status = "FILLED"
                    order.add_history_event(old_status, "FILLED", f"Fully filled at {actual_price}")
                else:
                    order.status = "PARTIALLY_FILLED"
                    order.add_history_event(old_status, "PARTIALLY_FILLED", f"Partially filled {actual_qty} at {actual_price}")

            # Update daily PnL
            today = get_today_str()
            daily = db.query(DailyPnlDB).filter(DailyPnlDB.date == today).first()
            if not daily:
                daily = DailyPnlDB(date=today, realized_pnl=realized_pnl, fees_paid=fee, net_pnl=realized_pnl - fee)
                db.add(daily)
            else:
                daily.realized_pnl += realized_pnl
                daily.fees_paid += fee
                daily.net_pnl = daily.realized_pnl - daily.fees_paid

            db.commit()
            return fill

    # Backward-compatible alias
    save_fill = record_fill

    @staticmethod
    def get_trades(limit: int = 50) -> List[FillDB]:
        with SessionLocal() as db:
            return db.query(FillDB).order_by(FillDB.timestamp.desc()).limit(limit).all()

    @staticmethod
    def get_daily_pnl(date_str: Optional[str] = None) -> DailyPnlDB:
        today = date_str or get_today_str()
        with SessionLocal() as db:
            record = db.query(DailyPnlDB).filter(DailyPnlDB.date == today).first()
            if not record:
                record = DailyPnlDB(date=today, realized_pnl=0.0, unrealized_pnl=0.0, fees_paid=0.0, net_pnl=0.0)
                db.add(record)
                db.commit()
                db.refresh(record)
            return record

    @staticmethod
    def update_position(
        symbol: str,
        size: int,
        entry_price: float,
        mark_price: float = 0.0,
        realized_pnl: float = 0.0,
        unrealized_pnl: float = 0.0
    ) -> PositionDB:
        with SessionLocal() as db:
            effective_mark = mark_price if mark_price > 0 else entry_price
            pos = db.query(PositionDB).filter(PositionDB.symbol == symbol).first()
            if not pos:
                pos = PositionDB(
                    symbol=symbol,
                    size=size,
                    entry_price=entry_price,
                    mark_price=effective_mark,
                    realized_pnl=realized_pnl,
                    unrealized_pnl=unrealized_pnl
                )
                db.add(pos)
            else:
                pos.size = size
                pos.entry_price = entry_price
                pos.mark_price = effective_mark
                pos.realized_pnl = realized_pnl
                pos.unrealized_pnl = unrealized_pnl
            db.commit()
            db.refresh(pos)
            return pos

    @staticmethod
    def get_position(symbol: str) -> Optional[PositionDB]:
        with SessionLocal() as db:
            return db.query(PositionDB).filter(PositionDB.symbol == symbol).first()

    @staticmethod
    def log_risk_event(event_type: str, severity: str, description: str) -> RiskEventDB:
        with SessionLocal() as db:
            event = RiskEventDB(event_type=event_type, severity=severity, description=description)
            db.add(event)
            db.commit()
            db.refresh(event)
            return event

    @staticmethod
    def get_risk_events(limit: int = 50) -> List[RiskEventDB]:
        with SessionLocal() as db:
            return db.query(RiskEventDB).order_by(RiskEventDB.timestamp.desc()).limit(limit).all()

    @staticmethod
    def save_grid_config(config_dict: Dict[str, Any]) -> GridConfigDB:
        with SessionLocal() as db:
            valid_keys = {c.name for c in GridConfigDB.__table__.columns}
            filtered_dict = {k: v for k, v in config_dict.items() if k in valid_keys and k != "id"}
            cfg = db.query(GridConfigDB).filter(GridConfigDB.id == 1).first()
            if not cfg:
                cfg = GridConfigDB(id=1, **filtered_dict)
                db.add(cfg)
            else:
                for k, v in filtered_dict.items():
                    setattr(cfg, k, v)
            db.commit()
            db.refresh(cfg)
            return cfg

    @staticmethod
    def get_grid_config() -> Optional[GridConfigDB]:
        with SessionLocal() as db:
            return db.query(GridConfigDB).filter(GridConfigDB.id == 1).first()

    @staticmethod
    def save_grid_level(
        level_id: int,
        price: float,
        target_side: str,
        status: str = "VACANT",
        entry_client_order_id: Optional[str] = None,
        exit_client_order_id: Optional[str] = None,
        paired_price: Optional[float] = None,
        filled_qty: int = 0,
        cycle_count: int = 0
    ) -> GridLevelDB:
        with SessionLocal() as db:
            lvl = db.query(GridLevelDB).filter(GridLevelDB.level_id == level_id).first()
            if not lvl:
                lvl = GridLevelDB(
                    level_id=level_id,
                    price=price,
                    target_side=target_side,
                    status=status,
                    entry_client_order_id=entry_client_order_id,
                    exit_client_order_id=exit_client_order_id,
                    paired_price=paired_price,
                    filled_qty=filled_qty,
                    cycle_count=cycle_count
                )
                db.add(lvl)
            else:
                lvl.price = price
                lvl.target_side = target_side
                lvl.status = status
                lvl.entry_client_order_id = entry_client_order_id
                lvl.exit_client_order_id = exit_client_order_id
                lvl.paired_price = paired_price
                lvl.filled_qty = filled_qty
                lvl.cycle_count = cycle_count
            db.commit()
            db.refresh(lvl)
            return lvl

    @staticmethod
    def get_all_grid_levels() -> List[GridLevelDB]:
        with SessionLocal() as db:
            return db.query(GridLevelDB).order_by(GridLevelDB.level_id.asc()).all()

    @staticmethod
    def clear_grid_levels() -> None:
        with SessionLocal() as db:
            db.query(GridLevelDB).delete()
            db.commit()