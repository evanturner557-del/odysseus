"""Governor cannot be bypassed; STOP wins; budget/risk/authority gates."""

import pytest

from tests.helpers.sqlite_db import make_temp_sqlite


def _session():
    from core.database import Base
    import autonomy.models  # noqa: F401
    SessionLocal, engine, tmp = make_temp_sqlite(Base.metadata)
    return SessionLocal, tmp


def _seeded():
    from autonomy.constants import ACTOR_HUMAN
    from autonomy.runtime import set_paused
    from autonomy.seed import seed_v1
    SessionLocal, tmp = _session()
    db = SessionLocal()
    seed_v1(db, "alice")
    set_paused(db, "alice", actor=ACTOR_HUMAN, paused=False)
    db.commit()
    return db, tmp


def test_global_stop_denies_execution():
    from autonomy.constants import ACTOR_HUMAN, ACTOR_SYSTEM
    from autonomy.governor import ActionRequest, Governor
    from autonomy.runtime import set_stopped
    db, _ = _seeded()
    set_stopped(db, "alice", actor=ACTOR_HUMAN, stopped=True)
    v = Governor().evaluate(db, "alice", ActionRequest(
        name="tool:web_search", tool_name="web_search", cost_cents=0,
        reversible=True, required_level=3, actor=ACTOR_SYSTEM,
    ))
    assert v.allowed is False
    assert v.authorization == "stopped"
    db.close()


def test_default_level_requests_approval_for_execute():
    from autonomy.constants import ACTOR_SYSTEM
    from autonomy.governor import ActionRequest, Governor
    db, _ = _seeded()
    v = Governor().evaluate(db, "alice", ActionRequest(
        name="tool:web_search", tool_name="web_search", cost_cents=0,
        reversible=True, required_level=3, actor=ACTOR_SYSTEM,
    ))
    assert v.allowed is False
    assert v.needs_approval is True
    assert v.approval is not None
    db.close()


def test_level_3_allows_reversible_zero_cost_search():
    from autonomy.constants import ACTOR_HUMAN, ACTOR_SYSTEM
    from autonomy.governor import ActionRequest, Governor
    from autonomy.runtime import change_autonomy_level
    db, _ = _seeded()
    change_autonomy_level(db, "alice", new_level=3, actor=ACTOR_HUMAN, reason="test permit reversible search")
    v = Governor().evaluate(db, "alice", ActionRequest(
        name="tool:web_search", tool_name="web_search", cost_cents=0,
        reversible=True, required_level=3, risk_level="low", actor=ACTOR_SYSTEM,
    ))
    assert v.allowed is True
    assert v.authorization == "autonomous"
    db.close()


def test_cannot_silently_raise_autonomy():
    from autonomy.constants import ACTOR_SYSTEM
    from autonomy.runtime import AutonomyChangeError, change_autonomy_level
    db, _ = _seeded()
    with pytest.raises(AutonomyChangeError):
        change_autonomy_level(db, "alice", new_level=5, actor=ACTOR_SYSTEM, reason="I feel like it")
    db.close()


def test_human_raise_is_logged_not_silent():
    from autonomy.constants import ACTOR_HUMAN
    from autonomy.models import OsAutonomyLevelHistory
    from autonomy.runtime import change_autonomy_level
    db, _ = _seeded()
    change_autonomy_level(db, "alice", new_level=3, actor=ACTOR_HUMAN, reason="explicit human raise")
    rows = db.query(OsAutonomyLevelHistory).all()
    assert rows
    assert rows[-1].silent is False
    assert rows[-1].new_level == 3
    db.close()


def test_insufficient_budget_requests_approval():
    from autonomy.constants import ACTOR_HUMAN, ACTOR_SYSTEM
    from autonomy.governor import ActionRequest, Governor
    from autonomy.runtime import change_autonomy_level
    db, _ = _seeded()
    change_autonomy_level(db, "alice", new_level=3, actor=ACTOR_HUMAN, reason="budget test")
    v = Governor().evaluate(db, "alice", ActionRequest(
        name="paid_thing", tool_name="web_search", cost_cents=500,
        reversible=True, required_level=3, actor=ACTOR_SYSTEM,
    ))
    assert v.allowed is False
    assert v.needs_approval is True
    db.close()


def test_forbidden_action_denied():
    from autonomy.constants import ACTOR_HUMAN, ACTOR_SYSTEM
    from autonomy.governor import ActionRequest, Governor
    from autonomy.runtime import change_autonomy_level
    db, _ = _seeded()
    change_autonomy_level(db, "alice", new_level=3, actor=ACTOR_HUMAN, reason="forbid test")
    v = Governor().evaluate(db, "alice", ActionRequest(
        name="spend_money", cost_cents=0, reversible=True, required_level=3, actor=ACTOR_SYSTEM,
    ))
    assert v.allowed is False
    assert v.authorization == "denied"
    db.close()


def test_check_spend_zero_passes():
    from autonomy.treasury import check_spend
    db, _ = _seeded()
    info = check_spend(db, owner="alice", cost_cents=0)
    assert info["cost_cents"] == 0
    db.close()


def test_check_spend_nonzero_denied_at_zero_limit():
    from autonomy.treasury import BudgetDenial, check_spend
    db, _ = _seeded()
    with pytest.raises(BudgetDenial):
        check_spend(db, owner="alice", cost_cents=1)
    db.close()
