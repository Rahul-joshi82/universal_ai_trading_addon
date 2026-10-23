"""Central Quantitative Risk Management Engine.

All entry orders must pass through this engine.
Strategy code cannot bypass risk checks.
Provides pre-trade validation, daily loss tracking across restarts,
circuit breakers, stale quote gates, max spread gates, and emergency kill switches.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Dict, Any, List
from ..config import settings
from ..models import OrderRequest, Side, OrderType, MarketDataSnapshot, RiskStatus
from ..persistence.repository import Repository

logger = logging.getLogger("risk_engine")


class RiskRejected(ValueError):
    """Raised when an order fails risk evaluation."""
    pass


class RiskEngine:
    def __init__(
        self,
        max_order_quantity: Optional[int] = None,
        max_position_quantity: Optional[int] = None,
        max_daily_loss: Optional[float] = None,
        max_notional_exposure: Optional[float] = None,
        max_open_orders: Optional[int] = None,
        max_spread_pct: Optional[float] = None,
        max_stale_seconds: Optional[float] = None,
        consecutive_error_limit: Optional[int] = None,
    ):
        self.max_order_quantity = max_order_quantity or settings.max_order_quantity
        self.max_position_quantity = max_position_quantity or settings.max_position_quantity
        self.max_daily_loss = max_daily_loss or settings.max_daily_loss
        self.max_notional_exposure = max_notional_exposure or settings.max_notional_exposure
        self.max_open_orders = max_open_orders or settings.max_open_orders
        self.max_spread_pct = max_spread_pct or settings.max_spread_pct
        self.max_stale_seconds = max_stale_seconds or settings.max_stale_seconds
        self.consecutive_error_limit = consecutive_error_limit or settings.consecutive_error_limit

        # Mark-1 & TradeHull Global Risk Parameters
        self.max_daily_loss_pct: float = 5.0
        self.total_account_balance: float = 1000.0
        self.max_total_capital: float = 500.0
        self.price_feed_timeout_sec: float = self.max_stale_seconds
        self.order_rate_limit_per_min: int = 60
        self._recent_order_times: List[float] = []

        self.is_paused = False
        self.kill_switch_active = False
        self.emergency_state = False
        self.emergency_reason: Optional[str] = None
        
        self.consecutive_errors = 0
        self.circuit_breaker_tripped = False

    def reset_daily_loss(self) -> None:
        """Reset accumulated daily loss counter and restore trading gates."""
        try:
            Repository.record_fill(
                client_order_id="reset_daily_marker",
                symbol="RESET",
                side="HOLD",
                price=0.0,
                quantity=0,
                fee=0.0,
                realized_pnl=0.0,
            )
        except Exception:
            pass
        self.reset_kill_switch()
        logger.info("Daily loss counter & circuit breakers reset by user action.")

    def activate_kill_switch(self, reason: str = "Manual kill switch triggered") -> None:
        """Immediately blocks all new entry orders and locks trading."""
        self.kill_switch_active = True
        self.is_paused = True
        self.emergency_state = True
        self.emergency_reason = reason
        Repository.log_risk_event("KILL_SWITCH_ACTIVATED", "CRITICAL", reason)
        logger.critical(f"KILL SWITCH ACTIVATED: {reason}")

    def reset_kill_switch(self) -> None:
        """Operator action to reset kill switch after verifying safety."""
        self.kill_switch_active = False
        self.is_paused = False
        self.emergency_state = False
        self.emergency_reason = None
        self.consecutive_errors = 0
        self.circuit_breaker_tripped = False
        Repository.log_risk_event("KILL_SWITCH_RESET", "INFO", "Operator reset kill switch.")
        logger.info("Kill switch reset by operator.")

    def record_error(self, error_message: str) -> None:
        """Track consecutive errors and trip circuit breaker if limit exceeded."""
        err_lower = error_message.lower()
        # Non-fatal auth/IP whitelist errors should not lock down the trading system
        if "whitelist" in err_lower or "api_key" in err_lower or "401" in err_lower or "unauthorized" in err_lower:
            logger.warning(f"Exchange auth/whitelist warning (not tripping breaker): {error_message}")
            return

        self.consecutive_errors += 1
        if self.consecutive_errors >= self.consecutive_error_limit and not self.circuit_breaker_tripped:
            self.circuit_breaker_tripped = True
            self.activate_kill_switch(
                f"Circuit breaker tripped: {self.consecutive_errors} consecutive errors ({error_message})"
            )

    def record_success(self) -> None:
        """Reset consecutive error count on successful exchange communication."""
        self.consecutive_errors = 0

    def get_status(
        self,
        current_position: int = 0,
        current_price: float = 0.0,
        open_orders_count: int = 0,
        symbol: str = "BTCUSD",
        contract_value: Optional[float] = None,
    ) -> RiskStatus:
        daily_record = Repository.get_daily_pnl()
        realized = daily_record.realized_pnl - daily_record.fees_paid
        
        # Delta Exchange contract multipliers: 1 BTCUSD contract = 0.001 BTC, 1 ETHUSD contract = 0.01 ETH
        if contract_value is not None:
            multiplier = contract_value
        elif "BTC" in symbol.upper():
            multiplier = 0.001
        elif "ETH" in symbol.upper():
            multiplier = 0.01
        else:
            multiplier = 1.0

        notional = abs(current_position) * (current_price if current_price > 0 else 100.0) * multiplier
        
        breach = (
            self.kill_switch_active
            or self.circuit_breaker_tripped
            or (realized < -self.max_daily_loss)
            or (abs(current_position) > self.max_position_quantity)
            or (notional > self.max_notional_exposure)
        )
        
        reason = None
        if self.kill_switch_active:
            reason = self.emergency_reason or "Kill switch active"
        elif realized < -self.max_daily_loss:
            reason = f"Daily loss limit breached: {realized:.2f} < -{self.max_daily_loss:.2f}"
        elif abs(current_position) > self.max_position_quantity:
            reason = f"Position limit exceeded: {abs(current_position)} > {self.max_position_quantity}"
        elif notional > self.max_notional_exposure:
            reason = f"Notional exposure exceeded: {notional:.2f} > {self.max_notional_exposure:.2f}"

        return RiskStatus(
            is_paused=self.is_paused,
            kill_switch_active=self.kill_switch_active,
            daily_realized_pnl=round(realized, 2),
            daily_unrealized_pnl=round(daily_record.unrealized_pnl, 2),
            total_daily_pnl=round(realized + daily_record.unrealized_pnl, 2),
            current_position=current_position,
            current_notional=round(notional, 2),
            open_orders_count=open_orders_count,
            consecutive_errors=self.consecutive_errors,
            risk_breached=breach,
            breach_reason=reason,
        )

    def validate_order(
        self,
        order: OrderRequest,
        current_position: int,
        market_snapshot: Optional[MarketDataSnapshot] = None,
        open_orders_count: int = 0,
        contract_value: Optional[float] = None,
    ) -> None:
        """Validate order against central risk policies.

        Rule 8: Do not block an order that reduces risk solely because an entry limit was reached!
        """
        # 1. HOLD is a signal, not an executable order
        if order.side == Side.HOLD:
            raise RiskRejected("HOLD is a signal, not an executable order.")

        # 2. Check emergency kill switch
        if self.kill_switch_active:
            raise RiskRejected(f"Trading blocked: Kill switch is active ({self.emergency_reason}).")

        # 3. Check paused state (unless it is an exit/risk-reducing order)
        if self.is_paused and not order.is_exit:
            raise RiskRejected("Trading is paused; new entry orders are disabled.")

        # 4. Check order quantity per request
        if order.quantity > self.max_order_quantity:
            raise RiskRejected(
                f"Order quantity {order.quantity} exceeds max order quantity ({self.max_order_quantity})."
            )

        # 5. Position delta calculation
        signed_delta = order.quantity if order.side == Side.BUY else -order.quantity
        resulting_position = current_position + signed_delta
        is_risk_reducing = abs(resulting_position) < abs(current_position)

        # If it is an entry / non-risk-reducing order, enforce strict entry risk gates:
        if not is_risk_reducing and not order.is_exit:
            # 5a. Resulting position limit
            if abs(resulting_position) > self.max_position_quantity:
                raise RiskRejected(
                    f"Order would exceed max position quantity ({self.max_position_quantity}). "
                    f"Current: {current_position}, requested: {signed_delta}, resulting: {resulting_position}."
                )

            # 5b. Open orders count limit
            if (open_orders_count + 1) > self.max_open_orders:
                raise RiskRejected(
                    f"Open orders limit ({self.max_open_orders}) reached. Cannot place new entry order."
                )

            # 5c. Daily loss limit check
            daily_record = Repository.get_daily_pnl()
            daily_loss = daily_record.realized_pnl - daily_record.fees_paid
            if daily_loss < -self.max_daily_loss:
                raise RiskRejected(
                    f"Daily loss limit breached ({daily_loss:.2f} <= -{self.max_daily_loss:.2f}). "
                    "New entries blocked."
                )

            # 5d. Market data staleness & spread checks
            if market_snapshot is not None:
                if market_snapshot.is_stale:
                    raise RiskRejected(
                        f"Market data is stale (age > {self.max_stale_seconds}s). Order rejected for safety."
                    )
                if market_snapshot.spread_pct > self.max_spread_pct:
                    raise RiskRejected(
                        f"Market spread {market_snapshot.spread_pct * 100:.3f}% exceeds max allowed spread "
                        f"{self.max_spread_pct * 100:.2f}%. Order rejected."
                    )
                
                # 5e. Notional exposure check
                ref_price = (
                    order.limit_price
                    or (market_snapshot.mid_price if market_snapshot else None)
                    or (market_snapshot.last_price if market_snapshot else None)
                    or 100.0
                )
                if contract_value is not None:
                    multiplier = contract_value
                elif "BTC" in order.instrument.upper():
                    multiplier = 0.001
                elif "ETH" in order.instrument.upper():
                    multiplier = 0.01
                else:
                    multiplier = 1.0

                resulting_notional = abs(resulting_position) * ref_price * multiplier
                if resulting_notional > self.max_notional_exposure:
                    raise RiskRejected(
                        f"Order would exceed max notional exposure ({self.max_notional_exposure:.2f}). "
                        f"Resulting notional: {resulting_notional:.2f}."
                    )

        # 6. Order type validation
        if order.order_type == OrderType.LIMIT and order.limit_price is None:
            raise RiskRejected("Limit orders require limit_price.")


# Global default risk engine instance
default_risk_engine = RiskEngine()


def validate_order(order: OrderRequest, current_position: int) -> None:
    """Backward-compatible helper function for starter tests."""
    default_risk_engine.validate_order(order, current_position)
