"""Tests for State Reconciliation and Restart Recovery."""
import pytest
from unittest.mock import AsyncMock
from backend.models import OrderStatus
from backend.order_manager.reconciler import OrderReconciler
from backend.persistence.repository import Repository
from backend.persistence.database import init_db


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


@pytest.mark.asyncio
async def test_reconciliation_resolves_discrepant_positions():
    # Setup mock exchange returning a position of 5 contracts
    mock_exchange = AsyncMock()
    mock_exchange.get_open_orders.return_value = []
    mock_exchange.get_positions.return_value = [{"symbol": "BTCUSD", "size": 5}]

    # Local DB has position of 2 contracts
    Repository.update_position("BTCUSD", size=2, entry_price=59000.0, mark_price=60000.0, realized_pnl=0.0, unrealized_pnl=0.0)

    reconciler = OrderReconciler(exchange=mock_exchange)
    report = await reconciler.reconcile(symbol="BTCUSD")

    assert report.local_position == 2
    assert report.exchange_position == 5
    assert report.position_discrepancy == 3
    assert "Position discrepancy detected" in report.actions_taken[0]

    # Verify local position in DB has been updated to exchange source of truth
    updated_pos = Repository.get_position("BTCUSD")
    assert updated_pos.size == 5


@pytest.mark.asyncio
async def test_reconciliation_resolves_filled_exchange_order():
    client_id = "recon_order_101"
    exchange_id = "ex_999"

    # Local order is OPEN in DB
    Repository.save_order(
        client_order_id=client_id,
        symbol="BTCUSD",
        side="BUY",
        order_type="LIMIT",
        price=59500.0,
        quantity=1,
        status=OrderStatus.OPEN.value,
        exchange_order_id=exchange_id,
    )

    # Exchange open orders is empty (order has filled on exchange!)
    mock_exchange = AsyncMock()
    mock_exchange.get_open_orders.return_value = []
    mock_exchange.get_order_status.return_value = {"result": {"id": exchange_id, "state": "filled"}}
    mock_exchange.get_positions.return_value = []

    reconciler = OrderReconciler(exchange=mock_exchange)
    report = await reconciler.reconcile(symbol="BTCUSD")

    # Order should be resolved to FILLED
    db_order = Repository.get_order_by_client_id(client_id)
    assert db_order.status == OrderStatus.FILLED.value
