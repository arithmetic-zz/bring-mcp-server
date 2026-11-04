# Tool reference

[Setup and installation](../README.md)

## Tools

- `get_lists` — list all Bring! shopping lists and their UUIDs, as text or JSON
- `get_list` — fetch one shopping list as text or JSON, optionally hiding Recently Purchased
- `search_items` — find items across all shopping lists with list and item identities
- `add_item` — add, update or restore an item; return its status and UUID
- `remove_item` — delete an item completely from a list; return its UUID
- `complete_item` — move an item to Recently Purchased, rather than checking it off in place; return its UUID
- `batch_update` — apply mixed ADD, COMPLETE and REMOVE operations

Single-item tools accept `item_id` (preferred), `item_name`, or `item` for the item name, in that order of precedence. Tool failures return a short message with the MCP `isError` flag set to `true`. Unknown parameters are rejected, including unknown fields inside batch items; validation errors identify the tool and parameter. `specification` is an alias for `spec`.

### Select a list and read its contents

All list-specific tools accept either `list_uuid` or the exact, case-sensitive `list_name`. At least one is required; `list_uuid` takes precedence when both are supplied. Names are cached after discovery. A name miss refreshes the cache once; unknown names report the available names, and duplicate names require a UUID. If a list was renamed, call `get_lists` to refresh cached names.

`get_lists` keeps its readable default text and also exposes the index as MCP `structuredContent`. Use `{"format":"json"}` to receive JSON text:

```json
{"lists":[{"name":"Home","list_uuid":"list-1"},{"name":"Hardware","list_uuid":"list-2"}]}
```

`get_list` defaults to the existing text output with both **To buy** and **Recently Purchased** sections. Set `include_recent` to `false` to omit history, or `format` to `"json"` for the same JSON shape as `bring://lists/{listUuid}`. Both options can be combined:

```json
{"list_name": "Home", "format": "json", "include_recent": false}
```

Example tool JSON response:

```json
{
  "listUuid": "list-1",
  "name": "Home",
  "items": {
    "purchase": [{"itemId": "Milk", "specification": "2 l", "uuid": "item-1"}],
    "recently": []
  }
}
```

`purchase` contains open items; `recently` is purchase history. Filtering history leaves `recently` as an empty array in JSON. Dates in item attributes (such as discount validity) are returned as ISO 8601 strings. A cold read lazily fetches list names once; a cached name avoids that extra request.

Use `filter` (or its alias `query`) for a substring lookup across item names, display names, specifications and UUIDs:

```json
{"list_name": "Home", "filter": "milk", "format": "json", "include_recent": false}
```

The text and JSON sections contain only matching items. Filtered JSON also includes `matches`, with `item_id`, `item_uuid`, `specification` and `status` (`purchase` or `recently`) for each hit. No hits produce empty sections and matches. Matching normalizes case, whitespace and German umlauts. `include_recent` accepts booleans and case-insensitive strings `"true"`/`"false"`; other values produce a contextual validation error.

To find an item without knowing its list, call `search_items`:

```json
{"filter":"milk","include_recent":false,"format":"json"}
```

It searches every accessible list with the same normalization and returns `{"matches":[...]}`. Each match includes `list_uuid`, `list_name`, `item_id`, `item_uuid`, `specification` and `status` (`purchase` or `recently`). The default is readable text; JSON is available through `format` and MCP `structuredContent` is always present. An empty search result is `{"matches":[]}`. If any list cannot be read, the call returns an error rather than presenting an incomplete search as complete.

### Add, remove and complete items

`add_item` is an upsert: a matching existing item is updated; a new item is added. `complete_item` moves the item to **Recently Purchased**; it does not check it off in place. `remove_item` deletes it completely. Batch `ADD`, `COMPLETE` and `REMOVE` have the same respective semantics.

Single-item changes retain a readable first text block and add a JSON text block, also available as MCP `structuredContent`:

```json
{"list_uuid": "list-1", "item_id": "Milk", "item_uuid": "item-1", "status": "updated"}
```

For `add_item`, `status` is `updated` when the target UUID was already in `purchase`, `restored` when it was in `recently`, and `created` for a new item. Restoring moves the existing UUID back to the open list and preserves the specification when omitted. Removal returns `removed`; completion returns `completed`. These statuses describe the state read immediately before the write; simultaneous edits by other Bring clients are not atomic with this check.

The server reads the items before a single-item change, reuses an existing UUID, or assigns a new UUID when adding a new item. Specify `item_uuid` to select among duplicate names or to create a separate same-name item with a new, valid UUID. New client-supplied UUIDs are validated before writing; existing legacy identifiers remain addressable. `add_item` also accepts legacy `uuid`; `item_uuid` takes precedence. An item-name argument remains required even when a UUID is supplied.

An explicit UUID selects an existing item; it does not rename it. If the supplied name differs from that item's canonical, display or normalized name, the request fails with an explicit unsupported-rename error before writing. This prevents a successful response from silently discarding the requested name change.

`item_id` is the base item name, while `spec` is its specification: `{"item_id":"Rotwein","spec":"SMS"}` is displayed as `Rotwein (SMS)`. That complete display name also resolves the existing item, without renaming it. Exact names are matched first, with a fallback that normalizes case, whitespace, German umlauts (including `ue`/`ü`/`u`) and `ß`/`ss`. Ambiguous matches are rejected with candidate UUIDs so the client can retry using `item_uuid`. This is normalization, not translation between languages.

Upsert matches by name or UUID, not by specification. An explicitly supplied `spec` replaces the existing specification; `""` clears it. Omitting it preserves the matched specification. To keep an already-open item completely unchanged, set `on_exists` to `"skip"`; the result has status `skipped`. `"error"` rejects an existing open item. The default `"update"` retains upsert behavior. A matching history item can be re-added. Name-only completion requires an open item, so typos cannot create history entries. Removing an unknown name also reports an error.

### Batch changes

Batch items accept `item_id` / legacy `itemId`, `item_uuid` / legacy `uuid`, and `spec` / `specification`. The first spelling in each pair takes precedence. Items with an empty `attributes` array can be passed back from `get_list`. Nonempty attributes are read-only and must be removed before submitting a mutation. For example:

```json
{"list_name":"Home","operation":"ADD","items":[{"item_id":"Milk","specification":"2 l"},{"itemId":"Bread","spec":"wholemeal"}]}
```

All items are resolved and validated before the batch write. Unknown or ambiguous REMOVE/COMPLETE targets reject the whole request before any mutation; COMPLETE requires open items and cannot create history entries for unknown names. A successful batch retains its readable text summary and adds a JSON text block plus matching `structuredContent`:

```json
{"list_uuid":"list-1","operation":"ADD","results":[{"item_id":"Milk","item_uuid":"item-1","operation":"ADD","status":"updated"},{"item_id":"Bread","item_uuid":"item-2","operation":"ADD","status":"created"}]}
```

The global `operation` defaults to `ADD`. Each item can override it, so additions, completions and removals can share one batch request:

```json
{"list_name":"Home","items":[{"item_id":"Milk"},{"item_id":"Bread","operation":"REMOVE"}]}
```

Each result reports its UUID, effective operation and actual `created`, `updated`, `restored`, `removed`, or `completed` status. The top-level result `operation` is `MIXED` when items use different operations, otherwise their effective operation. Each target may occur only once in a batch; duplicate targets are rejected before writing. All changes are sent through one bulk API request after successful preflight. Preflight and writes are separate API calls, so concurrent changes from other Bring clients can still race with this snapshot; batch validation does not provide a cross-client transaction.

### Preview changes

`add_item`, `remove_item`, `complete_item` and `batch_update` accept `dry_run: true`. The server reads and resolves the same targets and applies the same validation as a real call, but sends no writes. Preview results have the same JSON structure with `dry_run: true` added; the text identifies the result as a preview. For example:

```json
{"list_name":"Home","dry_run":true,"items":[{"item_id":"Milk"},{"item_id":"Bread","operation":"REMOVE"}]}
```

Like `include_recent`, `dry_run` accepts booleans and case-insensitive `"true"`/`"false"` strings. This is a preview, not a reservation: the list may change before execution. A preview of a new item may allocate a UUID that differs from a later call unless supplied explicitly.

### Item attributes are read-only

Bring returns attributes in list JSON, including ISO 8601 dates. Attribute **writes are disabled**: the v1.4.0 `ATTRIBUTE_UPDATE` payload is rejected by the live Bring API with HTTP 400. The previous transport-only tests did not establish that Bring accepted these writes.

Use the Bring app to edit attributes. `add_item` and batch items accept `attributes: []` for compatibility, but reject nonempty arrays before any network request. `ATTRIBUTE_UPDATE` is no longer advertised as an operation; old clients receive an explicit unsupported-feature error. A batch containing an unsupported attribute edit is rejected in full. `dry_run` applies exactly the same rejection and never sends a write. Omitting attributes does not request any attribute change.

### API error diagnostics

Failed API calls set MCP `isError: true`. HTTP failures include the status in the readable text and an `error` object in `structuredContent`, for example:

```json
{"error":{"http_status":400,"category":"invalid_request","retryable":false,"detail":"Invalid item payload","message":"Bring API request failed (HTTP 400). The request payload or parameters were rejected; correct the request before retrying. Invalid item payload"}}
```

- **400 / 422:** `invalid_request`; fix the payload or parameters before retrying.
- **401 / 403:** `authentication`; client, session and list-name caches are cleared so the next call logs in again. Check credentials if authentication keeps failing.
- **429:** `rate_limit`; `retryable: true`, with `retry_after` when Bring supplies a `Retry-After` header (seconds or an HTTP date).
- **5xx / connection errors / timeouts:** `temporary`; `retryable: true`. Network failures have `http_status: null`.
- Other HTTP errors and unclassified request failures use `request`, with `retryable: false`; the status is `null` when unavailable.

API response details are limited to 512 characters; JSON responses expose only a string `message`, `detail` or `error` field. Known credentials and common token formats are redacted. Raw response documents, headers other than `Retry-After`, and request URLs are not returned. HTML error pages and incomplete JSON bodies are omitted. Resource reads return the same readable diagnostics; resource discovery still falls back to the lists index when Bring is unavailable.

HTTP failures are surfaced before the Bring library's internal error parsing and retries, including during login. The server does not automatically replay a failed HTTP request. `retryable` describes the error category, not a guarantee that a write had no effect: read the list before repeating a write after a timeout or server error.

## Resources

- `bring://lists` — JSON index of every shopping list
- `bring://lists/{listUuid}` — JSON contents of a single list (purchase + recently sections)

Clients such as Claude Desktop expose these in their "attach resource" picker.

## Prompts

- `meal_plan` — plan a meal and stage missing ingredients on a target list
- `weekly_groceries` — draft a balanced weekly grocery list and stage it on a target list

## Server behavior

The server keeps one authenticated Bring client per process and reuses its HTTP session. Authentication failures during tool or resource access clear the cached client and session so the next request can log in again. Resource discovery falls back to the lists index when credentials or network access are unavailable.

Known Bring, API, and input errors are caught, logged, and translated into user-facing messages. See [API error diagnostics](#api-error-diagnostics) for structured error details.
