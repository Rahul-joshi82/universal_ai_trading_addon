"""Tests for BTC Grid Trading Strategy Engine."""
import pytest
from backend.models import GridConfig, Side, OrderType, MarketDataSnapshot
from backend.strategies.grid_strategy import GridStrategyEngine
from backend.exchange.paper_broker import PaperBroker
from backend.exchange.delta_client import DeltaClient
from backend.risk.engine import RiskEngine
from backend.persistence.repository import Repository
from backend.persistence.database import init_db


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_grid_level_generation_arithmetic():
    cfg = GridConfig(
        instrument="BTCUSD",
        center_price=60000.0,
        upper_price=65000.0,
        lower_price=55000.0,
        grid_levels=10,
        quantity_per_level=1,
    )
    risk = RiskEngine()
    broker = PaperBroker(risk_engine=risk)
    engine = GridStrategyEngine(config=cfg, exchange=broker, risk_engine=risk, tick_size=0.5)

    assert len(engine.levels) == 11
    # Check bounds
    assert engine.levels[0].price == 55000.0
    assert engine.levels[10].price == 65000.0
    # Levels below 60000 should be BUY, above should be SELL
    for lvl_id, lvl in engine.levels.items():
        if lvl.price < 60000.0:
            assert lvl.target_side == Side.BUY
        elif lvl.price > 60000.0:
            assert lvl.target_side == Side.SELL


def test_grid_level_generation_geometric():
    cfg = GridConfig(
        instrument="BTCUSD",
        center_price=60000.0,
        upper_price=66000.0,
        lower_price=55000.0,
        grid_levels=4,
        spacing_type="percentage",
    )
    risk = RiskEngine()
    broker = PaperBroker(risk_engine=risk)
    engine = GridStrategyEngine(config=cfg, exchange=broker, risk_engine=risk, tick_size=0.5)

    assert len(engine.levels) == 5
    assert engine.levels[0].price == 55000.0
    assert engine.levels[4].price == 66000.0
    # Prices strictly increasing
    prices = [lvl.price for lvl in engine.levels.values()]
    assert prices == sorted(prices)


def test_tick_size_rounding():
    assert DeltaClient.round_to_tick(60000.12, 0.5) == 60000.0
    assert DeltaClient.round_to_tick(60000.35, 0.5) == 60000.5
    assert DeltaClient.round_to_tick(60000.78, 0.5) == 60001.0
    assert DeltaClient.round_to_tick(1234.567, 0.05) == 1234.55


@pytest.mark.asyncio
async def test_grid_entry_and_exit_paired_lifecycle():
    cfg = GridConfig(
        instrument="BTCUSD",
        center_price=60000.0,
        upper_price=62000.0,
        lower_price=58000.0,
        grid_levels=4,  # step = 1000: 58000, 59000 (buy), 60000, 61000, 62000 (sell)
        quantity_per_level=1,
        max_open_orders=5,
    )
    risk = RiskEngine()
    broker = PaperBroker(risk_engine=risk)
    engine = GridStrategyEngine(config=cfg, exchange=broker, risk_engine=risk, tick_size=0.5)
    await engine.start()

    # Feed market snapshot at 60000.0
    snap = MarketDataSnapshot(
        symbol="BTCUSD",
        best_bid=59999.5,
        best_ask=60000.5,
        mid_price=60000.0,
        last_price=60000.0,
        timestamp_exchange=1000.0,
        timestamp_local=1000.0,
        is_stale=False,
    )
    await engine.on_market_update(snap)

    # Levels below 60000 (e.g. 58000, 59000) should now have ENTRY_OPEN orders
    lvl_59k = [lvl for lvl in engine.levels.values() if lvl.price == 59000.0][0]
    assert lvl_59k.status == "ENTRY_OPEN"
    assert lvl_59k.entry_client_order_id is not None

    # Now price drops to 58900 (penetrating 59000 buy level)
    drop_snap = MarketDataSnapshot(
        symbol="BTCUSD",
        best_bid=58899.5,
        best_ask=58900.5,
        mid_price=58900.0,
        last_price=58900.0,
        timestamp_exchange=1001.0,
        timestamp_local=1001.0,
        is_stale=False,
    )
    await engine.on_market_update(drop_snap)

    # 59000 entry should have filled, and an EXIT_OPEN order placed at 60000 (1 step higher)
    assert lvl_59k.status == "EXIT_OPEN"
    assert lvl_59k.exit_client_order_id is not None
    assert lvl_59k.paired_price == 60000.0

    # Price rebounds back up to 60050 (penetrating 60000 exit price)
    rally_snap = MarketDataSnapshot(
        symbol="BTCUSD",
        best_bid=60049.5,
        best_ask=60050.5,
        mid_price=60050.0,
        last_price=60050.0,
        timestamp_exchange=1002.0,
        timestamp_local=1002.0,
        is_stale=False,
    )
    await engine.on_market_update(rally_snap)

    # Level cycle should be complete and status reset to VACANT
    assert lvl_59k.status == "VACANT"
    assert lvl_59k.cycle_count == 1


@pytest.mark.asyncio
async def test_duplicate_order_prevention():
    cfg = GridConfig(
        instrument="BTCUSD",
        center_price=60000.0,
        upper_price=62000.0,
        lower_price=58000.0,
        grid_levels=4,
        quantity_per_level=1,
    )
    risk = RiskEngine()
    broker = PaperBroker(risk_engine=risk)
    engine = GridStrategyEngine(config=cfg, exchange=broker, risk_engine=risk, tick_size=0.5)
    await engine.start()

    snap = MarketDataSnapshot(
        symbol="BTCUSD",
        best_bid=59999.0,
        best_ask=60001.0,
        mid_price=60000.0,
        last_price=60000.0,
        timestamp_local=1000.0,
        is_stale=False,
    )

    # Feed twice in succession
    await engine.on_market_update(snap)
    open_orders_1 = Repository.get_open_orders()
    
    await engine.on_market_update(snap)
    open_orders_2 = Repository.get_open_orders()

    # Count of open orders should not double
    assert len(open_orders_1) == len(open_orders_2)


@pytest.mark.asyncio
async def test_grid_stop_loss_trigger():
    cfg = GridConfig(
        instrument="BTCUSD",
        center_price=60000.0,
        upper_price=65000.0,
        lower_price=55000.0,
        grid_levels=10,
        stop_loss_price=52000.0,
    )
    risk = RiskEngine()
    broker = PaperBroker(risk_engine=risk)
    engine = GridStrategyEngine(config=cfg, exchange=broker, risk_engine=risk, tick_size=0.5)
    await engine.start()

    crash_snap = MarketDataSnapshot(
        symbol="BTCUSD",
        best_bid=51900.0,
        best_ask=51950.0,
        mid_price=51925.0,
        last_price=51900.0,
        timestamp_local=1000.0,
        is_stale=False,
    )
    await engine.on_market_update(crash_snap)

    assert risk.kill_switch_active is True
    assert engine.is_paused is True
    assert "stop-loss" in risk.emergency_reason.lower()
