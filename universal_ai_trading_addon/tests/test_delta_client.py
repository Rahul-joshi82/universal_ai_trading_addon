"""Tests for Delta Exchange India Async REST Client."""
import hashlib
import hmac
import pytest
from backend.exchange.delta_client import DeltaClient, DeltaAPIError


def test_signature_generation_matches_specification():
    secret = "test_secret_key"
    method = "POST"
    timestamp = "1700000000"
    path = "/v2/orders"
    query_suffix = ""
    payload = '{"product_id":27,"side":"buy","size":1}'
    
    expected_msg = method + timestamp + path + query_suffix + payload
    expected_sig = hmac.new(secret.encode("utf-8"), expected_msg.encode("utf-8"), hashlib.sha256).hexdigest()
    
    computed_sig = DeltaClient.generate_signature(secret, method, timestamp, path, query_suffix, payload)
    assert computed_sig == expected_sig


def test_signature_with_query_params():
    secret = "test_secret_2"
    method = "GET"
    timestamp = "1700000500"
    path = "/v2/tickers"
    query_suffix = "?symbol=BTCUSD"
    payload = ""
    
    expected_msg = method + timestamp + path + query_suffix + payload
    expected_sig = hmac.new(secret.encode("utf-8"), expected_msg.encode("utf-8"), hashlib.sha256).hexdigest()
    
    computed_sig = DeltaClient.generate_signature(secret, method, timestamp, path, query_suffix, payload)
    assert computed_sig == expected_sig


@pytest.mark.asyncio
async def test_order_placement_blocked_when_disabled():
    client = DeltaClient(api_key="key", api_secret="secret", enable_orders=False)
    from backend.models import OrderRequest, Side, OrderType
    order = OrderRequest(instrument="BTCUSD", side=Side.BUY, quantity=1, order_type=OrderType.MARKET)
    with pytest.raises(DeltaAPIError) as exc:
        await client.place_order(order)
    assert "disabled" in str(exc.value).lower()


def test_round_to_tick_precision():
    client = DeltaClient()
    assert client.round_to_tick(61234.34, 0.5) == 61234.5
    assert client.round_to_tick(61234.12, 0.5) == 61234.0
    assert client.round_to_tick(105.123, 0.05) == 105.1
    assert client.round_to_tick(105.14, 0.05) == 105.15
