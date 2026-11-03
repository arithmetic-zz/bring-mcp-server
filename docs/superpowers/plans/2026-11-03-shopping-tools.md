# Shopping tools implementation plan

**Goal:** Resolve GitHub issues #1–#5 together, preserve default text output and legacy item aliases, then obtain acceptance before merge and publish one release.
**Architecture:** Keep dispatch in server.py. Resolve list names through the existing cache, expose optional JSON and recent-history filtering, and return JSON metadata alongside mutation text. Read current items before mutations to distinguish active-list creation/update and resolve UUIDs. Generate UUIDs for new items as recommended by bring-api; reject ambiguous name-only mutations.
**Tech stack:** Python 3.11+, unittest, MCP 1.x, bring-api.
**Spec:** GitHub issues #1–#5 in arithmetic-zz/bring-mcp-server.

## Constraints and decisions
- Retain list_uuid with precedence over list_name; missing selectors fail. This intentionally extends the old required-list_uuid contributor rule per issue #2.
- Exact list-name matching, duplicate names require UUID; refresh cache once on a name miss.
- get_list defaults to text with history. format=json matches resource shape; include_recent=false empties recently in JSON and omits it in text.
- Cold get_list loads names once; warm reads make no name lookup.
- Mutation status created/updated describes membership in purchase before the operation; re-adding a recent item is created. No atomicity guarantee with concurrent external edits.
- Retain execute_tool's list-of-TextContent interface; mutation metadata is a second JSON text block and call_tool also exposes it as structuredContent.

## Tasks
- [x] Add failing unit/protocol tests for selectors, cold/warm cache, filtered/JSON output; implement and run suite.
- [x] Add failing tests for created/updated, UUID selection, duplicate names and recent items; implement and run suite.
- [x] Update descriptions, README, contributor guidance and combined v1.2.0 release notes.
- [x] Run syntax, unit/protocol, dependency checks and independent branch review; fix findings.
- [ ] Push feature branch and PR closing #1–#5; wait for green CI and user acceptance.
- [ ] Merge accepted PR, tag v1.2.0, verify four release builds and published assets/notes; audit issue closure.

## Review focus
Duplicate list/item names; explicit UUID precedence; dataclass item conversion; auth failures during added lookups; external concurrent changes between read and write.
