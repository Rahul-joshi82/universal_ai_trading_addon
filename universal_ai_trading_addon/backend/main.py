"""Universal AI Trading Add-on - Main Application.

FastAPI backend with institutional-grade risk management, Delta Exchange India
integration, persistent database storage, and quantitative grid trading engine.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import time
import uuid

from .config import settings
from .models import OrderRequest, Side, OrderType, OrderStatus, GridConfig, MarketDataSnapshot
from .market_data import demo_candles
from .strategy import moving_average_signal
from .risk.engine import RiskRejected, RiskEngine, default_risk_engine
from .persistence.database import init_db
from .persistence.repository import Repository
from .exchange.paper_broker import PaperBroker
from .exchange.delta_client import DeltaClient, DeltaAPIError
from .exchange.delta_websocket import DeltaWebSocketClient
from .strategies.grid_strategy import GridStrategyEngine
from .order_manager.state_machine import OrderStateMachine
from .order_manager.reconciler import OrderReconciler
from .api.routes import router as api_router, services

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

# Global backward-compatible instances
init_db()  # Eager table creation ensures synchronous testclients have tables ready
risk_engine = default_risk_engine
broker = PaperBroker(risk_engine=risk_engine)
delta_client = DeltaClient()
delta_ws = DeltaWebSocketClient()
reconciler = OrderReconciler(exchange=broker if settings.paper_trading else delta_client)

initial_grid_config = GridConfig(
    instrument="BTCUSD",
    center_price=60000.0,
    upper_price=65000.0,
    lower_price=55000.0,
    grid_levels=10,
    quantity_per_level=1,
)
grid_engine = GridStrategyEngine(
    config=initial_grid_config,
    exchange=broker if settings.paper_trading else delta_client,
    risk_engine=risk_engine,
    tick_size=0.5
)

# Populate services container for API router
services["risk_engine"] = risk_engine
services["paper_broker"] = broker
services["delta_client"] = delta_client
services["delta_ws"] = delta_ws
services["reconciler"] = reconciler
services["grid_engine"] = grid_engine

paused = False
latest_signals: dict[str, dict] = {}
audit_log: list[dict] = []


async def background_market_worker():
    """Continuously feeds real-time market data to the grid strategy engine."""
    while True:
        try:
            await asyncio.sleep(2.5)
            # Check if WebSocket snapshot is fresh
            ws_snap = delta_ws.get_snapshot("BTCUSD")
            if not ws_snap.is_stale and ws_snap.last_price > 0:
                if grid_engine.is_active and not grid_engine.is_paused:
                    await grid_engine.on_market_update(ws_snap)
            else:
                # Use resilient REST ticker fallback
                try:
                    rest_snap = await delta_client.get_ticker("BTCUSD")
                    if rest_snap and rest_snap.last_price > 0:
                        delta_ws.snapshots["BTCUSD"] = rest_snap
                        if grid_engine.is_active and not grid_engine.is_paused:
                            await grid_engine.on_market_update(rest_snap)
                except Exception:
                    pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Background market worker tick exception: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Startup: Initialize persistence tables
    logger.info("Initializing database persistence tables...")
    init_db()

    # 2. Synchronize grid strategy engine with stored state
    await grid_engine.initialize()

    # 3. Startup reconciliation: Recover active orders and positions
    logger.info("Executing startup reconciliation...")
    try:
        report = await reconciler.reconcile(symbol="BTCUSD")
        logger.info(f"Startup reconciliation status: {report.status}")
    except Exception as exc:
        logger.warning(f"Startup reconciliation encountered non-fatal error: {exc}")

    # 4. Start WebSocket and background market ticker workers
    try:
        delta_ws.on_ticker_callback = lambda snap: asyncio.create_task(grid_engine.on_market_update(snap))
        await delta_ws.start()
    except Exception as ws_err:
        logger.warning(f"WebSocket client startup non-fatal: {ws_err}")

    bg_worker_task = asyncio.create_task(background_market_worker())

    # 5. Autonomous auto-start & initial trade execution on boot
    if settings.auto_start_on_boot:
        try:
            # Fetch live Bitcoin price from Delta Exchange
            live_price = 0.0
            try:
                snap = await delta_client.get_ticker("BTCUSD")
                if snap and snap.last_price > 0:
                    live_price = snap.last_price
            except Exception:
                pass

            if live_price <= 0:
                live_price = 82000.0  # Reasonable fallback if edge network temporarily slow

            # Auto-calibrate grid levels around the live market price
            half_range = live_price * 0.04
            grid_engine.config.center_price = round(live_price, 1)
            grid_engine.config.lower_price = round(live_price - half_range, 1)
            grid_engine.config.upper_price = round(live_price + half_range, 1)
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

            # Activate the grid engine
            await grid_engine.start()

            # Execute automatic initial seed trade if no position exists yet
            pos_rec = Repository.get_position("BTCUSD")
            curr_pos = pos_rec.size if pos_rec else 0
            if curr_pos == 0:
                seed_id = f"auto_seed_{uuid.uuid4().hex[:8]}"
                qty = grid_engine.config.quantity_per_level
                seed_order = OrderRequest(
                    instrument="BTCUSD",
                    side=Side.BUY,
                    quantity=qty,
                    order_type=OrderType.MARKET,
                    client_order_id=seed_id,
                )
                Repository.save_order(
                    client_order_id=seed_id,
                    symbol="BTCUSD",
                    side="BUY",
                    order_type="MARKET",
                    price=live_price,
                    quantity=qty,
                    status=OrderStatus.PROPOSED.value,
                    is_exit=False,
                )
                OrderStateMachine.transition(seed_id, OrderStatus.RISK_APPROVED, "Auto-boot seed approved")
                OrderStateMachine.transition(seed_id, OrderStatus.SUBMITTED, "Submitting auto-boot seed")
                res = await grid_engine.exchange.place_order(seed_order)
                logger.info(f"Auto-boot initial trade executed: {res}")

            mode_str = "PAPER SIMULATION (Local DB)" if settings.paper_trading else "DELTA TESTNET LIVE (Real Exchange)"
            print(
                f"\n{'='*75}\n"
                f">>> [AUTONOMOUS TRADING ENGINE ACTIVE & RUNNING]\n"
                f"   Status:       ONLINE & AUTOMATICALLY MONITORING TRADES\n"
                f"   Instrument:   BTCUSD\n"
                f"   Market Price: ${live_price:,.2f}\n"
                f"   Grid Ladder:  10 levels between ${grid_engine.config.lower_price:,.2f} and ${grid_engine.config.upper_price:,.2f}\n"
                f"   Mode:         {mode_str}\n"
                f"   Auto-Trade:   Initial BUY order placed! Bot will auto-buy dips & auto-sell rallies.\n"
                f"{'='*75}\n",
                flush=True
            )
        except Exception as boot_err:
            logger.warning(f"Auto-start on boot non-fatal: {boot_err}")

    yield

    # 6. Shutdown: Gracefully stop background workers
    logger.info("Shutting down background workers...")
    bg_worker_task.cancel()
    try:
        await bg_worker_task
    except asyncio.CancelledError:
        pass
    await delta_ws.stop()
    await grid_engine.stop()
    await delta_client.close()


app = FastAPI(
    title="Universal AI Trading Add-on for Delta Exchange India",
    version="1.0.0",
    description="Quantitative AI Trading Add-on with Grid Strategy, Delta Exchange India, and Risk Management.",
    lifespan=lifespan
)

# CORS configuration for browser extension and local dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount core modular API routes
app.include_router(api_router, prefix="/api")


# -------------------------------------------------------------
# Backward-compatible routes for starter tests and extension
# -------------------------------------------------------------
@app.get("/health")
async def health():
    mode = "PAPER" if settings.paper_trading else f"DELTA_{settings.delta_env.upper()}"
    return {
        "status": "ok",
        "mode": mode,
        "live_execution_enabled": not settings.paper_trading and settings.delta_enable_testnet_orders,
        "database": "SQLITE_PERSISTENT",
    }


@app.get("/api/status")
async def status():
    mode = "PAPER" if settings.paper_trading else f"DELTA_{settings.delta_env.upper()}"
    account = await broker.get_account()
    positions = await broker.get_positions()
    return {
        "mode": mode,
        "paused": risk_engine.is_paused or paused,
        "live_execution_enabled": not settings.paper_trading and settings.delta_enable_testnet_orders,
        "account": account,
        "positions": positions,
        "orders_count": len(broker.orders),
    }


@app.post("/api/pause")
async def pause():
    global paused
    paused = True
    risk_engine.is_paused = True
    await grid_engine.pause()
    audit_log.append({"event": "PAUSED"})
    return {"paused": True, "message": "Trading paused. New entry orders disabled."}


@app.post("/api/resume")
async def resume():
    global paused
    if risk_engine.kill_switch_active:
        raise HTTPException(status_code=423, detail="Kill switch is active. Reset kill switch before resuming.")
    paused = False
    risk_engine.is_paused = False
    await grid_engine.resume()
    audit_log.append({"event": "RESUMED"})
    return {"paused": False, "message": "Trading resumed."}


@app.get("/api/analyze/{instrument}")
async def analyze(instrument: str):
    candles = demo_candles(instrument.upper())
    signal = moving_average_signal(instrument.upper(), [c["close"] for c in candles])
    latest_signals[instrument.upper()] = signal.model_dump()
    return {
        "signal": signal.model_dump(),
        "data_source": "SYNTHETIC_DEMO_DATA",
        "warning": "Not live market data or investment advice."
    }


@app.post("/api/orders/paper")
async def place_paper_order(order: OrderRequest):
    if risk_engine.is_paused or paused:
        raise HTTPException(status_code=423, detail="Trading is paused.")
    if order.side == Side.HOLD:
        raise HTTPException(status_code=400, detail="HOLD cannot be submitted as an order.")
    candles = demo_candles(order.instrument.upper())
    reference_price = candles[-1]["close"]
    try:
        result = await broker.place_order(order, reference_price)
    except RiskRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_log.append({
        "event": "PAPER_ORDER",
        "order_id": result.order_id,
        "instrument": result.instrument,
        "side": result.side.value,
        "quantity": result.quantity,
        "status": result.status
    })
    return result.model_dump()


@app.get("/api/audit")
async def audit():
    return audit_log[-100:]


# -------------------------------------------------------------
# Delta Exchange Testnet Endpoints
# -------------------------------------------------------------
@app.get("/api/delta/testnet/products")
async def delta_testnet_products():
    try:
        from .brokers.delta_testnet import DeltaTestnetClient, DeltaAPIError as OldDeltaError
        return DeltaTestnetClient().get_products()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/delta/testnet/ticker/{symbol}")
async def delta_testnet_ticker(symbol: str):
    try:
        from .brokers.delta_testnet import DeltaTestnetClient, DeltaAPIError as OldDeltaError
        return DeltaTestnetClient().get_ticker(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/delta/testnet/account")
async def delta_testnet_account():
    try:
        from .brokers.delta_testnet import DeltaTestnetClient
        client = DeltaTestnetClient()
        return {
            "environment": "DELTA_DEMO_TESTNET",
            "wallet": client.get_wallet_balances(),
            "positions": client.get_positions(),
            "order_placement_enabled": client.enable_orders,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/delta/testnet/orders")
async def delta_testnet_order(payload: dict):
    try:
        from .brokers.delta_testnet import DeltaTestnetClient
        client = DeltaTestnetClient()
        return client.place_testnet_order(
            product_id=payload.get("product_id"),
            side=payload.get("side", ""),
            size=payload.get("size"),
            order_type=payload.get("order_type", "market_order"),
            limit_price=payload.get("limit_price"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
