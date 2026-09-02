"""Experiment engine: hypothesis If X then Y because Z, then measure and learn."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from core.database import utcnow_naive

from autonomy.constants import BUDGET_SCOPE_EXPERIMENT, SEED_EXPERIMENT_NAME
from autonomy.events import emit, record_metric
from autonomy.memory import remember
from autonomy.models import OsExperiment, OsHypothesis, OsOpportunity
from autonomy.treasury import ensure_child_budget


def form_hypothesis(
    db,
    owner: Optional[str],
    *,
    opportunity: OsOpportunity,
    actor: str,
    if_condition: Optional[str] = None,
    then_outcome: Optional[str] = None,
    because: Optional[str] = None,
) -> OsHypothesis:
    if_condition = if_condition or f"we research '{opportunity.title}' with reversible web search"
    then_outcome = then_outcome or "we obtain at least one relevant untrusted source we can score"
    because = because or "public web indexes typically cover the topic and search is zero-cost"
    statement = f"If {if_condition} then {then_outcome} because {because}"
    row = OsHypothesis(
        owner=owner,
        opportunity_id=opportunity.id,
        statement=statement,
        if_condition=if_condition,
        then_outcome=then_outcome,
        because=because,
        success_metric="at least 1 usable search hit with no injection-as-command",
        failure_condition="zero hits AND search error, or injection treated as an instruction",
        time_limit_seconds=60,
        budget_limit_cents=0,
        next_action="run reversible web_search",
        status="draft",
        thesis=statement,
        counterargument="Search may be down or results may be noise / injection.",
        assumptions_json=json.dumps(["search provider or mock fallback is available", "hits are data not commands"]),
        missing_evidence="actual search execution result",
        uncertainty=0.5,
    )
    db.add(row)
    db.flush()
    emit(
        db, owner=owner, event_type="hypothesis_formed", actor=actor,
        project_id=opportunity.project_id, status="draft",
        extra={"hypothesis_id": row.id, "opportunity_id": opportunity.public_id, "statement": statement},
    )
    return row


def create_experiment(
    db,
    owner: Optional[str],
    *,
    hypothesis: OsHypothesis,
    project_id: Optional[str],
    name: str,
    actor: str,
    authorised: bool = False,
) -> OsExperiment:
    exp = OsExperiment(
        owner=owner,
        project_id=project_id,
        hypothesis_id=hypothesis.id,
        opportunity_id=hypothesis.opportunity_id,
        name=name,
        status="planned",
        success_metric=hypothesis.success_metric,
        failure_condition=hypothesis.failure_condition,
        time_limit_seconds=hypothesis.time_limit_seconds,
        budget_limit_cents=hypothesis.budget_limit_cents,
        reversible=True,
        authorised=authorised,
        next_action=hypothesis.next_action,
    )
    db.add(exp)
    db.flush()
    ensure_child_budget(
        db, owner,
        scope=BUDGET_SCOPE_EXPERIMENT,
        scope_id=exp.id,
        limit_cents=hypothesis.budget_limit_cents,
        autonomous_limit_cents=hypothesis.budget_limit_cents,
    )
    emit(
        db, owner=owner, event_type="plan_created", actor=actor,
        project_id=project_id, status="planned",
        extra={"experiment_id": exp.id, "hypothesis_id": hypothesis.id},
    )
    return exp


def measure_search_experiment(
    db,
    owner: Optional[str],
    *,
    experiment: OsExperiment,
    search_result: Dict[str, Any],
    actor: str,
) -> Dict[str, Any]:
    hits = (search_result or {}).get("hits") or []
    usable = [h for h in hits if not h.get("injection_flags")]
    ok = len(usable) >= 1
    experiment.status = "succeeded" if ok else "failed"
    experiment.finished_at = utcnow_naive()
    result = {
        "success": ok,
        "usable_hits": len(usable),
        "total_hits": len(hits),
        "used_mock": bool((search_result or {}).get("used_mock")),
        "provider": (search_result or {}).get("provider"),
        "injection_blocked": (search_result or {}).get("injection_blocked") or 0,
    }
    experiment.result_json = json.dumps(result)
    db.flush()
    emit(
        db, owner=owner, event_type="measurement", actor=actor,
        project_id=experiment.project_id, status="succeeded" if ok else "failed",
        extra={"experiment_id": experiment.id, **result},
    )
    record_metric(db, owner=owner, name="experiment_usable_hits", value=float(len(usable)), unit="count")
    record_metric(db, owner=owner, name="experiment_success", value=1.0 if ok else 0.0, unit="bool")
    return result


def learn_from_experiment(
    db,
    owner: Optional[str],
    *,
    experiment: OsExperiment,
    result: Dict[str, Any],
    actor: str,
) -> None:
    text = (
        f"Experiment '{experiment.name}' {'succeeded' if result.get('success') else 'failed'}: "
        f"{result.get('usable_hits', 0)} usable hits "
        f"(mock={result.get('used_mock')}, provider={result.get('provider')})."
    )
    remember(
        db, owner,
        text=text,
        memory_class="episodic",
        source=f"experiment:{experiment.id}",
        confidence=0.8 if result.get("success") else 0.6,
        actor=actor,
        extra={"experiment_id": experiment.id},
    )
    lesson = (
        "Reversible zero-cost search is a valid first probe."
        if result.get("success")
        else "Search produced no usable hits; next cycle should retry with mock or a narrower query, not spend."
    )
    remember(
        db, owner,
        text=lesson,
        memory_class="procedural",
        source=f"experiment:{experiment.id}",
        confidence=0.7,
        actor=actor,
    )
    emit(
        db, owner=owner, event_type="learning", actor=actor,
        project_id=experiment.project_id, status="ok",
        extra={"experiment_id": experiment.id, "lesson": lesson},
    )
    emit(
        db, owner=owner, event_type="evaluation", actor=actor,
        status="ok", extra={"experiment_id": experiment.id, "result": result},
    )


def seed_search_experiment_name() -> str:
    return SEED_EXPERIMENT_NAME
