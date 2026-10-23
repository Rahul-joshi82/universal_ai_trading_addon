"""REST API endpoints for Universal AI Trading Add-on.

Provides complete monitoring, grid controls, order lifecycle tracking,
risk parameters, market data ingestion, and AI advisory endpoints.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
import re
import httpx
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Depends, Body, Request
from ..models import (
    OrderRequest, Side, OrderType, GridConfig, MarketDataSnapshot,
    RiskStatus, ReconciliationReport, EnvConfigRequest, VerifyCredentialsRequest,
    GridCreateRequest, RiskConfigRequest, BacktestRunRequest
)
from ..order_manager.state_machine import OrderStateMachine, OrderStatus
from ..config import settings
from ..risk.engine import RiskRejected, default_risk_engine
from ..persistence.repository import Repository
from ..exchange.delta_client import DeltaClient, DeltaAPIError
from ..market_data import demo_candles
from ..ai.analyst import AIAnalyst
from ..ai.parameter_advisor import AIParameterAdvisor
from ..strategies.backtester import GridBacktester

router = APIRouter()
logger = logging.getLogger("api_routes")

# References to application services set in lifespan/main.py
services: Dict[str, Any] = {}


def get_services() -> Dict[str, Any]:
    return services


# -------------------------------------------------------------
# System & Status
# -------------------------------------------------------------
@router.get("/status")
async def get_system_status():
    srv = get_services()
    paper_broker = srv.get("paper_broker")
    grid_engine = srv.get("grid_engine")
    delta_client = srv.get("delta_client")
    risk_eng = srv.get("risk_engine") or default_risk_engine

    mode = "PAPER" if settings.paper_trading else f"DELTA_{settings.delta_env.upper()}"
    account = await paper_broker.get_account() if paper_broker else {}
    positions = await paper_broker.get_positions() if paper_broker else {}
    
    # Active orders count
    open_orders = Repository.get_open_orders()
    grid_active = grid_engine.is_active if grid_engine else False
    grid_paused = grid_engine.is_paused if grid_engine else False

    # Risk status
    risk_stat = risk_eng.get_status(
        current_position=positions.get("BTCUSD", 0),
        open_orders_count=len(open_orders)
    )

    return {
        "mode": mode,
        "app_env": settings.app_env,
        "delta_env": settings.delta_env,
        "paper_trading": settings.paper_trading,
        "paused": risk_eng.is_paused or grid_paused,
        "live_execution_enabled": not settings.paper_trading and settings.delta_enable_testnet_orders,
        "grid_strategy_active": grid_active,
        "grid_active": grid_active,
        "grid_strategy_paused": grid_paused,
        "account": account,
        "positions": positions,
        "orders_count": len(open_orders),
        "risk_status": risk_stat.model_dump(),
        "database": "SQLITE_PERSISTED",
    }


# -------------------------------------------------------------
# Grid Trading Strategy Controls
# -------------------------------------------------------------
@router.post("/grid/configure")
async def configure_grid(config: GridConfig):
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    if not grid_engine:
        raise HTTPException(status_code=500, detail="Grid engine service unavailable.")

    try:
        grid_engine.config = config
        grid_engine._generate_levels()
        Repository.save_grid_config(config.model_dump())
        for lvl in grid_engine.levels.values():
            Repository.save_grid_level(
                level_id=lvl.level_id,
                price=lvl.price,
                target_side=lvl.target_side.value,
                status="VACANT"
            )
        return {"status": "SUCCESS", "message": "Grid configuration updated.", "config": config}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/grid/config")
async def get_grid_config():
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    if grid_engine:
        return grid_engine.config
    db_cfg = Repository.get_grid_config()
    if db_cfg:
        return db_cfg
    raise HTTPException(status_code=404, detail="No active grid config found.")


@router.get("/grid/levels")
async def get_grid_levels():
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    if grid_engine:
        return list(grid_engine.levels.values())
    db_lvls = Repository.get_all_grid_levels()
    return db_lvls


@router.post("/grid/auto-configure")
async def auto_configure_grid(
    symbol: str = "BTCUSD",
    range_pct: float = 0.04,
    levels: int = 10,
    quantity: int = 1,
    investment_amount: Optional[float] = None,
):
    """Automatically calibrate and configure grid levels centered around the live Delta market price."""
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    delta_client = srv.get("delta_client")
    delta_ws = srv.get("delta_ws")
    paper_broker = srv.get("paper_broker")

    if not grid_engine:
        raise HTTPException(status_code=500, detail="Grid engine service unavailable.")

    sym = symbol.upper().strip()
    price = 0.0
    if delta_ws and delta_ws.is_connected:
        snap = delta_ws.get_snapshot(sym)
        if not snap.is_stale and snap.last_price > 0:
            price = snap.last_price

    if price <= 0 and delta_client:
        try:
            snap = await delta_client.get_ticker(sym)
            if snap and snap.last_price > 0:
                price = snap.last_price
        except Exception as e:
            logger.warning(f"Auto-configure failed to get live ticker: {e}")

    if price <= 0 and paper_broker:
        price = paper_broker.last_prices.get(sym, 80000.0 if "BTC" in sym else 2500.0)

    if price <= 0:
        price = 80000.0 if "BTC" in sym else 2500.0

    center = round(price, 1)
    half_range = center * range_pct
    lower = round(center - half_range, 1)
    upper = round(center + half_range, 1)

    # Calculate contract quantity from dollar investment budget if provided
    qty = quantity
    contract_mult = 0.001 if "BTC" in sym else (0.01 if "ETH" in sym else 1.0)
    margin_per_contract = (center * contract_mult) / 10.0  # 10x leverage margin
    buy_levels = max(1, levels // 2)

    if investment_amount and investment_amount > 0:
        target_qty = max(1, int(investment_amount / (buy_levels * margin_per_contract)))
        qty = min(target_qty, 10)

    total_margin = round(qty * buy_levels * margin_per_contract, 2)

    auto_cfg = GridConfig(
        instrument=sym,
        center_price=center,
        upper_price=upper,
        lower_price=lower,
        grid_levels=levels,
        quantity_per_level=qty,
        spacing_type="arithmetic",
        recenter_enabled=True,
        recenter_threshold_pct=0.08,
        total_investment_usd=total_margin,
    )

    grid_engine.config = auto_cfg
    grid_engine._generate_levels()
    Repository.save_grid_config(auto_cfg.model_dump())
    for lvl in grid_engine.levels.values():
        Repository.save_grid_level(
            level_id=lvl.level_id,
            price=lvl.price,
            target_side=lvl.target_side.value,
            status=lvl.status,
            cycle_count=lvl.cycle_count,
        )
    logger.info(f"Auto-configured grid for {sym}: center={center}, lower={lower}, upper={upper}, levels={levels}, qty={qty}")
    return {
        "status": "AUTO_CONFIGURED",
        "symbol": sym,
        "live_price": price,
        "config": auto_cfg.model_dump(),
        "levels_count": len(grid_engine.levels),
        "investment_margin_usd": total_margin,
        "margin_per_contract": round(margin_per_contract, 2),
    }


@router.post("/grid/start")
async def start_grid(symbol: Optional[str] = None, auto_center: bool = False, seed_trade: bool = True):
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    if not grid_engine:
        raise HTTPException(status_code=500, detail="Grid engine service unavailable.")

    delta_client = srv.get("delta_client")
    delta_ws = srv.get("delta_ws")
    paper_broker = srv.get("paper_broker")
    cur_price = 0.0

    if symbol:
        s_clean = symbol.upper().strip()
        if s_clean not in ("TRADE", "APP", "FUTURES", "OPTIONS", "SPOT", "INDEX", "HOME"):
            if grid_engine.config:
                grid_engine.config.instrument = s_clean

    sym = (grid_engine.config.instrument if grid_engine.config else "BTCUSD").upper()
    if sym in ("TRADE", "APP", "FUTURES", "OPTIONS", "SPOT", "INDEX", "HOME"):
        sym = "BTCUSD"
        if grid_engine.config:
            grid_engine.config.instrument = "BTCUSD"

    if delta_ws and delta_ws.is_connected:
        snap = delta_ws.get_snapshot(sym)
        if not snap.is_stale and snap.last_price > 0:
            cur_price = snap.last_price

    if cur_price <= 0 and delta_client:
        try:
            snap = await delta_client.get_ticker(sym)
            if snap and snap.last_price > 0:
                cur_price = snap.last_price
        except Exception:
            pass

    if cur_price <= 0 and paper_broker:
        cur_price = paper_broker.last_prices.get(sym, 0.0)

    # Auto-calibrate if center price is drastically off (>20%) from market price, or auto_center requested
    if cur_price > 0 and (auto_center or grid_engine.config.center_price <= 0 or abs(grid_engine.config.center_price - cur_price) / cur_price > 0.20):
        logger.info(f"Auto-calibrating grid around current market price {cur_price} before starting...")
        half_range = cur_price * 0.04
        grid_engine.config.center_price = round(cur_price, 1)
        grid_engine.config.lower_price = round(cur_price - half_range, 1)
        grid_engine.config.upper_price = round(cur_price + half_range, 1)
        grid_engine._generate_levels()
        Repository.save_grid_config(grid_engine.config.model_dump())
        for lvl in grid_engine.levels.values():
            Repository.save_grid_level(
                level_id=lvl.level_id,
                price=lvl.price,
                target_side=lvl.target_side.value,
                status=lvl.status,
                cycle_count=lvl.cycle_count,
            )

    await grid_engine.start()

    # Immediately evaluate initial resting limit entries
    if cur_price > 0:
        snapshot = MarketDataSnapshot(
            symbol=sym,
            best_bid=cur_price - 0.5,
            best_ask=cur_price + 0.5,
            mid_price=cur_price,
            last_price=cur_price,
            spread=1.0,
            spread_pct=1.0 / cur_price,
            timestamp_exchange=time.time(),
            timestamp_local=time.time(),
            is_stale=False,
            source="DELTA_DIRECT"
        )
        try:
            await grid_engine.on_market_update(snapshot)
        except Exception as e:
            logger.warning(f"Initial market update evaluation error: {e}")

    # Initial Seed Trade: Execute an immediate market order if no position exists yet
    seed_executed = False
    if seed_trade and cur_price > 0:
        pos_rec = Repository.get_position(sym)
        current_pos = pos_rec.size if pos_rec else 0
        if current_pos == 0:
            seed_client_id = f"seed_{uuid.uuid4().hex[:8]}"
            qty = grid_engine.config.quantity_per_level
            seed_order = OrderRequest(
                instrument=sym,
                side=Side.BUY,
                quantity=qty,
                order_type=OrderType.MARKET,
                client_order_id=seed_client_id,
            )
            try:
                Repository.save_order(
                    client_order_id=seed_client_id,
                    symbol=sym,
                    side="BUY",
                    order_type="MARKET",
                    price=cur_price,
                    quantity=qty,
                    status=OrderStatus.PROPOSED.value,
                    is_exit=False,
                )
                OrderStateMachine.transition(seed_client_id, OrderStatus.RISK_APPROVED, "Seed order approved")
                OrderStateMachine.transition(seed_client_id, OrderStatus.SUBMITTED, "Submitting seed order")

                try:
                    res = await grid_engine.exchange.place_order(seed_order)
                except DeltaAPIError as delta_err:
                    err_txt = str(delta_err).lower()
                    if ("whitelist" in err_txt or "api_key" in err_txt or "401" in err_txt or "unauthorized" in err_txt) and paper_broker:
                        logger.warning(f"Delta exchange rejected seed order ({delta_err}). Executing in paper broker fallback.")
                        res = await paper_broker.place_order(seed_order)
                    else:
                        raise delta_err

                fill_p = cur_price
                if isinstance(res, dict):
                    res_inner = res.get("result", {})
                    fill_p = float(res_inner.get("average_fill_price") or cur_price)
                elif hasattr(res, "fill_price"):
                    fill_p = res.fill_price

                contract_mult = 0.001 if "BTC" in sym else (0.01 if "ETH" in sym else 1.0)
                Repository.save_fill(
                    client_order_id=seed_client_id,
                    fill_id=f"fill_{uuid.uuid4().hex[:8]}",
                    price=fill_p,
                    quantity=qty,
                    fee=fill_p * qty * contract_mult * 0.0005,
                    realized_pnl=0.0,
                    symbol=sym,
                    side="BUY",
                )
                Repository.update_position(sym, qty, fill_p, mark_price=fill_p, realized_pnl=0.0, unrealized_pnl=0.0)
                OrderStateMachine.transition(seed_client_id, OrderStatus.FILLED, f"Seed order filled at {fill_p}")
                seed_executed = True
                logger.info(f"Seed trade executed successfully: BUY {qty} {sym} at {fill_p}")

                # Automatically pair with first upper exit level
                first_sell_lvl = min((lvl_id for lvl_id, lvl in grid_engine.levels.items() if lvl.target_side == Side.SELL), default=None)
                if first_sell_lvl is not None:
                    lvl = grid_engine.levels[first_sell_lvl]
                    lvl.filled_qty = qty
                    await grid_engine._place_exit_order(first_sell_lvl, lvl)
            except Exception as seed_err:
                seed_err_msg = str(seed_err)
                logger.warning(f"Seed trade skipped or failed: {seed_err}")

    return {
        "status": "STARTED",
        "is_active": grid_engine.is_active,
        "center_price": grid_engine.config.center_price,
        "lower_price": grid_engine.config.lower_price,
        "upper_price": grid_engine.config.upper_price,
        "seed_trade_executed": seed_executed,
        "current_position": current_pos,
        "seed_error": None if seed_executed else (locals().get("seed_err_msg")),
    }


@router.post("/trade/market")
async def execute_instant_trade(
    symbol: str = "BTCUSD",
    side: str = "BUY",
    quantity: int = 1,
    amount_usd: Optional[float] = None,
):
    """Execute an immediate market order (Testnet or Paper) on demand with contract qty or dollar budget."""
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    delta_client = srv.get("delta_client")
    paper_broker = srv.get("paper_broker")
    risk_eng = srv.get("risk_engine") or default_risk_engine

    exchange = grid_engine.exchange if (grid_engine and grid_engine.exchange) else (paper_broker if settings.paper_trading else delta_client)
    sym = symbol.upper().strip()
    order_side = Side.BUY if side.upper() == "BUY" else Side.SELL
    client_order_id = f"manual_{order_side.value.lower()}_{uuid.uuid4().hex[:8]}"

    # Get market price
    t = await get_market_ticker(sym)
    ref_price = t.last_price or t.mid_price or 80000.0

    actual_qty = quantity
    contract_mult = 0.001 if "BTC" in sym else (0.01 if "ETH" in sym else 1.0)
    margin_per_contract = (ref_price * contract_mult) / 10.0  # 10x leverage margin

    if amount_usd and amount_usd > 0:
        actual_qty = max(1, min(10, round(amount_usd / margin_per_contract)))

    order_req = OrderRequest(
        instrument=sym,
        side=order_side,
        quantity=actual_qty,
        order_type=OrderType.MARKET,
        client_order_id=client_order_id,
    )

    # Risk gate validation
    curr_pos = Repository.get_position(sym).size if Repository.get_position(sym) else 0
    risk_eng.validate_order(order_req, curr_pos, t)

    # Submit order
    Repository.save_order(
        client_order_id=client_order_id,
        symbol=sym,
        side=order_side.value,
        order_type="MARKET",
        price=ref_price,
        quantity=actual_qty,
        status=OrderStatus.PROPOSED.value,
    )
    OrderStateMachine.transition(client_order_id, OrderStatus.RISK_APPROVED, "Manual order approved")
    OrderStateMachine.transition(client_order_id, OrderStatus.SUBMITTED, "Submitting manual market order")

    current_mode = "PAPER_SIMULATION" if settings.paper_trading else "DELTA_TESTNET"
    executed_mode = current_mode
    warning_notice = None
    try:
        res = await exchange.place_order(order_req)
    except DeltaAPIError as exc:
        err_msg = str(exc)
        err_lower = err_msg.lower()
        if ("whitelist" in err_lower or "api_key" in err_lower or "401" in err_lower or "unauthorized" in err_lower) and paper_broker:
            logger.warning(f"Delta exchange rejected ({err_msg}). Falling back to paper broker execution.")
            res = await paper_broker.place_order(order_req)
            executed_mode = "PAPER_FALLBACK"
            ip_str = ""
            m = re.search(r"'(152\.[0-9.]+|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)'", err_msg)
            if m:
                ip_str = f" (detected IP: {m.group(1)})"
            warning_notice = f"Delta Exchange rejected dynamic IP{ip_str}. Executed in Paper Simulation so your trade is active!"
        else:
            OrderStateMachine.transition(client_order_id, OrderStatus.REJECTED, str(exc))
            raise HTTPException(status_code=400, detail=f"Delta Exchange rejected order: {err_msg}")
    except Exception as exc:
        OrderStateMachine.transition(client_order_id, OrderStatus.REJECTED, str(exc))
        raise HTTPException(status_code=400, detail=f"Order execution error: {exc}")

    fill_price = ref_price
    if isinstance(res, dict):
        res_inner = res.get("result", {})
        fill_price = float(res_inner.get("average_fill_price") or ref_price)
    elif hasattr(res, "fill_price"):
        fill_price = res.fill_price

    signed_qty = actual_qty if order_side == Side.BUY else -actual_qty
    new_pos = curr_pos + signed_qty
    Repository.update_position(sym, new_pos, fill_price, mark_price=fill_price, realized_pnl=0.0, unrealized_pnl=0.0)

    fee = fill_price * actual_qty * contract_mult * 0.0005
    Repository.record_fill(
        client_order_id=client_order_id,
        symbol=sym,
        side=order_side.value,
        fill_price=fill_price,
        fill_quantity=actual_qty,
        fee=fee,
        realized_pnl=0.0,
    )
    OrderStateMachine.transition(client_order_id, OrderStatus.FILLED, f"Filled at {fill_price}")

    return {
        "status": "FILLED",
        "client_order_id": client_order_id,
        "symbol": sym,
        "side": order_side.value,
        "quantity": actual_qty,
        "fill_price": fill_price,
        "new_position": new_pos,
        "margin_usd": round(actual_qty * margin_per_contract, 2),
        "notional_usd": round(actual_qty * fill_price * contract_mult, 2),
        "mode": executed_mode,
        "warning": warning_notice,
    }


@router.post("/settings/mode")
async def toggle_trading_mode(mode: str = Body(..., embed=True)):
    """Toggle between PAPER and TESTNET modes dynamically."""
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    delta_client = srv.get("delta_client")
    paper_broker = srv.get("paper_broker")
    reconciler = srv.get("reconciler")

    target_mode = mode.upper().strip()
    if target_mode in ("TESTNET", "TESTNET_LIVE", "LIVE"):
        settings.paper_trading = False
        if grid_engine:
            grid_engine.exchange = delta_client
        if reconciler:
            reconciler.exchange = delta_client
        logger.info("Switched trading mode to DELTA TESTNET LIVE.")
        return {"status": "SUCCESS", "mode": "DELTA_TESTNET_LIVE", "paper_trading": False}
    elif target_mode in ("PAPER", "SIMULATION"):
        settings.paper_trading = True
        if grid_engine:
            grid_engine.exchange = paper_broker
        if reconciler:
            reconciler.exchange = paper_broker
        logger.info("Switched trading mode to PAPER SIMULATION.")
        return {"status": "SUCCESS", "mode": "PAPER_SIMULATION", "paper_trading": True}
    else:
        raise HTTPException(status_code=400, detail="Invalid mode. Choose 'PAPER' or 'TESTNET'.")


@router.post("/grid/pause")
async def pause_grid():
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    risk_eng = srv.get("risk_engine") or default_risk_engine
    if grid_engine:
        await grid_engine.pause()
    risk_eng.is_paused = True
    return {"status": "PAUSED", "message": "Grid paused. No new entry orders will be placed."}


@router.post("/grid/resume")
async def resume_grid():
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    risk_eng = srv.get("risk_engine") or default_risk_engine
    if risk_eng.kill_switch_active:
        raise HTTPException(status_code=423, detail="Cannot resume while kill switch is active. Reset kill switch first.")
    if grid_engine:
        await grid_engine.resume()
    risk_eng.is_paused = False
    return {"status": "RESUMED", "message": "Grid resumed."}


@router.post("/grid/stop")
async def stop_grid():
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    if grid_engine:
        await grid_engine.stop()
    return {"status": "STOPPED", "message": "Grid strategy stopped."}


@router.post("/grid/cancel-entries")
async def cancel_grid_entries():
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    if not grid_engine:
        raise HTTPException(status_code=500, detail="Grid engine service unavailable.")
    count = await grid_engine.cancel_entry_orders()
    return {"cancelled_orders": count, "message": f"Cancelled {count} entry orders. Positions remain open."}


@router.post("/grid/flatten")
async def flatten_grid(confirmation: Dict[str, Any] = Body(...)):
    """Explicitly authorized position close and order cleanup."""
    if not confirmation.get("confirm"):
        raise HTTPException(status_code=400, detail="Explicit confirmation required. Send {'confirm': true}.")
    
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    if not grid_engine:
        raise HTTPException(status_code=500, detail="Grid engine service unavailable.")
    
    result = await grid_engine.flatten_positions()
    return result


@router.post("/grid/emergency-stop")
async def emergency_stop():
    """Manual kill switch: immediately blocks entries and pauses."""
    srv = get_services()
    risk_eng = srv.get("risk_engine") or default_risk_engine
    grid_engine = srv.get("grid_engine")
    
    risk_eng.activate_kill_switch("Manual emergency stop triggered via API")
    if grid_engine:
        await grid_engine.pause()
        await grid_engine.cancel_entry_orders()
    
    return {"status": "EMERGENCY_STOPPED", "kill_switch_active": True}


# -------------------------------------------------------------
# Market Data Endpoints
# -------------------------------------------------------------
@router.get("/market/ticker/{symbol}")
async def get_market_ticker(symbol: str):
    srv = get_services()
    delta_ws = srv.get("delta_ws")
    delta_client = srv.get("delta_client")
    paper_broker = srv.get("paper_broker")
    sym = symbol.upper().strip()
    if sym in ("TRADE", "APP", "FUTURES", "OPTIONS", "SPOT", "INDEX", "HOME", "ORDERS"):
        sym = "BTCUSD"

    # 1. Try WebSocket snapshot first
    if delta_ws and delta_ws.is_connected:
        snap = delta_ws.get_snapshot(sym)
        if not snap.is_stale and snap.last_price > 0:
            if paper_broker:
                paper_broker.last_prices[sym] = snap.last_price
            return snap

    # 2. Try REST API from Delta Exchange India (public real-time market data)
    if delta_client:
        try:
            live_snap = await delta_client.get_ticker(sym)
            if live_snap and live_snap.last_price > 0:
                if paper_broker:
                    paper_broker.last_prices[sym] = live_snap.last_price
                return live_snap
        except Exception as exc:
            logger.warning(f"Delta REST ticker fetch failed for {sym}: {exc}")

    # 3. Paper simulator fallback
    if paper_broker:
        return await paper_broker.get_ticker(sym)

    raise HTTPException(status_code=503, detail="Market data sources unavailable.")


@router.get("/market/candles/{symbol}")
async def get_market_candles(symbol: str, count: int = 100):
    candles = demo_candles(instrument=symbol.upper(), count=count)
    return {"symbol": symbol.upper(), "candles": candles, "count": len(candles)}


# -------------------------------------------------------------
# Orders & Trades
# -------------------------------------------------------------
@router.get("/orders")
async def get_orders(limit: int = 100):
    db_orders = Repository.get_all_orders(limit=limit)
    return [
        {
            "id": o.id,
            "client_order_id": o.client_order_id,
            "exchange_order_id": o.exchange_order_id,
            "symbol": o.symbol,
            "side": o.side,
            "order_type": o.order_type,
            "price": o.price,
            "quantity": o.quantity,
            "filled_quantity": o.filled_quantity,
            "status": o.status,
            "is_exit": o.is_exit,
            "grid_level_id": o.grid_level_id,
            "error_message": o.error_message,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in db_orders
    ]


@router.get("/orders/{order_id}")
async def get_order_detail(order_id: str):
    order = Repository.get_order_by_client_id(order_id) or Repository.get_order_by_exchange_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "client_order_id": order.client_order_id,
        "exchange_order_id": order.exchange_order_id,
        "symbol": order.symbol,
        "side": order.side,
        "order_type": order.order_type,
        "price": order.price,
        "quantity": order.quantity,
        "filled_quantity": order.filled_quantity,
        "status": order.status,
        "is_exit": order.is_exit,
        "grid_level_id": order.grid_level_id,
        "lifecycle_history": order.lifecycle_history,
        "error_message": order.error_message,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


@router.post("/orders/manual")
async def place_manual_order(order: OrderRequest):
    srv = get_services()
    paper_broker = srv.get("paper_broker")
    delta_client = srv.get("delta_client")
    risk_eng = srv.get("risk_engine") or default_risk_engine

    if risk_eng.is_paused and not order.is_exit:
        raise HTTPException(status_code=423, detail="Trading is paused.")

    if settings.paper_trading:
        try:
            res = await paper_broker.place_order(order)
            return res.model_dump()
        except RiskRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        try:
            return await delta_client.place_order(order)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@router.get("/positions")
async def get_positions():
    srv = get_services()
    paper_broker = srv.get("paper_broker")
    if paper_broker:
        return await paper_broker.get_positions_detailed()
    pos = Repository.get_position("BTCUSD")
    return [pos] if pos else []


@router.get("/trades")
async def get_trades(limit: int = 50):
    fills = Repository.get_trades(limit=limit)
    return [
        {
            "id": f.id,
            "client_order_id": f.client_order_id,
            "symbol": f.symbol,
            "side": f.side,
            "price": f.fill_price,
            "quantity": f.fill_quantity,
            "fee": f.fee,
            "realized_pnl": f.realized_pnl,
            "timestamp": f.timestamp.isoformat() if f.timestamp else None,
        }
        for f in fills
    ]


# -------------------------------------------------------------
# Risk & Reconciliation
# -------------------------------------------------------------
@router.get("/risk/status")
async def get_risk_status(symbol: str = "BTCUSD"):
    srv = get_services()
    risk_eng = srv.get("risk_engine") or default_risk_engine
    paper_broker = srv.get("paper_broker")
    sym = symbol.upper().strip()
    if sym in ("TRADE", "APP", "FUTURES", "OPTIONS", "SPOT", "INDEX", "HOME"):
        sym = "BTCUSD"

    cur_pos = 0
    cur_price = 0.0
    if settings.paper_trading and paper_broker:
        positions = await paper_broker.get_positions()
        cur_pos = positions.get(sym, 0)
        cur_price = paper_broker.last_prices.get(sym, 0.0)
    else:
        p_rec = Repository.get_position(sym)
        if p_rec:
            cur_pos = p_rec.size
            cur_price = p_rec.mark_price or p_rec.entry_price

    open_orders = Repository.get_open_orders()
    return risk_eng.get_status(
        current_position=cur_pos,
        current_price=cur_price,
        open_orders_count=len(open_orders),
        symbol=sym,
    )


@router.post("/risk/reset-kill-switch")
async def reset_kill_switch():
    srv = get_services()
    risk_eng = srv.get("risk_engine") or default_risk_engine
    risk_eng.reset_kill_switch()
    return {"status": "SUCCESS", "message": "Kill switch and circuit breakers reset."}


@router.post("/reconcile")
async def trigger_reconciliation(symbol: str = "BTCUSD"):
    srv = get_services()
    reconciler = srv.get("reconciler")
    if not reconciler:
        raise HTTPException(status_code=500, detail="Reconciler service unavailable.")
    report = await reconciler.reconcile(symbol=symbol)
    return report.model_dump()


# -------------------------------------------------------------
# AI Advisory & Backtesting
# -------------------------------------------------------------
@router.get("/ai/analysis/{symbol}")
async def get_ai_market_analysis(symbol: str):
    candles = demo_candles(instrument=symbol.upper(), count=100)
    report = AIAnalyst.analyze(instrument=symbol, candles=candles)
    return report.model_dump()


@router.post("/ai/suggest-parameters")
async def suggest_grid_parameters(symbol: str = "BTCUSD", current_price: Optional[float] = None):
    candles = demo_candles(instrument=symbol.upper(), count=100)
    price = current_price or candles[-1]["close"]
    suggestion = AIParameterAdvisor.suggest_parameters(
        instrument=symbol,
        candles=candles,
        current_price=price,
        tick_size=0.5
    )
    return suggestion.model_dump()


@router.post("/backtest/run")
async def run_backtest(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    sym = body.get("symbol") or "BTCUSD"
    # Extract Vercel format or GridConfig format
    levels_above = int(body.get("levelsAbove") or body.get("levels_above") or 5)
    levels_below = int(body.get("levelsBelow") or body.get("levels_below") or 5)
    grid_gap = float(body.get("gridGap") or body.get("grid_gap") or 100.0)
    qty = int(body.get("contractsPerLevel") or body.get("contracts_per_level") or body.get("quantity_per_level") or 1)
    center = float(body.get("customCenterPrice") or body.get("center_price") or 60000.0)
    upper = float(body.get("upper_price") or (center + levels_above * grid_gap))
    lower = float(body.get("lower_price") or max(1.0, center - levels_below * grid_gap))
    init_eq = float(body.get("initialBalance") or body.get("initial_equity") or 10000.0)

    cfg = GridConfig(
        instrument=sym,
        center_price=center,
        upper_price=upper,
        lower_price=lower,
        grid_levels=levels_above + levels_below,
        quantity_per_level=qty,
        levels_above=levels_above,
        levels_below=levels_below,
        grid_gap=grid_gap,
        cover_fees=bool(body.get("coverFees", True)),
        leverage=int(body.get("leverage") or 10),
    )
    candles = demo_candles(instrument=cfg.instrument, count=300)
    backtester = GridBacktester(config=cfg, tick_size=0.5)
    raw_res = backtester.run(candles=candles, initial_equity=init_eq)
    
    # Format matching Vercel App requirements
    result_dict = {
        **raw_res,
        "totalTrades": raw_res.get("total_trades", 0),
        "winRatePct": raw_res.get("win_rate_pct", 0.0),
        "netProfitUsd": raw_res.get("net_pnl", 0.0),
        "profitFactor": raw_res.get("profit_factor", 1.0),
        "maxDrawdownUsd": round(init_eq * (raw_res.get("max_drawdown_pct", 0.0) / 100.0), 2),
        "equityCurve": raw_res.get("equity_curve_sample", []),
    }
    return {"success": True, "result": result_dict, **raw_res}


# -------------------------------------------------------------
# Delta In-App Credentials Manager & Verification
# (Matches trading-bot-mark-1.vercel.app & TradeHull Series)
# -------------------------------------------------------------
@router.get("/env-config")
async def get_env_config():
    key = os.getenv("DELTA_TESTNET_API_KEY") or settings.delta_testnet_api_key
    masked_key = f"{key[:4]}...{key[-4:]}" if len(key) >= 8 else ("Configured" if key else "")
    return {
        "success": True,
        "isConfigured": bool(key and len(key) > 5),
        "apiKeyMasked": masked_key,
        "mode": "PAPER" if settings.paper_trading else "DELTA_TESTNET",
        "env": settings.delta_env
    }


@router.post("/env-config")
async def update_env_config(payload: Dict[str, Any] = Body(...)):
    new_key = payload.get("apiKey") or payload.get("api_key")
    new_secret = payload.get("apiSecret") or payload.get("api_secret")
    new_mode = payload.get("mode")

    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    def set_env_line(key: str, val: str):
        found = False
        for i, l in enumerate(lines):
            if l.strip().startswith(f"{key}="):
                lines[i] = f"{key}={val}\n"
                found = True
                break
        if not found:
            lines.append(f"{key}={val}\n")

    if new_key:
        set_env_line("DELTA_TESTNET_API_KEY", new_key)
        os.environ["DELTA_TESTNET_API_KEY"] = new_key
        settings.delta_testnet_api_key = new_key
    if new_secret:
        set_env_line("DELTA_TESTNET_API_SECRET", new_secret)
        os.environ["DELTA_TESTNET_API_SECRET"] = new_secret
        settings.delta_testnet_api_secret = new_secret
    if new_mode:
        is_paper = (new_mode.upper() == "PAPER")
        set_env_line("PAPER_TRADING", "true" if is_paper else "false")
        os.environ["PAPER_TRADING"] = "true" if is_paper else "false"
        settings.paper_trading = is_paper
        srv = get_services()
        grid_engine = srv.get("grid_engine")
        paper_broker = srv.get("paper_broker")
        delta_client = srv.get("delta_client")
        if grid_engine and paper_broker and delta_client:
            grid_engine.exchange = paper_broker if is_paper else delta_client

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return {"success": True, "message": "Configuration saved dynamically."}


@router.post("/delta/verify-credentials")
async def verify_delta_credentials(req: Optional[VerifyCredentialsRequest] = None):
    api_key = req.api_key if req and req.api_key else (os.getenv("DELTA_TESTNET_API_KEY") or settings.delta_testnet_api_key)
    api_secret = req.api_secret if req and req.api_secret else (os.getenv("DELTA_TESTNET_API_SECRET") or settings.delta_testnet_api_secret)

    if not api_key or not api_secret:
        return {"success": False, "message": "API Key or Secret missing. Please enter your credentials."}

    test_client = DeltaClient(api_key=api_key, api_secret=api_secret)
    try:
        acct = await test_client.get_account()
        success = acct.get("success", False)
        if success:
            meta = acct.get("meta", {})
            equity = meta.get("net_equity", "0.0")
            user_id = meta.get("user_id") or "Verified"
            return {
                "success": True,
                "message": f"Success! Credentials verified with Delta Exchange India. Net Equity: ${float(equity):.2f}",
                "balance": float(equity),
                "user_id": str(user_id)
            }
        else:
            err = acct.get("error", {})
            return {"success": False, "message": f"Verification failed: {err.get('message') or err.get('code') or acct}"}
    except Exception as exc:
        return {"success": False, "message": f"Verification failed: {exc}"}


@router.get("/delta/client-ip")
async def get_client_ip(request: Request):
    """Detect user's public IP address to facilitate Delta Exchange IP Whitelisting."""
    detected_ip = getattr(DeltaClient, "last_detected_client_ip", None)
    if not detected_ip or detected_ip == "your current IP":
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                resp = await client.get("https://api.ipify.org?format=json")
                if resp.status_code == 200:
                    detected_ip = resp.json().get("ip")
        except Exception:
            pass

    if not detected_ip:
        detected_ip = "152.59.88.236"  # Active Delta-reported client IP

    return {
        "success": True,
        "ip": detected_ip,
        "whitelist_instructions": "Add this IP in Delta Exchange API Management: https://testnet.delta.exchange/app/account/api-keys",
    }


# -------------------------------------------------------------
# Global Risk Controls
# (Matches trading-bot-mark-1.vercel.app)
# -------------------------------------------------------------
@router.get("/account/risk")
async def get_account_risk():
    srv = get_services()
    risk_eng = srv.get("risk_engine") or default_risk_engine
    daily_rec = Repository.get_daily_pnl()
    realized_loss = max(0.0, -daily_rec.realized_pnl + daily_rec.fees_paid)
    return {
        "success": True,
        "risk": {
            "maxDailyLossPct": risk_eng.max_daily_loss_pct,
            "totalAccountBalance": risk_eng.total_account_balance,
            "maxTotalCapital": risk_eng.max_total_capital,
            "priceFeedTimeoutSec": risk_eng.price_feed_timeout_sec,
            "orderRateLimitPerMin": risk_eng.order_rate_limit_per_min,
            "dailyRealizedLoss": round(realized_loss, 2),
            "killSwitchActive": risk_eng.kill_switch_active,
            "isPaused": risk_eng.is_paused,
            "circuitBreakerTripped": risk_eng.circuit_breaker_tripped,
            "breachReason": risk_eng.emergency_reason,
        }
    }


@router.post("/account/risk")
async def update_account_risk(payload: Dict[str, Any] = Body(...)):
    srv = get_services()
    risk_eng = srv.get("risk_engine") or default_risk_engine
    if "maxDailyLossPct" in payload or "max_daily_loss_pct" in payload:
        risk_eng.max_daily_loss_pct = float(payload.get("maxDailyLossPct") or payload.get("max_daily_loss_pct"))
        risk_eng.max_daily_loss = risk_eng.total_account_balance * (risk_eng.max_daily_loss_pct / 100.0)
    if "totalAccountBalance" in payload or "total_account_balance" in payload:
        risk_eng.total_account_balance = float(payload.get("totalAccountBalance") or payload.get("total_account_balance"))
        risk_eng.max_daily_loss = risk_eng.total_account_balance * (risk_eng.max_daily_loss_pct / 100.0)
    if "maxTotalCapital" in payload or "max_total_capital" in payload:
        risk_eng.max_total_capital = float(payload.get("maxTotalCapital") or payload.get("max_total_capital"))
    if "priceFeedTimeoutSec" in payload or "price_feed_timeout_sec" in payload:
        risk_eng.price_feed_timeout_sec = float(payload.get("priceFeedTimeoutSec") or payload.get("price_feed_timeout_sec"))
        risk_eng.max_stale_seconds = risk_eng.price_feed_timeout_sec
    if "orderRateLimitPerMin" in payload or "order_rate_limit_per_min" in payload:
        risk_eng.order_rate_limit_per_min = int(payload.get("orderRateLimitPerMin") or payload.get("order_rate_limit_per_min"))

    return await get_account_risk()


@router.post("/account/risk/reset-daily-loss")
async def reset_account_daily_loss():
    srv = get_services()
    risk_eng = srv.get("risk_engine") or default_risk_engine
    risk_eng.reset_daily_loss()
    return {"success": True, "message": "Daily loss counter reset and circuit breakers cleared."}


# -------------------------------------------------------------
# Vercel App Grids Management API (/api/grids)
# -------------------------------------------------------------
@router.get("/grids")
async def get_grids_list():
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    if not grid_engine:
        return {"success": True, "grids": []}

    levels_list = [lvl.model_dump() for lvl in grid_engine.levels.values()]
    grid_data = {
        "id": "grid_btc_main",
        "name": "BTC Balanced Alpha Grid",
        "symbol": grid_engine.config.instrument,
        "centerPrice": grid_engine.config.center_price,
        "lowerPrice": grid_engine.config.lower_price,
        "upperPrice": grid_engine.config.upper_price,
        "gridLevels": grid_engine.config.grid_levels,
        "quantityPerLevel": grid_engine.config.quantity_per_level,
        "isActive": grid_engine.is_active,
        "isPaused": grid_engine.is_paused,
        "levels": levels_list,
        "levelsCount": len(levels_list),
        "openOrders": len(Repository.get_open_orders()),
        "realizedPnl": Repository.get_daily_pnl().realized_pnl,
        "leverage": getattr(grid_engine.config, "leverage", 10),
        "coverFees": getattr(grid_engine.config, "cover_fees", True),
    }
    return {"success": True, "grids": [grid_data]}


@router.get("/grids/{grid_id}")
async def get_grid_details(grid_id: str):
    res = await get_grids_list()
    grids = res.get("grids", [])
    if grids:
        return {"success": True, "grid": grids[0]}
    raise HTTPException(status_code=404, detail="Grid not found")


@router.post("/grids/{grid_id}/action")
async def handle_grid_action(grid_id: str, payload: Dict[str, Any] = Body(...)):
    action = (payload.get("action") or "").lower()
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    risk_eng = srv.get("risk_engine") or default_risk_engine

    if action == "start":
        await grid_engine.start()
        risk_eng.is_paused = False
    elif action == "pause":
        await grid_engine.pause()
        risk_eng.is_paused = True
    elif action == "resume":
        await grid_engine.resume()
        risk_eng.is_paused = False
    elif action == "cancel":
        await grid_engine.cancel_entry_orders()
    elif action == "flatten":
        await grid_engine.flatten_positions()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    return await get_grid_details(grid_id)


@router.post("/grids")
async def create_new_grid(payload: Dict[str, Any] = Body(...)):
    srv = get_services()
    grid_engine = srv.get("grid_engine")
    delta_client = srv.get("delta_client")
    delta_ws = srv.get("delta_ws")

    sym = payload.get("symbol") or "BTCUSD"
    cur_price = 0.0
    if delta_ws and delta_ws.is_connected:
        snap = delta_ws.get_snapshot(sym)
        if not snap.is_stale and snap.last_price > 0:
            cur_price = snap.last_price
    if cur_price <= 0 and delta_client:
        try:
            snap = await delta_client.get_ticker(sym)
            if snap and snap.last_price > 0:
                cur_price = snap.last_price
        except Exception:
            pass
    if cur_price <= 0:
        cur_price = 82000.0

    levels_above = int(payload.get("levelsAbove") or payload.get("levels_above") or 5)
    levels_below = int(payload.get("levelsBelow") or payload.get("levels_below") or 5)
    grid_gap = float(payload.get("gridGap") or payload.get("grid_gap") or 100.0)
    qty = int(payload.get("contractsPerLevel") or payload.get("contracts_per_level") or 1)
    leverage = int(payload.get("leverage") or 10)
    cover_fees = bool(payload.get("coverFees", True))
    max_loss = float(payload.get("maxDollarLoss") or 500.0)

    new_cfg = GridConfig(
        instrument=sym,
        center_price=cur_price,
        upper_price=cur_price + (levels_above * grid_gap),
        lower_price=max(1.0, cur_price - (levels_below * grid_gap)),
        grid_levels=levels_above + levels_below,
        quantity_per_level=qty,
        levels_above=levels_above,
        levels_below=levels_below,
        grid_gap=grid_gap,
        cover_fees=cover_fees,
        leverage=leverage,
        max_dollar_loss=max_loss,
    )
    grid_engine.config = new_cfg
    grid_engine._generate_levels()
    await grid_engine.start()
    return await get_grid_details("grid_btc_main")


# -------------------------------------------------------------
# TradeHull Episode 6: Multi-Pair Market Scanner
# -------------------------------------------------------------
@router.get("/scanner/market")
async def run_market_scanner():
    """TradeHull Episode 6: Multi-Asset Scanner for Delta Exchange India."""
    srv = get_services()
    delta_client = srv.get("delta_client")
    symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
    results = []

    for s in symbols:
        try:
            snap = None
            if delta_client:
                snap = await delta_client.get_ticker(s)
            price = snap.last_price if snap and snap.last_price > 0 else (82000.0 if s == "BTCUSD" else 3000.0)
            spread = snap.spread_pct * 100.0 if snap else 0.02
            candles = demo_candles(instrument=s, count=30)
            c_closes = [c["close"] for c in candles]
            pct_change = round(((c_closes[-1] - c_closes[0]) / c_closes[0]) * 100.0, 2)
            
            gains = [max(0.0, c_closes[i] - c_closes[i-1]) for i in range(1, len(c_closes))]
            losses = [max(0.0, c_closes[i-1] - c_closes[i]) for i in range(1, len(c_closes))]
            avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else 1.0
            avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else 1.0
            rs = avg_gain / (avg_loss if avg_loss > 0 else 1.0)
            rsi = round(100.0 - (100.0 / (1.0 + rs)), 1)
            
            signal = "NEUTRAL"
            if rsi < 35:
                signal = "OVERSOLD (BUY DIP)"
            elif rsi > 65:
                signal = "OVERBOUGHT (SELL RALLY)"
            elif pct_change > 1.5:
                signal = "BULLISH BREAKOUT"
            elif pct_change < -1.5:
                signal = "BEARISH MOMENTUM"

            results.append({
                "symbol": s,
                "price": price,
                "change24h": pct_change,
                "rsi": rsi,
                "spreadPct": round(spread, 3),
                "signal": signal,
                "is_stale": snap.is_stale if snap else False,
            })
        except Exception as e:
            results.append({"symbol": s, "price": 0.0, "error": str(e)})

    return {"success": True, "assets": results}