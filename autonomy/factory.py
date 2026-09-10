"""Business Factory command desktop — pipeline, HoldCo P&L, bots, approvals, feed.

Builds payloads from real autonomy rows. Does not invent Stripe/bank balances;
capital and unit finance fields stay at tracked zeros with honest labels until
the operator records figures.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from autonomy.constants import (
    FACTORY_APPROVAL_CATEGORIES,
    FACTORY_BOTS,
    FACTORY_CLASSES,
    FACTORY_STAGES,
)
from autonomy.events import event_to_dict
from autonomy.models import (
    OsAction,
    OsAgent,
    OsApprovalRequest,
    OsBusinessUnit,
    OsEvent,
)
from autonomy.treasury import ensure_treasury, remaining

CURRENCY_LABEL = "GBP"
CAPITAL_NOTE = "Internal tracked capital only — not linked to Stripe or bank"


def next_unit_id(db, owner: Optional[str]) -> str:
    year = datetime.utcnow().year
    prefix = f"UNIT-{year}-"
    q = db.query(OsBusinessUnit).filter(OsBusinessUnit.unit_id.like(f"{prefix}%"))
    if owner:
        q = q.filter((OsBusinessUnit.owner == owner) | (OsBusinessUnit.owner.is_(None)))
    n = q.count() + 1
    return f"{prefix}{n:06d}"


def margin_pct(revenue_gbp: float, cost_gbp: float) -> float:
    rev = float(revenue_gbp or 0.0)
    cost = float(cost_gbp or 0.0)
    if rev <= 0:
        return 0.0
    return round(((rev - cost) / rev) * 100.0, 2)


def unit_to_dict(row: OsBusinessUnit) -> Dict[str, Any]:
    """Metrics contract for Factory Command panels."""
    rev = float(row.revenue_gbp or 0.0)
    cost = float(row.cost_gbp or 0.0)
    payload = {
        "unit_id": row.unit_id,
        "class": row.business_class,
        "stage": row.stage,
        "revenue_gbp": rev,
        "cost_gbp": cost,
        "mrr_gbp": float(row.mrr_gbp or 0.0),
        "margin_pct": margin_pct(rev, cost),
        "customers": int(row.customers or 0),
        "conversations": int(row.conversations or 0),
        "next_action": row.next_action,
        "kill_date": row.kill_date.isoformat() if row.kill_date else None,
        "bot_owner": row.bot_owner,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        # Convenience (not part of strict metrics contract)
        "id": row.id,
        "name": row.name,
        "opportunity_id": row.opportunity_id,
        "project_id": row.project_id,
    }
    if row.business_class == "charity":
        payload["donors"] = int(row.customers or 0)
        payload["donor_mrr_gbp"] = float(row.mrr_gbp or 0.0)
    return payload


def ensure_factory_bots(db, owner: Optional[str]) -> List[OsAgent]:
    """Ensure the four Factory bots exist as OsAgent role records (idle)."""
    agents: List[OsAgent] = []
    for name in FACTORY_BOTS:
        q = db.query(OsAgent).filter(OsAgent.name == name)
        if owner:
            q = q.filter((OsAgent.owner == owner) | (OsAgent.owner.is_(None)))
        row = q.first()
        if row is None:
            row = OsAgent(
                owner=owner,
                name=name,
                role=name.lower(),
                status="idle",
            )
            db.add(row)
            db.flush()
        agents.append(row)
    return agents


def list_units(db, owner: Optional[str]) -> List[OsBusinessUnit]:
    q = db.query(OsBusinessUnit)
    if owner:
        q = q.filter(OsBusinessUnit.owner == owner)
    return q.order_by(OsBusinessUnit.updated_at.desc()).all()


def create_unit(
    db,
    owner: Optional[str],
    *,
    name: str,
    business_class: str = "for_profit",
    stage: str = "IDEA",
    bot_owner: Optional[str] = "businessbuilder",
    opportunity_id: Optional[str] = None,
    project_id: Optional[str] = None,
    next_action: Optional[str] = None,
    kill_date: Optional[datetime] = None,
) -> OsBusinessUnit:
    if business_class not in FACTORY_CLASSES:
        raise ValueError(f"class must be one of {FACTORY_CLASSES}")
    if stage not in FACTORY_STAGES:
        raise ValueError(f"stage must be one of {FACTORY_STAGES}")
    row = OsBusinessUnit(
        owner=owner,
        unit_id=next_unit_id(db, owner),
        name=name,
        business_class=business_class,
        stage=stage,
        revenue_gbp=0.0,
        cost_gbp=0.0,
        mrr_gbp=0.0,
        customers=0,
        conversations=0,
        next_action=next_action,
        kill_date=kill_date,
        bot_owner=bot_owner,
        opportunity_id=opportunity_id,
        project_id=project_id,
    )
    db.add(row)
    db.flush()
    return row


def pipeline_payload(units: List[OsBusinessUnit]) -> Dict[str, Any]:
    by_stage: Dict[str, List[Dict[str, Any]]] = {s: [] for s in FACTORY_STAGES}
    kill_dates: List[Dict[str, Any]] = []
    for u in units:
        stage = u.stage if u.stage in by_stage else "IDEA"
        d = unit_to_dict(u)
        by_stage[stage].append(d)
        if u.kill_date:
            kill_dates.append({
                "unit_id": u.unit_id,
                "name": u.name,
                "kill_date": u.kill_date.isoformat(),
                "stage": u.stage,
            })
    kill_dates.sort(key=lambda x: x["kill_date"])
    return {
        "stages": list(FACTORY_STAGES),
        "by_stage": by_stage,
        "counts": {s: len(by_stage[s]) for s in FACTORY_STAGES},
        "kill_dates": kill_dates,
        "units": [unit_to_dict(u) for u in units],
    }


def holdco_pnl(db, owner: Optional[str], units: List[OsBusinessUnit]) -> Dict[str, Any]:
    treasury = ensure_treasury(db, owner)
    # Prefer GBP label; do not invent external balances.
    limit_gbp = int(treasury.limit_cents or 0) / 100.0
    spent_gbp = int(treasury.spent_cents or 0) / 100.0
    remaining_gbp = remaining(treasury) / 100.0
    per_unit = []
    total_rev = 0.0
    total_cost = 0.0
    total_mrr = 0.0
    for u in units:
        d = unit_to_dict(u)
        entry = {
            "unit_id": d["unit_id"],
            "name": d["name"],
            "class": d["class"],
            "stage": d["stage"],
            "revenue_gbp": d["revenue_gbp"],
            "cost_gbp": d["cost_gbp"],
            "mrr_gbp": d["mrr_gbp"],
            "margin_pct": d["margin_pct"],
        }
        if d["class"] == "charity":
            entry["donors"] = d.get("donors", 0)
            entry["label"] = "charity — mrr_gbp is recurring donor income"
        else:
            entry["customers"] = d["customers"]
            entry["label"] = "for_profit"
        per_unit.append(entry)
        total_rev += d["revenue_gbp"]
        total_cost += d["cost_gbp"]
        total_mrr += d["mrr_gbp"]
    return {
        "currency": CURRENCY_LABEL,
        "capital_pool": {
            "limit_gbp": limit_gbp,
            "spent_gbp": spent_gbp,
            "remaining_gbp": remaining_gbp,
            "stripe_balance_gbp": 0.0,
            "bank_balance_gbp": 0.0,
            "note": CAPITAL_NOTE,
        },
        "totals": {
            "revenue_gbp": round(total_rev, 2),
            "cost_gbp": round(total_cost, 2),
            "mrr_gbp": round(total_mrr, 2),
            "margin_pct": margin_pct(total_rev, total_cost),
        },
        "per_unit": per_unit,
    }


def bots_status(db, owner: Optional[str]) -> List[Dict[str, Any]]:
    ensure_factory_bots(db, owner)
    out = []
    for name in FACTORY_BOTS:
        q = db.query(OsAgent).filter(OsAgent.name == name)
        if owner:
            q = q.filter((OsAgent.owner == owner) | (OsAgent.owner.is_(None)))
        row = q.first()
        out.append({
            "name": name,
            "role": row.role if row else name.lower(),
            "status": row.status if row else "idle",
            "last_run_at": row.last_run_at.isoformat() if row and row.last_run_at else None,
            "id": row.id if row else None,
        })
    return out


def categorize_approval(approval: OsApprovalRequest, action: Optional[OsAction]) -> str:
    """Bucket pending approvals into spend / external / irreversible."""
    reason = (approval.reason or "").lower()
    if action is not None:
        if action.reversible is False:
            return "irreversible"
        if int(action.cost_cents or 0) > 0:
            return "spend"
        tool = (action.tool_name or "").lower()
        name = (action.name or "").lower()
        blob = f"{tool} {name} {reason} {(action.governor_reason or '').lower()}"
        if any(k in blob for k in ("irreversible", "delete", "wipe", "destroy")):
            return "irreversible"
        if any(k in blob for k in ("spend", "budget", "payment", "purchase", "cost")):
            return "spend"
        if any(k in blob for k in (
            "email", "external", "post", "send", "web_search", "search", "http", "public",
        )):
            return "external"
        if action.risk_level in ("high", "critical"):
            return "irreversible"
    if "irreversible" in reason:
        return "irreversible"
    if any(k in reason for k in ("spend", "budget", "payment")):
        return "spend"
    return "external"


def approvals_queue(db, owner: Optional[str]) -> Dict[str, Any]:
    q = db.query(OsApprovalRequest).filter(OsApprovalRequest.status == "pending")
    if owner:
        q = q.filter(OsApprovalRequest.owner == owner)
    rows = q.order_by(OsApprovalRequest.created_at.desc()).all()
    buckets = {c: [] for c in FACTORY_APPROVAL_CATEGORIES}
    flat = []
    for a in rows:
        action = db.query(OsAction).filter(OsAction.id == a.action_id).first() if a.action_id else None
        cat = categorize_approval(a, action)
        item = {
            "id": a.id,
            "category": cat,
            "status": a.status,
            "reason": a.reason,
            "action_id": a.action_id,
            "decision_id": a.decision_id,
            "action_name": action.name if action else None,
            "tool_name": action.tool_name if action else None,
            "cost_cents": int(action.cost_cents or 0) if action else 0,
            "reversible": action.reversible if action else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        buckets[cat].append(item)
        flat.append(item)
    return {
        "categories": list(FACTORY_APPROVAL_CATEGORIES),
        "by_category": buckets,
        "counts": {c: len(buckets[c]) for c in FACTORY_APPROVAL_CATEGORIES},
        "pending": flat,
    }


def orchestrator_feed(db, owner: Optional[str], *, limit: int = 40) -> List[Dict[str, Any]]:
    """In-app super-orchestrator event stream (no external Grok API)."""
    q = db.query(OsEvent)
    if owner:
        q = q.filter(OsEvent.owner == owner)
    rows = q.order_by(OsEvent.timestamp.desc()).limit(min(int(limit), 200)).all()
    feed = []
    for e in rows:
        base = event_to_dict(e)
        feed.append({
            "id": base["id"],
            "timestamp": base["timestamp"],
            "event_type": base["event_type"],
            "actor": base["actor"],
            "action": base["action"],
            "status": base["status"],
            "risk_level": base["risk_level"],
            "authorization": base["authorization"],
            "cost_cents": base["cost_cents"],
            "summary": _feed_summary(base),
        })
    return feed


def _feed_summary(ev: Dict[str, Any]) -> str:
    parts = [ev.get("event_type") or "event"]
    if ev.get("action"):
        parts.append(str(ev["action"]))
    if ev.get("status"):
        parts.append(str(ev["status"]))
    return " — ".join(parts)


def build_factory_dashboard(db, owner: Optional[str]) -> Dict[str, Any]:
    ensure_factory_bots(db, owner)
    units = list_units(db, owner)
    return {
        "pipeline": pipeline_payload(units),
        "holdco_pnl": holdco_pnl(db, owner, units),
        "bots": bots_status(db, owner),
        "approvals_queue": approvals_queue(db, owner),
        "orchestrator_feed": orchestrator_feed(db, owner),
        "meta": {
            "stages": list(FACTORY_STAGES),
            "classes": list(FACTORY_CLASSES),
            "bots": list(FACTORY_BOTS),
            "currency": CURRENCY_LABEL,
            "capital_note": CAPITAL_NOTE,
        },
    }
