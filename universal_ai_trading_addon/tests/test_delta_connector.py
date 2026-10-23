from backend.brokers.delta_testnet import DeltaTestnetClient, DELTA_TESTNET_BASE_URL, DeltaAPIError

def test_delta_host_is_testnet_only():
    assert DELTA_TESTNET_BASE_URL == "https://cdn-ind.testnet.deltaex.org"

def test_delta_orders_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DELTA_ENABLE_TESTNET_ORDERS", raising=False)
    client = DeltaTestnetClient(api_key="demo-key", api_secret="demo-secret", enable_orders=False)
    try:
        client.place_testnet_order(product_id=1, side="buy", size=1)
    except DeltaAPIError as exc:
        assert "disabled" in str(exc).lower()
    else:
        raise AssertionError("Order should be disabled by default")

def test_delta_order_validates_side():
    client = DeltaTestnetClient(api_key="demo-key", api_secret="demo-secret", enable_orders=True)
    try:
        client.place_testnet_order(product_id=1, side="hold", size=1)
    except ValueError as exc:
        assert "side" in str(exc).lower()
    else:
        raise AssertionError("Invalid side should be rejected")
