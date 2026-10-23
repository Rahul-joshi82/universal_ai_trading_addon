"""Domain models and enumerations for the Universal AI Trading Add-on."""
from __future__ import annotations

from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PROPOSED = "PROPOSED"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REJECTED = "RISK_REJECTED"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class Signal(BaseModel):
    instrument: str
    side: Side
    confidence: float = Field(ge=0, le=1)
    reason: str
    price: float = Field(gt=0)
    timestamp: str


class OrderRequest(BaseModel):
    instrument: str = Field(min_length=1, max_length=40)
    side: Side
    quantity: int = Field(ge=1)
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = Field(default=None, gt=0)
    client_order_id: Optional[str] = None
    is_exit: bool = False
    grid_level_id: Optional[int] = None


class PaperOrder(BaseModel):
    order_id: str
    instrument: str
    side: Side
    quantity: int
    order_type: OrderType
    status: str
    fill_price: Optional[float] = None
    message: str


class GridConfig(BaseModel):
    instrument: str = Field(default="BTCUSD")
    center_price: float = Field(gt=0, description="Center reference price")
    upper_price: float = Field(gt=0, description="Upper boundary price")
    lower_price: float = Field(gt=0, description="Lower boundary price")
    grid_levels: int = Field(ge=2, le=50, default=10, description="Total grid intervals")
    spacing_type: str = Field(default="arithmetic", description="'arithmetic' or 'percentage'")
    quantity_per_level: int = Field(ge=1, default=1, description="Contracts per level")
    max_open_orders: int = Field(ge=1, default=10)
    max_inventory: int = Field(ge=1, default=10)
    stop_loss_price: Optional[float] = Field(default=None)
    take_profit_price: Optional[float] = Field(default=None)
    recenter_enabled: bool = Field(default=False)
    recenter_threshold_pct: float = Field(default=0.05)
    max_spread_pct: float = Field(default=0.005)
    max_data_age_seconds: float = Field(default=10.0)
    estimated_fee_rate: float = Field(default=0.0005)
    estimated_slippage_pct: float = Field(default=0.0002)
    total_investment_usd: Optional[float] = Field(default=None, description="Total investment capital budget in USD")
    trailing_stop_pct: Optional[float] = Field(default=None, description="Trailing stop loss percentage from peak price (e.g. 0.015 for 1.5%)")
    auto_breakeven_trigger_pct: Optional[float] = Field(default=None, description="Profit trigger to move stop loss to breakeven (e.g. 0.01 for 1%)")
    levels_above: Optional[int] = Field(default=None, description="Number of sell rungs above center price")
    levels_below: Optional[int] = Field(default=None, description="Number of buy rungs below center price")
    grid_gap: Optional[float] = Field(default=None, description="Distance between grid rungs in USD")
    cover_fees: bool = Field(default=True, description="Automatically pad grid gap to guarantee profit covers Delta Exchange maker/taker fees")
    leverage: int = Field(default=10, description="Trading leverage (e.g. 10x)")
    max_dollar_loss: Optional[float] = Field(default=None)
    stop_loss_distance: Optional[float] = Field(default=None)
    max_exposure: Optional[float] = Field(default=None)


class GridCreateRequest(BaseModel):
    name: Optional[str] = "Balanced Alpha"
    instrument: Optional[str] = "BTCUSD"
    center_price: Optional[float] = None
    levels_above: int = Field(default=5, ge=1, le=50, alias="levelsAbove")
    levels_below: int = Field(default=5, ge=1, le=50, alias="levelsBelow")
    grid_gap: float = Field(default=100.0, gt=0, alias="gridGap")
    gap_type: str = Field(default="arithmetic", alias="gapType")
    contracts_per_level: int = Field(default=1, ge=1, alias="contractsPerLevel")
    leverage: int = Field(default=10, ge=1, le=100)
    cover_fees: bool = Field(default=True, alias="coverFees")
    max_dollar_loss: Optional[float] = Field(default=None, alias="maxDollarLoss")
    stop_loss_distance: Optional[float] = Field(default=None, alias="stopLossDistance")
    max_exposure: Optional[float] = Field(default=None, alias="maxExposure")

    model_config = {"populate_by_name": True}


class EnvConfigRequest(BaseModel):
    api_key: Optional[str] = Field(default=None, alias="apiKey")
    api_secret: Optional[str] = Field(default=None, alias="apiSecret")
    mode: Optional[str] = None
    env: Optional[str] = None

    model_config = {"populate_by_name": True}


class VerifyCredentialsRequest(BaseModel):
    api_key: Optional[str] = Field(default=None, alias="apiKey")
    api_secret: Optional[str] = Field(default=None, alias="apiSecret")

    model_config = {"populate_by_name": True}


class RiskConfigRequest(BaseModel):
    max_daily_loss_pct: Optional[float] = Field(default=None, alias="maxDailyLossPct")
    total_account_balance: Optional[float] = Field(default=None, alias="totalAccountBalance")
    max_total_capital: Optional[float] = Field(default=None, alias="maxTotalCapital")
    price_feed_timeout_sec: Optional[float] = Field(default=None, alias="priceFeedTimeoutSec")
    order_rate_limit_per_min: Optional[int] = Field(default=None, alias="orderRateLimitPerMin")
    max_daily_loss: Optional[float] = Field(default=None, alias="maxDailyLoss")

    model_config = {"populate_by_name": True}


class BacktestRunRequest(BaseModel):
    symbol: str = "BTCUSD"
    timeframe_preset: Optional[str] = Field(default="7D", alias="timeframePreset")
    start_timestamp: Optional[int] = Field(default=None, alias="startTimestamp")
    end_timestamp: Optional[int] = Field(default=None, alias="endTimestamp")
    resolution: str = "5m"
    center_mode: str = Field(default="market", alias="centerMode")
    custom_center_price: Optional[float] = Field(default=None, alias="customCenterPrice")
    levels_above: int = Field(default=5, alias="levelsAbove")
    levels_below: int = Field(default=5, alias="levelsBelow")
    grid_gap: float = Field(default=100.0, alias="gridGap")
    gap_type: str = Field(default="arithmetic", alias="gapType")
    contracts_per_level: int = Field(default=1, alias="contractsPerLevel")
    leverage: int = 10
    cover_fees: bool = Field(default=True, alias="coverFees")
    max_dollar_loss: Optional[float] = Field(default=None, alias="maxDollarLoss")
    stop_loss_distance: Optional[float] = Field(default=None, alias="stopLossDistance")
    max_exposure: Optional[float] = Field(default=None, alias="maxExposure")
    initial_balance: float = Field(default=1000.0, alias="initialBalance")

    model_config = {"populate_by_name": True}


class GridLevelModel(BaseModel):
    level_id: int
    price: float
    target_side: Side
    status: str = "VACANT"  # VACANT, ENTRY_OPEN, ENTRY_FILLED, EXIT_OPEN, EXIT_FILLED
    entry_client_order_id: Optional[str] = None
    exit_client_order_id: Optional[str] = None
    paired_price: Optional[float] = None
    filled_qty: int = 0
    cycle_count: int = 0
    last_update: str = ""


class MarketDataSnapshot(BaseModel):
    symbol: str
    best_bid: float = 0.0
    best_ask: float = 0.0
    mid_price: float = 0.0
    last_price: float = 0.0
    spread: float = 0.0
    spread_pct: float = 0.0
    timestamp_exchange: float = 0.0
    timestamp_local: float = 0.0
    is_stale: bool = False
    source: str = "REST_FALLBACK"


class RiskStatus(BaseModel):
    is_paused: bool = False
    kill_switch_active: bool = False
    daily_realized_pnl: float = 0.0
    daily_unrealized_pnl: float = 0.0
    total_daily_pnl: float = 0.0
    current_position: int = 0
    current_notional: float = 0.0
    open_orders_count: int = 0
    consecutive_errors: int = 0
    risk_breached: bool = False
    breach_reason: Optional[str] = None


class ReconciliationReport(BaseModel):
    timestamp: str
    local_orders_count: int
    exchange_orders_count: int
    matched_orders_count: int
    unmatched_local_orders: List[str] = []
    unmatched_exchange_orders: List[str] = []
    local_position: int
    exchange_position: int
    position_discrepancy: int
    actions_taken: List[str] = []
    status: str = "IN_SYNC"
