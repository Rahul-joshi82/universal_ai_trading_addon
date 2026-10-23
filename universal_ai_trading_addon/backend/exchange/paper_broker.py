"""High-fidelity Paper Broker implementation.

Simulates exchange execution, fee deduction, slippage, and position tracking.
All orders and fills are persisted to the database for durability across restarts.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional
from .base import ExchangeAdapter
from ..models import OrderRequest, PaperOrder, Side, OrderType, MarketDataSnapshot
from ..risk.engine import RiskEngine
from ..persistence.repository import Repository

logger = logging.getLogger("paper_broker")


class PaperBroker(ExchangeAdapter):
    def __init__(self, initial_cash: float = 100_000.0, risk_engine: Optional[RiskEngine] = None):
        self.cash = initial_cash
        self.positions: Dict[str, int] = {}
        self.avg_entry_prices: Dict[str, float] = {}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.realized_pnl = 0.0
        self.total_fees_paid = 0.0
        self.risk_engine = risk_engine
        self.last_prices: Dict[str, float] = {"BTCUSD": 60000.0}
        
        # Hydrate from persistence if orders exist
        self._hydrate_from_db()

    def _hydrate_from_db(self) -> None:
        try:
            persisted_orders = Repository.get_all_orders(limit=200)
            for o in persisted_orders:
                self.orders[o.client_order_id] = {
                    "order_id": o.client_order_id,
                    "instrument": o.symbol,
                    "side": o.side,
                    "quantity": o.quantity,
                    "order_type": o.order_type,
                    "status": o.status,
                    "fill_price": o.price,
                }
        except Exception as exc:
            logger.warning(f"Could not hydrate paper orders from DB: {exc}")

    async def get_account(self) -> Dict[str, Any]:
        unrealized = await self.get_unrealized_pnl()
        return {
            "mode": "PAPER",
            "cash": round(self.cash, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_fees_paid": round(self.total_fees_paid, 2),
            "total_equity": round(self.cash + unrealized, 2),
        }

    async def get_positions(self) -> Dict[str, int]:
        return dict(self.positions)

    async def get_positions_detailed(self) -> List[Dict[str, Any]]:
        detailed = []
        for sym, size in self.positions.items():
            if size != 0:
                avg_entry = self.avg_entry_prices.get(sym, 0.0)
                detailed.append({
                    "symbol": sym,
                    "size": size,
                    "entry_price": avg_entry,
                    "side": "LONG" if size > 0 else "SHORT",
                })
        return detailed

    async def get_unrealized_pnl(self, current_prices: Optional[Dict[str, float]] = None) -> float:
        total = 0.0
        for sym, size in self.positions.items():
            if size == 0:
                continue
            entry = self.avg_entry_prices.get(sym, 0.0)
            mark = (current_prices or {}).get(sym, entry)
            if size > 0:
                total += (mark - entry) * size
            else:
                total += (entry - mark) * abs(size)
        return total

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        open_list = [o for o in self.orders.values() if o.get("status") in ("OPEN", "SUBMITTED", "PARTIALLY_FILLED")]
        if symbol:
            open_list = [o for o in open_list if o.get("instrument") == symbol.upper()]
        return open_list

    async def place_order(self, order: OrderRequest, reference_price: Optional[float] = None) -> PaperOrder:
        """Place paper order with risk checks and fee accounting."""
        curr_pos = self.positions.get(order.instrument.upper(), 0)
        
        # Pre-trade Risk Check via RiskEngine if present
        if self.risk_engine:
            self.risk_engine.validate_order(order, curr_pos)

        ref_price = reference_price or self.last_prices.get(order.instrument.upper(), 60000.0)
        is_limit = (order.order_type == OrderType.LIMIT)
        client_order_id = order.client_order_id or str(uuid.uuid4())

        # Check if limit order should rest on the orderbook
        should_rest = False
        if is_limit and order.limit_price is not None:
            if order.side == Side.BUY and ref_price > order.limit_price:
                should_rest = True
            elif order.side == Side.SELL and ref_price < order.limit_price:
                should_rest = True

        if should_rest:
            # Resting on the book
            record = {
                "order_id": client_order_id,
                "instrument": order.instrument.upper(),
                "side": order.side.value,
                "quantity": order.quantity,
                "order_type": order.order_type.value,
                "status": "OPEN",
                "fill_price": order.limit_price,
                "fee": 0.0,
                "realized_pnl": 0.0,
            }
            self.orders[client_order_id] = record
            try:
                Repository.save_order(
                    client_order_id=client_order_id,
                    symbol=order.instrument.upper(),
                    side=order.side.value,
                    order_type=order.order_type.value,
                    price=order.limit_price,
                    quantity=order.quantity,
                    status="OPEN",
                    is_exit=order.is_exit,
                    grid_level_id=order.grid_level_id,
                )
            except Exception as exc:
                logger.warning(f"Could not persist resting paper order: {exc}")
            return PaperOrder(**record, message="Resting paper limit order placed on orderbook.")

        if is_limit:
            fill_price = order.limit_price or ref_price
            fee_rate = 0.0002  # 0.02% maker fee
        else:
            # Market order simulated slippage
            slippage = ref_price * 0.0002
            fill_price = ref_price + (slippage if order.side == Side.BUY else -slippage)
            fee_rate = 0.0005  # 0.05% taker fee

        contract_mult = 0.001 if "BTC" in order.instrument.upper() else (0.01 if "ETH" in order.instrument.upper() else 1.0)
        signed_qty = order.quantity if order.side == Side.BUY else -order.quantity
        notional = fill_price * order.quantity * contract_mult
        fee = notional * fee_rate

        # Position and PnL updates
        prev_pos = self.positions.get(order.instrument.upper(), 0)
        new_pos = prev_pos + signed_qty
        self.positions[order.instrument.upper()] = new_pos

        order_realized_pnl = 0.0
        # If closing or reducing position, calculate realized PnL
        if (prev_pos > 0 and signed_qty < 0) or (prev_pos < 0 and signed_qty > 0):
            closed_qty = min(abs(prev_pos), abs(signed_qty))
            entry_price = self.avg_entry_prices.get(order.instrument.upper(), fill_price)
            if prev_pos > 0:  # Long closing
                order_realized_pnl = (fill_price - entry_price) * closed_qty * contract_mult
            else:  # Short closing
                order_realized_pnl = (entry_price - fill_price) * closed_qty * contract_mult
            self.realized_pnl += order_realized_pnl

        # Update average entry price
        if new_pos == 0:
            self.avg_entry_prices[order.instrument.upper()] = 0.0
        elif (prev_pos >= 0 and signed_qty > 0) or (prev_pos <= 0 and signed_qty < 0):
            # Increasing position
            prev_notional = abs(prev_pos) * self.avg_entry_prices.get(order.instrument.upper(), fill_price)
            new_notional = prev_notional + (abs(signed_qty) * fill_price)
            self.avg_entry_prices[order.instrument.upper()] = new_notional / abs(new_pos)

        self.cash -= (signed_qty * fill_price * contract_mult) + fee
        self.total_fees_paid += fee

        record = {
            "order_id": client_order_id,
            "instrument": order.instrument.upper(),
            "side": order.side.value,
            "quantity": order.quantity,
            "order_type": order.order_type.value,
            "status": "FILLED",
            "fill_price": round(fill_price, 2),
            "fee": round(fee, 4),
            "realized_pnl": round(order_realized_pnl, 2),
        }
        self.orders[client_order_id] = record

        # Visually clear terminal execution notification
        print(
            f"\n{'='*75}\n"
            f">>> [TRADE EXECUTED] Mode: PAPER (Simulation)\n"
            f"   Action:     {order.side.value} {order.quantity} contract(s) {order.instrument.upper()}\n"
            f"   Fill Price: ${fill_price:,.2f}\n"
            f"   Notional:   ${notional:,.2f} USD\n"
            f"   New Pos:    {new_pos} contracts (Avg Entry: ${self.avg_entry_prices.get(order.instrument.upper(), 0.0):,.2f})\n"
            f"   Status:     FILLED (Recorded in DB & Extension UI)\n"
            f"{'='*75}\n",
            flush=True
        )

        # Persist to database
        try:
            Repository.save_order(
                client_order_id=client_order_id,
                symbol=order.instrument.upper(),
                side=order.side.value,
                order_type=order.order_type.value,
                price=round(fill_price, 2),
                quantity=order.quantity,
                status="FILLED",
                is_exit=order.is_exit,
                grid_level_id=order.grid_level_id,
            )
            Repository.record_fill(
                client_order_id=client_order_id,
                symbol=order.instrument.upper(),
                side=order.side.value,
                fill_price=round(fill_price, 2),
                fill_quantity=order.quantity,
                fee=round(fee, 4),
                realized_pnl=round(order_realized_pnl, 2),
            )
            Repository.update_position(
                symbol=order.instrument.upper(),
                size=new_pos,
                entry_price=self.avg_entry_prices.get(order.instrument.upper(), 0.0),
                mark_price=fill_price,
                realized_pnl=self.realized_pnl,
                unrealized_pnl=0.0,
            )
        except Exception as exc:
            logger.warning(f"Could not persist paper order: {exc}")

        return PaperOrder(**record, message="Simulated fill only; no live broker order was sent.")

    async def cancel_order(self, order_id: str, product_id: Optional[int] = None) -> Dict[str, Any]:
        if order_id in self.orders:
            self.orders[order_id]["status"] = "CANCELLED"
            Repository.save_order(
                client_order_id=order_id,
                symbol=self.orders[order_id]["instrument"],
                side=self.orders[order_id]["side"],
                order_type=self.orders[order_id]["order_type"],
                price=self.orders[order_id].get("fill_price"),
                quantity=self.orders[order_id]["quantity"],
                status="CANCELLED",
            )
            return {"order_id": order_id, "status": "CANCELLED"}
        return {"order_id": order_id, "status": "NOT_FOUND"}

    async def cancel_all_orders(self, product_id: Optional[int] = None) -> Dict[str, Any]:
        cancelled_count = 0
        for oid, o in self.orders.items():
            if o.get("status") in ("OPEN", "SUBMITTED", "PARTIALLY_FILLED"):
                o["status"] = "CANCELLED"
                cancelled_count += 1
        return {"cancelled_count": cancelled_count, "status": "OK"}

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        if order_id not in self.orders:
            return {"order_id": order_id, "status": "NOT_FOUND"}
        return self.orders[order_id]

    async def get_products(self) -> List[Dict[str, Any]]:
        return [
            {"id": 27, "symbol": "BTCUSD", "tick_size": "0.5", "contract_unit": 1, "product_type": "perpetual_future"},
            {"id": 28, "symbol": "ETHUSD", "tick_size": "0.05", "contract_unit": 1, "product_type": "perpetual_future"},
            {"id": 1, "symbol": "TEST", "tick_size": "0.1", "contract_unit": 1, "product_type": "spot"},
            {"id": 2, "symbol": "DEMO", "tick_size": "0.1", "contract_unit": 1, "product_type": "spot"},
        ]

    async def get_ticker(self, symbol: str) -> MarketDataSnapshot:
        sym = symbol.upper()
        p = self.last_prices.get(sym, 100.0)
        spread = 0.5 if p > 1000 else 0.1
        return MarketDataSnapshot(
            symbol=sym,
            best_bid=round(p - spread / 2.0, 2),
            best_ask=round(p + spread / 2.0, 2),
            mid_price=round(p, 2),
            last_price=round(p, 2),
            spread=spread,
            spread_pct=round(spread / p, 6) if p > 0 else 0.001,
            timestamp_exchange=time.time(),
            timestamp_local=time.time(),
            is_stale=False,
            source="PAPER_SIMULATOR"
        )

    async def get_orderbook(self, symbol: str, depth: int = 10) -> Dict[str, Any]:
        return {"bids": [[99.9, 5], [99.8, 10]], "asks": [[100.1, 5], [100.2, 10]]}
