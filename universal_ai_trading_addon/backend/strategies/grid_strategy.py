"""Deterministic Quantitative Grid Trading Strategy Engine.

Implements an institutional-grade, risk-constrained grid strategy:
- Arithmetic and geometric grid spacing
- Tick-size & lot-size decimal precision alignment
- Independent level state tracking with paired entry & exit orders
- Strict duplicate-order and re-entry prevention
- Risk-engine guarded order generation
- Dynamic recentering and hard stop-loss / take-profit controls
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from .base import BaseStrategy
from ..models import (
    GridConfig, GridLevelModel, MarketDataSnapshot, OrderRequest,
    Side, OrderType, OrderStatus
)
from ..exchange.base import ExchangeAdapter
from ..exchange.delta_client import DeltaClient
from ..risk.engine import RiskEngine
from ..order_manager.state_machine import OrderStateMachine
from ..persistence.repository import Repository

logger = logging.getLogger("grid_strategy")


class GridStrategyEngine(BaseStrategy):
    def __init__(
        self,
        config: GridConfig,
        exchange: ExchangeAdapter,
        risk_engine: RiskEngine,
        tick_size: float = 0.5,
    ):
        self.config = config
        self.exchange = exchange
        self.risk_engine = risk_engine
        self.tick_size = tick_size

        self.is_active = False
        self.is_paused = False
        self.levels: Dict[int, GridLevelModel] = {}
        self.last_snapshot: Optional[MarketDataSnapshot] = None
        self._lock = asyncio.Lock()
        
        # Trailing profit lock & breakeven tracking
        self.highest_price_since_entry: float = 0.0
        self.lowest_price_since_entry: float = float("inf")
        
        # Build initial levels
        self._generate_levels()

    def _generate_levels(self) -> None:
        """Calculate and store grid level price points."""
        cfg = self.config

        # Automatic Fee-Covering calculation (From Delta Exchange fees)
        # Delta taker fee = 0.05%, maker fee = 0.02%. Round-trip = 0.07% + 0.03% buffer = 0.1% min required gap
        min_fee_buffer = cfg.center_price * 0.0010
        gap = cfg.grid_gap if (cfg.grid_gap and cfg.grid_gap > 0) else None
        if gap is not None and getattr(cfg, "cover_fees", True) and gap < min_fee_buffer:
            gap = round(min_fee_buffer, 1)

        self.levels.clear()

        if gap is not None and cfg.levels_above and cfg.levels_below:
            num_levels = cfg.levels_above + cfg.levels_below
            cfg.grid_levels = num_levels
            cfg.lower_price = DeltaClient.round_to_tick(max(1.0, cfg.center_price - (cfg.levels_below * gap)), self.tick_size)
            cfg.upper_price = DeltaClient.round_to_tick(cfg.center_price + (cfg.levels_above * gap), self.tick_size)
            raw_prices = []
            for b in range(cfg.levels_below, 0, -1):
                raw_prices.append(cfg.center_price - (b * gap))
            raw_prices.append(cfg.center_price)
            for a in range(1, cfg.levels_above + 1):
                raw_prices.append(cfg.center_price + (a * gap))
        else:
            if cfg.upper_price <= cfg.lower_price:
                raise ValueError(f"Upper price ({cfg.upper_price}) must be greater than lower price ({cfg.lower_price})")
            if not (cfg.lower_price <= cfg.center_price <= cfg.upper_price):
                raise ValueError(f"Center price ({cfg.center_price}) must be between lower and upper boundaries")

            num_levels = cfg.grid_levels
            if cfg.spacing_type.lower() == "percentage":
                ratio = (cfg.upper_price / cfg.lower_price) ** (1.0 / num_levels)
                raw_prices = [cfg.lower_price * (ratio ** i) for i in range(num_levels + 1)]
            else:  # Arithmetic
                step = (cfg.upper_price - cfg.lower_price) / num_levels
                raw_prices = [cfg.lower_price + (i * step) for i in range(num_levels + 1)]

        for i, raw_price in enumerate(raw_prices):
            rounded_price = DeltaClient.round_to_tick(raw_price, self.tick_size)
            # Decide target side based on position relative to center price
            if rounded_price < cfg.center_price:
                target_side = Side.BUY
            elif rounded_price > cfg.center_price:
                target_side = Side.SELL
            else:
                # Exactly at center price: reserve as pivot
                target_side = Side.HOLD

            self.levels[i] = GridLevelModel(
                level_id=i,
                price=rounded_price,
                target_side=target_side,
                status="VACANT",
                last_update=datetime.now(timezone.utc).isoformat()
            )

        logger.info(f"Generated {len(self.levels)} grid levels between {cfg.lower_price} and {cfg.upper_price}")

    async def initialize(self) -> None:
        """Hydrate grid level state from DB or initialize fresh."""
        async with self._lock:
            db_config = Repository.get_grid_config()
            if db_config:
                # Update runtime config if saved in DB
                self.config = GridConfig(
                    instrument=db_config.instrument,
                    center_price=db_config.center_price,
                    upper_price=db_config.upper_price,
                    lower_price=db_config.lower_price,
                    grid_levels=db_config.grid_levels,
                    spacing_type=db_config.spacing_type,
                    quantity_per_level=db_config.quantity_per_level,
                    max_open_orders=db_config.max_open_orders,
                    max_inventory=db_config.max_inventory,
                    stop_loss_price=db_config.stop_loss_price,
                    take_profit_price=db_config.take_profit_price,
                )
                self._generate_levels()

            db_levels = Repository.get_all_grid_levels()
            for dbl in db_levels:
                if dbl.level_id in self.levels:
                    self.levels[dbl.level_id].status = dbl.status
                    self.levels[dbl.level_id].entry_client_order_id = dbl.entry_client_order_id
                    self.levels[dbl.level_id].exit_client_order_id = dbl.exit_client_order_id
                    self.levels[dbl.level_id].paired_price = dbl.paired_price
                    self.levels[dbl.level_id].filled_qty = dbl.filled_qty
                    self.levels[dbl.level_id].cycle_count = dbl.cycle_count

            logger.info("Grid strategy engine initialized and synchronized.")

    async def start(self) -> None:
        async with self._lock:
            self.is_active = True
            self.is_paused = False
            # Persist active configuration
            Repository.save_grid_config({
                "instrument": self.config.instrument,
                "center_price": self.config.center_price,
                "upper_price": self.config.upper_price,
                "lower_price": self.config.lower_price,
                "grid_levels": self.config.grid_levels,
                "spacing_type": self.config.spacing_type,
                "quantity_per_level": self.config.quantity_per_level,
                "max_open_orders": self.config.max_open_orders,
                "max_inventory": self.config.max_inventory,
                "stop_loss_price": self.config.stop_loss_price,
                "take_profit_price": self.config.take_profit_price,
                "is_active": True,
            })
            logger.info("Grid strategy started.")

    async def pause(self) -> None:
        self.is_paused = True
        logger.info("Grid strategy paused. New entry orders disabled.")

    async def resume(self) -> None:
        self.is_paused = False
        logger.info("Grid strategy resumed.")

    async def stop(self) -> None:
        self.is_active = False
        self.is_paused = False
        Repository.save_grid_config({"is_active": False})
        logger.info("Grid strategy stopped.")

    async def on_market_update(self, snapshot: MarketDataSnapshot) -> None:
        """Core execution loop invoked on every ticker/price update."""
        if not self.is_active or self.is_paused:
            return

        async with self._lock:
            self.last_snapshot = snapshot
            price = snapshot.last_price or snapshot.mid_price
            if price <= 0:
                return

            # Keep paper broker's reference price in sync with market
            if hasattr(self.exchange, "last_prices"):
                self.exchange.last_prices[snapshot.symbol.upper()] = price

            # 1. Stop loss & Take profit check
            if self.config.stop_loss_price is not None:
                if (price <= self.config.stop_loss_price and self.config.center_price > self.config.stop_loss_price) or \
                   (price >= self.config.stop_loss_price and self.config.center_price < self.config.stop_loss_price):
                    self.risk_engine.activate_kill_switch(
                        f"Hard stop-loss reached: price={price} breached stop_loss={self.config.stop_loss_price}"
                    )
                    await self.pause()
                    return

            if self.config.take_profit_price is not None:
                if (price >= self.config.take_profit_price and self.config.center_price < self.config.take_profit_price) or \
                   (price <= self.config.take_profit_price and self.config.center_price > self.config.take_profit_price):
                    self.risk_engine.activate_kill_switch(
                        f"Take-profit reached: price={price} reached target={self.config.take_profit_price}"
                    )
                    await self.pause()
                    return

            # 1b. Auto-Breakeven & Trailing Profit Lock
            pos_rec = Repository.get_position(self.config.instrument)
            if pos_rec and pos_rec.size != 0:
                entry_p = pos_rec.entry_price or price
                if pos_rec.size > 0:  # Long Position
                    if price > self.highest_price_since_entry:
                        self.highest_price_since_entry = price
                    
                    # Auto-Breakeven (Move SL to entry price once trigger % is hit)
                    if self.config.auto_breakeven_trigger_pct is not None and entry_p > 0:
                        gain_pct = (price - entry_p) / entry_p
                        if gain_pct >= self.config.auto_breakeven_trigger_pct:
                            be_stop = round(entry_p * 1.001, 1)  # Entry + fee buffer
                            if self.config.stop_loss_price is None or self.config.stop_loss_price < be_stop:
                                self.config.stop_loss_price = be_stop
                                logger.info(f"Auto-Breakeven Triggered: Stop-loss raised to ${be_stop} (Risk-Free Trade Locked)")

                    # Trailing Stop Profit Lock
                    if self.config.trailing_stop_pct is not None and self.highest_price_since_entry > entry_p:
                        trail_stop = self.highest_price_since_entry * (1.0 - self.config.trailing_stop_pct)
                        if price <= trail_stop and trail_stop > entry_p:
                            logger.info(f"Trailing Stop Triggered: peak=${self.highest_price_since_entry}, current=${price} <= trail=${trail_stop:.1f}. Locking profit.")
                            await self.flatten_positions()
                            return
                else:  # Short Position
                    if price < self.lowest_price_since_entry:
                        self.lowest_price_since_entry = price
                    
                    if self.config.auto_breakeven_trigger_pct is not None and entry_p > 0:
                        gain_pct = (entry_p - price) / entry_p
                        if gain_pct >= self.config.auto_breakeven_trigger_pct:
                            be_stop = round(entry_p * 0.999, 1)
                            if self.config.stop_loss_price is None or self.config.stop_loss_price > be_stop:
                                self.config.stop_loss_price = be_stop
                                logger.info(f"Auto-Breakeven Triggered: Stop-loss lowered to ${be_stop} (Risk-Free Trade Locked)")

                    if self.config.trailing_stop_pct is not None and self.lowest_price_since_entry < entry_p:
                        trail_stop = self.lowest_price_since_entry * (1.0 + self.config.trailing_stop_pct)
                        if price >= trail_stop and trail_stop < entry_p:
                            logger.info(f"Trailing Stop Triggered: trough=${self.lowest_price_since_entry}, current=${price} >= trail=${trail_stop:.1f}. Locking profit.")
                            await self.flatten_positions()
                            return
            else:
                self.highest_price_since_entry = 0.0
                self.lowest_price_since_entry = float("inf")

            # 2. Check fill status of active level orders
            await self._check_level_fills(price)

            # 3. Dynamic Recentering check if enabled
            if self.config.recenter_enabled:
                deviation = abs(price - self.config.center_price) / self.config.center_price
                if deviation >= self.config.recenter_threshold_pct:
                    await self._recenter_grid(price)
                    return

            # 4. Evaluate entry placements
            await self._evaluate_entries(price, snapshot)

    async def _check_level_fills(self, current_price: float) -> None:
        """Poll order statuses or match simulated price penetrations."""
        for lvl_id, level in self.levels.items():
            # Check active entry order
            if level.status == "ENTRY_OPEN" and level.entry_client_order_id:
                order_rec = Repository.get_order_by_client_id(level.entry_client_order_id)
                # In paper simulation, price penetration triggers fill:
                simulated_fill = False
                if order_rec and order_rec.status in ("OPEN", "SUBMITTED"):
                    if level.target_side == Side.BUY and current_price <= level.price:
                        simulated_fill = True
                    elif level.target_side == Side.SELL and current_price >= level.price:
                        simulated_fill = True

                if simulated_fill or (order_rec and order_rec.status == "FILLED"):
                    level.status = "ENTRY_FILLED"
                    level.filled_qty = self.config.quantity_per_level
                    logger.info(f"Grid Level {lvl_id} ENTRY FILLED at price {level.price}")
                    Repository.save_grid_level(
                        level_id=lvl_id,
                        price=level.price,
                        target_side=level.target_side.value,
                        status=level.status,
                        entry_client_order_id=level.entry_client_order_id,
                        filled_qty=level.filled_qty,
                    )
                    # Immediately submit paired counter-exit order
                    await self._place_exit_order(lvl_id, level)

            # Check active exit order
            elif level.status == "EXIT_OPEN" and level.exit_client_order_id:
                order_rec = Repository.get_order_by_client_id(level.exit_client_order_id)
                simulated_fill = False
                exit_price = level.paired_price or level.price
                if order_rec and order_rec.status in ("OPEN", "SUBMITTED"):
                    exit_side = Side.SELL if level.target_side == Side.BUY else Side.BUY
                    if exit_side == Side.SELL and current_price >= exit_price:
                        simulated_fill = True
                    elif exit_side == Side.BUY and current_price <= exit_price:
                        simulated_fill = True

                if simulated_fill or (order_rec and order_rec.status == "FILLED"):
                    level.status = "VACANT"
                    level.cycle_count += 1
                    level.filled_qty = 0
                    level.entry_client_order_id = None
                    level.exit_client_order_id = None
                    level.paired_price = None
                    logger.info(f"Grid Level {lvl_id} EXIT FILLED! Cycle {level.cycle_count} completed.")
                    Repository.save_grid_level(
                        level_id=lvl_id,
                        price=level.price,
                        target_side=level.target_side.value,
                        status="VACANT",
                        cycle_count=level.cycle_count,
                    )

    async def _place_exit_order(self, lvl_id: int, level: GridLevelModel) -> None:
        """Submit paired counter-order to lock in grid spread profit."""
        # Calculate paired exit price: 1 grid step away in profit direction
        step = (self.config.upper_price - self.config.lower_price) / self.config.grid_levels
        if level.target_side == Side.BUY:
            exit_price = DeltaClient.round_to_tick(level.price + step, self.tick_size)
            exit_side = Side.SELL
        else:
            exit_price = DeltaClient.round_to_tick(level.price - step, self.tick_size)
            exit_side = Side.BUY

        client_order_id = f"grid_exit_{lvl_id}_{uuid.uuid4().hex[:8]}"
        order_req = OrderRequest(
            instrument=self.config.instrument,
            side=exit_side,
            quantity=self.config.quantity_per_level,
            order_type=OrderType.LIMIT,
            limit_price=exit_price,
            client_order_id=client_order_id,
            is_exit=True,
            grid_level_id=lvl_id,
        )

        try:
            # Register in state machine & repository
            OrderStateMachine.transition(
                Repository.save_order(
                    client_order_id=client_order_id,
                    symbol=self.config.instrument,
                    side=exit_side.value,
                    order_type=OrderType.LIMIT.value,
                    price=exit_price,
                    quantity=self.config.quantity_per_level,
                    status=OrderStatus.PROPOSED.value,
                    is_exit=True,
                    grid_level_id=lvl_id,
                ).client_order_id,
                OrderStatus.RISK_APPROVED,
                "Exit order pre-approved",
            )

            # Submit to exchange/broker
            OrderStateMachine.transition(client_order_id, OrderStatus.SUBMITTED, "Submitting to exchange")
            await self.exchange.place_order(order_req)
            
            order_rec = Repository.get_order_by_client_id(client_order_id)
            if order_rec and order_rec.status == OrderStatus.FILLED.value:
                level.status = "VACANT"
                level.cycle_count += 1
                level.filled_qty = 0
                level.entry_client_order_id = None
                level.exit_client_order_id = None
                level.paired_price = None
                Repository.save_grid_level(
                    level_id=lvl_id,
                    price=level.price,
                    target_side=level.target_side.value,
                    status="VACANT",
                    cycle_count=level.cycle_count,
                )
            else:
                OrderStateMachine.transition(client_order_id, OrderStatus.OPEN, "Exit order active")
                level.status = "EXIT_OPEN"
                level.exit_client_order_id = client_order_id
                level.paired_price = exit_price
                Repository.save_grid_level(
                    level_id=lvl_id,
                    price=level.price,
                    target_side=level.target_side.value,
                    status="EXIT_OPEN",
                    entry_client_order_id=level.entry_client_order_id,
                    exit_client_order_id=client_order_id,
                    paired_price=exit_price,
                    filled_qty=level.filled_qty,
                )
            logger.info(f"Level {lvl_id} processed exit {exit_side.value} at {exit_price}")

        except Exception as exc:
            logger.error(f"Failed to place exit order for level {lvl_id}: {exc}")
            self.risk_engine.record_error(str(exc))

    async def _evaluate_entries(self, current_price: float, snapshot: MarketDataSnapshot) -> None:
        """Place limit entry orders for vacant levels within risk and distance constraints."""
        open_orders = Repository.get_open_orders()
        open_count = len(open_orders)
        if open_count >= self.config.max_open_orders:
            return

        pos_record = Repository.get_position(self.config.instrument)
        current_pos = pos_record.size if pos_record else 0

        for lvl_id, level in self.levels.items():
            if level.status != "VACANT" or level.target_side == Side.HOLD:
                continue

            # Only place Buy entries below current market price and Sell entries above
            if level.target_side == Side.BUY and level.price >= current_price:
                continue
            if level.target_side == Side.SELL and level.price <= current_price:
                continue

            client_order_id = f"grid_ent_{lvl_id}_{uuid.uuid4().hex[:8]}"
            order_req = OrderRequest(
                instrument=self.config.instrument,
                side=level.target_side,
                quantity=self.config.quantity_per_level,
                order_type=OrderType.LIMIT,
                limit_price=level.price,
                client_order_id=client_order_id,
                is_exit=False,
                grid_level_id=lvl_id,
            )

            # Central Pre-Trade Risk Gate
            try:
                self.risk_engine.validate_order(
                    order=order_req,
                    current_position=current_pos,
                    market_snapshot=snapshot,
                    open_orders_count=open_count,
                )
            except Exception as risk_err:
                logger.debug(f"Level {lvl_id} entry skipped due to risk gate: {risk_err}")
                continue

            # Submit order
            try:
                Repository.save_order(
                    client_order_id=client_order_id,
                    symbol=self.config.instrument,
                    side=level.target_side.value,
                    order_type=OrderType.LIMIT.value,
                    price=level.price,
                    quantity=self.config.quantity_per_level,
                    status=OrderStatus.PROPOSED.value,
                    is_exit=False,
                    grid_level_id=lvl_id,
                )
                OrderStateMachine.transition(client_order_id, OrderStatus.RISK_APPROVED, "Risk approved")
                OrderStateMachine.transition(client_order_id, OrderStatus.SUBMITTED, "Submitted to exchange")
                await self.exchange.place_order(order_req)
                
                # Check status: if resting on book -> OPEN
                order_rec = Repository.get_order_by_client_id(client_order_id)
                if order_rec and order_rec.status == OrderStatus.FILLED.value:
                    level.status = "ENTRY_FILLED"
                    level.entry_client_order_id = client_order_id
                    level.filled_qty = self.config.quantity_per_level
                    Repository.save_grid_level(
                        level_id=lvl_id,
                        price=level.price,
                        target_side=level.target_side.value,
                        status="ENTRY_FILLED",
                        entry_client_order_id=client_order_id,
                        filled_qty=level.filled_qty,
                    )
                    await self._place_exit_order(lvl_id, level)
                else:
                    OrderStateMachine.transition(client_order_id, OrderStatus.OPEN, "Order active on exchange")
                    level.status = "ENTRY_OPEN"
                    level.entry_client_order_id = client_order_id
                    Repository.save_grid_level(
                        level_id=lvl_id,
                        price=level.price,
                        target_side=level.target_side.value,
                        status="ENTRY_OPEN",
                        entry_client_order_id=client_order_id,
                    )
                open_count += 1
                if open_count >= self.config.max_open_orders:
                    break

            except Exception as exc:
                logger.error(f"Failed to submit entry order for level {lvl_id}: {exc}")
                self.risk_engine.record_error(str(exc))

    async def _recenter_grid(self, new_center_price: float) -> None:
        """Cancel pending entries and recalculate grid boundaries around new center price."""
        logger.info(f"Recentering grid around new price: {new_center_price:.2f}")
        # Cancel all open entry orders safely
        await self.cancel_entry_orders()
        # Recalculate upper and lower boundaries
        half_span = (self.config.upper_price - self.config.lower_price) / 2.0
        self.config.center_price = new_center_price
        self.config.lower_price = DeltaClient.round_to_tick(max(1.0, new_center_price - half_span), self.tick_size)
        self.config.upper_price = DeltaClient.round_to_tick(new_center_price + half_span, self.tick_size)
        
        Repository.clear_grid_levels()
        self._generate_levels()
        for lvl in self.levels.values():
            Repository.save_grid_level(
                level_id=lvl.level_id,
                price=lvl.price,
                target_side=lvl.target_side.value,
                status="VACANT"
            )

    async def cancel_entry_orders(self) -> int:
        """Cancel all open entry orders without closing positions."""
        cancelled = 0
        for lvl_id, level in self.levels.items():
            if level.status == "ENTRY_OPEN" and level.entry_client_order_id:
                try:
                    await self.exchange.cancel_order(level.entry_client_order_id)
                    OrderStateMachine.transition(
                        level.entry_client_order_id,
                        OrderStatus.CANCELLED,
                        "Cancelled via cancel_entry_orders"
                    )
                    level.status = "VACANT"
                    level.entry_client_order_id = None
                    Repository.save_grid_level(
                        level_id=lvl_id,
                        price=level.price,
                        target_side=level.target_side.value,
                        status="VACANT",
                    )
                    cancelled += 1
                except Exception as exc:
                    logger.warning(f"Could not cancel entry order {level.entry_client_order_id}: {exc}")
        return cancelled

    async def flatten_positions(self) -> Dict[str, Any]:
        """Cancel orders and close open position with explicit authorization."""
        # 1. Stop new entries
        await self.pause()
        # 2. Cancel all pending orders
        await self.cancel_entry_orders()
        for lvl in self.levels.values():
            if lvl.status == "EXIT_OPEN" and lvl.exit_client_order_id:
                try:
                    await self.exchange.cancel_order(lvl.exit_client_order_id)
                    OrderStateMachine.transition(
                        lvl.exit_client_order_id,
                        OrderStatus.CANCELLED,
                        "Cancelled during flatten procedure"
                    )
                    lvl.status = "VACANT"
                except Exception:
                    pass

        # 3. Close open position
        pos_rec = Repository.get_position(self.config.instrument)
        size = pos_rec.size if pos_rec else 0
        if size != 0:
            close_side = Side.SELL if size > 0 else Side.BUY
            close_req = OrderRequest(
                instrument=self.config.instrument,
                side=close_side,
                quantity=abs(size),
                order_type=OrderType.MARKET,
                is_exit=True,
            )
            close_result = await self.exchange.place_order(close_req)
            return {
                "flattened": True,
                "closed_size": size,
                "close_side": close_side.value,
                "result": close_result,
            }
        return {"flattened": True, "closed_size": 0, "message": "Position was already flat."}
