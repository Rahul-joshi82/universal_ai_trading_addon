
"""Environment-backed configuration with validation."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    return value


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str
    symbol: str
    period: str
    interval: str
    poll_seconds: int
    duration_minutes: int
    news_limit: int
    min_confidence: float
    initial_cash: float
    max_position_fraction: float
    stop_loss_fraction: float
    take_profit_fraction: float
    log_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key or api_key == "replace_me":
            raise ValueError(
                "OPENAI_API_KEY is missing. Copy .env.example to .env "
                "and add your API key, or set the environment variable."
            )

        symbol = os.getenv("SYMBOL", "AAPL").strip().upper()
        if not symbol:
            raise ValueError("SYMBOL cannot be empty")

        confidence = _float("MIN_CONFIDENCE", 0.70)
        max_fraction = _float("MAX_POSITION_FRACTION", 0.10)
        stop_fraction = _float("STOP_LOSS_FRACTION", 0.03)
        target_fraction = _float("TAKE_PROFIT_FRACTION", 0.06)
        cash = _float("INITIAL_CASH", 10000.0)

        if not 0.0 <= confidence <= 1.0:
            raise ValueError("MIN_CONFIDENCE must be between 0 and 1")
        if not 0.0 < max_fraction <= 1.0:
            raise ValueError("MAX_POSITION_FRACTION must be in (0, 1]")
        if not 0.0 < stop_fraction < 1.0:
            raise ValueError("STOP_LOSS_FRACTION must be in (0, 1)")
        if target_fraction <= 0:
            raise ValueError("TAKE_PROFIT_FRACTION must be positive")
        if cash <= 0:
            raise ValueError("INITIAL_CASH must be positive")

        return cls(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", "gpt-4o").strip(),
            symbol=symbol,
            period=os.getenv("PERIOD", "5d").strip(),
            interval=os.getenv("INTERVAL", "5m").strip(),
            poll_seconds=_positive_int("POLL_INTERVAL_SECONDS", 60, 10),
            duration_minutes=_positive_int("DURATION_MINUTES", 60, 0),
            news_limit=_positive_int("NEWS_LIMIT", 10, 1),
            min_confidence=confidence,
            initial_cash=cash,
            max_position_fraction=max_fraction,
            stop_loss_fraction=stop_fraction,
            take_profit_fraction=target_fraction,
            log_dir=Path(os.getenv("LOG_DIR", "logs")),
        )
