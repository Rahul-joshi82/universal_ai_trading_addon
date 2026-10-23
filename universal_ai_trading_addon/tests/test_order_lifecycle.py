"""Tests for 12-state Order Lifecycle State Machine and Audit Trail."""
import json
import pytest
from backend.models import OrderStatus
from backend.order_manager.state_machine import OrderStateMachine, InvalidStateTransitionError
from backend.persistence.repository import Repository
from backend.persistence.database import init_db


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_order_lifecycle_full_happy_path():
    client_id = "test_lifecycle_001"
    # 1. Proposed
    order = Repository.save_order(
        client_order_id=client_id,
        symbol="BTCUSD",
        side="BUY",
        order_type="LIMIT",
        price=59000.0,
        quantity=1,
        status=OrderStatus.PROPOSED.value,
    )
    assert order.status == OrderStatus.PROPOSED.value

    # 2. Risk Approved
    order = OrderStateMachine.transition(client_id, OrderStatus.RISK_APPROVED, "Risk evaluation passed")
    assert order.status == OrderStatus.RISK_APPROVED.value

    # 3. Submitted
    order = OrderStateMachine.transition(client_id, OrderStatus.SUBMITTED, "Sent to exchange")
    assert order.status == OrderStatus.SUBMITTED.value

    # 4. Open
    order = OrderStateMachine.transition(client_id, OrderStatus.OPEN, "Exchange confirmed order placement", exchange_order_id="ex_123")
    assert order.status == OrderStatus.OPEN.value
    assert order.exchange_order_id == "ex_123"

    # 5. Partially Filled
    order = OrderStateMachine.transition(client_id, OrderStatus.PARTIALLY_FILLED, "Partial fill 0.5")
    assert order.status == OrderStatus.PARTIALLY_FILLED.value

    # 6. Filled
    order = OrderStateMachine.transition(client_id, OrderStatus.FILLED, "Completed remaining fill")
    assert order.status == OrderStatus.FILLED.value

    # 7. Exit Pending
    order = OrderStateMachine.transition(client_id, OrderStatus.EXIT_PENDING, "Waiting for exit order fill")
    assert order.status == OrderStatus.EXIT_PENDING.value

    # 8. Closed
    order = OrderStateMachine.transition(client_id, OrderStatus.CLOSED, "Cycle finalized")
    assert order.status == OrderStatus.CLOSED.value

    # Verify audit history length and events
    history = json.loads(order.lifecycle_history)
    assert len(history) >= 8
    statuses = [h["to"] for h in history]
    assert "PROPOSED" in statuses
    assert "RISK_APPROVED" in statuses
    assert "SUBMITTED" in statuses
    assert "OPEN" in statuses
    assert "PARTIALLY_FILLED" in statuses
    assert "FILLED" in statuses
    assert "EXIT_PENDING" in statuses
    assert "CLOSED" in statuses


def test_cancellation_flow():
    client_id = "test_cancel_002"
    Repository.save_order(
        client_order_id=client_id,
        symbol="BTCUSD",
        side="SELL",
        order_type="LIMIT",
        price=61000.0,
        quantity=1,
        status=OrderStatus.OPEN.value,
    )

    OrderStateMachine.transition(client_id, OrderStatus.CANCELLATION_REQUESTED, "Cancel request sent")
    order = OrderStateMachine.transition(client_id, OrderStatus.CANCELLED, "Exchange confirmed cancellation")
    assert order.status == OrderStatus.CANCELLED.value


def test_invalid_transition_triggers_reconciliation_required():
    client_id = "test_invalid_003"
    Repository.save_order(
        client_order_id=client_id,
        symbol="BTCUSD",
        side="BUY",
        order_type="LIMIT",
        price=59000.0,
        quantity=1,
        status=OrderStatus.CLOSED.value,  # Terminal state
    )

    # Attempting to move from CLOSED directly to SUBMITTED is illegal
    with pytest.raises(InvalidStateTransitionError):
        OrderStateMachine.transition(client_id, OrderStatus.SUBMITTED, "Illegal direct jump")

    # Order should now be flagged as RECONCILIATION_REQUIRED in DB
    order = Repository.get_order_by_client_id(client_id)
    assert order.status == OrderStatus.RECONCILIATION_REQUIRED.value
