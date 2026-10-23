"""Tests for Central Quantitative Risk Management Engine."""
import pytest
from backend.models import OrderRequest, Side, OrderType, MarketDataSnapshot
from backend.risk.engine import RiskEngine, RiskRejected
from backend.persistence.repository import Repository
from backend.persistence.database import init_db


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_risk_order_quantity_limit():
    engine = RiskEngine(max_order_quantity=5)
    order = OrderRequest(instrument="BTCUSD", side=Side.BUY, quantity=10, order_type=OrderType.MARKET)
    with pytest.raises(RiskRejected) as exc:
        engine.validate_order(order, current_position=0)
    assert "exceeds max order quantity" in str(exc.value)


def test_risk_position_limit():
    engine = RiskEngine(max_order_quantity=5, max_position_quantity=10)
    # Current position is 8. Buying 4 would lead to 12 (> 10)
    order = OrderRequest(instrument="BTCUSD", side=Side.BUY, quantity=4, order_type=OrderType.MARKET)
    with pytest.raises(RiskRejected) as exc:
        engine.validate_order(order, current_position=8)
    assert "exceed max position quantity" in str(exc.value)


def test_risk_reducing_order_exempt_from_position_limit():
    """Rule 8: Do not block an order that reduces risk solely because an entry limit was reached."""
    engine = RiskEngine(max_order_quantity=5, max_position_quantity=10)
    # Current position is 10 (at limit). Selling 3 reduces position to 7!
    order = OrderRequest(instrument="BTCUSD", side=Side.SELL, quantity=3, order_type=OrderType.MARKET, is_exit=True)
    # Should NOT raise RiskRejected
    engine.validate_order(order, current_position=10)


def test_stale_market_data_blocks_entry():
    engine = RiskEngine(max_stale_seconds=5.0)
    stale_snap = MarketDataSnapshot(
        symbol="BTCUSD",
        best_bid=60000.0,
        best_ask=60010.0,
        mid_price=60005.0,
        last_price=60005.0,
        is_stale=True,  # Stale flag active
    )
    order = OrderRequest(instrument="BTCUSD", side=Side.BUY, quantity=1, order_type=OrderType.MARKET)
    with pytest.raises(RiskRejected) as exc:
        engine.validate_order(order, current_position=0, market_snapshot=stale_snap)
    assert "stale" in str(exc.value).lower()


def test_excessive_spread_blocks_entry():
    engine = RiskEngine(max_spread_pct=0.005)  # 0.5% max
    wide_snap = MarketDataSnapshot(
        symbol="BTCUSD",
        best_bid=59000.0,
        best_ask=60000.0,
        mid_price=59500.0,
        last_price=59500.0,
        spread=1000.0,
        spread_pct=0.0168,  # 1.68% > 0.5%
        is_stale=False,
    )
    order = OrderRequest(instrument="BTCUSD", side=Side.BUY, quantity=1, order_type=OrderType.MARKET)
    with pytest.raises(RiskRejected) as exc:
        engine.validate_order(order, current_position=0, market_snapshot=wide_snap)
    assert "spread" in str(exc.value).lower()


def test_daily_loss_limit_persisted_blocks_entries():
    engine = RiskEngine(max_daily_loss=500.0)
    
    # Record a realized loss of 600 in repository
    Repository.record_fill(
        client_order_id="loss_order_1",
        symbol="BTCUSD",
        side="SELL",
        fill_price=58000.0,
        fill_quantity=1,
        realized_pnl=-650.0,
    )

    # An entry order should be rejected due to daily loss breach
    order = OrderRequest(instrument="BTCUSD", side=Side.BUY, quantity=1, order_type=OrderType.MARKET)
    with pytest.raises(RiskRejected) as exc:
        engine.validate_order(order, current_position=0)
    assert "daily loss limit breached" in str(exc.value).lower()


def test_kill_switch_blocks_and_resets():
    engine = RiskEngine()
    engine.activate_kill_switch("Manual test trip")
    assert engine.kill_switch_active is True

    order = OrderRequest(instrument="BTCUSD", side=Side.BUY, quantity=1, order_type=OrderType.MARKET)
    with pytest.raises(RiskRejected) as exc:
        engine.validate_order(order, current_position=0)
    assert "kill switch is active" in str(exc.value).lower()

    engine.reset_kill_switch()
    assert engine.kill_switch_active is False
    # Now order should pass
    engine.validate_order(order, current_position=0)


def test_circuit_breaker_consecutive_errors():
    engine = RiskEngine(consecutive_error_limit=3)
    assert engine.circuit_breaker_tripped is False

    engine.record_error("API timeout 1")
    engine.record_error("API timeout 2")
    assert engine.circuit_breaker_tripped is False

    engine.record_error("API timeout 3")
    assert engine.circuit_breaker_tripped is True
    assert engine.kill_switch_active is True
