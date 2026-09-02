"""OS cycles via existing ScheduledTask / builtin actions.

OS tasks are kept separate from HOUSEKEEPING_DEFAULTS so they are not
auto-seeded for every Odysseus user. ``ensure_os_schedules`` creates them
when a mission is started.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

from autonomy.constants import OS_CYCLE_ACTIONS
from autonomy.events import emit
from autonomy.runtime import view as runtime_view


def _run_named(owner: str, action: str, **kwargs) -> Tuple[str, bool]:
    from core.database import SessionLocal
    from autonomy.orchestrator import run_cycle
    from autonomy.seed import seed_v1

    db = SessionLocal()
    try:
        rt = runtime_view(db, owner)
        if rt.stopped:
            emit(db, owner=owner, event_type="scheduler_tick", actor="scheduler",
                 action=action, status="stopped")
            db.commit()
            from src.builtin_actions import TaskNoop
            raise TaskNoop(f"OS {action} skipped: GLOBAL STOP")
        if rt.paused:
            emit(db, owner=owner, event_type="scheduler_tick", actor="scheduler",
                 action=action, status="paused")
            db.commit()
            from src.builtin_actions import TaskNoop
            raise TaskNoop(f"OS {action} skipped: paused")

        seed_v1(db, owner)
        if action == "os_health":
            emit(db, owner=owner, event_type="scheduler_tick", actor="scheduler",
                 action=action, status="ok", extra={"autonomy_level": rt.autonomy_level})
            db.commit()
            return f"OS health: level={rt.autonomy_level} status={rt.status} cycles={rt.cycle_count}", True
        if action == "os_task_review":
            from autonomy.orchestrator import next_best_action
            nba = next_best_action(db, owner)
            emit(db, owner=owner, event_type="scheduler_tick", actor="scheduler",
                 action=action, status="ok", extra=nba)
            db.commit()
            return f"OS task review next-action={nba.get('action')}", True
        if action == "os_opportunity_scan":
            summary = run_cycle(db, owner, actor="scheduler", allow_mock_search=True)
            db.commit()
            return f"OS opportunity scan cycle={summary.get('cycle_id')} status={summary.get('status')}", True
        if action == "os_daily_performance":
            from autonomy.models import OsEvent, OsMetric
            n_events = db.query(OsEvent).filter(OsEvent.owner == owner).count() if owner else db.query(OsEvent).count()
            n_metrics = db.query(OsMetric).filter(OsMetric.owner == owner).count() if owner else db.query(OsMetric).count()
            emit(db, owner=owner, event_type="scheduler_tick", actor="scheduler",
                 action=action, status="ok", extra={"events": n_events, "metrics": n_metrics})
            db.commit()
            return f"OS daily performance events={n_events} metrics={n_metrics}", True
        db.commit()
        return f"unknown OS action {action}", False
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def action_os_health(owner: str, **kwargs) -> Tuple[str, bool]:
    try:
        return _run_named(owner, "os_health", **kwargs)
    except Exception as e:
        if e.__class__.__name__ == "TaskNoop":
            raise
        logger.error("os_health failed: %s", e)
        return str(e), False


async def action_os_task_review(owner: str, **kwargs) -> Tuple[str, bool]:
    try:
        return _run_named(owner, "os_task_review", **kwargs)
    except Exception as e:
        if e.__class__.__name__ == "TaskNoop":
            raise
        logger.error("os_task_review failed: %s", e)
        return str(e), False


async def action_os_opportunity_scan(owner: str, **kwargs) -> Tuple[str, bool]:
    try:
        return _run_named(owner, "os_opportunity_scan", **kwargs)
    except Exception as e:
        if e.__class__.__name__ == "TaskNoop":
            raise
        logger.error("os_opportunity_scan failed: %s", e)
        return str(e), False


async def action_os_daily_performance(owner: str, **kwargs) -> Tuple[str, bool]:
    try:
        return _run_named(owner, "os_daily_performance", **kwargs)
    except Exception as e:
        if e.__class__.__name__ == "TaskNoop":
            raise
        logger.error("os_daily_performance failed: %s", e)
        return str(e), False


OS_BUILTIN_ACTIONS = {
    "os_health": action_os_health,
    "os_task_review": action_os_task_review,
    "os_opportunity_scan": action_os_opportunity_scan,
    "os_daily_performance": action_os_daily_performance,
}

OS_ACTION_INFO = {
    "os_health": "Autonomous OS health tick (no execution while STOPPED)",
    "os_task_review": "Autonomous OS next-best-action review",
    "os_opportunity_scan": "Autonomous OS opportunity scan cycle (search, reversible, zero spend)",
    "os_daily_performance": "Autonomous OS daily performance metrics from real events",
}

OS_SCHEDULE_DEFAULTS = {
    "os_health": {"name": "OS Health", "schedule": "cron", "cron_expression": "*/30 * * * *"},
    "os_task_review": {"name": "OS Task Review", "schedule": "cron", "cron_expression": "0 * * * *"},
    "os_opportunity_scan": {"name": "OS Opportunity Scan", "schedule": "cron", "cron_expression": "0 */6 * * *"},
    "os_daily_performance": {"name": "OS Daily Performance", "schedule": "daily", "scheduled_time": "18:00"},
}


def ensure_os_schedules(owner: Optional[str]) -> int:
    """Create paused ScheduledTask rows for OS cycles if missing. Returns created count."""
    from datetime import datetime, timezone
    import uuid
    from core.database import SessionLocal, ScheduledTask
    from src.task_scheduler import compute_next_run

    created = 0
    db = SessionLocal()
    try:
        for action, defs in OS_SCHEDULE_DEFAULTS.items():
            q = db.query(ScheduledTask).filter(ScheduledTask.action == action)
            if owner:
                q = q.filter(ScheduledTask.owner == owner)
            if q.first():
                continue
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            task = ScheduledTask(
                id=uuid.uuid4().hex,
                owner=owner,
                name=defs["name"],
                task_type="action",
                action=action,
                schedule=defs.get("schedule"),
                scheduled_time=defs.get("scheduled_time"),
                cron_expression=defs.get("cron_expression"),
                status="paused",  # human START unsuspends runtime; tasks stay paused until explicitly enabled
                next_run=compute_next_run(
                    defs.get("schedule"),
                    defs.get("scheduled_time"),
                    cron_expression=defs.get("cron_expression"),
                    after=now,
                ),
            )
            db.add(task)
            created += 1
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("ensure_os_schedules failed")
        raise
    finally:
        db.close()
    return created
