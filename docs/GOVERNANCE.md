# Autonomous OS Governance

Human authority is absolute. The system may research, analyse, plan, draft, and (when policy allows) execute reversible actions. It may never silently raise its own autonomy, spend money, or treat untrusted content as instructions.

## Autonomy levels (0–7)

| Level | Name | What the system may do |
| --- | --- | --- |
| 0 | Observe | Collect events, metrics, and observations. No recommendations, no drafts, no execution. |
| 1 | Recommend | Produce next-best-action recommendations and human-attention cards. Default V1 floor. |
| 2 | Draft | Create hypotheses, experiment plans, and approval requests. Default V1 ceiling unless a policy row explicitly allows more. |
| 3 | Reversible | Execute actions that are reversible, zero/within autonomous budget, and within permitted risk. |
| 4 | Bounded | Execute within project/experiment budget, time, and risk bounds after those bounds are authorised. |
| 5 | Optimising | Reallocate effort among authorised experiments; still cannot raise autonomy or spend above limits. |
| 6 | System | Change internal OS configuration (schedules, scoring weights) within policy. Never changes mission/values/forbidden actions without a human. |
| 7 | Organisational | Reserved. Not enabled in V1. |

Default V1 operating band: **level 1–2**. Level is stored per owner in `os_runtime_state` and every change is appended to `os_autonomy_level_history`. Increases require an explicit human command (`CHANGE AUTONOMY LEVEL`). Decreases are allowed and logged.

## Governor (cannot be bypassed)

Every executable action is evaluated in this order. Short-circuit on the first failure.

```
ACTION
  → POLICY CHECK          (forbidden actions, mission constraints, GLOBAL STOP / PAUSE)
  → RISK CLASSIFICATION   (low / medium / high / critical from tool + action metadata)
  → AUTHORITY CHECK       (autonomy level vs required level; never self-elevates)
  → BUDGET CHECK          (treasury → project → experiment → action; cost ≤ remaining)
  → execute  OR  REQUEST HUMAN APPROVAL
```

The orchestrator, scheduler, CLI, and HTTP command APIs all call `Governor.evaluate()`. There is no alternate execute path.

### Approval rules

An action **must** request human approval when any of the following is true:

- GLOBAL STOP is set
- runtime is PAUSED (except STOP/START/health reads)
- required autonomy level is higher than the current level
- action is not reversible and current level < 4
- estimated cost exceeds the experiment/project/treasury `autonomous_limit`
- remaining budget is insufficient
- risk is above the permitted risk for the current level (V1: level ≤2 permits `low` only for execution; recommend/draft always allowed for low/medium)
- tool is not in the allowed tool set for the actor
- project is not authorised
- action name is in the mission `forbidden_actions` list

Approval requests are real rows in `os_approval_requests` and appear on the CEO dashboard HUMAN ATTENTION queue. `APPROVE` / `REJECT` / `OVERRIDE` are the only ways through.

### Treasury

```
TREASURY  →  PROJECT  →  EXPERIMENT  →  ACTION
```

Execute only if **all** of:

1. `cost <= autonomous_limit`
2. action is reversible (or an approved override exists)
3. project is authorised
4. budget remaining covers the cost
5. risk ≤ permitted for the current autonomy level

V1 seed treasury has `autonomous_limit = 0` (no live spend). The first tool is web search: reversible, cost 0.

### Untrusted content

Search results, crawled pages, and any external text are **DATA**. They are wrapped with Odysseus `src.prompt_security` guard markers before any model sees them. Injection attempts (instruction-like language inside results) are recorded as `SECURITY` errors and never executed as commands.
