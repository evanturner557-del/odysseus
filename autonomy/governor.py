"""Governor: ACTION → POLICY → RISK → AUTHORITY → BUDGET → execute or ask.

This is the only execution gate. Orchestrator, scheduler, CLI, and HTTP
command handlers must call ``Governor.evaluate`` before running a tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from autonomy.constants import (
    DEFAULT_FORBIDDEN_ACTIONS,
    PERMITTED_EXECUTION_RISK,
    RISK_LEVELS,
)
from autonomy.events import emit
from autonomy.models import OsAction, OsApprovalRequest, OsDecision, OsMission, OsPolicy, OsProject
from autonomy.runtime import view as runtime_view
from autonomy.treasury import BudgetDenial, check_spend


@dataclass
class ActionRequest:
    name: str
    tool_name: Optional[str] = None
    project_id: Optional[str] = None
    experiment_id: Optional[str] = None
    task_id: Optional[str] = None
    input_data: Any = None
    cost_cents: int = 0
    risk_level: str = "low"
    reversible: bool = True
    required_level: int = 3
    actor: str = "system"


@dataclass
class GovernorVerdict:
    allowed: bool
    needs_approval: bool
    reason: str
    authorization: str  # autonomous|approval_required|denied|stopped
    risk_level: str
    checks: dict = field(default_factory=dict)
    action: Optional[OsAction] = None
    approval: Optional[OsApprovalRequest] = None
    decision: Optional[OsDecision] = None


class Governor:
    def evaluate(self, db, owner: Optional[str], req: ActionRequest) -> GovernorVerdict:
        rt = runtime_view(db, owner)
        checks = {}

        action = OsAction(
            owner=owner,
            project_id=req.project_id,
            experiment_id=req.experiment_id,
            task_id=req.task_id,
            name=req.name,
            tool_name=req.tool_name,
            status="proposed",
            input_json=_json(req.input_data),
            cost_cents=int(req.cost_cents or 0),
            risk_level=_norm_risk(req.risk_level),
            reversible=bool(req.reversible),
        )
        db.add(action)
        db.flush()

        # 1. POLICY — GLOBAL STOP is absolute.
        if rt.stopped:
            checks["policy"] = "GLOBAL_STOP"
            return self._deny(
                db, owner, req, action, checks,
                reason="GLOBAL STOP is set — autonomous execution halted",
                authorization="stopped",
            )
        if rt.paused:
            checks["policy"] = "PAUSED"
            return self._request(
                db, owner, req, action, checks,
                reason="runtime is paused — human must START before execution",
            )

        forbidden = list(DEFAULT_FORBIDDEN_ACTIONS)
        mission = None
        if rt.active_mission_id:
            mission = db.query(OsMission).filter(OsMission.id == rt.active_mission_id).first()
        if mission and mission.forbidden_actions_json:
            try:
                extra = json.loads(mission.forbidden_actions_json)
                if isinstance(extra, list):
                    forbidden.extend(str(x) for x in extra)
            except (TypeError, ValueError):
                pass
        if req.name in forbidden or (req.tool_name and req.tool_name in forbidden):
            checks["policy"] = "FORBIDDEN"
            return self._deny(
                db, owner, req, action, checks,
                reason=f"action '{req.name}' is forbidden by policy",
                authorization="denied",
            )

        policy_ok, policy_reason = self._policy_tools(db, owner, req)
        checks["policy"] = "ok" if policy_ok else policy_reason
        if not policy_ok:
            return self._deny(db, owner, req, action, checks, reason=policy_reason, authorization="denied")

        # 2. RISK
        risk = _norm_risk(req.risk_level)
        checks["risk"] = risk
        permitted = PERMITTED_EXECUTION_RISK.get(int(rt.autonomy_level), frozenset())
        if risk not in permitted:
            return self._request(
                db, owner, req, action, checks,
                reason=f"risk '{risk}' exceeds permitted set {sorted(permitted)} at autonomy level {rt.autonomy_level}",
            )

        # 3. AUTHORITY
        checks["autonomy_level"] = rt.autonomy_level
        checks["required_level"] = req.required_level
        if int(rt.autonomy_level) < int(req.required_level):
            return self._request(
                db, owner, req, action, checks,
                reason=(
                    f"autonomy level {rt.autonomy_level} is below required {req.required_level} "
                    f"for execution; human approval needed"
                ),
            )
        if not req.reversible and int(rt.autonomy_level) < 4:
            return self._request(
                db, owner, req, action, checks,
                reason="irreversible actions require autonomy level 4+ or human approval",
            )

        # 4. PROJECT AUTHORISATION
        if req.project_id:
            project = db.query(OsProject).filter(OsProject.id == req.project_id).first()
            if project is None or not project.authorised:
                checks["project"] = "not_authorised"
                return self._request(
                    db, owner, req, action, checks,
                    reason="project is not authorised",
                )
            checks["project"] = "authorised"

        # 5. BUDGET
        try:
            budget_info = check_spend(
                db,
                owner=owner,
                cost_cents=int(req.cost_cents or 0),
                project_id=req.project_id,
                experiment_id=req.experiment_id,
            )
            checks["budget"] = budget_info
        except BudgetDenial as e:
            checks["budget"] = str(e)
            return self._request(db, owner, req, action, checks, reason=str(e))

        action.status = "approved"
        action.authorization = "autonomous"
        action.governor_reason = "all governor checks passed"
        db.flush()
        emit(
            db,
            owner=owner,
            event_type="governor_decision",
            actor=req.actor,
            project_id=req.project_id,
            task_id=req.task_id,
            action=req.name,
            input_data=req.input_data,
            status="allowed",
            cost_cents=req.cost_cents,
            risk_level=risk,
            authorization="autonomous",
            extra={"checks": checks, "action_id": action.id},
        )
        return GovernorVerdict(
            allowed=True,
            needs_approval=False,
            reason=action.governor_reason,
            authorization="autonomous",
            risk_level=risk,
            checks=checks,
            action=action,
        )

    def _policy_tools(self, db, owner, req: ActionRequest):
        q = db.query(OsPolicy).filter(OsPolicy.enabled.is_(True))
        if owner:
            q = q.filter((OsPolicy.owner == owner) | (OsPolicy.owner.is_(None)))
        policies = q.all()
        if not policies:
            return True, "ok"
        if not req.tool_name:
            return True, "ok"
        allowed_any = False
        saw_allowlist = False
        for p in policies:
            if not p.allowed_tools_json:
                continue
            saw_allowlist = True
            try:
                tools = json.loads(p.allowed_tools_json)
            except (TypeError, ValueError):
                continue
            if isinstance(tools, list) and req.tool_name in tools:
                allowed_any = True
        if saw_allowlist and not allowed_any:
            return False, f"tool '{req.tool_name}' is not in any enabled policy allowlist"
        return True, "ok"

    def _deny(self, db, owner, req, action, checks, *, reason, authorization) -> GovernorVerdict:
        action.status = "denied"
        action.authorization = authorization
        action.governor_reason = reason
        db.flush()
        emit(
            db, owner=owner, event_type="governor_decision", actor=req.actor,
            project_id=req.project_id, action=req.name, status="denied",
            cost_cents=req.cost_cents, risk_level=action.risk_level,
            authorization=authorization, extra={"checks": checks, "reason": reason, "action_id": action.id},
        )
        return GovernorVerdict(
            allowed=False, needs_approval=False, reason=reason,
            authorization=authorization, risk_level=action.risk_level,
            checks=checks, action=action,
        )

    def _request(self, db, owner, req, action, checks, *, reason) -> GovernorVerdict:
        action.status = "proposed"
        action.authorization = "approval_required"
        action.governor_reason = reason
        decision = OsDecision(
            owner=owner,
            what=f"Approve action '{req.name}'",
            why=reason,
            evidence=_json(checks),
            actor=req.actor,
            authority="human",
            cost_cents=int(req.cost_cents or 0),
            expected="execute if approved",
            alternatives_json=_json(["approve", "reject", "override"]),
            status="open",
            requires_human=True,
            urgency=5 if action.risk_level in ("high", "critical") else 3,
            importance=6,
            options_json=_json([
                {"id": "approve", "label": "Approve execution"},
                {"id": "reject", "label": "Reject"},
                {"id": "override", "label": "Override with note"},
            ]),
            recommendation="Review evidence. Default: reject spend; approve only reversible zero-cost tools you intend.",
            consequence_of_waiting="The cycle records the opportunity/hypothesis but will not execute this action.",
            thesis=f"Executing {req.name} is within mission if reversible and zero-cost.",
            counterargument="Any execution without explicit human OK at current autonomy is out of policy.",
            missing_evidence="Human confirmation of intent.",
            uncertainty=0.4,
        )
        db.add(decision)
        db.flush()
        approval = OsApprovalRequest(
            owner=owner,
            action_id=action.id,
            decision_id=decision.id,
            status="pending",
            reason=reason,
            requested_level=req.required_level,
        )
        db.add(approval)
        db.flush()
        decision.related_approval_id = approval.id
        emit(
            db, owner=owner, event_type="approval_requested", actor=req.actor,
            project_id=req.project_id, action=req.name, status="pending",
            cost_cents=req.cost_cents, risk_level=action.risk_level,
            authorization="approval_required",
            extra={"checks": checks, "reason": reason, "action_id": action.id, "approval_id": approval.id},
        )
        emit(
            db, owner=owner, event_type="governor_decision", actor=req.actor,
            project_id=req.project_id, action=req.name, status="approval_required",
            cost_cents=req.cost_cents, risk_level=action.risk_level,
            authorization="approval_required", extra={"action_id": action.id},
        )
        return GovernorVerdict(
            allowed=False, needs_approval=True, reason=reason,
            authorization="approval_required", risk_level=action.risk_level,
            checks=checks, action=action, approval=approval, decision=decision,
        )


def _norm_risk(risk: str) -> str:
    r = (risk or "low").lower().strip()
    return r if r in RISK_LEVELS else "critical"


def _json(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, ensure_ascii=False)
