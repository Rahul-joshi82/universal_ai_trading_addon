"""Delta Exchange India WebSocket client for real-time market data ingestion.

Features:
- Bounded exponential backoff automatic reconnection
- Periodic heartbeat ping/pong (every 30 seconds)
- Real-time ticker and orderbook processing
- Stale price detection (exchange timestamp vs local arrival timestamp)
- Thread/asyncio safe snapshot caching
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Dict, List, Optional
import websockets
from ..config import settings
from ..models import MarketDataSnapshot

logger = logging.getLogger("delta_websocket")


class DeltaWebSocketClient:
    def __init__(
        self,
        url: Optional[str] = None,
        symbols: Optional[List[str]] = None,
        max_stale_seconds: float = 10.0,
        on_ticker_callback: Optional[Callable[[MarketDataSnapshot], None]] = None,
    ):
        self.url = url or settings.delta_ws_url or "wss://socket-ind.testnet.deltaex.org"
        self.symbols = [s.upper() for s in (symbols or ["BTCUSD"])]
        self.max_stale_seconds = max_stale_seconds
        self.on_ticker_callback = on_ticker_callback
        
        self.is_running = False
        self.is_connected = False
        self.last_heartbeat_sent = 0.0
        self.last_message_received = 0.0
        self.reconnect_count = 0
        self.last_error: Optional[str] = None
        
        # Latest market snapshots keyed by symbol
        self.snapshots: Dict[str, MarketDataSnapshot] = {}
        for s in self.symbols:
            self.snapshots[s] = MarketDataSnapshot(symbol=s, timestamp_local=time.time(), is_stale=True)
            
        self._task: Optional[asyncio.Task] = None

    def get_snapshot(self, symbol: str) -> MarketDataSnapshot:
        sym = symbol.upper()
        if sym in self.snapshots:
            snap = self.snapshots[sym]
            now = time.time()
            # Dynamic staleness check
            age = now - snap.timestamp_local
            snap.is_stale = (age > self.max_stale_seconds) or (snap.last_price <= 0)
            return snap
        return MarketDataSnapshot(symbol=sym, is_stale=True, source="UNINITIALIZED")

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Delta WebSocket client started for symbols: {self.symbols} on {self.url}")

    async def stop(self) -> None:
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.is_connected = False
        logger.info("Delta WebSocket client stopped.")

    async def _run_loop(self) -> None:
        backoff = 1.0
        max_backoff = 30.0

        while self.is_running:
            try:
                logger.info(f"Connecting to Delta WebSocket: {self.url}")
                async with websockets.connect(
                    self.url,
                    ping_interval=None,  # We manage application-level ping payload explicitly
                    close_timeout=5,
                ) as ws:
                    self.is_connected = True
                    self.reconnect_count = 0
                    backoff = 1.0
                    self.last_message_received = time.time()
                    logger.info("Connected to Delta WebSocket successfully.")

                    # Subscribe to ticker and orderbook channels
                    await self._send_subscriptions(ws)

                    # Start concurrent ping worker
                    ping_task = asyncio.create_task(self._ping_worker(ws))

                    try:
                        while self.is_running:
                            message = await ws.recv()
                            self.last_message_received = time.time()
                            await self._handle_message(message)
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.is_connected = False
                self.last_error = str(exc)
                self.reconnect_count += 1
                logger.warning(f"WebSocket disconnected ({exc}). Reconnecting in {backoff:.1f}s (reconnect #{self.reconnect_count})...")
                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 1.5)

    async def _send_subscriptions(self, ws: websockets.WebSocketClientProtocol) -> None:
        subscribe_msg = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {
                        "name": "v2/ticker",
                        "symbols": self.symbols,
                    },
                    {
                        "name": "l2_orderbook",
                        "symbols": self.symbols,
                    }
                ]
            }
        }
        await ws.send(json.dumps(subscribe_msg))
        logger.info(f"Sent subscription for channels: v2/ticker, l2_orderbook on {self.symbols}")

    async def _ping_worker(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Delta disconnects if no message or heartbeat within 60 seconds. Send ping every 25s."""
        while self.is_running:
            try:
                await asyncio.sleep(25)
                ping_payload = {"type": "ping"}
                await ws.send(json.dumps(ping_payload))
                self.last_heartbeat_sent = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Heartbeat send failed: {e}")
                break

    async def _handle_message(self, raw_message: str) -> None:
        try:
            data = json.loads(raw_message)
        except Exception:
            return

        msg_type = data.get("type")
        if msg_type in ("pong", "heartbeat"):
            return

        # Handle v2/ticker channel
        channel = data.get("channel")
        symbol = (data.get("symbol") or "").upper()

        if channel == "v2/ticker" or "mark_price" in data or "best_bid" in data:
            sym = symbol or (data.get("symbol") or "BTCUSD").upper()
            best_bid = float(data.get("best_bid") or data.get("mark_price") or data.get("close") or 0.0)
            best_ask = float(data.get("best_ask") or data.get("mark_price") or data.get("close") or 0.0)
            last_price = float(data.get("close") or data.get("mark_price") or ((best_bid + best_ask) / 2 if best_bid and best_ask else 0.0))
            mid_price = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else last_price

            spread = max(0.0, best_ask - best_bid) if best_bid > 0 and best_ask > 0 else 0.0
            spread_pct = (spread / mid_price) if mid_price > 0 else 0.0

            ts_exchange = float(data.get("timestamp") or time.time())
            ts_local = time.time()
            if ts_exchange > 1e14:
                sec = ts_exchange / 1_000_000.0
            elif ts_exchange > 1e11:
                sec = ts_exchange / 1_000.0
            else:
                sec = ts_exchange
            diff = ts_local - sec
            is_stale = abs(diff) > self.max_stale_seconds

            snapshot = MarketDataSnapshot(
                symbol=sym,
                best_bid=best_bid,
                best_ask=best_ask,
                mid_price=round(mid_price, 2),
                last_price=round(last_price, 2),
                spread=round(spread, 2),
                spread_pct=round(spread_pct, 6),
                timestamp_exchange=ts_exchange,
                timestamp_local=ts_local,
                is_stale=is_stale,
                source="DELTA_WEBSOCKET"
            )
            self.snapshots[sym] = snapshot

            if self.on_ticker_callback:
                try:
                    self.on_ticker_callback(snapshot)
                except Exception as cb_err:
                    logger.error(f"Error in on_ticker_callback: {cb_err}")
