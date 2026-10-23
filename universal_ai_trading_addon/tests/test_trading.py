import pytest
from fastapi.testclient import TestClient
from backend.main import app, broker
from backend.models import OrderRequest, Side, OrderType
from backend.risk import validate_order, RiskRejected

client = TestClient(app)

def test_health_is_paper_only():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["mode"] in ("PAPER", "DELTA_TESTNET")

def test_hold_is_not_orderable():
    with pytest.raises(RiskRejected):
        validate_order(OrderRequest(instrument="DEMO", side=Side.HOLD, quantity=1), 0)

def test_analysis_marks_synthetic_data():
    response = client.get("/api/analyze/DEMO")
    assert response.status_code == 200
    assert response.json()["data_source"] == "SYNTHETIC_DEMO_DATA"

def test_paper_order_fills_without_live_execution():
    response = client.post("/api/orders/paper", json={
        "instrument": "TEST", "side": "BUY", "quantity": 1, "order_type": "MARKET"
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FILLED"
    assert "no live broker order" in body["message"]
