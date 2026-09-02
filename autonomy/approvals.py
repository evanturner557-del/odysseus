"""Human APPROVE / REJECT / OVERRIDE / ROLLBACK."""

from __future__ import annotations

from typing import Optional

from core.database import utcnow_naive

from autonomy.events import audit, emit
from autonomy.models import OsAction, OsApprovalRequest, OsDecision
from autonomy.tools import execute_tool


class ApprovalError(ValueError):
    pass


def resolve_approval(
    db,
    owner: Optional[str],
    approval_id: str,
    *,
    actor: str,
    decision: str,
    note: str = "",
) -> OsApprovalRequest:
    row = db.query(OsApprovalRequest).filter(OsApprovalRequest.id == approval_id).first()
    if row is None:
        raise ApprovalError("approval not found")
    if owner and row.owner and row.owner != owner:
        raise ApprovalError("approval not found")
    if row.status != "pending":
        raise ApprovalError(f"approval already {row.status}")

    decision = (decision or "").lower().strip()
    if decision not in ("approve", "reject", "override"):
        raise ApprovalError("decision must be approve, reject, or override")
    if decision == "override" and not (note or "").strip():
        raise ApprovalError("override requires a note")

    row.resolved_by = actor
    row.resolved_at = utcnow_naive()
    row.resolution_note = note or None
    if decision == "approve":
        row.status = "approved"
        auth = "approved"
    elif decision == "override":
        row.status = "overridden"
        auth = "override"
    else:
        row.status = "rejected"
        auth = "denied"

    action = db.query(OsAction).filter(OsAction.id == row.action_id).first() if row.action_id else None
    if action:
        if decision == "reject":
            action.status = "denied"
            action.authorization = "denied"
        else:
            action.status = "approved"
            action.authorization = auth
        action.governor_reason = (action.governor_reason or "") + f" | human:{decision}"

    dec = db.query(OsDecision).filter(OsDecision.id == row.decision_id).first() if row.decision_id else None
    if dec:
        dec.status = "resolved"
        dec.actual = decision
        dec.requires_human = False

    emit(
        db, owner=owner, event_type="approval_resolved", actor=actor,
        action=action.name if action else None, status=row.status, authorization=auth,
        extra={"approval_id": row.id, "note": note},
    )
    audit(db, owner=owner, actor=actor, command=decision.upper(), target_type="approval", target_id=row.id, detail=note)
    db.flush()
    return row


def execute_approved_action(
    db,
    owner: Optional[str],
    approval: OsApprovalRequest,
    *,
    actor: str,
    allow_mock: bool = True,
) -> dict:
    action = db.query(OsAction).filter(OsAction.id == approval.action_id).first() if approval.action_id else None
    if action is None:
        return {"ok": False, "reason": "no action on approval"}
    if approval.status not in ("approved", "overridden"):
        return {"ok": False, "reason": "approval not granted"}
    if not action.tool_name:
        action.status = "executed"
        db.flush()
        return {"ok": True, "status": "marked_executed", "action_id": action.id}
    import json
    inputs = {}
    if action.input_json:
        try:
            parsed = json.loads(action.input_json)
            if isinstance(parsed, dict):
                inputs = parsed
        except (TypeError, ValueError):
            pass
    return execute_tool(
        db, owner,
        tool_name=action.tool_name,
        inputs=inputs,
        actor=actor,
        project_id=action.project_id,
        experiment_id=action.experiment_id,
        task_id=action.task_id,
        allow_mock=allow_mock,
        force_action=action,
    )


def rollback_action(db, owner: Optional[str], action_id: str, *, actor: str) -> OsAction:
    action = db.query(OsAction).filter(OsAction.id == action_id).first()
    if action is None or (owner and action.owner and action.owner != owner):
        raise ApprovalError("action not found")
    if not action.reversible:
        raise ApprovalError("action is not reversible")
    action.rolled_back = True
    action.status = "rolled_back"
    db.flush()
    emit(
        db, owner=owner, event_type="action_rolled_back", actor=actor,
        action=action.name, status="rolled_back", extra={"action_id": action.id},
    )
    audit(db, owner=owner, actor=actor, command="ROLLBACK", target_type="action", target_id=action.id)
    return action
