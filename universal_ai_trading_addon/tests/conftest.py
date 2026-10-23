"""Pytest configuration and database/state isolation fixtures."""
import pytest
from backend.config import settings
from backend.persistence.database import engine, Base, init_db
from backend.main import risk_engine, grid_engine
import backend.main as main_mod


@pytest.fixture(autouse=True)
def isolate_database_and_state():
    """Drop and recreate all tables and reset global flags for clean isolation between tests."""
    orig_paper = settings.paper_trading
    orig_exchange = grid_engine.exchange
    settings.paper_trading = True
    grid_engine.exchange = main_mod.broker
    main_mod.reconciler.exchange = main_mod.broker
    init_db()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    # Reset singleton risk engine & app flags
    risk_engine.reset_kill_switch()
    risk_engine.is_paused = False
    grid_engine.is_paused = False
    grid_engine.is_active = False
    main_mod.paused = False
    
    yield

    # Clean up after test as well
    risk_engine.reset_kill_switch()
    risk_engine.is_paused = False
    grid_engine.is_paused = False
    main_mod.paused = False
    settings.paper_trading = orig_paper
    grid_engine.exchange = orig_exchange
    main_mod.reconciler.exchange = orig_exchange
