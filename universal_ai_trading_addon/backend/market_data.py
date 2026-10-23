from datetime import datetime, timezone
from random import Random
from typing import List

def demo_candles(instrument: str = "DEMO", count: int = 80) -> List[dict]:
    """Deterministic synthetic candles for UI/testing only; not real market data."""
    rng = Random(sum(ord(c) for c in instrument))
    price = 100.0 + (sum(ord(c) for c in instrument) % 100)
    candles = []
    for i in range(count):
        drift = 0.08 if (i // 12) % 2 == 0 else -0.04
        price = max(1.0, price + drift + rng.uniform(-1.2, 1.2))
        candles.append({
            "close": round(price, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return candles
