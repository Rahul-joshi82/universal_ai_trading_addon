"""Delta Exchange India REST Client (Asynchronous).

Provides robust authenticated interaction with official Delta Exchange India endpoints.
Defaults to DEMO/TESTNET host only. Never logs API secrets or authentication signatures.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from ..config import settings
from .base import ExchangeAdapter
from ..models import OrderRequest, MarketDataSnapshot, Side, OrderType

logger = logging.getLogger("delta_client")

DELTA_TESTNET_BASE_URL = "https://cdn-ind.testnet.deltaex.org"
DELTA_PROD_BASE_URL = "https://api.india.delta.exchange"


class DeltaAPIError(RuntimeError):
    """Raised when Delta Exchange returns an error or cannot be reached."""
    pass


class DeltaClient(ExchangeAdapter):
    last_detected_client_ip: Optional[str] = "152.59.88.236"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        enable_orders: Optional[bool] = None,
        timeout: float = 3.0,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._server_time_offset: float = 0.0
        self.base_url = (base_url or settings.delta_rest_url or DELTA_TESTNET_BASE_URL).rstrip("/")
        
        env_enabled = settings.delta_enable_testnet_orders
        self.enable_orders = env_enabled if enable_orders is None else enable_orders
        self.timeout = timeout
        
        # In-memory product specifications cache: symbol -> product metadata
        # Pre-seed standard Delta Exchange India contracts for instant resilience
        btc_id = 84 if settings.delta_env == "testnet" else 27
        eth_id = 85 if settings.delta_env == "testnet" else 28
        self._products_cache: Dict[str, Dict[str, Any]] = {
            "BTCUSD": {
                "id": btc_id,
                "symbol": "BTCUSD",
                "tick_size": 0.1,
                "contract_value": 0.001,
                "contract_type": "perpetual_futures",
            },
            "ETHUSD": {
                "id": eth_id,
                "symbol": "ETHUSD",
                "tick_size": 0.01,
                "contract_value": 0.01,
                "contract_type": "perpetual_futures",
            }
        }
        self._products_by_id: Dict[int, Dict[str, Any]] = {
            btc_id: self._products_cache["BTCUSD"],
            eth_id: self._products_cache["ETHUSD"],
        }
        
        # High-performance persistent HTTP connection pool (Keep-Alive)
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def api_key(self) -> str:
        return self._api_key or os.getenv("DELTA_TESTNET_API_KEY", "") or settings.delta_testnet_api_key

    @property
    def api_secret(self) -> str:
        return self._api_secret or os.getenv("DELTA_TESTNET_API_SECRET", "") or settings.delta_testnet_api_secret

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or initialize persistent HTTP keep-alive connection pool for sub-50ms execution."""
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(max_keepalive_connections=25, max_connections=50, keepalive_expiry=60.0)
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=limits,
                headers={"Accept": "application/json", "User-Agent": "UniversalAITradingAddon/1.0"}
            )
        return self._client

    async def close(self) -> None:
        """Close persistent connection pool."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def generate_signature(secret: str, method: str, timestamp: str, path: str, query_suffix: str = "", payload: str = "") -> str:
        """Calculate HMAC-SHA256 signature for Delta Exchange."""
        signature_message = method.upper() + timestamp + path + query_suffix + payload
        return hmac.new(secret.encode("utf-8"), signature_message.encode("utf-8"), hashlib.sha256).hexdigest()

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        authenticated: bool = False,
        retries: int = 3,
    ) -> Dict[str, Any]:
        params = params or {}
        # Clean None values from params
        clean_params = {k: v for k, v in params.items() if v is not None}
        query_string = urlencode(clean_params, doseq=True)
        query_suffix = f"?{query_string}" if query_string else ""
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body is not None else ""

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "UniversalAITradingAddon/1.0",
        }

        url = f"{self.base_url}{path}"
        last_error = None

        for attempt in range(1, retries + 1):
            if authenticated:
                if not self.api_key or not self.api_secret:
                    raise DeltaAPIError("Delta Exchange API credentials are missing. Configure them in .env.")
                timestamp = str(int(time.time() + self._server_time_offset))
                signature = self.generate_signature(self.api_secret, method, timestamp, path, query_suffix, payload)
                headers.update({
                    "api-key": self.api_key,
                    "timestamp": timestamp,
                    "signature": signature,
                })

            try:
                client = await self._get_client()
                response = await client.request(
                    method=method.upper(),
                    url=url,
                    params=clean_params,
                    content=payload if body is not None else None,
                    headers=headers,
                )

                # Keep server time offset dynamically synchronized
                date_hdr = response.headers.get("Date") or response.headers.get("date")
                if date_hdr:
                    try:
                        import email.utils
                        server_epoch = email.utils.parsedate_to_datetime(date_hdr).timestamp()
                        self._server_time_offset = server_epoch - time.time()
                    except Exception:
                        pass

                if response.status_code == 429:
                    backoff = 0.5 * (2 ** (attempt - 1))
                    logger.warning(f"Delta rate-limit (429) hit. Backing off for {backoff:.2f}s (attempt {attempt}/{retries})")
                    time.sleep(backoff)
                    continue

                try:
                    data = response.json()
                except ValueError:
                    data = {"raw": response.text[:500]}

                if not response.is_success or data.get("success") is False or data.get("error"):
                    # Check for signature expiry due to clock skew
                    if "expired_signature" in str(data):
                        s_time = data.get("error", {}).get("context", {}).get("server_time")
                        if s_time:
                            self._server_time_offset = float(s_time) - time.time()
                            logger.info(f"Synchronized clock with Delta server (offset: {self._server_time_offset:.2f}s). Retrying...")
                            continue

                    # Check for IP whitelist rejection
                    if "ip_not_whitelisted_for_api_key" in str(data):
                        client_ip = data.get("error", {}).get("context", {}).get("client_ip", "your current IP")
                        DeltaClient.last_detected_client_ip = client_ip
                        self.last_detected_client_ip = client_ip
                        raise DeltaAPIError(
                            f"Delta IP Whitelist Rejection: Your current IP address '{client_ip}' is not in your API key's whitelist on Delta Exchange. "
                            f"Please log in to Delta Exchange -> Settings -> API Keys and add '{client_ip}'."
                        )

                    error_msg = data.get("error", {}).get("message") or data.get("message") or str(data)
                    raise DeltaAPIError(f"Delta API HTTP {response.status_code}: {error_msg}")

                return data

            except (httpx.RequestError, DeltaAPIError) as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(0.3 * attempt)
                    continue
                break

        raise DeltaAPIError(f"Delta request failed after {retries} attempts: {last_error}")

    async def get_products(self, page_size: int = 1000) -> List[Dict[str, Any]]:
        """Fetch all tradable products and populate cache."""
        try:
            data = await self._request("GET", "/v2/products")
            products = data.get("result", [])
            if isinstance(products, list):
                for p in products:
                    sym = p.get("symbol")
                    pid = p.get("id")
                    if sym:
                        self._products_cache[sym.upper()] = p
                    if pid:
                        self._products_by_id[pid] = p
            return products
        except Exception as exc:
            logger.warning(f"Failed to fetch products list: {exc}")
            return []

    async def get_product_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        sym = symbol.upper().strip()
        if sym in self._products_cache:
            return self._products_cache[sym]

        # 1. Try direct single product endpoint /v2/products/{symbol}
        try:
            data = await self._request("GET", f"/v2/products/{sym}")
            prod = data.get("result")
            if prod and isinstance(prod, dict) and prod.get("symbol", "").upper() == sym:
                self._products_cache[sym] = prod
                if "id" in prod:
                    self._products_by_id[prod["id"]] = prod
                return prod
        except Exception:
            pass

        # 2. Fallback to full products listing
        await self.get_products()
        return self._products_cache.get(sym)

    async def get_ticker(self, symbol: str) -> MarketDataSnapshot:
        """Fetch single ticker and return normalized snapshot."""
        sym_clean = symbol.upper().strip()
        res = {}
        try:
            data = await self._request("GET", f"/v2/tickers/{sym_clean}", retries=1)
            res = data.get("result") or {}
        except Exception:
            try:
                # Fast fallback to India live market quotes if testnet endpoint is degraded
                async with httpx.AsyncClient(timeout=3.0) as fb_client:
                    fb_resp = await fb_client.get(f"https://api.india.delta.exchange/v2/tickers/{sym_clean}")
                    if fb_resp.is_success:
                        res = fb_resp.json().get("result") or {}
            except Exception:
                pass

        if not isinstance(res, dict) or not res:
            res = {}
            try:
                # Fast fallback to India live market quotes if testnet endpoint is degraded
                async with httpx.AsyncClient(timeout=3.0) as fb_client:
                    fb_resp = await fb_client.get(f"https://api.india.delta.exchange/v2/tickers/{sym_clean}")
                    if fb_resp.is_success:
                        res = fb_resp.json().get("result") or {}
            except Exception:
                pass

        best_bid = float(res.get("best_bid") or res.get("mark_price") or res.get("close") or 0.0)
        best_ask = float(res.get("best_ask") or res.get("mark_price") or res.get("close") or 0.0)
        last_price = float(res.get("close") or res.get("mark_price") or ((best_bid + best_ask) / 2 if best_bid and best_ask else 0.0))
        mid_price = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else last_price

        spread = max(0.0, best_ask - best_bid) if best_bid > 0 and best_ask > 0 else 0.0
        spread_pct = (spread / mid_price) if mid_price > 0 else 0.0

        ts_exchange = float(res.get("timestamp") or time.time())
        ts_local = time.time()
        if ts_exchange > 1e14:
            sec = ts_exchange / 1_000_000.0
        elif ts_exchange > 1e11:
            sec = ts_exchange / 1_000.0
        else:
            sec = ts_exchange
        is_stale = abs(ts_local - sec) > settings.max_stale_seconds

        return MarketDataSnapshot(
            symbol=sym_clean,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=round(mid_price, 2),
            last_price=round(last_price, 2),
            spread=round(spread, 2),
            spread_pct=round(spread_pct, 6),
            timestamp_exchange=ts_exchange,
            timestamp_local=ts_local,
            is_stale=is_stale,
            source="DELTA_REST"
        )

    async def get_orderbook(self, symbol: str, depth: int = 10) -> Dict[str, Any]:
        """Fetch L2 orderbook."""
        prod = await self.get_product_by_symbol(symbol)
        params = {"symbol": symbol.upper()}
        if prod and "id" in prod:
            params["product_id"] = prod["id"]
        return await self._request("GET", "/v2/l2orderbook", params=params)

    async def get_account(self) -> Dict[str, Any]:
        """Fetch wallet balances."""
        return await self._request("GET", "/v2/wallet/balances", authenticated=True)

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch margined positions."""
        data = await self._request("GET", "/v2/positions/margined", authenticated=True)
        return data.get("result", [])

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch open orders."""
        params: Dict[str, Any] = {"state": "open"}
        if symbol:
            prod = await self.get_product_by_symbol(symbol)
            if prod:
                params["product_id"] = prod.get("id")
        data = await self._request("GET", "/v2/orders", params=params, authenticated=True)
        return data.get("result", [])

    async def place_order(self, order: OrderRequest) -> Dict[str, Any]:
        """Submit a new order with explicit validation."""
        if not self.enable_orders:
            raise DeltaAPIError(
                "Delta order placement is disabled. Set DELTA_ENABLE_TESTNET_ORDERS=true "
                "in .env and verify you are in the DEMO/TESTNET environment."
            )

        prod = await self.get_product_by_symbol(order.instrument)
        if not prod:
            raise DeltaAPIError(f"Product metadata for symbol '{order.instrument}' could not be resolved on Delta.")

        product_id = prod["id"]
        tick_size = float(prod.get("tick_size") or 0.5)

        side_str = "buy" if order.side == Side.BUY else "sell"
        order_type_str = "limit_order" if order.order_type == OrderType.LIMIT else "market_order"

        body: Dict[str, Any] = {
            "product_id": product_id,
            "size": order.quantity,
            "side": side_str,
            "order_type": order_type_str,
        }

        if order.client_order_id:
            body["client_order_id"] = order.client_order_id

        if order_type_str == "limit_order":
            if order.limit_price is None or order.limit_price <= 0:
                raise ValueError("Limit orders require a positive limit_price.")
            
            # Decimal-safe tick-size rounding
            rounded_price = self.round_to_tick(order.limit_price, tick_size)
            body["limit_price"] = str(rounded_price)

        res = await self._request("POST", "/v2/orders", body=body, authenticated=True)
        print(
            f"\n{'='*75}\n"
            f">>> [DELTA TESTNET LIVE TRADE EXECUTED]\n"
            f"   Action:     {order.side.value} {order.quantity} contract(s) {order.instrument.upper()}\n"
            f"   Type:       {order_type_str.upper()}\n"
            f"   Price:      {body.get('limit_price') or 'MARKET'}\n"
            f"   Client ID:  {order.client_order_id or 'Auto'}\n"
            f"   Exchange:   Delta Exchange India Testnet (Live Order Submitted)\n"
            f"{'='*75}\n",
            flush=True
        )
        return res

    async def cancel_order(self, order_id: str, product_id: Optional[Any] = None) -> Dict[str, Any]:
        """Cancel an individual open order."""
        body: Dict[str, Any] = {"id": order_id}
        if product_id is not None:
            if isinstance(product_id, int):
                body["product_id"] = product_id
            elif isinstance(product_id, str):
                if product_id.isdigit():
                    body["product_id"] = int(product_id)
                else:
                    prod = await self.get_product_by_symbol(product_id)
                    if prod and "id" in prod:
                        body["product_id"] = prod["id"]
        return await self._request("DELETE", "/v2/orders", body=body, authenticated=True)

    async def cancel_all_orders(self, product_id: Optional[int] = None) -> Dict[str, Any]:
        """Cancel all open orders, optionally filtered by product."""
        body: Dict[str, Any] = {}
        if product_id:
            body["product_id"] = product_id
        return await self._request("DELETE", "/v2/orders/all", body=body, authenticated=True)

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Fetch status of a specific order."""
        return await self._request("GET", f"/v2/orders/{order_id}", authenticated=True)

    @staticmethod
    def round_to_tick(price: float, tick_size: float) -> float:
        """Round price to the nearest allowable tick size safely."""
        if tick_size <= 0:
            return round(price, 2)
        dec_price = Decimal(str(price))
        dec_tick = Decimal(str(tick_size))
        num_ticks = (dec_price / dec_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        rounded = float(num_ticks * dec_tick)
        return rounded
