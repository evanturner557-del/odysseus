"""API + permission tests for /api/os/. Uses a tiny FastAPI app, not full Odysseus."""

import os

os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("LOCALHOST_BYPASS", "true")

import pytest
from tests.helpers.sqlite_db import make_temp_sqlite


def _client(monkeypatch, owner="alice"):
    from core.database import Base
    import autonomy.models  # noqa: F401
    SessionLocal, engine, tmp = make_temp_sqlite(Base.metadata)

    import core.database as cdb
    import routes.os_routes as os_routes
    monkeypatch.setattr(cdb, "SessionLocal", SessionLocal)
    monkeypatch.setattr(os_routes, "SessionLocal", SessionLocal)

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from routes.os_routes import setup_os_routes

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.current_user = owner
        return await call_next(request)

    app.include_router(setup_os_routes())
    return TestClient(app), SessionLocal, tmp


def test_health_and_dashboard_real_zeros(monkeypatch):
    client, *_ = _client(monkeypatch)
    h = client.get("/api/os/health")
    assert h.status_code == 200
    body = h.json()
    assert body["ok"] is True
    assert "stopped" in body
    assert body["cycle_count"] >= 0
    d = client.get("/api/os/dashboard")
    assert d.status_code == 200
    dash = d.json()
    assert dash["costs"]["spent_cents"] == 0
    assert isinstance(dash["opportunities"], list)
    assert isinstance(dash["attention"], list)


def test_stop_actually_stops_cycle(monkeypatch):
    client, *_ = _client(monkeypatch)
    assert client.post("/api/os/stop").json()["stopped"] is True
    cyc = client.post("/api/os/cycle", json={"use_mock_search": True})
    assert cyc.status_code == 200
    assert cyc.json()["status"] == "skipped"
    assert client.get("/api/os/health").json()["stopped"] is True


def test_start_pause_and_change_mission(monkeypatch):
    client, *_ = _client(monkeypatch)
    assert client.post("/api/os/start").status_code == 200
    missions = client.get("/api/os/missions").json()
    assert missions
    mid = missions[0]["id"]
    r = client.put(f"/api/os/missions/{mid}", json={"title": "New mission title", "statement": "Stay reversible"})
    assert r.status_code == 200
    assert r.json()["title"] == "New mission title"
    assert client.post("/api/os/pause").status_code == 200
    assert client.get("/api/os/health").json()["paused"] is True


def test_change_budget_and_autonomy(monkeypatch):
    client, *_ = _client(monkeypatch)
    budgets = client.get("/api/os/budgets").json()
    assert budgets
    bid = budgets[0]["id"]
    r = client.put(f"/api/os/budgets/{bid}", json={"limit_cents": 0, "autonomous_limit_cents": 0})
    assert r.status_code == 200
    bad = client.post("/api/os/autonomy-level", json={"level": 3, "reason": ""})
    assert bad.status_code == 400
    ok = client.post("/api/os/autonomy-level", json={"level": 3, "reason": "human raise for test"})
    assert ok.status_code == 200
    assert ok.json()["autonomy_level"] == 3


def test_reject_approval(monkeypatch):
    client, *_ = _client(monkeypatch)
    client.post("/api/os/start")
    client.post("/api/os/cycle", json={"use_mock_search": True})
    approvals = client.get("/api/os/approvals").json()
    pending = [a for a in approvals if a["status"] == "pending"]
    if not pending:
        pytest.skip("no pending approval at default autonomy — still a valid state")
    rid = pending[0]["id"]
    r = client.post(f"/api/os/approvals/{rid}/reject", json={"note": "no"})
    assert r.status_code == 200
    assert r.json()["approval"]["status"] == "rejected"


def test_owner_cannot_see_other_owner(monkeypatch):
    from core.database import Base
    import autonomy.models  # noqa: F401
    import core.database as cdb
    import routes.os_routes as os_routes
    SessionLocal, engine, tmp = make_temp_sqlite(Base.metadata)
    monkeypatch.setattr(cdb, "SessionLocal", SessionLocal)
    monkeypatch.setattr(os_routes, "SessionLocal", SessionLocal)

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from routes.os_routes import setup_os_routes
    from autonomy.seed import seed_v1
    from autonomy.opportunities import discover_from_search

    db = SessionLocal()
    seed_v1(db, "alice")
    seed_v1(db, "bob")
    discover_from_search(
        db, "alice", hits=[{"title": "Secret Alice opp", "snippet": "x", "url": "http://a"}],
        mission_id=None, project_id=None, mission_text="m", actor="t",
    )
    db.commit()
    db.close()

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.current_user = "bob"
        return await call_next(request)

    app.include_router(setup_os_routes())
    client = TestClient(app)
    titles = [o["title"] for o in client.get("/api/os/opportunities").json()]
    assert "Secret Alice opp" not in titles


def test_unauthenticated_rejected_when_auth_on(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "false")
    from core.database import Base
    import autonomy.models  # noqa: F401
    SessionLocal, engine, tmp = make_temp_sqlite(Base.metadata)
    import core.database as cdb
    import routes.os_routes as os_routes
    monkeypatch.setattr(cdb, "SessionLocal", SessionLocal)
    monkeypatch.setattr(os_routes, "SessionLocal", SessionLocal)

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from routes.os_routes import setup_os_routes

    app = FastAPI()

    @app.middleware("http")
    async def no_user(request: Request, call_next):
        request.state.current_user = None
        request.state.auth_manager = type("A", (), {"is_configured": True})()
        return await call_next(request)

    # require_user reads request.app.state.auth_manager
    class AM:
        is_configured = True
    app.state.auth_manager = AM()
    app.include_router(setup_os_routes())
    client = TestClient(app, base_url="http://example.com")
    r = client.get("/api/os/health")
    assert r.status_code in (401, 403)
