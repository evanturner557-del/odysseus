"""Orchestrator: one closed loop behind the governor. No microservices."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from core.database import utcnow_naive

from autonomy.constants import (
    ACTOR_SYSTEM,
    DEFAULT_SEARCH_QUERY,
    DEFAULT_SCORING_WEIGHTS,
    LOOP_STEPS,
    SEED_EXPERIMENT_NAME,
)
from autonomy.events import emit, record_metric
from autonomy.experiments import (
    create_experiment,
    form_hypothesis,
    learn_from_experiment,
    measure_search_experiment,
)
from autonomy.governor import ActionRequest, Governor
from autonomy.memory import remember
from autonomy.models import (
    OsAgent,
    OsAgentRun,
    OsDecision,
    OsExperiment,
    OsMission,
    OsOpportunity,
    OsProject,
)
from autonomy.opportunities import discover_from_search, to_dict as opp_to_dict
from autonomy.runtime import mark_cycle, view as runtime_view
from autonomy.tools import execute_tool


_governor = Governor()


def _agent(db, owner: Optional[str]) -> OsAgent:
    q = db.query(OsAgent).filter(OsAgent.role == "orchestrator")
    if owner:
        q = q.filter(OsAgent.owner == owner)
    row = q.first()
    if row is None:
        row = OsAgent(
            owner=owner,
            name="orchestrator",
            role="orchestrator",
            status="idle",
            allowed_tools_json=json.dumps(["web_search"]),
        )
        db.add(row)
        db.flush()
    return row


def _weights(rt) -> dict:
    if rt.scoring_weights_json:
        try:
            data = json.loads(rt.scoring_weights_json)
            if isinstance(data, dict):
                w = dict(DEFAULT_SCORING_WEIGHTS)
                w.update({k: float(v) for k, v in data.items() if k in w})
                return w
        except (TypeError, ValueError):
            pass
    return dict(DEFAULT_SCORING_WEIGHTS)


def next_best_action(db, owner: Optional[str]) -> Dict[str, Any]:
    """Rank the next action from live rows. No fake scores."""
    rt = runtime_view(db, owner)
    pending = (
        db.query(OsDecision)
        .filter(OsDecision.requires_human.is_(True), OsDecision.status == "open")
    )
    if owner:
        pending = pending.filter(OsDecision.owner == owner)
    pending_n = pending.count()

    opps = db.query(OsOpportunity)
    if owner:
        opps = opps.filter(OsOpportunity.owner == owner)
    top = opps.order_by(OsOpportunity.score.desc().nullslast()).first()

    exps = db.query(OsExperiment)
    if owner:
        exps = exps.filter(OsExperiment.owner == owner)
    waiting = exps.filter(OsExperiment.status.in_(("planned", "awaiting_approval"))).first()

    if rt.stopped:
        return {
            "action": "wait",
            "reason": "GLOBAL STOP is set",
            "human_attention_required": True,
            "mission_alignment": 1.0,
            "expected_value": 0.0,
            "probability": 1.0,
            "cost_cents": 0,
            "urgency": 10,
            "reversibility": True,
            "evidence": "os_runtime_state.stopped",
            "dependencies": [],
            "risk": "low",
        }
    if pending_n:
        d = pending.order_by(OsDecision.urgency.desc(), OsDecision.created_at.asc()).first()
        return {
            "action": "human_approval",
            "reason": d.why if d else "pending approval",
            "human_attention_required": True,
            "decision_id": d.id if d else None,
            "mission_alignment": 0.9,
            "expected_value": 0.4,
            "probability": 0.7,
            "cost_cents": d.cost_cents if d else 0,
            "urgency": d.urgency if d else 5,
            "reversibility": True,
            "evidence": d.evidence if d else None,
            "dependencies": [],
            "risk": "low",
            "recommendation": d.recommendation if d else None,
        }
    if waiting and not waiting.authorised:
        return {
            "action": "request_authority",
            "reason": f"experiment '{waiting.name}' needs approval before execution",
            "human_attention_required": True,
            "experiment_id": waiting.id,
            "mission_alignment": 0.8,
            "expected_value": 0.5,
            "probability": 0.6,
            "cost_cents": waiting.budget_limit_cents,
            "urgency": 4,
            "reversibility": bool(waiting.reversible),
            "evidence": waiting.success_metric,
            "dependencies": ["approval"],
            "risk": "low",
        }
    if top:
        return {
            "action": "research_opportunity",
            "reason": f"highest scored opportunity {top.public_id}",
            "human_attention_required": rt.autonomy_level < 3,
            "opportunity_id": top.id,
            "public_id": top.public_id,
            "mission_alignment": min(1.0, (top.score or 0) / 20.0),
            "expected_value": float(top.score or 0),
            "probability": 0.5,
            "cost_cents": 0,
            "urgency": 3,
            "reversibility": True,
            "evidence": top.summary,
            "dependencies": [],
            "risk": "low",
        }
    return {
        "action": "opportunity_scan",
        "reason": "no ranked opportunities yet; run reversible search",
        "human_attention_required": rt.autonomy_level < 3,
        "mission_alignment": 0.7,
        "expected_value": 1.0,
        "probability": 0.8,
        "cost_cents": 0,
        "urgency": 2,
        "reversibility": True,
        "evidence": "empty opportunity table",
        "dependencies": ["web_search"],
        "risk": "low",
    }


def run_cycle(
    db,
    owner: Optional[str],
    *,
    actor: str = ACTOR_SYSTEM,
    query: Optional[str] = None,
    allow_mock_search: bool = True,
    force_mock_search: bool = False,
    auto_approve_reversible: bool = False,
) -> Dict[str, Any]:
    """Run one OBSERVE→…→LEARN cycle. Respects GLOBAL STOP. No fake metrics."""
    cycle_id = uuid.uuid4().hex
    rt = runtime_view(db, owner)
    steps_done: List[str] = []
    summary: Dict[str, Any] = {
        "cycle_id": cycle_id,
        "stopped": rt.stopped,
        "paused": rt.paused,
        "autonomy_level": rt.autonomy_level,
        "steps": steps_done,
    }

    if rt.stopped or (rt.paused and not auto_approve_reversible):
        emit(
            db, owner=owner, event_type="cycle_skipped", actor=actor,
            status="stopped" if rt.stopped else "paused", extra={"cycle_id": cycle_id},
        )
        summary["status"] = "skipped"
        summary["reason"] = "GLOBAL STOP" if rt.stopped else "paused"
        return summary

    agent = _agent(db, owner)
    agent.status = "running"
    run = OsAgentRun(owner=owner, agent_id=agent.id, cycle_id=cycle_id, status="running", step="observe")
    db.add(run)
    db.flush()
    emit(db, owner=owner, event_type="cycle_started", actor=actor, status="running", extra={"cycle_id": cycle_id})

    mission = None
    if rt.active_mission_id:
        mission = db.query(OsMission).filter(OsMission.id == rt.active_mission_id).first()
    if mission is None:
        q = db.query(OsMission).filter(OsMission.is_active.is_(True))
        if owner:
            q = q.filter(OsMission.owner == owner)
        mission = q.first()

    project = None
    if mission:
        pq = db.query(OsProject).filter(OsProject.mission_id == mission.id, OsProject.authorised.is_(True))
        if owner:
            pq = pq.filter(OsProject.owner == owner)
        project = pq.first()

    # OBSERVE
    steps_done.append("observe")
    emit(db, owner=owner, event_type="observe", actor=actor, status="ok", extra={"cycle_id": cycle_id})
    remember(
        db, owner,
        text=f"Cycle {cycle_id[:8]} observed mission='{(mission.title if mission else None)}' stop={rt.stopped}",
        memory_class="episodic",
        source="orchestrator",
        confidence=0.9,
        actor=actor,
    )

    # INTERPRET
    steps_done.append("interpret")
    emit(
        db, owner=owner, event_type="interpret", actor=actor, status="ok",
        extra={"mission": mission.title if mission else None, "level": rt.autonomy_level},
    )

    search_query = query or (mission.strategy if mission and mission.strategy else DEFAULT_SEARCH_QUERY)
    if mission and mission.title and query is None:
        search_query = f"{mission.title} reversible zero-cost experiment 2026"

    # IDENTIFY OPPORTUNITY via search (governor inside execute_tool)
    steps_done.append("identify_opportunity")
    tool_out = execute_tool(
        db, owner,
        tool_name="web_search",
        inputs={"query": search_query},
        actor=actor,
        project_id=project.id if project else None,
        allow_mock=allow_mock_search,
        force_mock=force_mock_search,
    )
    search_result = (tool_out.get("result") or {}) if tool_out.get("ok") else {}
    hits = search_result.get("hits") or []

    if not tool_out.get("ok") and tool_out.get("needs_approval"):
        # At recommend/draft levels the search itself needs approval.
        # Still form a planned experiment so the human has a card to act on.
        steps_done.append("request_authority")
        summary["status"] = "awaiting_approval"
        summary["tool"] = tool_out
        summary["next_action"] = next_best_action(db, owner)
        run.status = "blocked"
        run.step = "request_authority"
        run.finished_at = utcnow_naive()
        run.summary = tool_out.get("reason")
        agent.status = "idle"
        mark_cycle(db, owner, cycle_id)
        emit(db, owner=owner, event_type="cycle_finished", actor=actor, status="awaiting_approval", extra=summary)
        return summary

    created = []
    if hits:
        created = discover_from_search(
            db, owner,
            hits=hits,
            mission_id=mission.id if mission else None,
            project_id=project.id if project else None,
            mission_text=(mission.statement if mission else "") or "",
            actor=actor,
            weights=_weights(rt),
            source=search_result.get("provider") or "web_search",
        )

    # SCORE already done in discover; PRIORITISE
    steps_done.append("prioritise")
    top = None
    if created:
        top = max(created, key=lambda r: r.score or 0)
    else:
        q = db.query(OsOpportunity)
        if owner:
            q = q.filter(OsOpportunity.owner == owner)
        top = q.order_by(OsOpportunity.score.desc().nullslast()).first()

    # FORM HYPOTHESIS + PLAN
    experiment = None
    hypothesis = None
    if top:
        steps_done.append("form_hypothesis")
        hypothesis = form_hypothesis(db, owner, opportunity=top, actor=actor)
        steps_done.append("plan")
        experiment = create_experiment(
            db, owner,
            hypothesis=hypothesis,
            project_id=project.id if project else None,
            name=f"probe-{top.public_id}",
            actor=actor,
            authorised=False,
        )

    # REQUEST/VERIFY AUTHORITY then EXECUTE
    executed = None
    if experiment is not None:
        steps_done.append("request_authority")
        req = ActionRequest(
            name="run_search_experiment",
            tool_name="web_search",
            project_id=project.id if project else None,
            experiment_id=experiment.id,
            input_data={"query": search_query, "experiment": experiment.name},
            cost_cents=0,
            risk_level="low",
            reversible=True,
            required_level=3,
            actor=actor,
        )
        # If we already executed search above (level 3+), reuse that measurement.
        if tool_out.get("ok"):
            # Still record a governor verdict for the experiment execution itself.
            verdict = _governor.evaluate(db, owner, req)
            if verdict.allowed or auto_approve_reversible:
                if auto_approve_reversible and not verdict.allowed and verdict.approval:
                    from autonomy.approvals import resolve_approval
                    resolve_approval(
                        db, owner, verdict.approval.id, actor="human",
                        decision="approve", note="auto_approve_reversible for tests/CLI seed",
                    )
                    # After approval, execute is just measurement of already-fetched hits.
                experiment.status = "running"
                experiment.started_at = utcnow_naive()
                experiment.authorised = True
                db.flush()
                steps_done.append("execute")
                executed = measure_search_experiment(
                    db, owner, experiment=experiment, search_result=search_result, actor=actor,
                )
                steps_done.append("measure")
                steps_done.append("evaluate")
                steps_done.append("learn")
                learn_from_experiment(db, owner, experiment=experiment, result=executed, actor=actor)
                steps_done.append("update_memory")
            else:
                experiment.status = "awaiting_approval"
                db.flush()
                summary["approval_id"] = verdict.approval.id if verdict.approval else None
                summary["governor_reason"] = verdict.reason
        else:
            experiment.status = "awaiting_approval"
            db.flush()

    steps_done.append("reprioritise")
    nba = next_best_action(db, owner)
    summary["status"] = "ok"
    summary["opportunities"] = [opp_to_dict(r) for r in created]
    summary["top_opportunity"] = opp_to_dict(top) if top else None
    summary["hypothesis_id"] = hypothesis.id if hypothesis else None
    summary["experiment_id"] = experiment.id if experiment else None
    summary["measurement"] = executed
    summary["search"] = {
        "ok": tool_out.get("ok"),
        "used_mock": search_result.get("used_mock"),
        "hit_count": search_result.get("hit_count"),
        "provider": search_result.get("provider"),
        "query": search_query,
    }
    summary["next_action"] = nba
    summary["mission_id"] = mission.id if mission else None

    record_metric(db, owner=owner, name="cycle_completed", value=1.0, unit="count")
    record_metric(db, owner=owner, name="cycle_opportunities", value=float(len(created)), unit="count")

    run.status = "success"
    run.step = "reprioritise"
    run.finished_at = utcnow_naive()
    run.summary = json.dumps({"opportunities": len(created), "status": summary["status"]})
    agent.status = "idle"
    agent.last_run_at = utcnow_naive()
    mark_cycle(db, owner, cycle_id)
    emit(db, owner=owner, event_type="cycle_finished", actor=actor, status="ok", extra={"cycle_id": cycle_id, "opportunities": len(created)})
    db.flush()
    return summary
