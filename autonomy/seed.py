"""Seed one mission, authorised project, zero-spend treasury, reversible search experiment."""

from __future__ import annotations

import json
from typing import Optional

from autonomy.constants import (
    ACTOR_HUMAN,
    BUDGET_SCOPE_PROJECT,
    DEFAULT_FORBIDDEN_ACTIONS,
    DEFAULT_SEARCH_QUERY,
    SEED_EXPERIMENT_NAME,
    SEED_MISSION_TITLE,
)
from autonomy.experiments import create_experiment, form_hypothesis
from autonomy.models import OsGoal, OsMission, OsOpportunity, OsPolicy, OsProject
from autonomy.opportunities import next_public_id
from autonomy.runtime import get_or_create_runtime
from autonomy.treasury import ensure_child_budget, ensure_treasury
from autonomy.factory import ensure_factory_bots


def seed_v1(db, owner: Optional[str]) -> dict:
    rt = get_or_create_runtime(db, owner)

    q = db.query(OsMission).filter(OsMission.is_active.is_(True))
    if owner:
        q = q.filter(OsMission.owner == owner)
    mission = q.first()
    if mission is None:
        mission = OsMission(
            owner=owner,
            title=SEED_MISSION_TITLE,
            statement=(
                "Discover low-risk, reversible, zero-spend experiments that improve "
                "how Odysseus works for this operator. Do not spend money, send mail, "
                "or act outside policy. Prefer search-based research and human approval."
            ),
            values_json=json.dumps(["human authority", "reversibility", "zero surprise spend", "untrusted-data hygiene"]),
            constraints_json=json.dumps(["no live spend", "no external posts", "no irreversible changes"]),
            forbidden_actions_json=json.dumps(list(DEFAULT_FORBIDDEN_ACTIONS)),
            strategy=DEFAULT_SEARCH_QUERY,
            risk_appetite="low",
            status="active",
            is_active=True,
        )
        db.add(mission)
        db.flush()
        db.add(OsGoal(
            owner=owner,
            mission_id=mission.id,
            title="Run a reversible search probe",
            description="Prove the loop: discover → research → score → hypothesis → authority → measure → learn.",
            metric="usable_search_hits",
            target=">=1",
            status="open",
            priority=1,
        ))

    rt.active_mission_id = mission.id

    pq = db.query(OsProject).filter(OsProject.mission_id == mission.id)
    if owner:
        pq = pq.filter(OsProject.owner == owner)
    project = pq.first()
    if project is None:
        project = OsProject(
            owner=owner,
            mission_id=mission.id,
            name="V1 reversible probes",
            description="Authorised in-system project for zero-cost search experiments.",
            authorised=True,
            status="active",
            risk_limit="low",
        )
        db.add(project)
        db.flush()

    treasury = ensure_treasury(db, owner, limit_cents=0, autonomous_limit_cents=0)
    proj_budget = ensure_child_budget(
        db, owner, scope=BUDGET_SCOPE_PROJECT, scope_id=project.id,
        limit_cents=0, autonomous_limit_cents=0,
    )

    pol = db.query(OsPolicy).filter(OsPolicy.name == "v1-search-only")
    if owner:
        pol = pol.filter((OsPolicy.owner == owner) | (OsPolicy.owner.is_(None)))
    if pol.first() is None:
        db.add(OsPolicy(
            owner=owner,
            name="v1-search-only",
            rule="Only web_search is autonomously executable, and only when autonomy >= 3 and GLOBAL STOP is clear.",
            allowed_tools_json=json.dumps(["web_search"]),
            max_risk="low",
            max_autonomy_level=4,
            allow_irreversible=False,
            enabled=True,
        ))

    oq = db.query(OsOpportunity).filter(OsOpportunity.title == "In-system reversible search probe")
    if owner:
        oq = oq.filter(OsOpportunity.owner == owner)
    opp = oq.first()
    if opp is None:
        opp = OsOpportunity(
            public_id=next_public_id(db, owner),
            owner=owner,
            mission_id=mission.id,
            project_id=project.id,
            title="In-system reversible search probe",
            summary="Seeded low-risk opportunity: use existing SearxNG/web search (or mock fallback) as the first real tool.",
            source="seed",
            status="ranked",
            verified=True,
            score=8.0,
            rank=1,
            scores_json=json.dumps({"strategic_fit": 9, "speed_to_experiment": 10, "capital": 0, "regulatory": 0}),
            untrusted=False,
            priority=1,
        )
        db.add(opp)
        db.flush()

    from autonomy.models import OsExperiment, OsHypothesis
    hq = db.query(OsHypothesis).filter(OsHypothesis.opportunity_id == opp.id)
    hyp = hq.first()
    if hyp is None:
        hyp = form_hypothesis(
            db, owner, opportunity=opp, actor=ACTOR_HUMAN,
            if_condition="we run the seeded web_search probe",
            then_outcome="we collect at least one untrusted source hit",
            because="search indexes or the mock fallback return structured hits",
        )
    eq = db.query(OsExperiment).filter(OsExperiment.name == SEED_EXPERIMENT_NAME)
    if owner:
        eq = eq.filter(OsExperiment.owner == owner)
    exp = eq.first()
    if exp is None:
        exp = create_experiment(
            db, owner, hypothesis=hyp, project_id=project.id,
            name=SEED_EXPERIMENT_NAME, actor=ACTOR_HUMAN, authorised=False,
        )

    factory_bots = ensure_factory_bots(db, owner)

    db.flush()
    return {
        "mission_id": mission.id,
        "project_id": project.id,
        "opportunity_id": opp.id,
        "opportunity_public_id": opp.public_id,
        "hypothesis_id": hyp.id,
        "experiment_id": exp.id,
        "treasury_id": treasury.id,
        "project_budget_id": proj_budget.id,
        "runtime_id": rt.id,
        "factory_bot_ids": [b.id for b in factory_bots],
    }
