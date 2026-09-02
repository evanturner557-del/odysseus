"""Opportunity engine: discover → dedupe → verify → score → rank."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from autonomy.constants import DEFAULT_SCORING_WEIGHTS, NEGATIVE_CRITERIA, POSITIVE_CRITERIA
from autonomy.events import emit, record_metric
from autonomy.models import OsOpportunity
from autonomy.untrusted import detect_injection, facts_from_hits

_YEAR = datetime.now(timezone.utc).year
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def next_public_id(db, owner: Optional[str], year: int = _YEAR) -> str:
    prefix = f"OPP-{year}-"
    q = db.query(OsOpportunity).filter(OsOpportunity.public_id.like(f"{prefix}%"))
    existing = [r.public_id for r in q.all() if r.public_id]
    max_n = 0
    for pid in existing:
        try:
            max_n = max(max_n, int(pid.split("-")[-1]))
        except (TypeError, ValueError):
            continue
    return f"{prefix}{max_n + 1:06d}"


def dedupe_key(title: str) -> str:
    norm = _NON_ALNUM.sub(" ", (title or "").lower()).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]


def score_opportunity(
    scores: Dict[str, float],
    weights: Optional[Dict[str, float]] = None,
) -> float:
    w = dict(DEFAULT_SCORING_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})
    total = 0.0
    for k, weight in w.items():
        raw = float(scores.get(k, 0.0))
        raw = max(0.0, min(10.0, raw))
        total += raw * weight
    return round(total, 4)


def heuristic_scores(title: str, snippet: str, mission_text: str) -> Dict[str, float]:
    """Deterministic, no-LLM scoring so V1 works without API keys.

    Values are 0–10. This is a real heuristic, not a fake dashboard number:
    it is derived from the actual text of the hit + mission.
    """
    blob = f"{title} {snippet}".lower()
    mission = (mission_text or "").lower()
    words = set(w for w in re.findall(r"[a-z0-9]{3,}", blob))
    mwords = set(w for w in re.findall(r"[a-z0-9]{3,}", mission))
    overlap = len(words & mwords)

    def bump(*needles: str) -> float:
        return 2.0 * sum(1 for n in needles if n in blob)

    scores = {
        "market_potential": min(10.0, 3 + bump("market", "demand", "customer", "revenue")),
        "pain": min(10.0, 3 + bump("pain", "problem", "frustrat", "manual", "slow")),
        "distribution": min(10.0, 3 + bump("channel", "seo", "search", "audience", "viral")),
        "automation": min(10.0, 4 + bump("automat", "script", "agent", "workflow", "tool")),
        "margin": min(10.0, 3 + bump("margin", "zero-cost", "free", "self-host")),
        "strategic_fit": min(10.0, 2 + overlap * 1.5 + bump("mission", "strategy")),
        "speed_to_experiment": min(10.0, 5 + bump("search", "probe", "reversible", "local")),
        "competition": min(10.0, 2 + bump("crowded", "incumbent", "compet")),
        "capital": min(10.0, 1 + bump("fund", "capex", "paid api", "advertis")),
        "complexity": min(10.0, 2 + bump("complex", "integrat", "legacy")),
        "regulatory": min(10.0, 1 + bump("regulat", "gdpr", "license", "medical")),
        "execution": min(10.0, 2 + bump("hiring", "team", "ops")),
        "dependency_risk": min(10.0, 2 + bump("vendor", "lock-in", "api key", "paid")),
    }
    return {k: round(v, 2) for k, v in scores.items()}


def discover_from_search(
    db,
    owner: Optional[str],
    *,
    hits: Sequence[Dict[str, Any]],
    mission_id: Optional[str],
    project_id: Optional[str],
    mission_text: str,
    actor: str,
    weights: Optional[Dict[str, float]] = None,
    source: str = "web_search",
) -> List[OsOpportunity]:
    created: List[OsOpportunity] = []
    skipped_injection = 0
    for hit in hits:
        title = str(hit.get("title") or "").strip()
        snippet = str(hit.get("snippet") or "")
        url = str(hit.get("url") or "")
        if not title:
            continue
        blob = f"{title}\n{snippet}"
        if detect_injection(blob):
            skipped_injection += 1
            emit(
                db, owner=owner, event_type="injection_blocked", actor=actor,
                status="skipped_opportunity", extra={"title": title[:200]},
            )
            continue
        key = dedupe_key(title)
        q = db.query(OsOpportunity).filter(OsOpportunity.dedupe_key == key)
        if owner:
            q = q.filter(OsOpportunity.owner == owner)
        existing = q.first()
        if existing:
            emit(
                db, owner=owner, event_type="opportunity_deduped", actor=actor,
                status="duplicate", extra={"public_id": existing.public_id, "title": title},
            )
            continue
        scores = heuristic_scores(title, snippet, mission_text)
        total = score_opportunity(scores, weights)
        row = OsOpportunity(
            public_id=next_public_id(db, owner),
            owner=owner,
            mission_id=mission_id,
            project_id=project_id,
            title=title[:300],
            summary=snippet[:2000],
            source=source,
            source_url=url[:1000] or None,
            status="scored",
            dedupe_key=key,
            verified=bool(url),
            score=total,
            scores_json=json.dumps(scores),
            evidence_json=json.dumps({"url": url, "facts": facts_from_hits([hit])}),
            untrusted=True,
        )
        db.add(row)
        db.flush()
        emit(
            db, owner=owner, event_type="opportunity_discovered", actor=actor,
            project_id=project_id, status="scored",
            extra={"public_id": row.public_id, "score": total, "title": row.title},
        )
        emit(
            db, owner=owner, event_type="opportunity_scored", actor=actor,
            status="ok", extra={"public_id": row.public_id, "score": total, "scores": scores},
        )
        created.append(row)

    rank_opportunities(db, owner)
    record_metric(db, owner=owner, name="opportunities_discovered", value=float(len(created)), unit="count")
    if skipped_injection:
        record_metric(db, owner=owner, name="opportunities_injection_skipped", value=float(skipped_injection), unit="count")
    return created


def rank_opportunities(db, owner: Optional[str]) -> None:
    q = db.query(OsOpportunity).filter(OsOpportunity.status != "dismissed")
    if owner:
        q = q.filter(OsOpportunity.owner == owner)
    rows = q.order_by(OsOpportunity.score.desc().nullslast(), OsOpportunity.created_at.desc()).all()
    for i, row in enumerate(rows, start=1):
        row.rank = i
        row.priority = i
        if row.status in ("discovered", "verified", "scored"):
            row.status = "ranked"
    db.flush()


def to_dict(row: OsOpportunity) -> Dict[str, Any]:
    scores = None
    if row.scores_json:
        try:
            scores = json.loads(row.scores_json)
        except (TypeError, ValueError):
            scores = row.scores_json
    return {
        "id": row.id,
        "public_id": row.public_id,
        "title": row.title,
        "summary": row.summary,
        "source": row.source,
        "source_url": row.source_url,
        "status": row.status,
        "verified": row.verified,
        "score": row.score,
        "rank": row.rank,
        "scores": scores,
        "untrusted": row.untrusted,
        "priority": row.priority,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
