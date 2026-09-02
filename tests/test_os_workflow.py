"""End-to-end loop with mock search. No API keys, no spend."""

from unittest.mock import patch

from tests.helpers.sqlite_db import make_temp_sqlite


def _db():
    from core.database import Base
    import autonomy.models  # noqa: F401
    SessionLocal, engine, tmp = make_temp_sqlite(Base.metadata)
    return SessionLocal()


def test_full_loop_at_level_3_with_mock_search():
    from autonomy.constants import ACTOR_HUMAN
    from autonomy.models import OsEvent, OsExperiment, OsKnowledge, OsMetric, OsOpportunity
    from autonomy.orchestrator import run_cycle
    from autonomy.runtime import change_autonomy_level, set_paused, set_stopped
    from autonomy.seed import seed_v1

    db = _db()
    seed = seed_v1(db, "alice")
    set_stopped(db, "alice", actor=ACTOR_HUMAN, stopped=False)
    set_paused(db, "alice", actor=ACTOR_HUMAN, paused=False)
    change_autonomy_level(db, "alice", new_level=3, actor=ACTOR_HUMAN, reason="allow reversible search in test")
    db.commit()

    with patch("autonomy.tools._real_search", side_effect=Exception("search down")):
        summary = run_cycle(db, "alice", actor="test", allow_mock_search=True)
    db.commit()

    assert summary["status"] == "ok"
    assert summary["search"]["used_mock"] is True
    assert summary["search"]["hit_count"] >= 1
    assert db.query(OsOpportunity).filter(OsOpportunity.owner == "alice").count() >= 1
    assert db.query(OsExperiment).filter(OsExperiment.owner == "alice").count() >= 1
    assert db.query(OsEvent).filter(OsEvent.event_type == "cycle_finished").count() >= 1
    assert db.query(OsMetric).count() >= 1
    assert db.query(OsKnowledge).count() >= 1
    assert seed["experiment_id"]
    db.close()


def test_loop_at_default_level_queues_approval():
    from autonomy.constants import ACTOR_HUMAN
    from autonomy.models import OsApprovalRequest
    from autonomy.orchestrator import run_cycle
    from autonomy.runtime import set_paused, set_stopped
    from autonomy.seed import seed_v1

    db = _db()
    seed_v1(db, "alice")
    set_stopped(db, "alice", actor=ACTOR_HUMAN, stopped=False)
    set_paused(db, "alice", actor=ACTOR_HUMAN, paused=False)
    db.commit()
    summary = run_cycle(db, "alice", actor="test", allow_mock_search=True)
    db.commit()
    assert summary["status"] in ("awaiting_approval", "ok")
    pending = db.query(OsApprovalRequest).filter(OsApprovalRequest.status == "pending").count()
    assert pending >= 1
    db.close()


def test_global_stop_skips_cycle():
    from autonomy.constants import ACTOR_HUMAN
    from autonomy.orchestrator import run_cycle
    from autonomy.runtime import set_stopped
    from autonomy.seed import seed_v1

    db = _db()
    seed_v1(db, "alice")
    set_stopped(db, "alice", actor=ACTOR_HUMAN, stopped=True)
    db.commit()
    summary = run_cycle(db, "alice", actor="test")
    assert summary["status"] == "skipped"
    assert summary["reason"] == "GLOBAL STOP"
    db.close()


def test_approve_then_execute_search():
    from autonomy.approvals import execute_approved_action, resolve_approval
    from autonomy.constants import ACTOR_HUMAN
    from autonomy.models import OsApprovalRequest
    from autonomy.orchestrator import run_cycle
    from autonomy.runtime import set_paused, set_stopped
    from autonomy.seed import seed_v1

    db = _db()
    seed_v1(db, "alice")
    set_stopped(db, "alice", actor=ACTOR_HUMAN, stopped=False)
    set_paused(db, "alice", actor=ACTOR_HUMAN, paused=False)
    db.commit()
    run_cycle(db, "alice", actor="test", allow_mock_search=True)
    db.commit()
    pending = db.query(OsApprovalRequest).filter(OsApprovalRequest.status == "pending").first()
    assert pending is not None
    row = resolve_approval(db, "alice", pending.id, actor=ACTOR_HUMAN, decision="approve", note="ok")
    out = execute_approved_action(db, "alice", row, actor=ACTOR_HUMAN, allow_mock=True)
    db.commit()
    assert out.get("ok") is True
    db.close()
