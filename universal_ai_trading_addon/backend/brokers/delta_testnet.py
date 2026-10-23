"""Delta Exchange India testnet connector.

Uses only the documented demo/testnet host. There is deliberately no production
base URL or production-order path in this module.
"""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

DELTA_TESTNET_BASE_URL = "https://cdn-ind.testnet.deltaex.org"

class DeltaAPIError(RuntimeError):
    pass

class DeltaTestnetClient:
    def __init__(self, api_key=None, api_secret=None, enable_orders=None, timeout=10):
        self.api_key = api_key if api_key is not None else os.getenv("DELTA_TESTNET_API_KEY", "")
        self.api_secret = api_secret if api_secret is not None else os.getenv("DELTA_TESTNET_API_SECRET", "")
        env_enabled = os.getenv("DELTA_ENABLE_TESTNET_ORDERS", "false").lower() == "true"
        self.enable_orders = env_enabled if enable_orders is None else enable_orders
        self.timeout = timeout
        self.base_url = DELTA_TESTNET_BASE_URL

    @staticmethod
    def _signature(secret: str, message: str) -> str:
        return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    def _request(self, method, path, params=None, body=None, authenticated=False):
        params = params or {}
        # Use a stable query string for both the signature and HTTP request.
        query_string = urlencode(params, doseq=True)
        query_suffix = f"?{query_string}" if query_string else ""
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body is not None else ""

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "universal-ai-trading-addon/0.1",
        }
        if authenticated:
            if not self.api_key or not self.api_secret:
                raise DeltaAPIError("Delta testnet credentials are missing. Configure them in the backend .env file.")
            timestamp = str(int(time.time()))
            signature_message = method.upper() + timestamp + path + query_suffix + payload
            headers.update({
                "api-key": self.api_key,
                "timestamp": timestamp,
                "signature": self._signature(self.api_secret, signature_message),
            })

        url = self.base_url + path
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                params=params,
                data=payload if body is not None else None,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DeltaAPIError(f"Could not reach Delta testnet: {exc}") from exc

        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:500]}
        if not response.ok or data.get("success") is False or data.get("error"):
            raise DeltaAPIError(f"Delta testnet returned HTTP {response.status_code}: {data}")
        return data

    def get_products(self, page_size=100):
        return self._request("GET", "/v2/products", params={"page_size": page_size})

    def get_ticker(self, symbol):
        return self._request("GET", "/v2/tickers", params={"symbol": symbol.upper()})

    def get_wallet_balances(self):
        return self._request("GET", "/v2/wallet/balances", authenticated=True)

    def get_positions(self):
        return self._request("GET", "/v2/positions/margined", authenticated=True)

    def place_testnet_order(self, product_id, side, size, order_type="market_order", limit_price=None):
        """Place an order on Delta DEMO/TESTNET only, after explicit local opt-in."""
        if not self.enable_orders:
            raise DeltaAPIError(
                "Testnet order placement is disabled. Set DELTA_ENABLE_TESTNET_ORDERS=true "
                "after configuring demo credentials and confirming you intend to place demo orders."
            )
        side = side.lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        if not isinstance(product_id, int) or product_id <= 0:
            raise ValueError("product_id must be a positive integer")
        if not isinstance(size, int) or size <= 0:
            raise ValueError("size must be a positive integer")
        if order_type not in {"market_order", "limit_order"}:
            raise ValueError("Only market_order and limit_order are supported by this starter.")
        body = {
            "product_id": product_id,
            "size": size,
            "side": side,
            "order_type": order_type,
        }
        if order_type == "limit_order":
            if limit_price is None or limit_price <= 0:
                raise ValueError("limit_price must be positive for limit orders")
            body["limit_price"] = str(limit_price)
        return self._request("POST", "/v2/orders", body=body, authenticated=True)
