"""Append-only black-box event log."""

from __future__ import annotations

import json
from typing import Any, Optional

from autonomy.models import OsEvent, OsMetric, OsAuditLog


def _dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except TypeError:
        return json.dumps({"repr": repr(value)}, ensure_ascii=False)


def emit(
    db,
    *,
    owner: Optional[str],
    event_type: str,
    actor: str,
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    action: Optional[str] = None,
    input_data: Any = None,
    output_data: Any = None,
    status: Optional[str] = None,
    cost_cents: int = 0,
    risk_level: Optional[str] = None,
    authorization: Optional[str] = None,
    extra: Any = None,
) -> OsEvent:
    row = OsEvent(
        owner=owner,
        event_type=event_type,
        actor=actor,
        project_id=project_id,
        task_id=task_id,
        action=action,
        input_json=_dumps(input_data),
        output_json=_dumps(output_data),
        status=status,
        cost_cents=int(cost_cents or 0),
        risk_level=risk_level,
        authorization=authorization,
        extra_json=_dumps(extra),
    )
    db.add(row)
    db.flush()
    return row


def record_metric(
    db,
    *,
    owner: Optional[str],
    name: str,
    value: float,
    unit: Optional[str] = None,
    source_event_id: Optional[str] = None,
    extra: Any = None,
) -> OsMetric:
    row = OsMetric(
        owner=owner,
        name=name,
        value=float(value),
        unit=unit,
        source_event_id=source_event_id,
        extra_json=_dumps(extra),
    )
    db.add(row)
    db.flush()
    return row


def audit(
    db,
    *,
    owner: Optional[str],
    actor: str,
    command: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    detail: Any = None,
) -> OsAuditLog:
    row = OsAuditLog(
        owner=owner,
        actor=actor,
        command=command,
        target_type=target_type,
        target_id=target_id,
        detail=_dumps(detail) if not isinstance(detail, str) else detail,
    )
    db.add(row)
    db.flush()
    return row


def event_to_dict(row: OsEvent) -> dict:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "actor": row.actor,
        "project_id": row.project_id,
        "task_id": row.task_id,
        "action": row.action,
        "input": _loads(row.input_json),
        "output": _loads(row.output_json),
        "status": row.status,
        "cost_cents": row.cost_cents,
        "risk_level": row.risk_level,
        "authorization": row.authorization,
        "metadata": _loads(row.extra_json),
    }


def _loads(raw: Optional[str]):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw
