# Shopping workflows implementation plan

**Goal:** Resolve issues #21–#27 on a feature branch and publish v1.4.0 after independent tests, review, main CI and four-platform release builds.

**Architecture:** Retain the async stdio server and shared Bring lifecycle. Resolve and validate mutations before executing them so previews and real writes share the same rules. Send mixed operations and attribute updates through Bring's existing bulk endpoint.

**Specification:** GitHub issues #21–#27 and their acceptance criteria, read on 2026-11-04. User requests autonomous implementation, questions for unresolved decisions, and the same reviewed merge/release workflow as v1.3.0.

## Constraints and decisions

- Keep Python 3.11+, MCP <2, English tool messages, list/item aliases and authentication-cache recovery.
- Explicit UUID plus a genuinely different requested name produces a clear unsupported-rename error (#21 permits this). Equivalent canonical, display and normalized names remain valid selectors.
- Add `get_lists` JSON output and structured content without removing default text output (#22).
- Add `search_items` for normalized cross-list lookup with list UUID/name and item UUID/status on each result (#23).
- Write `attributes` as validated `{type, content}` objects using singular `attribute` changes with `BringItemOperation.ATTRIBUTE_UPDATE`, not a plural field passed blindly to the API (#24). Preserve roundtrip nulls/date strings and preflight all metadata before writes.
- Add `dry_run` to add/remove/complete/batch, using the real resolution and error path, returning the same structured shape plus `dry_run: true` and explicit preview text (#25).
- Distinguish history restoration as `restored`, retaining UUID and omitted specification (#26).
- Permit per-item operations overriding a global operation defaulting to ADD; validate every target before one bulk write and return actual per-item operation/status (#27).
- Preflight is not a cross-client transaction; never claim atomicity against other clients. Tests require no live Bring access.

## Work and verification

- [x] Implementation agent writes failing unit regressions, implements #21–#27 and runs them green. Owns server.py and unit tests.
- [x] Independent test agent writes failing MCP consumer regressions, verifies side-effect suppression and authentication recovery, then runs green. Owns protocol tests.
- [x] Root updates README, contributor guidance, release notes and standalone smoke checks for new tool/schema behavior.
- [x] Separate review agent verifies issue acceptance, supported dependency wire payloads, previews, mixed batches and documentation. Resolve and recheck findings.
- [x] Verify full unittest suite, py_compile, pip check, diff checks, local standalone build and offline archive smoke test.
- [ ] Commit/push feature branch, create PR closing #21–#27, verify Python 3.11/3.12/3.13 CI, merge and verify main CI.
- [ ] Tag v1.4.0, verify all four release build/source-test/archive-smoke jobs and publication; compare uploaded archive digests with SHA256SUMS.

## Baseline

main 7666f13, latest release v1.3.0, clean worktree, 99 tests pass. Branch: feature/shopping-issues-21-27. Separate implementation, test and review agents are assigned.

## Verification before integration

133 tests pass, including 23 independently authored MCP/SDK regressions (initially red). The installed Bring SDK's actual request serialization is exercised for singular attribute updates and mixed operations. Independent review reports no open findings. Root additionally checked ISO date preflight and duplicate-UUID filtered output, both fixed with regressions. Syntax, dependency consistency and diff checks pass. Local macOS arm64 build, packaging and offline archive MCP smoke test pass with the expanded tool/schema checks. CodeRabbit CLI is absent; review was performed by the separate review agent. No live shopping data was modified.

Integration and release gates are tracked by the resulting GitHub PR and Actions runs, which are authoritative after this pre-integration plan is committed.
