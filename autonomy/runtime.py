"""GLOBAL STOP, pause, and autonomy level. Never silently increased."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from autonomy.constants import (
    ACTOR_HUMAN,
    DEFAULT_AUTONOMY_CEILING,
    DEFAULT_AUTONOMY_LEVEL,
    MAX_AUTONOMY_LEVEL,
    RUNTIME_PAUSED,
    RUNTIME_RUNNING,
    RUNTIME_STOPPED,
    V1_MAX_AUTONOMY_LEVEL,
)
from autonomy.events import audit, emit
from autonomy.models import OsAutonomyLevelHistory, OsRuntimeState


@dataclass
class RuntimeView:
    owner: Optional[str]
    status: str
    stopped: bool
    paused: bool
    autonomy_level: int
    autonomy_ceiling: int
    active_mission_id: Optional[str]
    cycle_count: int
    last_cycle_id: Optional[str]
    scoring_weights_json: Optional[str]

    @property
    def can_run_autonomous(self) -> bool:
        return (not self.stopped) and (not self.paused) and self.status == RUNTIME_RUNNING


def get_or_create_runtime(db, owner: Optional[str]) -> OsRuntimeState:
    q = db.query(OsRuntimeState)
    if owner:
        row = q.filter(OsRuntimeState.owner == owner).first()
    else:
        row = q.filter(OsRuntimeState.owner.is_(None)).first()
        if row is None:
            row = q.first()
    if row is None:
        row = OsRuntimeState(
            owner=owner,
            status=RUNTIME_PAUSED,
            stopped=False,
            paused=True,
            autonomy_level=DEFAULT_AUTONOMY_LEVEL,
            autonomy_ceiling=DEFAULT_AUTONOMY_CEILING,
        )
        db.add(row)
        db.flush()
    return row


def view(db, owner: Optional[str]) -> RuntimeView:
    row = get_or_create_runtime(db, owner)
    return RuntimeView(
        owner=row.owner,
        status=row.status,
        stopped=bool(row.stopped),
        paused=bool(row.paused),
        autonomy_level=int(row.autonomy_level or 0),
        autonomy_ceiling=int(row.autonomy_ceiling or DEFAULT_AUTONOMY_CEILING),
        active_mission_id=row.active_mission_id,
        cycle_count=int(row.cycle_count or 0),
        last_cycle_id=row.last_cycle_id,
        scoring_weights_json=row.scoring_weights_json,
    )


def set_stopped(db, owner: Optional[str], *, actor: str, stopped: bool) -> OsRuntimeState:
    row = get_or_create_runtime(db, owner)
    row.stopped = bool(stopped)
    if stopped:
        row.status = RUNTIME_STOPPED
        row.paused = True
        command = "STOP"
    else:
        row.status = RUNTIME_RUNNING
        row.paused = False
        command = "START"
    db.flush()
    emit(
        db,
        owner=owner,
        event_type="stop" if stopped else "start",
        actor=actor,
        status=row.status,
        extra={"stopped": row.stopped},
    )
    audit(db, owner=owner, actor=actor, command=command, target_type="runtime", target_id=row.id)
    return row


def set_paused(db, owner: Optional[str], *, actor: str, paused: bool) -> OsRuntimeState:
    row = get_or_create_runtime(db, owner)
    if row.stopped and not paused:
        # Cannot unpause while globally stopped — START is required.
        return row
    row.paused = bool(paused)
    if paused:
        row.status = RUNTIME_PAUSED
    else:
        row.status = RUNTIME_RUNNING
    db.flush()
    emit(db, owner=owner, event_type="pause", actor=actor, status=row.status, extra={"paused": row.paused})
    audit(db, owner=owner, actor=actor, command="PAUSE" if paused else "RESUME", target_type="runtime", target_id=row.id)
    return row


class AutonomyChangeError(ValueError):
    pass


def change_autonomy_level(
    db,
    owner: Optional[str],
    *,
    new_level: int,
    actor: str,
    reason: str,
) -> OsRuntimeState:
    """Change autonomy level. Increases require a human actor and a reason.

    Never silent. Never self-elevated by the orchestrator.
    """
    if not isinstance(new_level, int) or new_level < 0 or new_level > MAX_AUTONOMY_LEVEL:
        raise AutonomyChangeError(f"invalid autonomy level {new_level}")
    if not (reason or "").strip():
        raise AutonomyChangeError("reason is required for every autonomy change")
    if actor != ACTOR_HUMAN and new_level > DEFAULT_AUTONOMY_LEVEL:
        raise AutonomyChangeError("only a human may raise autonomy above the default")
    if new_level > V1_MAX_AUTONOMY_LEVEL:
        raise AutonomyChangeError(f"V1 refuses levels above {V1_MAX_AUTONOMY_LEVEL}")

    row = get_or_create_runtime(db, owner)
    previous = int(row.autonomy_level or 0)
    if new_level > previous and actor != ACTOR_HUMAN:
        raise AutonomyChangeError("autonomy must never be silently increased")
    if new_level > int(row.autonomy_ceiling or DEFAULT_AUTONOMY_CEILING) and actor != ACTOR_HUMAN:
        raise AutonomyChangeError("new level exceeds configured ceiling")

    hist = OsAutonomyLevelHistory(
        owner=owner,
        previous_level=previous,
        new_level=new_level,
        actor=actor,
        reason=reason.strip(),
        silent=False,
    )
    db.add(hist)
    row.autonomy_level = new_level
    db.flush()
    emit(
        db,
        owner=owner,
        event_type="autonomy_level_changed",
        actor=actor,
        status="ok",
        extra={"previous": previous, "new": new_level, "reason": reason.strip(), "history_id": hist.id},
    )
    audit(
        db,
        owner=owner,
        actor=actor,
        command="CHANGE_AUTONOMY_LEVEL",
        target_type="runtime",
        target_id=row.id,
        detail={"previous": previous, "new": new_level, "reason": reason.strip()},
    )
    return row


def mark_cycle(db, owner: Optional[str], cycle_id: str) -> OsRuntimeState:
    row = get_or_create_runtime(db, owner)
    from core.database import utcnow_naive
    row.last_cycle_at = utcnow_naive()
    row.last_cycle_id = cycle_id
    row.cycle_count = int(row.cycle_count or 0) + 1
    db.flush()
    return row
