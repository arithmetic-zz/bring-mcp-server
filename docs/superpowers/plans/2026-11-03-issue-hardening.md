# Shopping issue hardening implementation plan

**Goal:** Resolve all open issues #7–#19, review and test the changes on a feature branch, merge to main, and publish tested downloads.

**Architecture:** Keep the async server and Bring client lifecycle. Share item matching and validation between single and batch operations. Preserve MCP 1.x and existing aliases.

**Specification:** GitHub issues #7–#19, including the normalization comment on #11. The user authorized autonomous implementation of issues requiring no clarification, with separate implementation, test, and review agents.

## Tasks

- [x] Add failing regressions, then implement display-name matching (#7), documented spec replacement (#8), contextual validation and string booleans (#9), creation UUID validation (#10), normalized name fallback with ambiguity detection (#11), completion existence checks (#12), and candidate UUIDs (#16).
- [x] Add failing regressions, then implement batch preflight (#13), per-item structured results (#14), explicit aliases and JSON roundtrip (#15), and strict unknown-parameter validation (#19). Preflight rejects the full batch before writes if any target is invalid; results describe the snapshot before mutation.
- [x] Add failing regressions, then implement add_item on_exists update/skip/error (#17) and get_list filter/query lookup with UUID and status (#18).
- [x] Update README with all public behavior, examples and limitations. Keep English tool messages, authentication cache recovery and resource shapes.
- [x] Independent protocol tests and separate code review; resolve findings and verify syntax, dependency consistency, and full unit suite.
- [ ] Commit and push feature branch, create PR referencing all issues, verify Python 3.11/3.12/3.13 CI, merge to main and verify main CI.
- [ ] Tag v1.3.0, run release pipeline, verify all four build/smoke-test targets and published archives plus SHA256SUMS.

## Review focus

Exact matches precede normalized fallback; ambiguous matches must never choose arbitrarily. Matched display names must write the canonical stored name. Omitted spec preserves existing data while explicit empty spec clears it. Batch inputs cannot override operation through unknown fields. Legacy stored UUIDs remain addressable, while new UUIDs must be valid. Tests use fake Bring data and never mutate the user's real shopping lists.

## Baseline

On main f8ec45f: 66 tests passed. Work proceeds on feature/shopping-issues-7-19. Latest published release is v1.2.0.

## Implementation verification

99 tests pass, including independent MCP protocol regressions verified red before implementation. Syntax, dependency consistency and whitespace checks pass. Separate review identified UUID case comparison, read-only batch metadata, blank item names and trailing-newline booleans; all were fixed with regressions and rechecked. No remaining review findings. CodeRabbit CLI was unavailable; review was performed by the independent review agent. Local macOS build and offline archive MCP smoke test passed. CI and release evidence will be recorded in the PR and GitHub Actions runs.
