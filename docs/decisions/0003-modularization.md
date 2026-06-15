# ADR-0003: Modularize into the mrr_ai package

**Status:** Accepted

## Context
The app was a single 2,088-line `app.py` mixing config, helpers, ~26 routes, and global
state - unmaintainable and a blocker for further work.

## Decision
Adopt the Flask application-factory pattern with blueprints and a services layer:
`create_app()` in `mrr_ai/__init__.py`; routes in `mrr_ai/blueprints/` (by area); logic in
`mrr_ai/services/` (no Flask imports); `config`, `extensions`, and `state` modules. `app.py`
is a thin entry. Shared mutable globals are isolated in `state.py` and accessed as `state.x`.

## Alternatives
- Lighter split (a few modules, single app object) - less idiomatic, less testable.
- Redesign the shared state into a per-session store now - higher risk; deferred.

## Consequences
- Testable services; clear separation; room to grow (B5/B6).
- `state.py` preserves the original **single-process** requirement (multi-worker would break
  the cross-request flow). Replacing it with a session store is future work.
- Routes moved verbatim; behavior preserved (route-smoke tests + pyright no-undefined-var).
