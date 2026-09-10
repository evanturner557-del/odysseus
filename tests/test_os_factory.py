"""Business Factory command desktop — models, API, dashboard payload."""

import os
from datetime import datetime, timedelta

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


def test_factory_dashboard_zeros_and_bots(monkeypatch):
    client, *_ = _client(monkeypatch)
    d = client.get("/api/os/dashboard")
    assert d.status_code == 200
    dash = d.json()
    assert dash["costs"]["currency"] == "GBP"
    assert dash["costs"]["spent_cents"] == 0
    factory = dash["factory"]
    assert factory is not None
    assert factory["meta"]["stages"] == [
        "IDEA", "SCORED", "VALIDATING", "FIRST_SALE",
        "KITTED", "LIVE", "AUTOMATING", "AUTONOMOUS",
    ]
    assert factory["meta"]["classes"] == ["for_profit", "charity"]
    assert [b["name"] for b in factory["bots"]] == [
        "businessbuilder", "Businessbot", "Holdingbot", "orchestratorbot",
    ]
    for b in factory["bots"]:
        assert b["status"] == "idle"
    cap = factory["holdco_pnl"]["capital_pool"]
    assert cap["stripe_balance_gbp"] == 0.0
    assert cap["bank_balance_gbp"] == 0.0
    assert "not linked" in cap["note"].lower() or "Stripe" in cap["note"]
    assert factory["holdco_pnl"]["totals"]["revenue_gbp"] == 0.0
    assert factory["pipeline"]["counts"]["IDEA"] == 0
    assert set(factory["approvals_queue"]["categories"]) == {"spend", "external", "irreversible"}
    assert isinstance(factory["orchestrator_feed"], list)


def test_factory_endpoint_and_create_unit(monkeypatch):
    client, SessionLocal, _ = _client(monkeypatch)
    f = client.get("/api/os/factory")
    assert f.status_code == 200
    body = f.json()
    assert "pipeline" in body and "holdco_pnl" in body

    created = client.post("/api/os/factory/units", json={
        "name": "Probe Co",
        "business_class": "for_profit",
        "stage": "IDEA",
        "next_action": "score opportunity",
    })
    assert created.status_code == 200
    unit = created.json()
    assert unit["unit_id"].startswith("UNIT-")
    assert unit["class"] == "for_profit"
    assert unit["stage"] == "IDEA"
    assert unit["revenue_gbp"] == 0.0
    assert unit["cost_gbp"] == 0.0
    assert unit["mrr_gbp"] == 0.0
    assert unit["margin_pct"] == 0.0
    assert unit["customers"] == 0
    assert "updated_at" in unit
    assert unit["bot_owner"] == "businessbuilder"

    charity = client.post("/api/os/factory/units", json={
        "name": "Good Cause",
        "business_class": "charity",
        "stage": "SCORED",
        "bot_owner": "Holdingbot",
    })
    assert charity.status_code == 200
    c = charity.json()
    assert c["class"] == "charity"
    assert c["donors"] == 0
    assert c["donor_mrr_gbp"] == 0.0

    bad = client.post("/api/os/factory/units", json={
        "name": "Nope", "business_class": "llc", "stage": "IDEA",
    })
    assert bad.status_code == 400

    units = client.get("/api/os/factory/units").json()
    assert len(units) == 2

    dash = client.get("/api/os/dashboard").json()
    assert dash["factory"]["pipeline"]["counts"]["IDEA"] == 1
    assert dash["factory"]["pipeline"]["counts"]["SCORED"] == 1
    assert len(dash["factory"]["holdco_pnl"]["per_unit"]) == 2


def test_factory_kill_date_and_approval_categories(monkeypatch):
    client, SessionLocal, _ = _client(monkeypatch)
    from autonomy.constants import ACTOR_HUMAN
    from autonomy.factory import create_unit, categorize_approval
    from autonomy.models import OsAction, OsApprovalRequest
    from autonomy.seed import seed_v1

    db = SessionLocal()
    seed_v1(db, "alice")
    kill = datetime.utcnow() + timedelta(days=14)
    create_unit(
        db, "alice", name="Kill Me Inc", stage="VALIDATING",
        kill_date=kill, next_action="validate or kill",
    )
    # spend / irreversible / external actions + approvals
    spend_a = OsAction(
        owner="alice", name="buy ads", tool_name="spend_money",
        status="proposed", cost_cents=500, reversible=True, risk_level="medium",
    )
    irr_a = OsAction(
        owner="alice", name="wipe prod", tool_name="delete_production_data",
        status="proposed", cost_cents=0, reversible=False, risk_level="critical",
    )
    ext_a = OsAction(
        owner="alice", name="search probe", tool_name="web_search",
        status="proposed", cost_cents=0, reversible=True, risk_level="low",
    )
    db.add_all([spend_a, irr_a, ext_a])
    db.flush()
    for action, reason in (
        (spend_a, "needs spend approval"),
        (irr_a, "irreversible change"),
        (ext_a, "external search"),
    ):
        db.add(OsApprovalRequest(
            owner="alice", action_id=action.id, status="pending", reason=reason,
        ))
    db.commit()

    assert categorize_approval(
        db.query(OsApprovalRequest).filter(OsApprovalRequest.action_id == spend_a.id).one(),
        spend_a,
    ) == "spend"
    assert categorize_approval(
        db.query(OsApprovalRequest).filter(OsApprovalRequest.action_id == irr_a.id).one(),
        irr_a,
    ) == "irreversible"
    assert categorize_approval(
        db.query(OsApprovalRequest).filter(OsApprovalRequest.action_id == ext_a.id).one(),
        ext_a,
    ) == "external"
    db.close()

    factory = client.get("/api/os/factory").json()
    assert factory["pipeline"]["counts"]["VALIDATING"] == 1
    assert len(factory["pipeline"]["kill_dates"]) == 1
    assert factory["approvals_queue"]["counts"]["spend"] >= 1
    assert factory["approvals_queue"]["counts"]["irreversible"] >= 1
    assert factory["approvals_queue"]["counts"]["external"] >= 1


def test_metrics_contract_keys(monkeypatch):
    client, *_ = _client(monkeypatch)
    unit = client.post("/api/os/factory/units", json={"name": "Contract Check"}).json()
    required = {
        "unit_id", "class", "stage", "revenue_gbp", "cost_gbp", "mrr_gbp",
        "margin_pct", "customers", "conversations", "next_action", "kill_date",
        "bot_owner", "updated_at",
    }
    assert required.issubset(unit.keys())
