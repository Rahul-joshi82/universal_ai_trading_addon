"""Order and position reconciliation engine.

Reconciles local database state against the exchange source of truth on startup
and after reconnects. Recovers active orders and positions without duplication.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from .state_machine import OrderStateMachine
from ..exchange.base import ExchangeAdapter
from ..models import OrderStatus, ReconciliationReport
from ..persistence.repository import Repository
from ..persistence.models import OrderDB

logger = logging.getLogger("reconciler")


class OrderReconciler:
    def __init__(self, exchange: ExchangeAdapter):
        self.exchange = exchange

    async def reconcile(self, symbol: str = "BTCUSD") -> ReconciliationReport:
        now_str = datetime.now(timezone.utc).isoformat()
        actions: List[str] = []
        
        # 1. Fetch exchange open orders
        try:
            exchange_orders = await self.exchange.get_open_orders(symbol=symbol)
        except Exception as exc:
            logger.error(f"Reconciliation failed to fetch exchange open orders: {exc}")
            exchange_orders = []
            actions.append(f"Failed to query exchange orders: {exc}")

        # Map exchange orders by exchange order ID and client_order_id
        exchange_by_id: Dict[str, Dict[str, Any]] = {}
        exchange_by_client_id: Dict[str, Dict[str, Any]] = {}
        for eo in exchange_orders:
            e_id = str(eo.get("id") or "")
            c_id = str(eo.get("client_order_id") or "")
            if e_id:
                exchange_by_id[e_id] = eo
            if c_id:
                exchange_by_client_id[c_id] = eo

        # 2. Fetch local active orders from DB
        local_open_orders = Repository.get_open_orders()
        local_by_client_id = {o.client_order_id: o for o in local_open_orders}

        matched_count = 0
        unmatched_local: List[str] = []
        unmatched_exchange: List[str] = list(exchange_by_id.keys())

        # 3. Match local orders against exchange orders
        for c_id, l_order in local_by_client_id.items():
            matched_exchange_order = None
            if c_id in exchange_by_client_id:
                matched_exchange_order = exchange_by_client_id[c_id]
            elif l_order.exchange_order_id and l_order.exchange_order_id in exchange_by_id:
                matched_exchange_order = exchange_by_id[l_order.exchange_order_id]

            if matched_exchange_order:
                matched_count += 1
                ex_id = str(matched_exchange_order.get("id"))
                if ex_id in unmatched_exchange:
                    unmatched_exchange.remove(ex_id)
                
                # Check for state sync
                ex_state = str(matched_exchange_order.get("state", "")).lower()
                if ex_state == "open" and l_order.status != OrderStatus.OPEN.value:
                    OrderStateMachine.transition(c_id, OrderStatus.OPEN, "Reconciliation synced to OPEN", exchange_order_id=ex_id)
                    actions.append(f"Order {c_id} confirmed OPEN on exchange.")
            else:
                # Local order thought it was open, but not found in exchange open orders!
                unmatched_local.append(c_id)
                # Check if it was filled or cancelled by querying exchange status
                if l_order.exchange_order_id:
                    try:
                        status_resp = await self.exchange.get_order_status(l_order.exchange_order_id)
                        res = status_resp.get("result", status_resp)
                        actual_state = str(res.get("state", "")).lower()
                        if actual_state in ("closed", "filled"):
                            OrderStateMachine.transition(c_id, OrderStatus.FILLED, "Reconciliation detected filled on exchange")
                            actions.append(f"Order {c_id} resolved to FILLED.")
                        elif actual_state in ("cancelled", "canceled"):
                            OrderStateMachine.transition(c_id, OrderStatus.CANCELLED, "Reconciliation detected cancelled on exchange")
                            actions.append(f"Order {c_id} resolved to CANCELLED.")
                        else:
                            OrderStateMachine.transition(c_id, OrderStatus.RECONCILIATION_REQUIRED, f"Unknown exchange state: {actual_state}")
                            actions.append(f"Order {c_id} marked RECONCILIATION_REQUIRED.")
                    except Exception as err:
                        OrderStateMachine.transition(c_id, OrderStatus.RECONCILIATION_REQUIRED, f"Status check failed: {err}")
                        actions.append(f"Order {c_id} marked RECONCILIATION_REQUIRED (lookup error).")
                else:
                    # Never reached exchange or missing ID
                    OrderStateMachine.transition(c_id, OrderStatus.RECONCILIATION_REQUIRED, "Missing exchange order ID")
                    actions.append(f"Order {c_id} marked RECONCILIATION_REQUIRED (no exchange ID).")

        # 4. Handle unmatched exchange orders (unmanaged external orders)
        for ue_id in unmatched_exchange:
            actions.append(f"Unmanaged external order detected on exchange: ID {ue_id}")

        # 5. Position reconciliation
        local_pos_record = Repository.get_position(symbol.upper())
        local_position = local_pos_record.size if local_pos_record else 0
        
        exchange_position = 0
        try:
            positions_data = await self.exchange.get_positions()
            for p in positions_data:
                p_sym = str(p.get("symbol") or p.get("product_symbol") or "").upper()
                if p_sym == symbol.upper():
                    exchange_position = int(float(p.get("size") or 0))
                    break
        except Exception as exc:
            actions.append(f"Could not fetch exchange positions: {exc}")

        discrepancy = exchange_position - local_position
        if discrepancy != 0:
            actions.append(
                f"Position discrepancy detected: Local={local_position}, Exchange={exchange_position}. "
                "Updating local position to match exchange source of truth."
            )
            Repository.update_position(
                symbol=symbol.upper(),
                size=exchange_position,
                entry_price=local_pos_record.entry_price if local_pos_record else 0.0,
                mark_price=local_pos_record.mark_price if local_pos_record else 0.0,
                realized_pnl=local_pos_record.realized_pnl if local_pos_record else 0.0,
                unrealized_pnl=0.0
            )

        status = "IN_SYNC" if (len(unmatched_local) == 0 and discrepancy == 0) else "DISCREPANCIES_RESOLVED"

        report = ReconciliationReport(
            timestamp=now_str,
            local_orders_count=len(local_open_orders),
            exchange_orders_count=len(exchange_orders),
            matched_orders_count=matched_count,
            unmatched_local_orders=unmatched_local,
            unmatched_exchange_orders=unmatched_exchange,
            local_position=local_position,
            exchange_position=exchange_position,
            position_discrepancy=discrepancy,
            actions_taken=actions,
            status=status
        )
        logger.info(f"Reconciliation completed: {status} (Matched: {matched_count}, Discrepancies: {len(actions)})")
        return report
