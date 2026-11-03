# Atomic operations and private chat

## Bounded undo

`undo_preview(request, entry_id)` requires master access inside a short atomic
transaction and returns `{status: confirm, entry_id, message}` for the exact
current journal head, without writes. `undo_last(request, entry_id, request_id)`
is master-only, atomic and idempotent; only that exact head may be reversed.
Invalid identifiers return `error`; an absent/replaced head or changed game
state returns `stale` with a Russian explanation. A failed reversal changes
neither any recipient nor logs/history. A conflicting head is not skipped.

The global UndoEntry stack retains 10 pending successful catalog grants,
player item removals and action uses. One multi-recipient grant is one entry;
all its message IDs and actual effects are recorded in the original transaction.
No-op duplicate action grants, failed requests, slot offers and replays add no
entry. Item reversal restores exact prior quantities, action-grant reversal
removes only newly created memberships, event reversal removes its receipts,
action use refunds the recorded resource and actual consumed slot level.
Resource-free use reverses only its log. Related messages disappear from all
chats; other messages stay. Missing cleared messages are harmless. No undo log
is sent to chat. Catalog edits do not change the recorded resource expense.

Game effects require the expected character revision, invitation generation,
active status and matching affected inventory/membership/resource state; required
deleted entities and numeric bounds cause conflicts. Successful reversals bump
revision, never decrease it. The nearest earlier effect for each character is
rebased only when its expected revision matches the reversed effect's prior
revision and generation. This permits consecutive undos without bridging an
intervening health change, rest, manual edit or access revocation. Log-only
reversal can erase an old log despite changed/deleted state, without rebasing
through that change.

Eviction and reversal remove only UndoEntry; Operation rows remain permanently.
Repeating an undone request never reapplies its effect or recreates its logs;
repeating an undo UUID replays its saved result before checking the stack and
never reverses another step. Evicted entries never return. There is no redo or
backfill from legacy operations. Chat clearing preserves the stack. Successful
ZIP commit (including a new no-change commit) and valid domain wipe reset it in
their transaction; replay of a completed import, previews, cancellation and
failed imports/wipes do not reset it. The stack is excluded from ZIP.

`game.chat.master_activity(request, after)` requires master capability and returns
`{count: int, max_id: str}` for existing messages with `id > after`,
`from_master=False`, and kind `text`, `action_use`, or `item_remove`. Count and
maximum come from one aggregate query; an empty result has `max_id="0"`.
It never changes read cursors or creates operations/messages.

`game.operations` exposes keyword-compatible `apply_action(request, character_id,
action_id, request_id, confirmed_level=None, proposal=None)`, `set_health(request,
character_id, value, request_id)`, `remove_item(request, character_id, item_id,
quantity, request_id)`, `grant_catalog(request, kind, object_id, character_ids,
request_id, *, quantities=None)`, `rest_characters(request, request_id)`, `send_chat(request, *, text,
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
Action use metadata records the consumed resource kind and actual slot level
(zero for rage/wildshape). Chat expense notes use this persisted metadata rather
than the live action resource, including after catalog edits. System text is neutral and catalog
references remain live. Each player log has only its own recipient. Item grants
add the positive integer from the optional UUID-to-quantity dictionary per unique
recipient (default 1); known action memberships create neither duplicate
membership nor false grant log. A grant operation is atomic across all active
recipients. Rest includes every active character, including offline browsers,
restores health exactly to maximum and only enabled resources. Each changed
character gets a private rest summary with typed before/after metadata.

Item quantities are validated inside the transaction, including the resulting
inventory limit. Unselected dictionary entries are ignored. A failure rolls back
all inventory, revision, operation and chat writes. Item grants create one private
`item_grant` message per recipient in UUID order, storing `metadata.quantity`;
`message_id` in the operation result refers to the first message. Non-unit
quantities participate in the normalized fingerprint. All-unit grants retain
legacy fingerprints so old successful operations remain replayable.

When an action grant skips existing memberships, its successful result also
contains `action_name` and `duplicate_recipients`, a list of `{id, name}` objects
with string UUIDs, sorted by name then UUID. These are persisted snapshots:
replaying the request returns the same notice even after renaming or further
grants. Recipients without the action still receive it normally. With no skipped
memberships these fields are absent; item and event results are unchanged.

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

`clear_chat(request, request_id)` is a master-only, atomic, idempotent operation. It deletes all ChatMessage rows and cascading MessageRecipient rows and resets every CharacterGrant read cursor to zero. It creates no chat message and does not change characters, resources, inventory, learned actions/spells, catalog entries or browser capabilities. Operation rows remain: replaying a successful clear UUID does not delete messages created later, and replaying prior game operations cannot reapply their effects or recreate deleted logs. A fresh clear removes history present when its transaction executes. Received-event card access disappears with its receipt; catalog events remain available to the master for sending again.
