from datetime import datetime, timezone
from statistics import mean
from .models import Signal, Side

def moving_average_signal(instrument: str, closes: list[float], fast: int = 5, slow: int = 15) -> Signal:
    if len(closes) < slow:
        raise ValueError(f"Need at least {slow} closing prices")
    fast_ma = mean(closes[-fast:])
    slow_ma = mean(closes[-slow:])
    delta = (fast_ma - slow_ma) / slow_ma if slow_ma else 0
    if delta > 0.003:
        side, reason = Side.BUY, f"Fast MA ({fast_ma:.2f}) is above slow MA ({slow_ma:.2f})."
    elif delta < -0.003:
        side, reason = Side.SELL, f"Fast MA ({fast_ma:.2f}) is below slow MA ({slow_ma:.2f})."
    else:
        side, reason = Side.HOLD, f"Moving averages are close (fast={fast_ma:.2f}, slow={slow_ma:.2f})."
    confidence = min(0.95, max(0.50, 0.50 + abs(delta) * 20))
    return Signal(
        instrument=instrument, side=side, confidence=round(confidence, 3),
        reason=reason, price=round(closes[-1], 2),
        timestamp=datetime.now(timezone.utc).isoformat()
    )
