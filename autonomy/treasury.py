"""Treasury: TREASURY → PROJECT → EXPERIMENT → ACTION."""

from __future__ import annotations

from typing import Any, Dict, Optional

from autonomy.constants import (
    BUDGET_SCOPE_EXPERIMENT,
    BUDGET_SCOPE_PROJECT,
    BUDGET_SCOPE_TREASURY,
)
from autonomy.events import audit, emit
from autonomy.models import OsBudget, OsTransaction


class BudgetDenial(Exception):
    pass


def remaining(budget: OsBudget) -> int:
    return int(budget.limit_cents or 0) - int(budget.spent_cents or 0)


def get_budget(db, owner: Optional[str], scope: str, scope_id: Optional[str] = None) -> Optional[OsBudget]:
    q = db.query(OsBudget).filter(OsBudget.scope == scope)
    if owner:
        q = q.filter((OsBudget.owner == owner) | (OsBudget.owner.is_(None)))
    else:
        q = q.filter(OsBudget.owner.is_(None))
    if scope_id:
        q = q.filter(OsBudget.scope_id == scope_id)
    else:
        q = q.filter(OsBudget.scope_id.is_(None))
    return q.order_by(OsBudget.created_at.desc()).first()


def ensure_treasury(db, owner: Optional[str], *, limit_cents: int = 0, autonomous_limit_cents: int = 0) -> OsBudget:
    row = get_budget(db, owner, BUDGET_SCOPE_TREASURY, None)
    if row is None:
        row = OsBudget(
            owner=owner,
            scope=BUDGET_SCOPE_TREASURY,
            scope_id=None,
            limit_cents=int(limit_cents),
            spent_cents=0,
            autonomous_limit_cents=int(autonomous_limit_cents),
            authorised=True,
        )
        db.add(row)
        db.flush()
    return row


def ensure_child_budget(
    db,
    owner: Optional[str],
    *,
    scope: str,
    scope_id: str,
    limit_cents: int,
    autonomous_limit_cents: int,
) -> OsBudget:
    row = get_budget(db, owner, scope, scope_id)
    if row is None:
        row = OsBudget(
            owner=owner,
            scope=scope,
            scope_id=scope_id,
            limit_cents=int(limit_cents),
            spent_cents=0,
            autonomous_limit_cents=int(autonomous_limit_cents),
            authorised=True,
        )
        db.add(row)
        db.flush()
    return row


def check_spend(
    db,
    *,
    owner: Optional[str],
    cost_cents: int,
    project_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Raise BudgetDenial unless every layer can cover ``cost_cents``.

    Zero-cost actions always pass (search).
    """
    cost = int(cost_cents or 0)
    if cost < 0:
        raise BudgetDenial("negative cost is invalid")

    treasury = ensure_treasury(db, owner)
    chain = [("treasury", treasury)]

    if project_id:
        proj = get_budget(db, owner, BUDGET_SCOPE_PROJECT, project_id)
        if proj is None:
            raise BudgetDenial("project has no budget")
        chain.append(("project", proj))
    if experiment_id:
        exp = get_budget(db, owner, BUDGET_SCOPE_EXPERIMENT, experiment_id)
        if exp is None:
            raise BudgetDenial("experiment has no budget")
        chain.append(("experiment", exp))

    info = {"cost_cents": cost, "layers": []}
    for name, b in chain:
        if not b.authorised:
            raise BudgetDenial(f"{name} budget is not authorised")
        if cost > int(b.autonomous_limit_cents or 0):
            raise BudgetDenial(
                f"cost {cost} exceeds {name} autonomous_limit {b.autonomous_limit_cents}"
            )
        rem = remaining(b)
        if cost > rem:
            raise BudgetDenial(f"insufficient {name} budget: need {cost}, remaining {rem}")
        info["layers"].append({
            "scope": name,
            "id": b.id,
            "remaining_cents": rem,
            "autonomous_limit_cents": b.autonomous_limit_cents,
        })
    return info


def record_spend(
    db,
    *,
    owner: Optional[str],
    cost_cents: int,
    action_id: Optional[str],
    project_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    memo: str = "",
) -> None:
    cost = int(cost_cents or 0)
    if cost == 0:
        return
    check_spend(db, owner=owner, cost_cents=cost, project_id=project_id, experiment_id=experiment_id)
    budgets = [ensure_treasury(db, owner)]
    if project_id:
        b = get_budget(db, owner, BUDGET_SCOPE_PROJECT, project_id)
        if b:
            budgets.append(b)
    if experiment_id:
        b = get_budget(db, owner, BUDGET_SCOPE_EXPERIMENT, experiment_id)
        if b:
            budgets.append(b)
    for b in budgets:
        b.spent_cents = int(b.spent_cents or 0) + cost
        tx = OsTransaction(
            owner=owner,
            budget_id=b.id,
            action_id=action_id,
            amount_cents=cost,
            memo=memo or b.scope,
        )
        db.add(tx)
    db.flush()


def update_budget(
    db,
    budget: OsBudget,
    *,
    actor: str,
    limit_cents: Optional[int] = None,
    autonomous_limit_cents: Optional[int] = None,
    authorised: Optional[bool] = None,
) -> OsBudget:
    if limit_cents is not None:
        budget.limit_cents = int(limit_cents)
    if autonomous_limit_cents is not None:
        budget.autonomous_limit_cents = int(autonomous_limit_cents)
    if authorised is not None:
        budget.authorised = bool(authorised)
    db.flush()
    emit(
        db,
        owner=budget.owner,
        event_type="budget_changed",
        actor=actor,
        status="ok",
        extra={
            "budget_id": budget.id,
            "scope": budget.scope,
            "limit_cents": budget.limit_cents,
            "autonomous_limit_cents": budget.autonomous_limit_cents,
        },
    )
    audit(
        db,
        owner=budget.owner,
        actor=actor,
        command="CHANGE_BUDGET",
        target_type="budget",
        target_id=budget.id,
        detail={"limit_cents": budget.limit_cents, "autonomous_limit_cents": budget.autonomous_limit_cents},
    )
    return budget
