# Changelog

## Unreleased

### Added

- Autonomous OS V1 (`autonomy/` package, HTTP `/api/os/`, dashboard `/os`).
  Closed loop: opportunity → research (SearxNG or mock) → score → hypothesis →
  governor/approval → reversible execution → measurement → learning.
  Default autonomy is recommend/draft (levels 1–2). GLOBAL STOP actually
  halts execution. Metrics come from real `os_events` / `os_metrics` rows.
  Run one cycle: `python -m autonomy.run_cycle --owner local --use-mock-search`.
  See `docs/AUTONOMOUS_OS.md` and `docs/GOVERNANCE.md`.
