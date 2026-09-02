"""SQLAlchemy models for Autonomous OS V1.

Tables are prefixed ``os_`` to avoid colliding with existing Odysseus tables.
Imported from ``core.database`` before ``init_db()`` so ``create_all`` picks
them up. SQLite-compatible, Postgres-portable.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from core.database import Base, TimestampMixin, utcnow_naive


def _uid() -> str:
    return uuid.uuid4().hex


class OsRuntimeState(TimestampMixin, Base):
    """Singleton-per-owner runtime: STOP/PAUSE, autonomy level, active mission."""

    __tablename__ = "os_runtime_state"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default="paused")  # running|paused|stopped
    stopped = Column(Boolean, nullable=False, default=False)
    paused = Column(Boolean, nullable=False, default=True)
    autonomy_level = Column(Integer, nullable=False, default=1)
    autonomy_ceiling = Column(Integer, nullable=False, default=2)
    active_mission_id = Column(String, nullable=True, index=True)
    last_cycle_at = Column(DateTime, nullable=True)
    last_cycle_id = Column(String, nullable=True)
    cycle_count = Column(Integer, nullable=False, default=0)
    scoring_weights_json = Column(Text, nullable=True)  # JSON overrides
    notes = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("owner", name="uq_os_runtime_owner"),
        Index("ix_os_runtime_status", "owner", "status"),
    )


class OsAutonomyLevelHistory(Base):
    __tablename__ = "os_autonomy_level_history"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    previous_level = Column(Integer, nullable=False)
    new_level = Column(Integer, nullable=False)
    actor = Column(String, nullable=False)
    reason = Column(Text, nullable=False)
    silent = Column(Boolean, nullable=False, default=False)  # always False; column exists to prove it
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)


class OsMission(TimestampMixin, Base):
    __tablename__ = "os_missions"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    title = Column(String, nullable=False)
    statement = Column(Text, nullable=False)
    values_json = Column(Text, nullable=True)
    constraints_json = Column(Text, nullable=True)
    forbidden_actions_json = Column(Text, nullable=True)
    strategy = Column(Text, nullable=True)
    risk_appetite = Column(String, nullable=False, default="low")
    status = Column(String, nullable=False, default="active")
    is_active = Column(Boolean, nullable=False, default=True)


class OsGoal(TimestampMixin, Base):
    __tablename__ = "os_goals"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    mission_id = Column(String, ForeignKey("os_missions.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    metric = Column(String, nullable=True)
    target = Column(String, nullable=True)
    status = Column(String, nullable=False, default="open")
    priority = Column(Integer, nullable=False, default=0)

    mission = relationship("OsMission", backref="goals")


class OsProject(TimestampMixin, Base):
    __tablename__ = "os_projects"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    mission_id = Column(String, ForeignKey("os_missions.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    authorised = Column(Boolean, nullable=False, default=False)
    status = Column(String, nullable=False, default="draft")
    risk_limit = Column(String, nullable=False, default="low")

    mission = relationship("OsMission", backref="projects")


class OsAgent(TimestampMixin, Base):
    """Orchestrator role record — not a separate process."""

    __tablename__ = "os_agents"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False, default="orchestrator")
    status = Column(String, nullable=False, default="idle")
    allowed_tools_json = Column(Text, nullable=True)
    last_run_at = Column(DateTime, nullable=True)


class OsAgentRun(Base):
    __tablename__ = "os_agent_runs"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    agent_id = Column(String, ForeignKey("os_agents.id", ondelete="SET NULL"), nullable=True, index=True)
    cycle_id = Column(String, nullable=True, index=True)
    started_at = Column(DateTime, nullable=False, default=utcnow_naive)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="running")
    step = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    error = Column(Text, nullable=True)

    agent = relationship("OsAgent", backref="runs")


class OsOpportunity(TimestampMixin, Base):
    __tablename__ = "os_opportunities"

    id = Column(String, primary_key=True, default=_uid)
    public_id = Column(String, nullable=False, unique=True, index=True)  # OPP-2026-000001
    owner = Column(String, nullable=True, index=True)
    mission_id = Column(String, ForeignKey("os_missions.id", ondelete="SET NULL"), nullable=True)
    project_id = Column(String, ForeignKey("os_projects.id", ondelete="SET NULL"), nullable=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    source = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    status = Column(String, nullable=False, default="discovered")  # discovered|verified|scored|ranked|dismissed
    dedupe_key = Column(String, nullable=True, index=True)
    verified = Column(Boolean, nullable=False, default=False)
    score = Column(Float, nullable=True)
    rank = Column(Integer, nullable=True)
    scores_json = Column(Text, nullable=True)
    evidence_json = Column(Text, nullable=True)
    untrusted = Column(Boolean, nullable=False, default=True)
    priority = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_os_opp_owner_status", "owner", "status"),
        Index("ix_os_opp_owner_dedupe", "owner", "dedupe_key"),
    )


class OsHypothesis(TimestampMixin, Base):
    __tablename__ = "os_hypotheses"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    opportunity_id = Column(String, ForeignKey("os_opportunities.id", ondelete="CASCADE"), nullable=True, index=True)
    statement = Column(Text, nullable=False)  # If X then Y because Z
    if_condition = Column(Text, nullable=False)
    then_outcome = Column(Text, nullable=False)
    because = Column(Text, nullable=False)
    success_metric = Column(Text, nullable=False)
    failure_condition = Column(Text, nullable=False)
    time_limit_seconds = Column(Integer, nullable=False, default=60)
    budget_limit_cents = Column(Integer, nullable=False, default=0)
    next_action = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="draft")
    thesis = Column(Text, nullable=True)
    counterargument = Column(Text, nullable=True)
    assumptions_json = Column(Text, nullable=True)
    missing_evidence = Column(Text, nullable=True)
    uncertainty = Column(Float, nullable=True)

    opportunity = relationship("OsOpportunity", backref="hypotheses")


class OsExperiment(TimestampMixin, Base):
    __tablename__ = "os_experiments"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    project_id = Column(String, ForeignKey("os_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    hypothesis_id = Column(String, ForeignKey("os_hypotheses.id", ondelete="SET NULL"), nullable=True, index=True)
    opportunity_id = Column(String, ForeignKey("os_opportunities.id", ondelete="SET NULL"), nullable=True)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="planned")  # planned|awaiting_approval|running|succeeded|failed|cancelled
    success_metric = Column(Text, nullable=True)
    failure_condition = Column(Text, nullable=True)
    time_limit_seconds = Column(Integer, nullable=False, default=60)
    budget_limit_cents = Column(Integer, nullable=False, default=0)
    spent_cents = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    result_json = Column(Text, nullable=True)
    next_action = Column(Text, nullable=True)
    reversible = Column(Boolean, nullable=False, default=True)
    authorised = Column(Boolean, nullable=False, default=False)
    priority = Column(Integer, nullable=False, default=0)

    project = relationship("OsProject", backref="experiments")
    hypothesis = relationship("OsHypothesis", backref="experiments")


class OsAction(TimestampMixin, Base):
    __tablename__ = "os_actions"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    project_id = Column(String, ForeignKey("os_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    experiment_id = Column(String, ForeignKey("os_experiments.id", ondelete="SET NULL"), nullable=True, index=True)
    task_id = Column(String, nullable=True, index=True)
    name = Column(String, nullable=False)
    tool_name = Column(String, nullable=True)
    status = Column(String, nullable=False, default="proposed")  # proposed|approved|denied|executed|failed|rolled_back
    input_json = Column(Text, nullable=True)
    output_json = Column(Text, nullable=True)
    cost_cents = Column(Integer, nullable=False, default=0)
    risk_level = Column(String, nullable=False, default="low")
    reversible = Column(Boolean, nullable=False, default=True)
    authorization = Column(String, nullable=True)  # autonomous|approved|denied|override
    governor_reason = Column(Text, nullable=True)
    error_class = Column(String, nullable=True)
    rolled_back = Column(Boolean, nullable=False, default=False)

    experiment = relationship("OsExperiment", backref="actions")


class OsPolicy(TimestampMixin, Base):
    __tablename__ = "os_policies"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    name = Column(String, nullable=False)
    rule = Column(Text, nullable=False)
    allowed_tools_json = Column(Text, nullable=True)
    max_risk = Column(String, nullable=False, default="low")
    max_autonomy_level = Column(Integer, nullable=False, default=2)
    allow_irreversible = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)


class OsBudget(TimestampMixin, Base):
    __tablename__ = "os_budgets"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    scope = Column(String, nullable=False)  # treasury|project|experiment|action
    scope_id = Column(String, nullable=True, index=True)
    currency = Column(String, nullable=False, default="USD")
    limit_cents = Column(Integer, nullable=False, default=0)
    spent_cents = Column(Integer, nullable=False, default=0)
    autonomous_limit_cents = Column(Integer, nullable=False, default=0)
    authorised = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("ix_os_budget_scope", "owner", "scope", "scope_id"),
    )


class OsTransaction(Base):
    __tablename__ = "os_transactions"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    budget_id = Column(String, ForeignKey("os_budgets.id", ondelete="SET NULL"), nullable=True, index=True)
    action_id = Column(String, ForeignKey("os_actions.id", ondelete="SET NULL"), nullable=True)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String, nullable=False, default="USD")
    memo = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)

    budget = relationship("OsBudget", backref="transactions")


class OsEvent(Base):
    """Black-box event log. Append-only in application code."""

    __tablename__ = "os_events"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, default=utcnow_naive, index=True)
    actor = Column(String, nullable=False)
    project_id = Column(String, nullable=True, index=True)
    task_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=True)
    input_json = Column(Text, nullable=True)
    output_json = Column(Text, nullable=True)
    status = Column(String, nullable=True)
    cost_cents = Column(Integer, nullable=False, default=0)
    risk_level = Column(String, nullable=True)
    authorization = Column(String, nullable=True)
    extra_json = Column(Text, nullable=True)  # metadata (avoid SQLAlchemy reserved name)

    __table_args__ = (
        Index("ix_os_events_owner_time", "owner", "timestamp"),
        Index("ix_os_events_owner_type", "owner", "event_type"),
    )


class OsMetric(Base):
    __tablename__ = "os_metrics"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    name = Column(String, nullable=False, index=True)
    value = Column(Float, nullable=False)
    unit = Column(String, nullable=True)
    source_event_id = Column(String, nullable=True)
    recorded_at = Column(DateTime, nullable=False, default=utcnow_naive)
    extra_json = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_os_metrics_owner_name", "owner", "name", "recorded_at"),
    )


class OsObservation(Base):
    __tablename__ = "os_observations"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    kind = Column(String, nullable=False, default="environment")
    content = Column(Text, nullable=False)
    source = Column(String, nullable=True)
    untrusted = Column(Boolean, nullable=False, default=True)
    cycle_id = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)


class OsDecision(TimestampMixin, Base):
    __tablename__ = "os_decisions"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    what = Column(Text, nullable=False)
    why = Column(Text, nullable=False)
    evidence = Column(Text, nullable=True)
    actor = Column(String, nullable=False)
    authority = Column(String, nullable=True)
    cost_cents = Column(Integer, nullable=False, default=0)
    expected = Column(Text, nullable=True)
    actual = Column(Text, nullable=True)
    alternatives_json = Column(Text, nullable=True)
    revisit_conditions = Column(Text, nullable=True)
    thesis = Column(Text, nullable=True)
    counterargument = Column(Text, nullable=True)
    assumptions_json = Column(Text, nullable=True)
    missing_evidence = Column(Text, nullable=True)
    uncertainty = Column(Float, nullable=True)
    status = Column(String, nullable=False, default="open")
    requires_human = Column(Boolean, nullable=False, default=False)
    urgency = Column(Integer, nullable=False, default=0)
    importance = Column(Integer, nullable=False, default=0)
    options_json = Column(Text, nullable=True)
    recommendation = Column(Text, nullable=True)
    consequence_of_waiting = Column(Text, nullable=True)
    related_approval_id = Column(String, nullable=True)


class OsKnowledge(TimestampMixin, Base):
    __tablename__ = "os_knowledge"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    memory_class = Column(String, nullable=False, default="semantic")
    text = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=0.5)
    source = Column(String, nullable=True)
    source_id = Column(String, nullable=True)
    verified = Column(String, nullable=False, default="unverified")
    freshness_hours = Column(Integer, nullable=True)
    existing_memory_id = Column(String, nullable=True)  # link to core Memory.id
    untrusted = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_os_knowledge_class", "owner", "memory_class"),
    )


class OsSource(TimestampMixin, Base):
    __tablename__ = "os_sources"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    url = Column(String, nullable=True)
    title = Column(String, nullable=True)
    snippet = Column(Text, nullable=True)
    provider = Column(String, nullable=True)
    untrusted = Column(Boolean, nullable=False, default=True)
    fetched_at = Column(DateTime, nullable=False, default=utcnow_naive)
    content_hash = Column(String, nullable=True, index=True)


class OsArtifact(TimestampMixin, Base):
    __tablename__ = "os_artifacts"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    kind = Column(String, nullable=False, default="note")
    title = Column(String, nullable=True)
    body = Column(Text, nullable=True)
    path = Column(String, nullable=True)
    related_type = Column(String, nullable=True)
    related_id = Column(String, nullable=True)


class OsError(Base):
    __tablename__ = "os_errors"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    error_class = Column(String, nullable=False, default="UNKNOWN")
    message = Column(Text, nullable=False)
    retryable = Column(Boolean, nullable=False, default=False)
    retry_count = Column(Integer, nullable=False, default=0)
    action_id = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)
    extra_json = Column(Text, nullable=True)


class OsAlert(Base):
    __tablename__ = "os_alerts"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    severity = Column(String, nullable=False, default="info")
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    acked = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)


class OsAuditLog(Base):
    __tablename__ = "os_audit_logs"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    actor = Column(String, nullable=False)
    command = Column(String, nullable=False)
    target_type = Column(String, nullable=True)
    target_id = Column(String, nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)


class OsApprovalRequest(TimestampMixin, Base):
    __tablename__ = "os_approval_requests"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, nullable=True, index=True)
    action_id = Column(String, ForeignKey("os_actions.id", ondelete="SET NULL"), nullable=True)
    decision_id = Column(String, ForeignKey("os_decisions.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending|approved|rejected|overridden
    reason = Column(Text, nullable=False)
    requested_level = Column(Integer, nullable=True)
    resolved_by = Column(String, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)

    action = relationship("OsAction", backref="approvals")
    decision = relationship("OsDecision", backref="approvals")


def all_os_tables():
    """Return table objects so tests can create just OS tables if needed."""
    return [
        OsRuntimeState.__table__,
        OsAutonomyLevelHistory.__table__,
        OsMission.__table__,
        OsGoal.__table__,
        OsProject.__table__,
        OsAgent.__table__,
        OsAgentRun.__table__,
        OsOpportunity.__table__,
        OsHypothesis.__table__,
        OsExperiment.__table__,
        OsAction.__table__,
        OsPolicy.__table__,
        OsBudget.__table__,
        OsTransaction.__table__,
        OsEvent.__table__,
        OsMetric.__table__,
        OsObservation.__table__,
        OsDecision.__table__,
        OsKnowledge.__table__,
        OsSource.__table__,
        OsArtifact.__table__,
        OsError.__table__,
        OsAlert.__table__,
        OsAuditLog.__table__,
        OsApprovalRequest.__table__,
    ]
