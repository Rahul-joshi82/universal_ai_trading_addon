"""Application configuration and validation.

Maintains paper-trading by default and supports Delta Exchange India DEMO/TESTNET.
Live production order execution is guarded and disabled by default.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv(override=True)


@dataclass
class Settings:
    # Environment & mode
    app_env: str = os.getenv("APP_ENV", "development")
    paper_trading: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
    delta_env: str = os.getenv("DELTA_ENV", "testnet").lower()  # "testnet" or "production"
    
    # Delta Exchange India credentials
    delta_testnet_api_key: str = os.getenv("DELTA_TESTNET_API_KEY", "")
    delta_testnet_api_secret: str = os.getenv("DELTA_TESTNET_API_SECRET", "")
    delta_enable_testnet_orders: bool = os.getenv("DELTA_ENABLE_TESTNET_ORDERS", "false").lower() == "true"
    
    # Exchange Endpoints
    # Testnet: https://cdn-ind.testnet.deltaex.org | wss://socket-ind.testnet.deltaex.org
    # Production: https://api.india.delta.exchange | wss://socket.india.delta.exchange
    delta_rest_url: str = os.getenv(
        "DELTA_REST_URL",
        "https://cdn-ind.testnet.deltaex.org" if os.getenv("DELTA_ENV", "testnet").lower() == "testnet" else "https://api.india.delta.exchange"
    )
    delta_ws_url: str = os.getenv(
        "DELTA_WS_URL",
        "wss://socket-ind.testnet.deltaex.org" if os.getenv("DELTA_ENV", "testnet").lower() == "testnet" else "wss://socket.india.delta.exchange"
    )

    # Risk limits
    max_order_quantity: int = int(os.getenv("MAX_ORDER_QUANTITY", "5"))
    max_daily_loss: float = float(os.getenv("MAX_DAILY_LOSS", "1000"))
    max_position_quantity: int = int(os.getenv("MAX_POSITION_QUANTITY", "10"))
    max_notional_exposure: float = float(os.getenv("MAX_NOTIONAL_EXPOSURE", "1000000.0"))
    max_open_orders: int = int(os.getenv("MAX_OPEN_ORDERS", "20"))
    max_spread_pct: float = float(os.getenv("MAX_SPREAD_PCT", "0.005"))  # 0.5% max bid-ask spread
    max_stale_seconds: float = float(os.getenv("MAX_STALE_SECONDS", "10.0"))
    consecutive_error_limit: int = int(os.getenv("CONSECUTIVE_ERROR_LIMIT", "5"))

    # Database & Storage
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./trading_addon.db")

    # Optional AI Advisory
    ai_analysis_enabled: bool = os.getenv("AI_ANALYSIS_ENABLED", "true").lower() == "true"
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    # Autonomous Trading & Automation
    auto_start_on_boot: bool = os.getenv("AUTO_START_ON_BOOT", "true").lower() == "true"

    # Safety: Production order execution guard
    allow_production_trading: bool = os.getenv("ALLOW_PRODUCTION_TRADING", "false").lower() == "true"


settings = Settings()

# Enforce safety guard: Never allow live production orders unless explicitly unlocked with strict safety flags
if not settings.paper_trading:
    if settings.delta_env == "production" and not settings.allow_production_trading:
        raise RuntimeError(
            "Live production trading is locked. To use Delta Exchange India demo/testnet, "
            "keep DELTA_ENV=testnet or enable PAPER_TRADING=true."
        )
