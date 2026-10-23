"""Integration tests for FastAPI REST API endpoints."""
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.persistence.database import init_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_api_status_comprehensive():
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "mode" in data
    assert "account" in data
    assert "positions" in data
    assert "orders_count" in data


def test_grid_configure_and_levels():
    cfg_payload = {
        "instrument": "BTCUSD",
        "center_price": 60000.0,
        "upper_price": 64000.0,
        "lower_price": 56000.0,
        "grid_levels": 8,
        "quantity_per_level": 1,
        "max_open_orders": 8,
    }
    res = client.post("/api/grid/configure", json=cfg_payload)
    assert res.status_code == 200
    assert res.json()["status"] == "SUCCESS"

    levels_res = client.get("/api/grid/levels")
    assert levels_res.status_code == 200
    levels = levels_res.json()
    assert len(levels) == 9  # 8 intervals = 9 price points


def test_grid_start_pause_resume_stop():
    res = client.post("/api/grid/start")
    assert res.status_code == 200
    assert res.json()["status"] == "STARTED"

    res = client.post("/api/grid/pause")
    assert res.status_code == 200
    assert res.json()["status"] == "PAUSED"

    res = client.post("/api/grid/resume")
    assert res.status_code == 200
    assert res.json()["status"] == "RESUMED"

    res = client.post("/api/grid/stop")
    assert res.status_code == 200
    assert res.json()["status"] == "STOPPED"


def test_grid_cancel_entries():
    res = client.post("/api/grid/cancel-entries")
    assert res.status_code == 200
    assert "cancelled_orders" in res.json()


def test_grid_flatten_requires_confirmation():
    # Without confirm: true, should reject with 400
    res = client.post("/api/grid/flatten", json={"confirm": False})
    assert res.status_code == 400
    assert "Explicit confirmation required" in res.json()["detail"]

    # With confirm: true
    res = client.post("/api/grid/flatten", json={"confirm": True})
    assert res.status_code == 200
    assert res.json()["flattened"] is True


def test_emergency_stop_kill_switch():
    res = client.post("/api/grid/emergency-stop")
    assert res.status_code == 200
    assert res.json()["kill_switch_active"] is True

    # Reset kill switch
    res = client.post("/api/risk/reset-kill-switch")
    assert res.status_code == 200
    assert res.json()["status"] == "SUCCESS"


def test_market_ticker_and_candles():
    res = client.get("/api/market/ticker/BTCUSD")
    assert res.status_code == 200
    assert res.json()["symbol"] == "BTCUSD"

    res_candles = client.get("/api/market/candles/BTCUSD?count=20")
    assert res_candles.status_code == 200
    assert len(res_candles.json()["candles"]) == 20


def test_ai_market_analysis():
    res = client.get("/api/ai/analysis/BTCUSD")
    assert res.status_code == 200
    data = res.json()
    assert "market_regime" in data
    assert "grid_suitability_score" in data
    assert "safety_disclaimer" in data


def test_ai_parameter_suggestion():
    res = client.post("/api/ai/suggest-parameters?symbol=BTCUSD&current_price=60000.0")
    assert res.status_code == 200
    data = res.json()
    assert "recommended_config" in data
    assert "rationale" in data
    assert data["recommended_config"]["center_price"] == 60000.0


def test_backtest_run():
    res = client.post("/api/backtest/run")
    assert res.status_code == 200
    data = res.json()
    assert "net_pnl" in data
    assert "win_rate_pct" in data
    assert "limitations_notice" in data


def test_auto_configure_grid():
    res = client.post("/api/grid/auto-configure?symbol=BTCUSD&range_pct=0.04&levels=10&quantity=1")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "AUTO_CONFIGURED"
    assert data["symbol"] == "BTCUSD"
    assert data["live_price"] > 0
    assert data["config"]["center_price"] == round(data["live_price"], 1)
    assert data["config"]["lower_price"] < data["config"]["center_price"]
    assert data["config"]["upper_price"] > data["config"]["center_price"]
    assert data["levels_count"] > 0


def test_env_config_and_client_ip():
    res = client.get("/api/env-config")
    assert res.status_code == 200
    assert res.json()["success"] is True

    res_ip = client.get("/api/delta/client-ip")
    assert res_ip.status_code == 200
    assert res_ip.json()["success"] is True
    assert "ip" in res_ip.json()


def test_market_scanner_endpoint():
    res = client.get("/api/scanner/market")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert len(data["assets"]) >= 4
    symbols = [a["symbol"] for a in data["assets"]]
    assert "BTCUSD" in symbols


def test_account_risk_and_reset():
    res = client.get("/api/account/risk")
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert "risk" in res.json()

    res_reset = client.post("/api/account/risk/reset-daily-loss")
    assert res_reset.status_code == 200
    assert res_reset.json()["success"] is True


def test_grids_lifecycle_endpoints():
    res = client.get("/api/grids")
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert len(res.json()["grids"]) >= 1

    grid_id = res.json()["grids"][0]["id"]
    res_detail = client.get(f"/api/grids/{grid_id}")
    assert res_detail.status_code == 200
    assert res_detail.json()["grid"]["symbol"] == "BTCUSD"

    res_action = client.post(f"/api/grids/{grid_id}/action", json={"action": "pause"})
    assert res_action.status_code == 200
    assert res_action.json()["grid"]["isPaused"] is True
