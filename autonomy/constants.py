"""Constants for Autonomous OS V1. No I/O, no secrets."""

from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

# 0 Observe, 1 Recommend, 2 Draft, 3 Reversible, 4 Bounded,
# 5 Optimising, 6 System, 7 Organisational
AUTONOMY_LEVELS: Dict[int, str] = {
    0: "observe",
    1: "recommend",
    2: "draft",
    3: "reversible",
    4: "bounded",
    5: "optimising",
    6: "system",
    7: "organisational",
}

DEFAULT_AUTONOMY_LEVEL = 1
DEFAULT_AUTONOMY_CEILING = 2
MAX_AUTONOMY_LEVEL = 7
V1_MAX_AUTONOMY_LEVEL = 4  # organisational/system not enabled for auto-raise

RISK_LEVELS: Tuple[str, ...] = ("low", "medium", "high", "critical")

# Permitted execution risk by autonomy level. Recommend/draft (1–2) may
# *plan* medium-risk work but may only *execute* low-risk reversible tools.
PERMITTED_EXECUTION_RISK: Dict[int, FrozenSet[str]] = {
    0: frozenset(),
    1: frozenset(),
    2: frozenset(),
    3: frozenset({"low"}),
    4: frozenset({"low", "medium"}),
    5: frozenset({"low", "medium"}),
    6: frozenset({"low", "medium"}),
    7: frozenset({"low", "medium", "high"}),
}

ERROR_CLASSES: Tuple[str, ...] = (
    "TRANSIENT",
    "PERMISSION",
    "VALIDATION",
    "DEPENDENCY",
    "LOGIC",
    "SECURITY",
    "UNKNOWN",
)

# Security failures must never be blindly retried.
NO_RETRY_ERROR_CLASSES = frozenset({"SECURITY", "PERMISSION", "VALIDATION"})
MAX_TRANSIENT_RETRIES = 2

MEMORY_CLASSES: Tuple[str, ...] = (
    "episodic",
    "semantic",
    "procedural",
    "strategic",
    "decision",
)

VERIFICATION_STATUSES: Tuple[str, ...] = (
    "unverified",
    "corroborated",
    "refuted",
    "stale",
)

LOOP_STEPS: Tuple[str, ...] = (
    "observe",
    "interpret",
    "identify_opportunity",
    "form_hypothesis",
    "prioritise",
    "plan",
    "request_authority",
    "execute",
    "measure",
    "evaluate",
    "learn",
    "update_memory",
    "reprioritise",
)

# Positive criteria add; negative (prefixed conceptually) subtract.
DEFAULT_SCORING_WEIGHTS: Dict[str, float] = {
    "market_potential": 1.0,
    "pain": 1.0,
    "distribution": 0.8,
    "automation": 0.9,
    "margin": 0.8,
    "strategic_fit": 1.2,
    "speed_to_experiment": 1.0,
    "competition": -0.7,
    "capital": -0.8,
    "complexity": -0.6,
    "regulatory": -0.9,
    "execution": -0.5,
    "dependency_risk": -0.7,
}

POSITIVE_CRITERIA = (
    "market_potential",
    "pain",
    "distribution",
    "automation",
    "margin",
    "strategic_fit",
    "speed_to_experiment",
)
NEGATIVE_CRITERIA = (
    "competition",
    "capital",
    "complexity",
    "regulatory",
    "execution",
    "dependency_risk",
)

DEFAULT_FORBIDDEN_ACTIONS = (
    "spend_money",
    "send_external_email",
    "post_publicly",
    "delete_production_data",
    "irreversible_live_change",
    "raise_autonomy_silently",
    "bypass_governor",
)

BUDGET_SCOPE_TREASURY = "treasury"
BUDGET_SCOPE_PROJECT = "project"
BUDGET_SCOPE_EXPERIMENT = "experiment"
BUDGET_SCOPE_ACTION = "action"

ACTOR_SYSTEM = "system"
ACTOR_HUMAN = "human"
ACTOR_SCHEDULER = "scheduler"
ACTOR_CLI = "cli"

RUNTIME_STOPPED = "stopped"
RUNTIME_PAUSED = "paused"
RUNTIME_RUNNING = "running"

EVENT_TYPES = (
    "cycle_started",
    "cycle_finished",
    "cycle_skipped",
    "observe",
    "interpret",
    "opportunity_discovered",
    "opportunity_deduped",
    "opportunity_scored",
    "hypothesis_formed",
    "plan_created",
    "authority_requested",
    "authority_granted",
    "authority_denied",
    "governor_decision",
    "action_executed",
    "action_rolled_back",
    "measurement",
    "evaluation",
    "learning",
    "memory_updated",
    "error",
    "alert",
    "stop",
    "start",
    "pause",
    "autonomy_level_changed",
    "mission_changed",
    "budget_changed",
    "approval_requested",
    "approval_resolved",
    "untrusted_content",
    "injection_blocked",
    "search_fallback",
    "scheduler_tick",
)

DEFAULT_SEARCH_QUERY = "low-risk reversible in-system automation opportunities 2026"
SEED_EXPERIMENT_NAME = "search-probe-reversible"
SEED_MISSION_TITLE = "Learn, then propose reversible experiments"

# Cost is stored in integer cents. Search is free.
SEARCH_TOOL_COST_CENTS = 0
SEARCH_TOOL_TIMEOUT_SECONDS = 20

OS_CYCLE_ACTIONS = (
    "os_health",
    "os_task_review",
    "os_opportunity_scan",
    "os_daily_performance",
)
