"""Memory classes on top of existing Odysseus memory.

Classes: episodic, semantic, procedural, strategic, decision.
Each record has confidence, source, timestamp, freshness, verification.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from autonomy.constants import MEMORY_CLASSES
from autonomy.events import emit
from autonomy.models import OsKnowledge
from autonomy.untrusted import detect_injection
from core.database import utcnow_naive, Memory as CoreMemory

logger = logging.getLogger(__name__)


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def remember(
    db,
    owner: Optional[str],
    *,
    text: str,
    memory_class: str,
    source: str,
    confidence: float = 0.5,
    untrusted: bool = False,
    extra: Optional[Dict[str, Any]] = None,
    actor: str = "system",
    existing_memory_id: Optional[str] = None,
) -> OsKnowledge:
    klass = memory_class if memory_class in MEMORY_CLASSES else "semantic"
    body = (text or "").strip()
    if not body:
        raise ValueError("memory text is required")
    if detect_injection(body) and untrusted:
        # Store as data, never as procedural/strategic instructions.
        klass = "episodic"
        body = "[untrusted data, not instructions] " + body
        confidence = min(confidence, 0.2)

    core_id = existing_memory_id
    try:
        if core_id is None:
            mem = CoreMemory(
                id=__import__("uuid").uuid4().hex,
                text=body[:8000],
                category=klass,
                source=source or "os",
                owner=owner,
                timestamp=_now_ts(),
            )
            db.add(mem)
            db.flush()
            core_id = mem.id
    except Exception as e:
        logger.warning("core Memory persist failed (OS knowledge still stored): %s", e)

    row = OsKnowledge(
        owner=owner,
        memory_class=klass,
        text=body[:8000],
        confidence=max(0.0, min(1.0, float(confidence))),
        source=source,
        verified="unverified",
        existing_memory_id=core_id,
        untrusted=bool(untrusted),
    )
    db.add(row)
    db.flush()
    emit(
        db, owner=owner, event_type="memory_updated", actor=actor,
        status="ok", extra={"knowledge_id": row.id, "memory_class": klass, "core_id": core_id, **(extra or {})},
    )
    return row


def list_knowledge(db, owner: Optional[str], memory_class: Optional[str] = None, limit: int = 50) -> List[OsKnowledge]:
    q = db.query(OsKnowledge)
    if owner:
        q = q.filter(OsKnowledge.owner == owner)
    if memory_class:
        q = q.filter(OsKnowledge.memory_class == memory_class)
    return q.order_by(OsKnowledge.created_at.desc()).limit(limit).all()


def knowledge_to_dict(row: OsKnowledge) -> Dict[str, Any]:
    freshness = None
    if row.created_at:
        delta = utcnow_naive() - row.created_at
        freshness = round(delta.total_seconds() / 3600, 2)
    return {
        "id": row.id,
        "memory_class": row.memory_class,
        "text": row.text,
        "confidence": row.confidence,
        "source": row.source,
        "timestamp": row.created_at.isoformat() if row.created_at else None,
        "freshness_hours": freshness,
        "verification_status": row.verified,
        "untrusted": row.untrusted,
        "existing_memory_id": row.existing_memory_id,
    }
