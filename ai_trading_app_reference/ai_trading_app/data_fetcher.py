
"""Yahoo Finance market data and Google News RSS ingestion.

Important: yfinance is not an exchange-grade guaranteed real-time feed.
Intraday availability, delay, history windows and symbols vary by market.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

VALID_INTERVALS = {
    "1m", "2m", "5m", "15m", "30m", "60m", "90m",
    "1h", "1d", "5d", "1wk", "1mo", "3mo",
}


class DataFetchError(RuntimeError):
    """Market data could not be retrieved or validated."""


def fetch_prices(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Fetch OHLCV data and calculate SMA/RSI indicators."""
    if interval not in VALID_INTERVALS:
        raise ValueError(
            f"Unsupported interval {interval!r}. Choose from: "
            + ", ".join(sorted(VALID_INTERVALS))
        )

    try:
        frame = yf.Ticker(symbol).history(
            period=period,
            interval=interval,
            auto_adjust=True,
            actions=False,
            timeout=20,
        )
        if frame is None or frame.empty:
            raise DataFetchError(
                f"No price data for {symbol}. Check ticker, interval, "
                "period limits, market hours and internet connection."
            )

        required = {"Open", "High", "Low", "Close", "Volume"}
        missing = required.difference(frame.columns)
        if missing:
            raise DataFetchError(f"Missing OHLCV columns: {sorted(missing)}")

        frame = frame.dropna(subset=["Close"]).copy()
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        if len(frame) < 20:
            raise DataFetchError(
                f"Only {len(frame)} bars returned; need at least 20. "
                "Try a longer --period or a larger interval."
            )

        close = pd.to_numeric(frame["Close"], errors="coerce")
        frame["SMA_20"] = close.rolling(20, min_periods=20).mean()
        frame["SMA_50"] = close.rolling(50, min_periods=50).mean()

        delta = close.diff()
        gains = delta.clip(lower=0)
        losses = -delta.clip(upper=0)
        avg_gain = gains.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss = losses.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        frame["RSI_14"] = 100 - (100 / (1 + rs))
        frame.loc[(avg_loss == 0) & (avg_gain > 0), "RSI_14"] = 100.0
        frame.loc[(avg_gain == 0) & (avg_loss > 0), "RSI_14"] = 0.0
        frame["Return_1D"] = close.pct_change()

        latest = frame.iloc[-1]
        if not np.isfinite(float(latest["Close"])) or float(latest["Close"]) <= 0:
            raise DataFetchError("Latest close is not a valid positive price")
        return frame

    except DataFetchError:
        raise
    except Exception as exc:
        logger.exception("Price fetch failed for %s", symbol)
        raise DataFetchError(f"Unable to fetch price data for {symbol}") from exc


def fetch_news(symbol: str, limit: int = 10) -> list[dict]:
    """Fetch symbol-related headlines from Google News RSS."""
    if limit < 1:
        raise ValueError("limit must be at least 1")

    query = quote_plus(f'"{symbol}" stock')
    url = (
        "https://news.google.com/rss/search"
        f"?q={query}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        feed = feedparser.parse(url)
        if getattr(feed, "bozo", False) and not feed.entries:
            logger.warning("RSS feed could not be parsed for %s", symbol)
            return []

        results, seen = [], set()
        for entry in feed.entries:
            title = str(entry.get("title", "")).strip()
            if not title or title in seen:
                continue
            seen.add(title)
            source = entry.get("source", {})
            results.append({
                "title": title[:500],
                "published": str(entry.get("published", ""))[:100],
                "source": str(source.get("title", "Unknown"))[:100]
                if isinstance(source, dict) else "Unknown",
            })
            if len(results) >= limit:
                break
        return results
    except Exception:
        logger.exception("News fetch failed for %s", symbol)
        return []


def _number(value):
    try:
        number = float(value)
        return number if np.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def build_snapshot(symbol: str, frame: pd.DataFrame, news: list[dict]) -> dict:
    latest = frame.iloc[-1]
    index_value = frame.index[-1]
    if hasattr(index_value, "isoformat"):
        bar_timestamp = index_value.isoformat()
    else:
        bar_timestamp = str(index_value)

    return {
        "symbol": symbol,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "latest_bar_timestamp": bar_timestamp,
        "price": {
            "open": _number(latest["Open"]),
            "high": _number(latest["High"]),
            "low": _number(latest["Low"]),
            "close": _number(latest["Close"]),
            "volume": _number(latest["Volume"]),
            "sma_20": _number(latest["SMA_20"]),
            "sma_50": _number(latest["SMA_50"]),
            "rsi_14": _number(latest["RSI_14"]),
            "return_1d": _number(latest["Return_1D"]),
        },
        "recent_bars": [
            {
                "timestamp": str(index),
                "close": _number(row["Close"]),
                "volume": _number(row["Volume"]),
            }
            for index, row in frame.tail(5).iterrows()
        ],
        "news": news,
    }
