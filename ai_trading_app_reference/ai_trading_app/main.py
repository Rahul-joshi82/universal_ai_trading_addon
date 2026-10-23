
"""CLI runner: configurable symbol, interval, polling and run duration."""
from __future__ import annotations

import argparse
import logging
import signal
import time
from datetime import datetime, timezone

from config import Settings
from data_fetcher import DataFetchError, build_snapshot, fetch_news, fetch_prices
from llm_analyst import LLMAnalyst, TradingSignal
from order_executor import MockBroker, RiskManagedExecutor


def parse_args():
    parser = argparse.ArgumentParser(
        description="LLM-assisted paper trading research app (no live orders)."
    )
    parser.add_argument("--symbol", help="Ticker, e.g. AAPL, MSFT, RELIANCE.NS, BTC-USD")
    parser.add_argument("--period", help="Yahoo Finance history period, e.g. 1d, 5d, 1mo, 3mo")
    parser.add_argument("--interval", help="Bar interval: 1m, 5m, 15m, 1h, 1d, etc.")
    parser.add_argument("--poll-seconds", type=int, help="How often to refresh data; minimum 10 seconds")
    parser.add_argument("--duration-minutes", type=int, help="Run duration; 0 means until Ctrl+C")
    parser.add_argument("--news-limit", type=int, help="Maximum headlines per analysis")
    return parser.parse_args()


def show_status(symbol, snapshot, analysis, action, broker):
    price = snapshot["price"]
    print("\n" + "=" * 72)
    print(f"AI PAPER TRADING | {symbol} | {datetime.now(timezone.utc).isoformat()}")
    print(f"Bar timestamp: {snapshot['latest_bar_timestamp']}")
    print(
        f"Close: {price['close']} | SMA20: {price['sma_20']} | "
        f"SMA50: {price['sma_50']} | RSI14: {price['rsi_14']}"
    )
    print(f"Headlines: {len(snapshot['news'])}")
    if analysis:
        print(f"Signal: {analysis.signal} | Confidence: {analysis.confidence_score:.2f}")
        print(f"News view: {analysis.sentiment_analysis}")
        print(f"Technical view: {analysis.technical_rationale}")
        print(f"Suggested stop: {analysis.recommended_stop_loss}")
    else:
        print("Signal: no new analysis this cycle")
    print(f"Action: {action}")
    print(f"Cash: {broker.cash:.2f} | Realized P&L: {broker.realized_pnl:.2f}")
    if broker.position:
        p = broker.position
        print(
            f"Position: {p.quantity} units @ {p.entry_price:.4f}; "
            f"stop={p.stop_loss:.4f}; target={p.take_profit:.4f}"
        )
    else:
        print("Position: none")
    print("=" * 72, flush=True)


def main():
    args = parse_args()
    settings = Settings.from_env()

    symbol = (args.symbol or settings.symbol).strip().upper()
    period = args.period or settings.period
    interval = args.interval or settings.interval
    poll_seconds = args.poll_seconds or settings.poll_seconds
    duration_minutes = (
        settings.duration_minutes if args.duration_minutes is None
        else args.duration_minutes
    )
    news_limit = args.news_limit or settings.news_limit

    if poll_seconds < 10:
        raise ValueError("--poll-seconds must be at least 10")
    if duration_minutes < 0:
        raise ValueError("--duration-minutes cannot be negative")
    if news_limit < 1:
        raise ValueError("--news-limit must be positive")

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(settings.log_dir / "trading_app.log", encoding="utf-8"),
        ],
    )
    logger = logging.getLogger("main")

    broker = MockBroker(settings.initial_cash)
    executor = RiskManagedExecutor(
        broker=broker,
        min_confidence=settings.min_confidence,
        max_position_fraction=settings.max_position_fraction,
        default_stop_fraction=settings.stop_loss_fraction,
        take_profit_fraction=settings.take_profit_fraction,
    )
    analyst = LLMAnalyst(settings.api_key, settings.model)

    stop_requested = False

    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True
        logger.info("Shutdown requested")

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    deadline = None if duration_minutes == 0 else time.monotonic() + duration_minutes * 60
    last_analyzed_bar = None

    logger.info(
        "Starting paper mode: symbol=%s period=%s interval=%s poll=%ss duration=%s",
        symbol, period, interval, poll_seconds,
        "until stopped" if duration_minutes == 0 else f"{duration_minutes} minutes",
    )

    while not stop_requested and (deadline is None or time.monotonic() < deadline):
        try:
            frame = fetch_prices(symbol, period, interval)
            news = fetch_news(symbol, news_limit)
            snapshot = build_snapshot(symbol, frame, news)
            price = snapshot["price"]["close"]

            # Protective checks run on every polling cycle, independently of LLM.
            exit_action = executor.check_protective_exits(price)
            analysis = None
            if exit_action:
                action = exit_action
            else:
                bar_key = snapshot["latest_bar_timestamp"]
                if bar_key != last_analyzed_bar:
                    # Prevent repeated decisions on the same candle.
                    last_analyzed_bar = bar_key
                    try:
                        analysis = analyst.analyze(snapshot)
                        action = executor.execute(analysis, price)
                    except Exception:
                        logger.exception("Analysis failed; failing closed")
                        action = "NO TRADE: analysis unavailable or invalid"
                else:
                    action = "NO TRADE: waiting for a new market bar"

            show_status(symbol, snapshot, analysis, action, broker)

        except (DataFetchError, ValueError):
            logger.exception("Data/configuration cycle failed; no new trade")
        except Exception:
            logger.exception("Unexpected cycle failure; no new trade")

        if not stop_requested and (deadline is None or time.monotonic() < deadline):
            time.sleep(poll_seconds)

    logger.info(
        "Stopped. Cash=%.2f realized_pnl=%.2f",
        broker.cash, broker.realized_pnl,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Application startup failed")
        raise
