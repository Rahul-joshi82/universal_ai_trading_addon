"""12-state Order Lifecycle State Machine.

States:
- PROPOSED
- RISK_APPROVED
- RISK_REJECTED
- SUBMITTED
- OPEN
- PARTIALLY_FILLED
- FILLED
- CANCELLATION_REQUESTED
- CANCELLED
- REJECTED
- EXIT_PENDING
- CLOSED
- RECONCILIATION_REQUIRED
"""
from __future__ import annotations

import logging
from typing import Dict, Set, Optional, Any
from ..models import OrderStatus
from ..persistence.repository import Repository
from ..persistence.models import OrderDB

logger = logging.getLogger("state_machine")


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal order lifecycle transition is requested."""
    pass


class OrderStateMachine:
    # Valid state transition graph
    ALLOWED_TRANSITIONS: Dict[OrderStatus, Set[OrderStatus]] = {
        OrderStatus.PROPOSED: {
            OrderStatus.RISK_APPROVED,
            OrderStatus.RISK_REJECTED,
            OrderStatus.RECONCILIATION_REQUIRED,
        },
        OrderStatus.RISK_APPROVED: {
            OrderStatus.SUBMITTED,
            OrderStatus.REJECTED,
            OrderStatus.RECONCILIATION_REQUIRED,
        },
        OrderStatus.RISK_REJECTED: set(),  # Terminal
        OrderStatus.SUBMITTED: {
            OrderStatus.OPEN,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
            OrderStatus.RECONCILIATION_REQUIRED,
        },
        OrderStatus.OPEN: {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLATION_REQUESTED,
            OrderStatus.CANCELLED,
            OrderStatus.RECONCILIATION_REQUIRED,
        },
        OrderStatus.PARTIALLY_FILLED: {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLATION_REQUESTED,
            OrderStatus.CANCELLED,
            OrderStatus.RECONCILIATION_REQUIRED,
        },
        OrderStatus.FILLED: {
            OrderStatus.EXIT_PENDING,
            OrderStatus.CLOSED,
            OrderStatus.RECONCILIATION_REQUIRED,
        },
        OrderStatus.CANCELLATION_REQUESTED: {
            OrderStatus.CANCELLED,
            OrderStatus.FILLED,  # If fill arrived before cancel
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.RECONCILIATION_REQUIRED,
        },
        OrderStatus.CANCELLED: set(),  # Terminal
        OrderStatus.REJECTED: set(),   # Terminal
        OrderStatus.EXIT_PENDING: {
            OrderStatus.CLOSED,
            OrderStatus.RECONCILIATION_REQUIRED,
        },
        OrderStatus.CLOSED: set(),     # Terminal
        OrderStatus.RECONCILIATION_REQUIRED: {
            OrderStatus.OPEN,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.CLOSED,
        },
    }

    @classmethod
    def can_transition(cls, current_status: OrderStatus, target_status: OrderStatus) -> bool:
        if current_status == target_status:
            return True
        allowed = cls.ALLOWED_TRANSITIONS.get(current_status, set())
        return target_status in allowed

    @classmethod
    def transition(
        cls,
        client_order_id: str,
        target_status: OrderStatus,
        reason: str = "",
        exchange_order_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> OrderDB:
        order = Repository.get_order_by_client_id(client_order_id)
        if not order:
            raise ValueError(f"Order not found for client_order_id: {client_order_id}")

        current_enum = OrderStatus(order.status)
        if not cls.can_transition(current_enum, target_status):
            msg = f"Illegal transition for order {client_order_id}: {order.status} -> {target_status.value} ({reason})"
            logger.error(msg)
            # Flag order as requiring reconciliation if transition mismatch
            order = Repository.save_order(
                client_order_id=client_order_id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                price=order.price,
                quantity=order.quantity,
                status=OrderStatus.RECONCILIATION_REQUIRED.value,
                error_message=msg,
            )
            raise InvalidStateTransitionError(msg)

        order = Repository.save_order(
            client_order_id=client_order_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            price=order.price,
            quantity=order.quantity,
            status=target_status.value,
            product_id=order.product_id,
            is_exit=order.is_exit,
            grid_level_id=order.grid_level_id,
            exchange_order_id=exchange_order_id or order.exchange_order_id,
            error_message=error_message,
        )
        logger.info(f"Order {client_order_id} transitioned: {current_enum.value} -> {target_status.value} ({reason})")
        return order
