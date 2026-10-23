# Universal AI Trading Add-on for Delta Exchange India

An institutional-grade, broker-independent algorithmic trading system featuring a **deterministic BTC Grid Trading Engine**, **asynchronous Delta Exchange India REST & WebSocket connectors**, a **12-state persistent order lifecycle machine**, a **central quantitative risk engine**, and a **Chrome/Edge Manifest V3 monitoring dashboard**.

---

## 1. Key System Architecture

```
                                +-----------------------------+
                                |  Chrome/Edge Extension UI   |
                                |  (Manifest V3 Dashboard)    |
                                +--------------+--------------+
                                               | REST / Polling
                                               v
+-----------------------------------------------------------------------------------------+
|                                    FastAPI Backend                                      |
|                                                                                         |
|   +--------------------+     +---------------------+     +--------------------------+   |
|   |  Market Ingestion  |     |  Quantitative Risk  |     |   Order State Machine    |   |
|   |  (WS + REST Poll)  | --> |    Pre-Trade Gate   | --> |  (12-State Idempotent)   |   |
|   +---------+----------+     +----------+----------+     +------------+-------------+   |
|             |                           |                             |                 |
|             v                           v                             v                 |
|   +--------------------+     +---------------------+     +--------------------------+   |
|   | BTC Grid Strategy  |     | AI Advisory & Test  |     |   Database Persistence   |   |
|   | (Arithmetic/Geom)  |     |  (Untrusted Input)  |     |   (SQLite + SQLAlchemy)  |   |
|   +---------+----------+     +---------------------+     +--------------------------+   |
|             |                                                         |                 |
|             +---------------------------+-----------------------------+                 |
|                                         |                                               |
|                                         v                                               |
|                      +--------------------------------------+                           |
|                      |        Exchange Adapter Layer        |                           |
|                      |   [Paper Broker] | [Delta Testnet]   |                           |
|                      +------------------+-------------------+                           |
+-----------------------------------------|-----------------------------------------------+
                                          v
                      Official Delta Exchange India API
                      • REST: https://cdn-ind.testnet.deltaex.org
                      • WS:   wss://socket-ind.testnet.deltaex.org
```

---

## 2. What Is Implemented

1. **Delta Exchange India Integration**:
   - Official endpoints verified: REST (`https://cdn-ind.testnet.deltaex.org`), WebSocket (`wss://socket-ind.testnet.deltaex.org`).
   - HMAC-SHA256 signature generation over `METHOD + TIMESTAMP + PATH + QUERY_SUFFIX + PAYLOAD`.
   - Real-time WebSocket channels: `v2/ticker` and `l2_orderbook`.
   - 25s ping/pong heartbeats and exponential backoff automatic reconnection.
   - Stale-price detection comparing exchange timestamp with local clock.
   - Decimal-safe tick-size (`round_to_tick`) and lot-size rounding.
   - Read wallet balances, positions, open orders, and submit/cancel orders.
2. **BTC Grid Trading Strategy Engine**:
   - Arithmetic and geometric grid spacing.
   - Independent level state tracking: `VACANT`, `ENTRY_OPEN`, `ENTRY_FILLED`, `EXIT_OPEN`.
   - Automatic paired counter-order submission upon entry fills.
   - Strict duplicate-order and re-entry prevention.
   - Dynamic recentering logic and hard stop-loss / take-profit controls.
3. **12-State Persistent Order Lifecycle**:
   - `PROPOSED` -> `RISK_APPROVED` -> `SUBMITTED` -> `OPEN` -> `PARTIALLY_FILLED` -> `FILLED` -> `EXIT_PENDING` -> `CLOSED`.
   - Cancellations: `OPEN` -> `CANCELLATION_REQUESTED` -> `CANCELLED`.
   - Rejections: `RISK_REJECTED` and `REJECTED`.
   - Mismatches: `RECONCILIATION_REQUIRED`.
   - Fully audited in `OrderDB.lifecycle_history` and persisted in SQLite.
4. **Order Reconciliation & Restart Recovery**:
   - Startup reconciliation syncs local database state against exchange state of truth.
   - Identifies filled, cancelled, or orphan orders without duplicate submissions.
   - Positions automatically aligned to exchange reports.
5. **Central Quantitative Risk Management**:
   - Pre-trade evaluation for max order size, position limit, and notional exposure.
   - Persistent daily loss tracking across server restarts.
   - Bid-ask spread gate (`max_spread_pct`) and stale quote gate (`max_stale_seconds`).
   - **Rule 8 Exemption**: Risk-reducing exit orders are never blocked by entry limits.
   - Emergency kill switch and consecutive-error circuit breaker.
6. **Browser Extension Dashboard**:
   - Manifest V3 popup with live market strip, visual grid ladder, positions & PnL, strategy configuration form, risk utilization gauges, and AI backtest viewer.
   - Confirmation modal required for destructive actions (`Flatten` and `Kill Switch`).

---

## 3. Quickstart (Windows PowerShell)

### Step 1: Open Terminal & Navigate to Project
```powershell
cd C:\Users\RAHUL\.gemini\antigravity\scratch\universal_ai_trading_addon
```

### Step 2: Configure Environment
```powershell
Copy-Item .env.example .env
```
*(By default, `PAPER_TRADING=true` is enabled for 100% risk-free local simulation).*

### Step 3: Run the Automated Test Suite
```powershell
$env:PYTHONPATH = "."
python -m pytest tests -v
```

### Step 4: Launch the Backend
```powershell
$env:PYTHONPATH = "."
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```
API Documentation will be live at: **http://127.0.0.1:8000/docs**

---

## 4. Quickstart (Linux / macOS)

```bash
cd universal_ai_trading_addon
cp .env.example .env
export PYTHONPATH="."
pytest -v
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

---

## 5. Loading the Browser Extension (Chrome / Edge)

1. Open **Google Chrome** or **Microsoft Edge**.
2. Navigate to `chrome://extensions` or `edge://extensions`.
3. Toggle on **Developer mode** in the top right corner.
4. Click **Load unpacked**.
5. Select the directory:
   `C:\Users\RAHUL\.gemini\antigravity\scratch\universal_ai_trading_addon\extension`
6. Click the extension icon in your browser toolbar to open the trading dashboard.

---

## 6. Trading Semantics & Controls

- **Start Grid (`POST /api/grid/start`)**:
  Activates grid strategy loop. Evaluates levels and places limit entry orders according to risk gates.
- **Pause (`POST /api/grid/pause`)**:
  Stops new entry orders immediately. Active positions and resting exit orders are safely maintained.
- **Resume (`POST /api/grid/resume`)**:
  Re-enables entry order evaluation after verifying risk limits and kill-switch status.
- **Cancel Entries (`POST /api/grid/cancel-entries`)**:
  Cancels all open entry limit orders on the book. Does NOT close or flatten open positions.
- **Flatten (`POST /api/grid/flatten`)**:
  **Requires explicit confirmation (`{"confirm": true}`)**. Pauses strategy, cancels all pending orders, and closes open contracts via market order.
- **Kill Switch / Emergency Stop (`POST /api/grid/emergency-stop`)**:
  Blocks all entry orders, trips emergency state, cancels pending entries. Resetting requires operator confirmation via `POST /api/risk/reset-kill-switch`.

---

## 7. Delta Exchange India Setup & IP Whitelisting

To connect to Delta Exchange India DEMO/TESTNET:

1. Register at the official Delta India Demo portal ([testnet.delta.exchange](https://testnet.delta.exchange)).
2. Go to **Settings -> API Keys** and generate a new key with Trading permissions.
3. **MANDATORY - IP Whitelisting**:
   - Delta Exchange India strictly requires your public IP to be added to the API key's whitelist.
   - Open the **⚡ Delta Connect** tab in the Chrome extension to see your current detected public IP (e.g. `152.59.88.236`).
   - Click **📋 Copy IP** and paste it into your API key whitelist on Delta Exchange.
   > **Note on Indian Retail ISPs (Jio / Airtel / Broadband):** Your public IP may rotate periodically. If an order fails with `ip_not_whitelisted_for_api_key`, simply copy the updated IP from the extension's Delta Connect tab and add it to your Delta whitelist.
4. In the extension's **⚡ Delta Connect** tab, enter your API Key and Secret, and click **⚡ Verify Live Balance**.
5. Switch to **Paper Trading Mode** at any time to test strategies 100% risk-free with local simulation.

---

## 8. Complete REST API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness and mode health check |
| `GET` | `/api/status` | Unified system status, mode, account, positions, and orders |
| `GET` | `/api/env-config` | View masked credentials and active execution mode |
| `POST` | `/api/env-config` | Dynamically update API credentials and execution mode |
| `POST` | `/api/delta/verify-credentials` | Verify credentials and fetch live equity balance from Delta India |
| `GET` | `/api/delta/client-ip` | Detect user's current public IP for Delta IP Whitelisting |
| `GET` | `/api/scanner/market` | TradeHull EP 6 Multi-Pair Scanner (BTC, ETH, SOL, XRP) with RSI and signals |
| `GET` | `/api/grids` | Vercel Mark-1 Grids list with levels, leverage, and fee coverage |
| `POST` | `/api/grids` | Create and deploy preset or custom grid |
| `POST` | `/api/grids/{id}/action` | Trigger grid lifecycle action (start, pause, resume, cancel, flatten) |
| `GET` | `/api/account/risk` | Get global risk parameters, daily loss limit, and circuit breaker status |
| `POST` | `/api/account/risk` | Update global risk parameters and limits |
| `POST` | `/api/account/risk/reset-daily-loss` | Reset daily loss tracker and clear system circuit breakers |
| `POST` | `/api/grid/configure` | Configure center price, bounds, levels, spacing, and stops |
| `GET` | `/api/grid/config` | Retrieve current grid configuration |
| `GET` | `/api/grid/levels` | List all grid levels with statuses and paired exit orders |
| `POST` | `/api/grid/start` | Start automated grid strategy engine (executes seed buy and pairs exit) |
| `POST` | `/api/grid/pause` | Pause strategy (disables new entry placements) |
| `POST` | `/api/grid/resume` | Resume strategy |
| `POST` | `/api/grid/stop` | Stop strategy and deactivate |
| `POST` | `/api/grid/cancel-entries`| Cancel open entry orders without closing positions |
| `POST` | `/api/grid/flatten` | Close positions and cancel orders (requires `{"confirm": true}`) |
| `POST` | `/api/grid/emergency-stop`| Immediate emergency kill switch |
| `GET` | `/api/market/ticker/{symbol}`| Live ticker, bid/ask, spread, and data staleness |
| `GET` | `/api/market/candles/{symbol}`| Candlestick data for charting |
| `GET` | `/api/orders` | Query persisted orders with full state machine statuses |
| `GET` | `/api/orders/{order_id}` | Detailed lifecycle audit trail for an order |
| `GET` | `/api/positions` | Detailed active positions and unrealized P&L |
| `GET` | `/api/trades` | Fill-by-fill trade execution history |
| `GET` | `/api/risk/status` | Current risk limits, utilization, and circuit breaker |
| `POST` | `/api/risk/reset-kill-switch`| Reset tripped circuit breaker and kill switch |
| `POST` | `/api/reconcile` | Trigger exchange state reconciliation |
| `GET` | `/api/ai/analysis/{symbol}` | AI market regime and grid suitability assessment |
| `POST` | `/api/ai/suggest-parameters`| Risk-constrained parameter suggestions |
| `POST` | `/api/backtest/run` | Run historical backtest simulator (Mark-1 format with win rate & profit factor) |

---

## 9. Production Cloud Deployment & Reliability

When deploying to a Linux cloud instance (e.g. AWS EC2, GCP Compute Engine, Ubuntu 22.04 LTS):

### Systemd Service Configuration (`/etc/systemd/system/delta-trading.service`)
```ini
[Unit]
Description=Delta India Universal AI Trading Backend
After=network.target

[Service]
User=trader
WorkingDirectory=/opt/universal_ai_trading_addon
Environment="PYTHONPATH=/opt/universal_ai_trading_addon"
ExecStart=/opt/universal_ai_trading_addon/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5s
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

### Process Supervision & Recovery
- The server automatically executes startup reconciliation (`OrderReconciler.reconcile()`) on boot to recover open orders and positions from the persistent SQLite database.
- Use an Nginx reverse proxy with SSL/TLS termination and basic/token authentication before exposing any control routes to external networks.
- Schedule SQLite database backups via cron (`sqlite3 trading_addon.db ".backup /backup/trading_$(date +%F).db"`).
