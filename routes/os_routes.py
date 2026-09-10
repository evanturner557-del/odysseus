"""Autonomous OS HTTP APIs under /api/os/. Reuses Odysseus auth."""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.auth_helpers import require_user
from core.database import SessionLocal

from autonomy.approvals import ApprovalError, execute_approved_action, resolve_approval, rollback_action
from autonomy.constants import ACTOR_HUMAN, DEFAULT_SCORING_WEIGHTS
from autonomy.events import audit, event_to_dict, record_metric
from autonomy.memory import knowledge_to_dict, list_knowledge, remember
from autonomy.models import (
    OsAction,
    OsAgent,
    OsApprovalRequest,
    OsBudget,
    OsDecision,
    OsEvent,
    OsExperiment,
    OsHypothesis,
    OsMetric,
    OsMission,
    OsOpportunity,
    OsProject,
    OsRuntimeState,
)
from autonomy.opportunities import to_dict as opp_to_dict
from autonomy.orchestrator import next_best_action, run_cycle
from autonomy.runtime import (
    AutonomyChangeError,
    change_autonomy_level,
    get_or_create_runtime,
    set_paused,
    set_stopped,
    view as runtime_view,
)
from autonomy.seed import seed_v1
from autonomy.tools import REGISTRY
from autonomy.treasury import update_budget
from autonomy.factory import (
    build_factory_dashboard,
    create_unit,
    delete_unit,
    find_unit_by_name,
    get_unit_by_unit_id,
    list_units,
    parse_kill_date,
    unit_to_dict,
    update_unit,
)
from autonomy.constants import FACTORY_CLASSES, FACTORY_STAGES


def _owner(request: Request) -> str:
    return require_user(request) or ""


def _dumps_maybe(raw):
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return json.dumps(raw)
    return str(raw)


class MissionIn(BaseModel):
    title: str
    statement: str
    values: Optional[list] = None
    constraints: Optional[list] = None
    forbidden_actions: Optional[list] = None
    strategy: Optional[str] = None
    risk_appetite: str = "low"


class MissionPatch(BaseModel):
    title: Optional[str] = None
    statement: Optional[str] = None
    values: Optional[list] = None
    constraints: Optional[list] = None
    forbidden_actions: Optional[list] = None
    strategy: Optional[str] = None
    risk_appetite: Optional[str] = None
    is_active: Optional[bool] = None


class BudgetPatch(BaseModel):
    limit_cents: Optional[int] = None
    autonomous_limit_cents: Optional[int] = None
    authorised: Optional[bool] = None


class AutonomyIn(BaseModel):
    level: int
    reason: str


class ApprovalIn(BaseModel):
    note: str = ""


class OverrideIn(BaseModel):
    note: str = Field(..., min_length=1)


class PrioritiseIn(BaseModel):
    opportunity_id: Optional[str] = None
    experiment_id: Optional[str] = None
    priority: int


class UnitIn(BaseModel):
    name: str
    business_class: str = "for_profit"
    stage: str = "IDEA"
    bot_owner: Optional[str] = "businessbuilder"
    next_action: Optional[str] = None
    opportunity_id: Optional[str] = None
    project_id: Optional[str] = None
    kill_date: Optional[str] = None  # ISO date or datetime; not P&L


class UnitPatch(BaseModel):
    """Businessbot-safe unit updates — no revenue/cost/mrr/customers."""
    stage: Optional[str] = None
    next_action: Optional[str] = None
    kill_date: Optional[str] = None
    bot_owner: Optional[str] = None
    name: Optional[str] = None


class CycleIn(BaseModel):
    query: Optional[str] = None
    use_mock_search: bool = True


class MemoryIn(BaseModel):
    text: str
    memory_class: str = "semantic"
    source: str = "human"
    confidence: float = 0.9


def setup_os_routes() -> APIRouter:
    router = APIRouter(prefix="/api/os", tags=["autonomous-os"])

    def session():
        return SessionLocal()

    @router.get("/health")
    def health(request: Request):
        owner = _owner(request)
        db = session()
        try:
            seed_v1(db, owner)
            rt = runtime_view(db, owner)
            pending = db.query(OsApprovalRequest).filter(OsApprovalRequest.status == "pending")
            if owner:
                pending = pending.filter(OsApprovalRequest.owner == owner)
            n_opp = db.query(OsOpportunity)
            n_exp = db.query(OsExperiment)
            n_evt = db.query(OsEvent)
            if owner:
                n_opp = n_opp.filter(OsOpportunity.owner == owner)
                n_exp = n_exp.filter(OsExperiment.owner == owner)
                n_evt = n_evt.filter(OsEvent.owner == owner)
            db.commit()
            return {
                "ok": True,
                "status": rt.status,
                "stopped": rt.stopped,
                "paused": rt.paused,
                "autonomy_level": rt.autonomy_level,
                "autonomy_ceiling": rt.autonomy_ceiling,
                "cycle_count": rt.cycle_count,
                "last_cycle_id": rt.last_cycle_id,
                "pending_approvals": pending.count(),
                "opportunities": n_opp.count(),
                "experiments": n_exp.count(),
                "events": n_evt.count(),
                "active_mission_id": rt.active_mission_id,
            }
        finally:
            db.close()

    @router.post("/start")
    def cmd_start(request: Request):
        owner = _owner(request)
        db = session()
        try:
            seed_v1(db, owner)
            set_stopped(db, owner, actor=ACTOR_HUMAN, stopped=False)
            set_paused(db, owner, actor=ACTOR_HUMAN, paused=False)
            try:
                from autonomy.scheduler import ensure_os_schedules
                ensure_os_schedules(owner)
            except Exception:
                pass
            db.commit()
            return {"ok": True, "status": "running"}
        finally:
            db.close()

    @router.post("/stop")
    def cmd_stop(request: Request):
        owner = _owner(request)
        db = session()
        try:
            set_stopped(db, owner, actor=ACTOR_HUMAN, stopped=True)
            db.commit()
            return {"ok": True, "status": "stopped", "stopped": True}
        finally:
            db.close()

    @router.post("/pause")
    def cmd_pause(request: Request):
        owner = _owner(request)
        db = session()
        try:
            set_paused(db, owner, actor=ACTOR_HUMAN, paused=True)
            db.commit()
            return {"ok": True, "status": "paused"}
        finally:
            db.close()

    @router.post("/autonomy-level")
    def cmd_autonomy(body: AutonomyIn, request: Request):
        owner = _owner(request)
        db = session()
        try:
            change_autonomy_level(db, owner, new_level=body.level, actor=ACTOR_HUMAN, reason=body.reason)
            db.commit()
            rt = runtime_view(db, owner)
            return {"ok": True, "autonomy_level": rt.autonomy_level}
        except AutonomyChangeError as e:
            raise HTTPException(400, str(e))
        finally:
            db.close()

    @router.get("/missions")
    def list_missions(request: Request):
        owner = _owner(request)
        db = session()
        try:
            q = db.query(OsMission)
            if owner:
                q = q.filter(OsMission.owner == owner)
            return [_mission_dict(m) for m in q.order_by(OsMission.created_at.desc()).all()]
        finally:
            db.close()

    @router.post("/missions")
    def create_mission(body: MissionIn, request: Request):
        owner = _owner(request)
        db = session()
        try:
            m = OsMission(
                owner=owner,
                title=body.title,
                statement=body.statement,
                values_json=_dumps_maybe(body.values),
                constraints_json=_dumps_maybe(body.constraints),
                forbidden_actions_json=_dumps_maybe(body.forbidden_actions),
                strategy=body.strategy,
                risk_appetite=body.risk_appetite,
                is_active=True,
            )
            db.add(m)
            db.flush()
            rt = get_or_create_runtime(db, owner)
            rt.active_mission_id = m.id
            audit(db, owner=owner, actor=ACTOR_HUMAN, command="CHANGE_MISSION", target_type="mission", target_id=m.id)
            db.commit()
            return _mission_dict(m)
        finally:
            db.close()

    @router.put("/missions/{mission_id}")
    def update_mission(mission_id: str, body: MissionPatch, request: Request):
        owner = _owner(request)
        db = session()
        try:
            m = _get_owned(db, OsMission, mission_id, owner)
            if body.title is not None:
                m.title = body.title
            if body.statement is not None:
                m.statement = body.statement
            if body.values is not None:
                m.values_json = _dumps_maybe(body.values)
            if body.constraints is not None:
                m.constraints_json = _dumps_maybe(body.constraints)
            if body.forbidden_actions is not None:
                m.forbidden_actions_json = _dumps_maybe(body.forbidden_actions)
            if body.strategy is not None:
                m.strategy = body.strategy
            if body.risk_appetite is not None:
                m.risk_appetite = body.risk_appetite
            if body.is_active is not None:
                m.is_active = body.is_active
            audit(db, owner=owner, actor=ACTOR_HUMAN, command="CHANGE_MISSION", target_type="mission", target_id=m.id)
            db.commit()
            return _mission_dict(m)
        finally:
            db.close()

    @router.get("/projects")
    def list_projects(request: Request):
        owner = _owner(request)
        db = session()
        try:
            q = db.query(OsProject)
            if owner:
                q = q.filter(OsProject.owner == owner)
            return [
                {
                    "id": p.id, "name": p.name, "authorised": p.authorised,
                    "status": p.status, "risk_limit": p.risk_limit, "mission_id": p.mission_id,
                }
                for p in q.all()
            ]
        finally:
            db.close()

    @router.get("/opportunities")
    def list_opps(request: Request):
        owner = _owner(request)
        db = session()
        try:
            q = db.query(OsOpportunity)
            if owner:
                q = q.filter(OsOpportunity.owner == owner)
            rows = q.order_by(OsOpportunity.rank.asc().nullslast()).all()
            return [opp_to_dict(r) for r in rows]
        finally:
            db.close()

    @router.get("/experiments")
    def list_exps(request: Request):
        owner = _owner(request)
        db = session()
        try:
            q = db.query(OsExperiment)
            if owner:
                q = q.filter(OsExperiment.owner == owner)
            return [_exp_dict(e) for e in q.order_by(OsExperiment.created_at.desc()).all()]
        finally:
            db.close()

    @router.get("/hypotheses")
    def list_hyps(request: Request):
        owner = _owner(request)
        db = session()
        try:
            q = db.query(OsHypothesis)
            if owner:
                q = q.filter(OsHypothesis.owner == owner)
            return [
                {
                    "id": h.id, "statement": h.statement, "success_metric": h.success_metric,
                    "failure_condition": h.failure_condition, "status": h.status,
                    "budget_limit_cents": h.budget_limit_cents, "time_limit_seconds": h.time_limit_seconds,
                    "thesis": h.thesis, "counterargument": h.counterargument,
                    "uncertainty": h.uncertainty,
                }
                for h in q.all()
            ]
        finally:
            db.close()

    @router.get("/actions")
    def list_actions(request: Request):
        owner = _owner(request)
        db = session()
        try:
            q = db.query(OsAction)
            if owner:
                q = q.filter(OsAction.owner == owner)
            return [_action_dict(a) for a in q.order_by(OsAction.created_at.desc()).limit(100).all()]
        finally:
            db.close()

    @router.post("/actions/{action_id}/rollback")
    def cmd_rollback(action_id: str, request: Request):
        owner = _owner(request)
        db = session()
        try:
            a = rollback_action(db, owner, action_id, actor=ACTOR_HUMAN)
            db.commit()
            return _action_dict(a)
        except ApprovalError as e:
            raise HTTPException(400, str(e))
        finally:
            db.close()

    @router.get("/agents")
    def list_agents(request: Request):
        owner = _owner(request)
        db = session()
        try:
            q = db.query(OsAgent)
            if owner:
                q = q.filter(OsAgent.owner == owner)
            return [
                {"id": a.id, "name": a.name, "role": a.role, "status": a.status, "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None}
                for a in q.all()
            ]
        finally:
            db.close()

    @router.get("/memory")
    def list_mem(request: Request, memory_class: Optional[str] = None):
        owner = _owner(request)
        db = session()
        try:
            return [knowledge_to_dict(k) for k in list_knowledge(db, owner, memory_class=memory_class)]
        finally:
            db.close()

    @router.post("/memory")
    def add_mem(body: MemoryIn, request: Request):
        owner = _owner(request)
        db = session()
        try:
            row = remember(
                db, owner, text=body.text, memory_class=body.memory_class,
                source=body.source, confidence=body.confidence, actor=ACTOR_HUMAN,
            )
            db.commit()
            return knowledge_to_dict(row)
        finally:
            db.close()

    @router.get("/metrics")
    def list_metrics(request: Request):
        owner = _owner(request)
        db = session()
        try:
            q = db.query(OsMetric)
            if owner:
                q = q.filter(OsMetric.owner == owner)
            rows = q.order_by(OsMetric.recorded_at.desc()).limit(200).all()
            return [
                {"id": m.id, "name": m.name, "value": m.value, "unit": m.unit,
                 "recorded_at": m.recorded_at.isoformat() if m.recorded_at else None}
                for m in rows
            ]
        finally:
            db.close()

    @router.get("/events")
    def list_events(request: Request, limit: int = 100):
        owner = _owner(request)
        db = session()
        try:
            q = db.query(OsEvent)
            if owner:
                q = q.filter(OsEvent.owner == owner)
            rows = q.order_by(OsEvent.timestamp.desc()).limit(min(limit, 500)).all()
            return [event_to_dict(e) for e in rows]
        finally:
            db.close()

    @router.get("/budgets")
    def list_budgets(request: Request):
        owner = _owner(request)
        db = session()
        try:
            seed_v1(db, owner)
            db.commit()
            q = db.query(OsBudget)
            if owner:
                q = q.filter(OsBudget.owner == owner)
            return [_budget_dict(b) for b in q.all()]
        finally:
            db.close()

    @router.put("/budgets/{budget_id}")
    def patch_budget(budget_id: str, body: BudgetPatch, request: Request):
        owner = _owner(request)
        db = session()
        try:
            b = _get_owned(db, OsBudget, budget_id, owner)
            update_budget(
                db, b, actor=ACTOR_HUMAN,
                limit_cents=body.limit_cents,
                autonomous_limit_cents=body.autonomous_limit_cents,
                authorised=body.authorised,
            )
            db.commit()
            return _budget_dict(b)
        finally:
            db.close()

    @router.get("/approvals")
    def list_approvals(request: Request):
        owner = _owner(request)
        db = session()
        try:
            q = db.query(OsApprovalRequest)
            if owner:
                q = q.filter(OsApprovalRequest.owner == owner)
            return [_approval_dict(a) for a in q.order_by(OsApprovalRequest.created_at.desc()).all()]
        finally:
            db.close()

    @router.post("/approvals/{approval_id}/approve")
    def cmd_approve(approval_id: str, body: ApprovalIn, request: Request):
        owner = _owner(request)
        db = session()
        try:
            row = resolve_approval(db, owner, approval_id, actor=ACTOR_HUMAN, decision="approve", note=body.note)
            executed = execute_approved_action(db, owner, row, actor=ACTOR_HUMAN)
            db.commit()
            return {"ok": True, "approval": _approval_dict(row), "execution": executed}
        except ApprovalError as e:
            raise HTTPException(400, str(e))
        finally:
            db.close()

    @router.post("/approvals/{approval_id}/reject")
    def cmd_reject(approval_id: str, body: ApprovalIn, request: Request):
        owner = _owner(request)
        db = session()
        try:
            row = resolve_approval(db, owner, approval_id, actor=ACTOR_HUMAN, decision="reject", note=body.note)
            db.commit()
            return {"ok": True, "approval": _approval_dict(row)}
        except ApprovalError as e:
            raise HTTPException(400, str(e))
        finally:
            db.close()

    @router.post("/approvals/{approval_id}/override")
    def cmd_override(approval_id: str, body: OverrideIn, request: Request):
        owner = _owner(request)
        db = session()
        try:
            row = resolve_approval(db, owner, approval_id, actor=ACTOR_HUMAN, decision="override", note=body.note)
            executed = execute_approved_action(db, owner, row, actor=ACTOR_HUMAN)
            db.commit()
            return {"ok": True, "approval": _approval_dict(row), "execution": executed}
        except ApprovalError as e:
            raise HTTPException(400, str(e))
        finally:
            db.close()

    @router.get("/decisions")
    def list_decisions(request: Request):
        owner = _owner(request)
        db = session()
        try:
            q = db.query(OsDecision)
            if owner:
                q = q.filter(OsDecision.owner == owner)
            return [_decision_dict(d) for d in q.order_by(OsDecision.created_at.desc()).all()]
        finally:
            db.close()

    @router.get("/next-action")
    def get_next(request: Request):
        owner = _owner(request)
        db = session()
        try:
            seed_v1(db, owner)
            db.commit()
            return next_best_action(db, owner)
        finally:
            db.close()

    @router.post("/cycle")
    def post_cycle(body: CycleIn, request: Request):
        owner = _owner(request)
        db = session()
        try:
            seed_v1(db, owner)
            summary = run_cycle(
                db, owner, actor=ACTOR_HUMAN, query=body.query,
                allow_mock_search=body.use_mock_search,
            )
            db.commit()
            return summary
        finally:
            db.close()

    @router.post("/prioritise")
    def cmd_prioritise(body: PrioritiseIn, request: Request):
        owner = _owner(request)
        db = session()
        try:
            if body.opportunity_id:
                row = _get_owned(db, OsOpportunity, body.opportunity_id, owner)
                row.priority = body.priority
            elif body.experiment_id:
                row = _get_owned(db, OsExperiment, body.experiment_id, owner)
                row.priority = body.priority
            else:
                raise HTTPException(400, "opportunity_id or experiment_id required")
            audit(db, owner=owner, actor=ACTOR_HUMAN, command="PRIORITISE",
                  target_type="opportunity" if body.opportunity_id else "experiment",
                  target_id=row.id, detail={"priority": body.priority})
            db.commit()
            return {"ok": True, "id": row.id, "priority": body.priority}
        finally:
            db.close()

    @router.get("/tools")
    def list_tools(request: Request):
        _owner(request)
        return REGISTRY.list_specs()

    @router.get("/scoring-weights")
    def get_weights(request: Request):
        owner = _owner(request)
        db = session()
        try:
            rt = runtime_view(db, owner)
            w = dict(DEFAULT_SCORING_WEIGHTS)
            if rt.scoring_weights_json:
                try:
                    w.update(json.loads(rt.scoring_weights_json))
                except (TypeError, ValueError):
                    pass
            return w
        finally:
            db.close()

    @router.put("/scoring-weights")
    def put_weights(body: dict, request: Request):
        owner = _owner(request)
        db = session()
        try:
            rt_row = get_or_create_runtime(db, owner)
            cleaned = {k: float(v) for k, v in body.items() if k in DEFAULT_SCORING_WEIGHTS}
            merged = dict(DEFAULT_SCORING_WEIGHTS)
            merged.update(cleaned)
            rt_row.scoring_weights_json = json.dumps(merged)
            db.commit()
            return merged
        finally:
            db.close()

    @router.get("/dashboard")
    def dashboard(request: Request):
        """Single payload for the CEO dashboard. All numbers from real rows."""
        owner = _owner(request)
        db = session()
        try:
            seed_v1(db, owner)
            db.commit()
            health = health_payload(db, owner)
            opps = db.query(OsOpportunity)
            exps = db.query(OsExperiment)
            decisions = db.query(OsDecision).filter(OsDecision.requires_human.is_(True), OsDecision.status == "open")
            events = db.query(OsEvent)
            metrics = db.query(OsMetric)
            agents = db.query(OsAgent)
            if owner:
                opps = opps.filter(OsOpportunity.owner == owner)
                exps = exps.filter(OsExperiment.owner == owner)
                decisions = decisions.filter(OsDecision.owner == owner)
                events = events.filter(OsEvent.owner == owner)
                metrics = metrics.filter(OsMetric.owner == owner)
                agents = agents.filter(OsAgent.owner == owner)
            spent = 0
            for b in db.query(OsBudget).filter(OsBudget.scope == "treasury").all():
                if owner and b.owner and b.owner != owner:
                    continue
                spent += int(b.spent_cents or 0)
            return {
                "health": health,
                "costs": {"spent_cents": spent, "currency": "GBP", "note": "Tracked internal spend only — not Stripe/bank"},
                "agents": [{"name": a.name, "role": a.role, "status": a.status} for a in agents.all()],
                "opportunities": [opp_to_dict(o) for o in opps.order_by(OsOpportunity.rank.asc().nullslast()).limit(20).all()],
                "experiments": [_exp_dict(e) for e in exps.order_by(OsExperiment.created_at.desc()).limit(20).all()],
                "attention": [_decision_dict(d) for d in decisions.order_by(OsDecision.urgency.desc()).all()],
                "events": [event_to_dict(e) for e in events.order_by(OsEvent.timestamp.desc()).limit(30).all()],
                "metrics": [
                    {"name": m.name, "value": m.value, "unit": m.unit,
                     "recorded_at": m.recorded_at.isoformat() if m.recorded_at else None}
                    for m in metrics.order_by(OsMetric.recorded_at.desc()).limit(50).all()
                ],
                "next_action": next_best_action(db, owner),
                "tools": REGISTRY.list_specs(),
                "factory": build_factory_dashboard(db, owner),
            }
        finally:
            db.close()

    @router.get("/factory")
    def get_factory(request: Request):
        """Business Factory command desktop payload."""
        owner = _owner(request)
        db = session()
        try:
            seed_v1(db, owner)
            db.commit()
            return build_factory_dashboard(db, owner)
        finally:
            db.close()

    @router.get("/factory/units")
    def get_factory_units(request: Request):
        owner = _owner(request)
        db = session()
        try:
            seed_v1(db, owner)
            db.commit()
            return [unit_to_dict(u) for u in list_units(db, owner)]
        finally:
            db.close()

    @router.post("/factory/units")
    def post_factory_unit(body: UnitIn, request: Request):
        owner = _owner(request)
        db = session()
        try:
            seed_v1(db, owner)
            existing = find_unit_by_name(db, owner, body.name)
            if existing is not None:
                db.commit()
                return unit_to_dict(existing)
            if body.business_class not in FACTORY_CLASSES:
                raise HTTPException(400, f"business_class must be one of {FACTORY_CLASSES}")
            if body.stage not in FACTORY_STAGES:
                raise HTTPException(400, f"stage must be one of {FACTORY_STAGES}")
            try:
                kill = parse_kill_date(body.kill_date)
            except ValueError as e:
                raise HTTPException(400, str(e))
            row = create_unit(
                db, owner,
                name=body.name,
                business_class=body.business_class,
                stage=body.stage,
                bot_owner=body.bot_owner,
                next_action=body.next_action,
                opportunity_id=body.opportunity_id,
                project_id=body.project_id,
                kill_date=kill,
            )
            db.commit()
            return unit_to_dict(row)
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            db.close()

    @router.patch("/factory/units/{unit_id}")
    def patch_factory_unit(unit_id: str, body: UnitPatch, request: Request):
        owner = _owner(request)
        db = session()
        try:
            seed_v1(db, owner)
            row = get_unit_by_unit_id(db, owner, unit_id)
            if row is None:
                raise HTTPException(404, "not found")
            try:
                set_kill = "kill_date" in body.model_fields_set
                kill = parse_kill_date(body.kill_date) if set_kill else None
                update_unit(
                    db, row,
                    stage=body.stage,
                    next_action=body.next_action,
                    kill_date=kill,
                    set_kill_date=set_kill,
                    bot_owner=body.bot_owner,
                    name=body.name,
                )
            except ValueError as e:
                raise HTTPException(400, str(e))
            db.commit()
            return unit_to_dict(row)
        finally:
            db.close()

    @router.delete("/factory/units/{unit_id}")
    def delete_factory_unit(unit_id: str, request: Request):
        owner = _owner(request)
        db = session()
        try:
            seed_v1(db, owner)
            row = get_unit_by_unit_id(db, owner, unit_id)
            if row is None:
                raise HTTPException(404, "not found")
            delete_unit(db, row)
            db.commit()
            return {"ok": True, "unit_id": unit_id}
        finally:
            db.close()


    return router


def health_payload(db, owner: str) -> dict:
    rt = runtime_view(db, owner)
    pending = db.query(OsApprovalRequest).filter(OsApprovalRequest.status == "pending")
    if owner:
        pending = pending.filter(OsApprovalRequest.owner == owner)
    return {
        "ok": True,
        "status": rt.status,
        "stopped": rt.stopped,
        "paused": rt.paused,
        "autonomy_level": rt.autonomy_level,
        "cycle_count": rt.cycle_count,
        "pending_approvals": pending.count(),
        "active_mission_id": rt.active_mission_id,
    }


def _get_owned(db, model, id_, owner: str):
    row = db.query(model).filter(model.id == id_).first()
    if row is None:
        raise HTTPException(404, "not found")
    if owner and getattr(row, "owner", None) and row.owner != owner:
        raise HTTPException(404, "not found")
    return row


def _mission_dict(m: OsMission) -> dict:
    def _load(s):
        if not s:
            return None
        try:
            return json.loads(s)
        except (TypeError, ValueError):
            return s
    return {
        "id": m.id, "title": m.title, "statement": m.statement,
        "values": _load(m.values_json), "constraints": _load(m.constraints_json),
        "forbidden_actions": _load(m.forbidden_actions_json), "strategy": m.strategy,
        "risk_appetite": m.risk_appetite, "status": m.status, "is_active": m.is_active,
    }


def _exp_dict(e: OsExperiment) -> dict:
    result = None
    if e.result_json:
        try:
            result = json.loads(e.result_json)
        except (TypeError, ValueError):
            result = e.result_json
    return {
        "id": e.id, "name": e.name, "status": e.status, "success_metric": e.success_metric,
        "failure_condition": e.failure_condition, "time_limit_seconds": e.time_limit_seconds,
        "budget_limit_cents": e.budget_limit_cents, "spent_cents": e.spent_cents,
        "reversible": e.reversible, "authorised": e.authorised, "result": result,
        "next_action": e.next_action, "priority": e.priority,
    }


def _action_dict(a: OsAction) -> dict:
    return {
        "id": a.id, "name": a.name, "tool_name": a.tool_name, "status": a.status,
        "cost_cents": a.cost_cents, "risk_level": a.risk_level, "reversible": a.reversible,
        "authorization": a.authorization, "governor_reason": a.governor_reason,
        "rolled_back": a.rolled_back, "error_class": a.error_class,
    }


def _budget_dict(b: OsBudget) -> dict:
    return {
        "id": b.id, "scope": b.scope, "scope_id": b.scope_id,
        "currency": getattr(b, "currency", None) or "GBP",
        "limit_cents": b.limit_cents, "spent_cents": b.spent_cents,
        "autonomous_limit_cents": b.autonomous_limit_cents, "authorised": b.authorised,
        "remaining_cents": int(b.limit_cents or 0) - int(b.spent_cents or 0),
    }


def _approval_dict(a: OsApprovalRequest) -> dict:
    return {
        "id": a.id, "status": a.status, "reason": a.reason, "action_id": a.action_id,
        "decision_id": a.decision_id, "resolved_by": a.resolved_by,
        "resolution_note": a.resolution_note,
    }


def _decision_dict(d: OsDecision) -> dict:
    def _load(s):
        if not s:
            return None
        try:
            return json.loads(s)
        except (TypeError, ValueError):
            return s
    return {
        "id": d.id, "what": d.what, "why": d.why, "evidence": d.evidence,
        "actor": d.actor, "authority": d.authority, "cost_cents": d.cost_cents,
        "expected": d.expected, "actual": d.actual, "alternatives": _load(d.alternatives_json),
        "revisit_conditions": d.revisit_conditions, "thesis": d.thesis,
        "counterargument": d.counterargument, "assumptions": _load(d.assumptions_json),
        "missing_evidence": d.missing_evidence, "uncertainty": d.uncertainty,
        "status": d.status, "requires_human": d.requires_human,
        "urgency": d.urgency, "importance": d.importance,
        "options": _load(d.options_json), "recommendation": d.recommendation,
        "consequence_of_waiting": d.consequence_of_waiting,
        "related_approval_id": d.related_approval_id,
    }
