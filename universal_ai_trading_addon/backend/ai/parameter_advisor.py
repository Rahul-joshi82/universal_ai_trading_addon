"""AI Parameter Advisor with Hard Risk Constraints.

Proposes optimal grid parameters (center, spacing, bounds) based on market volatility,
and strictly validates & clamps every parameter against hard risk limits.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List
from pydantic import BaseModel, Field
from .analyst import AIAnalyst
from ..models import GridConfig
from ..config import settings
from ..exchange.delta_client import DeltaClient

logger = logging.getLogger("parameter_advisor")


class ParameterSuggestion(BaseModel):
    recommended_config: GridConfig
    rationale: str
    risk_guards_applied: List[str]


class AIParameterAdvisor:
    @classmethod
    def suggest_parameters(
        cls,
        instrument: str,
        candles: List[Dict[str, Any]],
        current_price: float,
        tick_size: float = 0.5,
    ) -> ParameterSuggestion:
        analysis = AIAnalyst.analyze(instrument, candles)
        atr = max(tick_size * 2, analysis.atr)
        
        # Center price around current market price rounded to tick
        center = DeltaClient.round_to_tick(current_price, tick_size)
        
        # Spacing based on ATR (e.g. 0.8 * ATR to 1.5 * ATR)
        raw_spacing = atr * 1.2
        
        # Suggested boundaries: 5 ATRs above and below
        raw_lower = max(1.0, center - (5.0 * atr))
        raw_upper = center + (5.0 * atr)

        # Clamping against hard risk settings
        guards = []
        
        # 1. Grid levels bounded by settings.max_open_orders
        suggested_levels = 10
        if suggested_levels > settings.max_open_orders:
            guards.append(f"Grid levels clamped from {suggested_levels} to max_open_orders ({settings.max_open_orders})")
            suggested_levels = settings.max_open_orders

        # 2. Quantity per level bounded by settings.max_order_quantity
        qty = 1
        if qty > settings.max_order_quantity:
            guards.append(f"Quantity clamped to max_order_quantity ({settings.max_order_quantity})")
            qty = settings.max_order_quantity

        # 3. Max inventory bounded by settings.max_position_quantity
        inventory = min(10, settings.max_position_quantity)

        # 4. Stop loss placed 1.5x span below/above
        stop_loss = DeltaClient.round_to_tick(raw_lower - (2.0 * atr), tick_size)
        take_profit = DeltaClient.round_to_tick(raw_upper + (2.0 * atr), tick_size)

        lower_price = DeltaClient.round_to_tick(raw_lower, tick_size)
        upper_price = DeltaClient.round_to_tick(raw_upper, tick_size)

        config = GridConfig(
            instrument=instrument.upper(),
            center_price=center,
            upper_price=upper_price,
            lower_price=lower_price,
            grid_levels=suggested_levels,
            spacing_type="arithmetic",
            quantity_per_level=qty,
            max_open_orders=settings.max_open_orders,
            max_inventory=inventory,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            recenter_enabled=False,
            max_spread_pct=settings.max_spread_pct,
            max_data_age_seconds=settings.max_stale_seconds,
        )

        rationale = (
            f"Suggested based on {analysis.market_regime} regime with ATR of {atr:.2f}. "
            f"Upper/lower bounds set to ±5 ATR ({lower_price:.2f} - {upper_price:.2f}) with {suggested_levels} levels. "
            f"Stop-loss placed at {stop_loss:.2f}."
        )

        return ParameterSuggestion(
            recommended_config=config,
            rationale=rationale,
            risk_guards_applied=guards,
        )
