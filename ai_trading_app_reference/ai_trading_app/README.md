
# AI-Assisted Trading Research App (Paper Trading Only)

A cross-platform Python CLI prototype that fetches Yahoo Finance bars and RSS headlines, computes technical indicators, requests a structured JSON analysis from the OpenAI API, and passes the result through deterministic risk checks to an **in-memory mock broker**. It does not submit live orders.

## Requirements

- Python 3.11 or newer
- Internet access
- An OpenAI API key with API access/billing configured
- A ticker supported by Yahoo Finance

## Setup: Windows

1. Extract this folder.
2. Double-click `run_windows.bat` once, or open PowerShell in this folder and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell cannot run activation scripts, use `.\.venv\Scripts\activate.bat` from Command Prompt, or run `Set-ExecutionPolicy -Scope Process Bypass` in the current PowerShell session. Activation is optional: `.venv\Scripts\python.exe -m pip install -r requirements.txt` and `.venv\Scripts\python.exe main.py` work without activating.

3. Edit `.env` and set `OPENAI_API_KEY`.
4. Run `python main.py --symbol AAPL --period 5d --interval 5m --poll-seconds 60 --duration-minutes 30`.

## Setup: macOS / Linux

```bash
chmod +x run_unix.sh
./run_unix.sh --symbol AAPL --period 5d --interval 5m --poll-seconds 60 --duration-minutes 30
```

On first run, the script creates `.venv` and `.env.example` is copied to `.env`. Edit `.env` to set the API key, then rerun.

## CLI options

```text
python main.py --help
```

- `--symbol`: ticker, e.g. `AAPL`, `MSFT`, `RELIANCE.NS`, `TCS.NS`, `BTC-USD`
- `--period`: Yahoo Finance lookback, e.g. `1d`, `5d`, `1mo`, `3mo`, `6mo`, `1y`
- `--interval`: bar interval, e.g. `1m`, `5m`, `15m`, `30m`, `1h`, `1d`
- `--poll-seconds`: how often to refresh, minimum 10 seconds
- `--duration-minutes`: how long the app runs; `0` runs until Ctrl+C
- `--news-limit`: headlines supplied to the analyst

CLI options override `.env` defaults. Examples:

```bash
python main.py --symbol MSFT --period 5d --interval 5m --poll-seconds 30 --duration-minutes 60
python main.py --symbol RELIANCE.NS --period 1mo --interval 15m --poll-seconds 60 --duration-minutes 120
python main.py --symbol BTC-USD --period 5d --interval 15m --poll-seconds 60 --duration-minutes 0
```

## Data freshness and symbol support

The app refreshes the data provider at the selected polling interval, but **polling is not the same as real-time market data**. Yahoo Finance data may be delayed, unavailable, rate-limited, or restricted by interval/history limits. Intraday history is generally limited; the exact supported period depends on interval/provider. The newest returned bar may be incomplete or delayed. Check provider terms and validate freshness before making decisions.

For guaranteed real-time data, use a market-data provider that explicitly supports your exchange and account tier. Build a provider adapter in `data_fetcher.py` rather than assuming one free API covers every stock worldwide. Indian exchanges, US equities, and crypto may require different symbols, entitlements, and feeds.

## Safety limitations

- Paper trading only; no broker API is connected.
- Positions and cash are held in memory and reset when the process restarts.
- Stop/target checks use the latest observed price, not exchange-side orders; gaps can cause worse simulated fills.
- This is not investment advice or a validated profitable strategy.
- Never put API keys in source code or commit `.env`.
- Before any live deployment, add persistent state, stale-data checks, market calendar handling, transaction costs/slippage, daily loss limits, broker reconciliation, audit trails, monitoring, and extensive tests.

## Project files

- `config.py`: environment configuration and validation
- `data_fetcher.py`: market data, indicators and RSS headlines
- `llm_analyst.py`: strict JSON-schema LLM response and validation
- `order_executor.py`: mock broker and risk controls
- `main.py`: CLI, duration/polling loop, logging and graceful shutdown
