# Autonomous OS V1

Closed-loop autonomy inside Odysseus. Not a separate product, not a microservice mesh, not a Next.js rewrite.

Package: `autonomy/` (the name `os` is reserved by the Python standard library).
HTTP prefix: `/api/os/`. Dashboard: `/os`. CLI: `python -m autonomy.run_cycle`.

## Loop

```
OBSERVE → INTERPRET → IDENTIFY OPPORTUNITY → FORM HYPOTHESIS → PRIORITISE
  → PLAN → REQUEST/VERIFY AUTHORITY → EXECUTE → MEASURE → EVALUATE
  → LEARN → UPDATE MEMORY → REPRIORITISE → REPEAT
```

V1 must actually run this end-to-end path:

```
OPPORTUNITY → RESEARCH → SCORE → HYPOTHESIS → APPROVAL → EXECUTION → MEASUREMENT → LEARNING
```

Human sets mission, values, constraints, risk, financial limits, forbidden actions, and strategy. The system researches, scores, plans, and (only inside policy) executes reversible actions. The human is the ultimate authority.

## Components

| Piece | Module | Role |
| --- | --- | --- |
| Orchestrator | `autonomy/orchestrator.py` | Current state, assign work, priority, next-best-action, one cycle |
| Memory classes | `autonomy/memory.py` | Episodic / semantic / procedural / strategic / decision on top of existing memory |
| Opportunity engine | `autonomy/opportunities.py` | Discover → dedupe → verify → score → rank. IDs `OPP-YYYY-NNNNNN` |
| Experiment engine | `autonomy/experiments.py` | Hypothesis `If X then Y because Z`, metrics, time/budget limits |
| Tool layer | `autonomy/tools.py` | Named tools with permissions, risk, cost, reversibility. First: SearxNG search + mock fallback |
| Governor | `autonomy/governor.py` | Final policy layer. Cannot be bypassed |
| Event log | `autonomy/events.py` | Black box: type, time, actor, project, task, action, I/O, status, cost, risk, auth |
| Scheduler | `autonomy/scheduler.py` | Reuses `ScheduledTask` for health / task review / opportunity scan / daily performance |
| Runtime | `autonomy/runtime.py` | GLOBAL STOP, pause, autonomy level (logged) |
| Treasury | `autonomy/treasury.py` | TREASURY → PROJECT → EXPERIMENT → ACTION |

Roles (CEO, researcher, etc.) are **not** separate services. They are orchestrator steps behind the governor and tools.

## GLOBAL STOP

`POST /api/os/stop` (and CLI `--stop`) sets `os_runtime_state.stopped = true`.

Every cycle, scheduler tick, and tool execution checks this flag **before** the governor. If stopped:

- no autonomous execution
- no opportunity scan
- no experiment run
- health / dashboard / event reads still work
- `POST /api/os/start` is required to resume

This is a real flag, not a UI decoration.

## How to run one cycle

From the Odysseus repo root, with the same Python environment you use for the app:

```bash
# Isolated DB for a dry run (recommended first time)
DATABASE_URL=sqlite:////tmp/odysseus-os-cycle.db \
  python -m autonomy.run_cycle --owner local --use-mock-search

# Against the live local DB (still zero spend; search is reversible)
python -m autonomy.run_cycle --owner YOUR_USERNAME
```

What it does:

1. Ensures tables exist (`Base.metadata.create_all`)
2. Seeds one mission, one authorised project, treasury with `autonomous_limit=0`, and one low-risk reversible search experiment if missing
3. Runs a single loop: discover → research (search or mock) → score → hypothesis → governor → execute if policy allows else queue approval → measure → learn
4. Prints inspectable event / opportunity / experiment / memory / metric IDs
5. Writes a JSON summary to stdout

Inspect afterwards:

```bash
python -m autonomy.run_cycle --dump --owner local
```

Or open the CEO dashboard at `http://localhost:7000/os` while Odysseus is running.

## Command APIs

All under `/api/os/` and gated by existing Odysseus auth (`require_user`).

| Command | Method | Effect |
| --- | --- | --- |
| START | `POST /api/os/start` | Clear STOP, resume cycles |
| STOP | `POST /api/os/stop` | GLOBAL STOP |
| PAUSE | `POST /api/os/pause` | Pause autonomous cycles; reads still work |
| APPROVE | `POST /api/os/approvals/{id}/approve` | Human approval |
| REJECT | `POST /api/os/approvals/{id}/reject` | Human rejection |
| OVERRIDE | `POST /api/os/approvals/{id}/override` | Human override with reason (still logged) |
| ROLLBACK | `POST /api/os/actions/{id}/rollback` | Mark reversible action rolled back |
| PRIORITISE | `POST /api/os/prioritise` | Set opportunity/experiment priority |
| CHANGE MISSION | `PUT /api/os/missions/{id}` | Human-only mission edit |
| CHANGE BUDGET | `PUT /api/os/budgets/{id}` | Human-only budget edit |
| CHANGE AUTONOMY LEVEL | `POST /api/os/autonomy-level` | Logged; never silent |

## Next-best-action scoring

`GET /api/os/next-action` ranks candidates by mission alignment, expected value, probability, cost, urgency, reversibility, evidence, dependencies, risk, and whether human attention is required.

## Self-monitor

Failures are classified: `TRANSIENT`, `PERMISSION`, `VALIDATION`, `DEPENDENCY`, `LOGIC`, `SECURITY`, `UNKNOWN`. Transient failures may retry up to a configured limit. **Security failures are never retried blindly** — they escalate to the attention queue.

## Business Factory Command

The `/os` CEO dashboard includes Factory Command panels backed by `/api/os/dashboard` → `factory` and `/api/os/factory`:

| Panel | Source |
| --- | --- |
| Pipeline | `os_business_units` by stage (`IDEA` → `AUTONOMOUS`) + kill dates |
| HoldCo P&L | Treasury capital pool + per-unit revenue/cost/MRR (charity: donors). £ / GBP. Stripe/bank shown as **0 — not linked** |
| Bots | `businessbuilder`, `Businessbot`, `Holdingbot`, `orchestratorbot` (`os_agents`) |
| Approvals queue | Pending approvals bucketed `spend` / `external` / `irreversible` |
| Super-orchestrator feed | In-app `os_events` stream (no external Grok API) |

Create a unit: `POST /api/os/factory/units` with `name`, optional `business_class` (`for_profit`\|`charity`), `stage`. Metrics start at honest zeros.

