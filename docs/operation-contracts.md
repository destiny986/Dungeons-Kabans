# Atomic operations and private chat

`game.operations` exposes keyword-compatible `apply_action(request, character_id,
action_id, request_id, confirmed_level=None, proposal=None)`, `set_health(request,
character_id, value, request_id)`, `remove_item(request, character_id, item_id,
quantity, request_id)`, `grant_catalog(request, kind, object_id, character_ids,
request_id)`, `rest_characters(request, request_id)`, `send_chat(request, *, text,
character_id, as_master, request_id)`, and `mark_chat_read(request, character_id,
through_id=None)`.

Each mutation opens a short atomic transaction under the configured SQLite
IMMEDIATE transaction mode. Authorization is checked inside it. Missing browser
capabilities raise `PermissionDenied`; expected input/domain conflicts return a
JSON-safe dict with `status=error|stale` and Russian `message`. Success returns
`status=ok`, `message`, optionally `message_id`, `used_level`, `slot_level`,
`resource` or `changed_count`. No service trusts `request.user` or a posted sender.

UUID arguments accept UUID objects or UUID strings; counters accept strict ints
or ASCII decimal integer strings, never bool/float. Persisted counter limits use
the model field's MaxValueValidator, including health, inventory, and revisions.
Health can exceed maximum. Read acknowledgement cursors clamp to the latest
accessible message before persistence. Invalid inputs return domain errors.

Durable `Operation` scope is `player:<grant UUID>` or `master:<session key>`.
Fingerprints bind canonical intent (including targets, normalized text and
quantity). Same UUID/same intent replays its stored result with no further writes;
same UUID/different intent errors. Only successful operations are stored. Browser
authorization remains required even for replay. Higher-slot confirmation fields
are deliberately excluded from the action intent fingerprint.

When the required slot is empty, `apply_action` returns `status=offer`,
`required_level`, `offered_level`, and a signed opaque `proposal`. It spends
nothing and creates neither message nor completed operation. Confirm by posting
the same `request_id`, exact `confirmed_level`, and `proposal`. Tokens expire in
ten minutes and bind grant, character/action IDs, both revisions, request UUID,
and required/offered level. Confirmation rechecks capability and stock. A spent
offered slot produces a fresh offer at the smallest eligible higher level;
another confirmation is required. If the original slot becomes available,
confirmation returns `stale`: deliberately apply again to use it. Edited action
or character revisions invalidate old proposals. Manual upcasting is rejected.

Actions only spend resources and record use; they do not calculate damage/effects.
Action use metadata records actual slot level. System text is neutral and catalog
references remain live. Each player log has only its own recipient. Item grants
add one per unique recipient; known action memberships create neither duplicate
membership nor false grant log. A grant operation is atomic across all active
recipients. Rest includes every active character, including offline browsers,
restores health exactly to maximum and only enabled resources. Each changed
character gets a private rest summary with typed before/after metadata.

`game.chat.messages_for(request, character_id=None)` is master-global only when
the argument is absent. A character argument always requires that browser's own
grant, even for a master. It returns ordered ChatMessage rows, with live catalog
and sender references and prefetched recipients. `unread_count(request,
character_id)` counts only incoming master receipts after this grant's cursor.
`mark_chat_read` advances that browser/character cursor monotonically; omitted
cursor means latest accessible message. `create_message(*, recipients, **fields)`
is an internal helper whose caller must own authorization and the transaction.

Master free text targets exactly one active character through `character_id`
(HTTP wrappers translate `recipient_id`). Player free text targets only its own
character. Text is trimmed and limited to 10000 characters. Successful free-text
results include `message_id`; player messages never increment unread counts.
