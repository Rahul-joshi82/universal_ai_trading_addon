"""Structured AI Market Analyst.

Guarded by safety principles:
- AI is strictly advisory and treated as untrusted input.
- Cannot place orders or bypass deterministic strategy or risk rules.
- Calculates objective indicators (SMA, RSI, ATR, Imbalance) and summarizes regimes.
"""
from __future__ import annotations

import logging
from statistics import mean, stdev
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("ai_analyst")


class MarketAnalysisReport(BaseModel):
    instrument: str
    current_price: float
    market_regime: str  # RANGING, TRENDING_BULL, TRENDING_BEAR, HIGH_VOLATILITY
    volatility_level: str  # LOW, MODERATE, HIGH
    grid_suitability_score: float = Field(ge=0.0, le=1.0)
    sma_20: float
    sma_50: float
    rsi_14: float
    atr: float
    key_observations: List[str]
    risk_warnings: List[str]
    safety_disclaimer: str = (
        "AI output is strictly advisory and untrusted input. It does not place orders "
        "and is not financial advice. All executions require deterministic risk engine approval."
    )


class AIAnalyst:
    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(prices)):
            diff = prices[i] - prices[i - 1]
            if diff > 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))
        
        recent_gains = gains[-period:]
        recent_losses = losses[-period:]
        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - (100.0 / (1.0 + rs)), 2)

    @staticmethod
    def calculate_atr(candles: List[Dict[str, Any]], period: int = 14) -> float:
        if len(candles) < period:
            return 10.0
        tr_list = []
        for i in range(1, len(candles)):
            h = candles[i].get("high", candles[i].get("close", 0))
            l = candles[i].get("low", candles[i].get("close", 0))
            prev_c = candles[i - 1].get("close", 0)
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)
        return round(mean(tr_list[-period:]) if tr_list else 10.0, 2)

    @classmethod
    def analyze(cls, instrument: str, candles: List[Dict[str, Any]]) -> MarketAnalysisReport:
        closes = [float(c.get("close", 0)) for c in candles if c.get("close")]
        if not closes:
            closes = [100.0] * 50

        curr_p = closes[-1]
        sma_20 = mean(closes[-20:]) if len(closes) >= 20 else curr_p
        sma_50 = mean(closes[-50:]) if len(closes) >= 50 else curr_p
        rsi = cls.calculate_rsi(closes)
        atr = cls.calculate_atr(candles)

        # Volatility estimation
        vol_pct = (atr / curr_p) if curr_p > 0 else 0.01
        if vol_pct < 0.005:
            vol_level = "LOW"
        elif vol_pct < 0.02:
            vol_level = "MODERATE"
        else:
            vol_level = "HIGH"

        # Regime determination
        trend_delta = (sma_20 - sma_50) / sma_50 if sma_50 > 0 else 0.0
        if vol_level == "HIGH" and abs(trend_delta) > 0.03:
            regime = "HIGH_VOLATILITY"
            suitability = 0.35  # Grid strategies risk trending drawdown in high trend
        elif abs(trend_delta) < 0.01:
            regime = "RANGING"
            suitability = 0.90  # Perfect for grid trading
        elif trend_delta >= 0.01:
            regime = "TRENDING_BULL"
            suitability = 0.65
        else:
            regime = "TRENDING_BEAR"
            suitability = 0.55

        observations = [
            f"SMA(20) is {sma_20:.2f} vs SMA(50) {sma_50:.2f} (Trend delta: {trend_delta * 100:+.2f}%).",
            f"14-period RSI is {rsi:.1f}.",
            f"Estimated ATR is {atr:.2f} ({vol_pct * 100:.2f}% of price).",
        ]

        warnings = []
        if rsi > 75:
            warnings.append("RSI indicates overbought conditions; caution on new long grid exposure.")
        elif rsi < 25:
            warnings.append("RSI indicates oversold conditions; caution on new short grid exposure.")
        if vol_level == "HIGH":
            warnings.append("High volatility detected. Widen grid spacing to reduce whipsaw risk.")

        return MarketAnalysisReport(
            instrument=instrument.upper(),
            current_price=round(curr_p, 2),
            market_regime=regime,
            volatility_level=vol_level,
            grid_suitability_score=round(suitability, 2),
            sma_20=round(sma_20, 2),
            sma_50=round(sma_50, 2),
            rsi_14=rsi,
            atr=atr,
            key_observations=observations,
            risk_warnings=warnings,
        )
