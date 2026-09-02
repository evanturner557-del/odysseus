"""CLI: python -m autonomy.run_cycle

Runs one closed loop with mock or real search and prints inspectable IDs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)



def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run one Autonomous OS cycle")
    parser.add_argument("--owner", default="local")
    parser.add_argument("--use-mock-search", action="store_true", help="Force mock search fallback")
    parser.add_argument("--no-mock", action="store_true", help="Do not fall back to mock if search is down")
    parser.add_argument("--query", default=None)
    parser.add_argument("--dump", action="store_true", help="Dump recent events/metrics/memory and exit")
    parser.add_argument("--stop", action="store_true", help="Set GLOBAL STOP and exit")
    parser.add_argument("--start", action="store_true", help="Clear GLOBAL STOP, unpause, then run")
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--autonomy-level", type=int, default=None, help="Human-set level (logged)")
    args = parser.parse_args(argv)

    # Import after env so DATABASE_URL is honoured by core.database.
    from core.database import SessionLocal, engine, Base
    import autonomy.models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    from autonomy.approvals import resolve_approval
    from autonomy.constants import ACTOR_CLI, ACTOR_HUMAN
    from autonomy.events import event_to_dict
    from autonomy.memory import knowledge_to_dict, list_knowledge
    from autonomy.models import (
        OsApprovalRequest,
        OsEvent,
        OsExperiment,
        OsMetric,
        OsOpportunity,
    )
    from autonomy.orchestrator import run_cycle
    from autonomy.runtime import change_autonomy_level, set_paused, set_stopped, view
    from autonomy.seed import seed_v1

    owner = args.owner
    db = SessionLocal()
    try:
        seed = seed_v1(db, owner)
        db.commit()

        if args.stop:
            set_stopped(db, owner, actor=ACTOR_HUMAN, stopped=True)
            db.commit()
            print(json.dumps({"ok": True, "stopped": True, "seed": seed}, indent=2))
            return 0

        if args.dump:
            rt = view(db, owner)
            events = db.query(OsEvent).filter(OsEvent.owner == owner).order_by(OsEvent.timestamp.desc()).limit(30).all()
            metrics = db.query(OsMetric).filter(OsMetric.owner == owner).order_by(OsMetric.recorded_at.desc()).limit(30).all()
            opps = db.query(OsOpportunity).filter(OsOpportunity.owner == owner).all()
            exps = db.query(OsExperiment).filter(OsExperiment.owner == owner).all()
            payload = {
                "runtime": rt.__dict__,
                "seed": seed,
                "events": [event_to_dict(e) for e in events],
                "metrics": [{"name": m.name, "value": m.value, "unit": m.unit, "at": m.recorded_at.isoformat() if m.recorded_at else None} for m in metrics],
                "opportunities": [{"public_id": o.public_id, "title": o.title, "score": o.score, "status": o.status} for o in opps],
                "experiments": [{"id": e.id, "name": e.name, "status": e.status, "result": e.result_json} for e in exps],
                "memory": [knowledge_to_dict(k) for k in list_knowledge(db, owner, limit=20)],
            }
            print(json.dumps(payload, indent=2, default=str))
            return 0

        if args.seed_only:
            print(json.dumps({"ok": True, "seed": seed}, indent=2))
            return 0

        if args.start:
            set_stopped(db, owner, actor=ACTOR_HUMAN, stopped=False)
            set_paused(db, owner, actor=ACTOR_HUMAN, paused=False)
            db.commit()

        if args.autonomy_level is not None:
            change_autonomy_level(
                db, owner, new_level=args.autonomy_level, actor=ACTOR_HUMAN,
                reason="CLI --autonomy-level",
            )
            db.commit()

        # Default CLI run: unpause so a cycle can request approval or execute.
        # Does NOT raise autonomy. GLOBAL STOP still wins.
        rt = view(db, owner)
        if rt.paused and not rt.stopped:
            set_paused(db, owner, actor=ACTOR_HUMAN, paused=False)
            db.commit()

        allow_mock = (not args.no_mock)
        force_mock = bool(args.use_mock_search)
        if force_mock:
            allow_mock = True

        summary = run_cycle(
            db, owner, actor=ACTOR_CLI, query=args.query,
            allow_mock_search=allow_mock,
            force_mock_search=force_mock,
        )
        db.commit()

        # If the only blocker is autonomy<3 for a reversible zero-cost search,
        # leave the approval pending. Humans use --dump / dashboard to approve.
        pending = db.query(OsApprovalRequest).filter(
            OsApprovalRequest.owner == owner,
            OsApprovalRequest.status == "pending",
        ).all()
        summary["pending_approvals"] = [p.id for p in pending]
        print(json.dumps({"ok": True, "seed": seed, "cycle": summary}, indent=2, default=str))
        return 0
    except Exception as e:
        db.rollback()
        print(json.dumps({"ok": False, "error": str(e), "type": type(e).__name__}), file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
