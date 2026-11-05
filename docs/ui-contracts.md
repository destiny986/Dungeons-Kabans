# Journal UI contracts

## Character management

The character list shows all characters. Its scoped `.character-management`
cards put the portrait beside a centered name/class/health column with equal
text padding and gaps. Status remains upper-right. Four `button secondary small`
controls appear in one row: «Изменить», «Копировать», «Отозвать приглашение»,
«Удалить». Narrow layouts wrap labels without overflow; the invite button stays
below. Editor previews and master cards retain their existing layout.

Copy submits immediately as an ordinary POST, with a separate pre-bound
`copy_request_id` per character form. Success redirects to the full list;
422/409 errors render that list with an escaped Russian alert and retain the
submitted UUID on the affected form, marked `data-operation-key="true"`.
The revoke form uses the existing confirmation and `return_to=characters`,
returning to the updated list after revocation.

## Master settings

The «Настройки игры» dialog keeps its eyebrow and title left-aligned. All six
controls share the neutral `button secondary` style in a single centered column,
with equal widths, centered labels and equal dialog padding on both sides.
Navigation links retain their leading symbols without trailing arrows. The layout
reset button uses ↺ and remains a local GridStack layout reset.

## Footer phrases

Every full-page render selects one line with equal probability from the root
`phrases.txt` using the `footer_phrase` template tag in `base.html`. Repeats are
allowed. The file is read on each render as UTF-8 with optional BOM; blank lines
are ignored and surrounding whitespace is stripped. Text is HTML-escaped. A
missing, unreadable, invalid UTF-8 or empty file uses «Броски решают многое.
Историю создаёте вы.». The existing footer styling and ✦ remain. HTMX fragments,
including master/player polls, do not render the footer or select another phrase.

## Master players and undo

The «Игроки» heading shows «Отдых» and then an `icon-button` plus link to
`game:characters`, labelled «Управление персонажами». There is no ⋮ menu.
Inside the unchanged `#master-players` OOB target, `.players-list` lays out
compact cards left to right with automatic wrapping based on the panel width.
Cards use their portrait and text content width without stretching to fill a
row; long names wrap within the panel width. Cards have no bottom divider.
The empty state spans the full
list width. The list scrolls independently above `.players-footer`, whose
neutral full-width «Отменить последнее действие» button is disabled for an empty
stack. Polls preserve the nested list's scroll position. `master_context`
includes optional `undo_entry`, the latest retained global entry.

The master-only full character card has portrait upper-left, name/class/health
upper-right, six abilities/saves below using `_ability_grid.html` shared with
the player sheet. It does not add skills/traits. Bottom controls are neutral
`button secondary small`: edit left, revoke access right. On narrow screens
the ability tiles stack without horizontal overflow. Card context includes
`ability_rows` in addition to `character`.

The name, class and health share one vertical centerline in the block beside
the portrait. That block centers its contents vertically, uses equal padding on
all sides and equal gaps between the three entries, including on narrow screens.
The master page navigation does not render a `span.role-label`.

Undo opens a centered «Важное решение» confirmation with the exact operation,
actual recipients/quantities or resource refund and consequences for all chats.
«Назад», Escape and closing do not mutate. Its hidden target and UUID survive
polls and network retry; a changed stack head produces a stale result and
requires a fresh preview. No-JavaScript flow uses a full confirmation page.
Existing chat drafts, selected recipient, player tab and GridStack layout stay.

The master item send form shows a numeric `quantity_<UUID>` next to each
recipient, default 1, minimum 1, step 1, labelled «Количество для <name>».
JavaScript disables unselected quantities without changing values, including
after bulk selection, initial load and HTMX swaps. Collapsing the form and
polling retain selections/quantities. Recipient names wrap beside the quantity
on mobile. Item send errors retain the bound form and request UUID for retry;
the returned UUID is already marked as an operation key. Events/actions do not
show quantity controls. Both chats show «Количество: N» for item grants with
recorded quantity; legacy messages without it keep their prior appearance.
Item send forms use `novalidate`: selected-only server validation also works
without JavaScript, so an invalid unchecked quantity cannot block submission.

All templates live under `game/`. The stable base is `game/base.html`. All URL names use the `game` namespace and the names in the task UI brief. All mutations are POST with CSRF. File forms use multipart encoding. A `request_id` hidden UUID accompanies operations; JS renews it after successful completion, preserves it after network/validation errors, and preserves proposal tokens for higher-slot confirmation.

## Duplicate action grant notice

After sending an action/spell already owned by any selected recipient, the master
sees one centered application dialog titled «Уже есть у персонажей». Its message
is `«<action name>» уже есть у персонажей: <names>.` Only existing owners are listed; names and
the action title are escaped. The «Понятно» button, standard close button and
Escape close the dialog. Grants to other recipients finish before this notice;
no extra confirmation is required. Without HTMX, the notice is a full page with
a «Понятно» link back to the master page. Other sending behavior is unchanged.

## Forms

Catalog form fields: `name`, `description`, `tags`, event `color`, item/action `icon` (PNG upload), action `activation`, `resource`, `slot_level`. Character base fields match model names, portrait is a PNG upload. Login field: `password` (a Django form is optional).

The compact event preview and master event catalog show only the short description (`name`), using the same small rounded rectangle sized to its text; long names wrap within the available width. Master event catalog cards flow left to right and wrap automatically as the panel width changes, with 12px gaps and rows aligned to the top. Empty-list and no-search-results messages occupy the full row. The full description and tags appear in the full card. Event color applies only to compact cards; full cards and catalog form controls, including browser-autofilled fields, keep the neutral paper palette. Master event card controls share the edit button style and occupy one row: edit on the left, send in the center, delete on the right. The recipient form expands below that row without discarding selections.

Item and action/spell icons have no decorative border, padding, background, or shadow in catalogs, inventory/actions, full cards, character selection, import review, or catalog forms. Keyboard focus remains visible on interactive icons. Item and action/spell catalog forms show only the full preview, with the current icon beside the name; selecting a PNG updates that icon immediately. Full previews share their content template and typography with full catalog cards (category, title/icon, action metadata, description and tags), without management controls. Action metadata uses the shared action_metadata inclusion tag in full cards, form previews, character action selection and import comparison. Activation symbols are defined centrally: action U+25EF, bonus U+25B3, reaction U+2606; resource symbol U+25A3. The free activation badge and none resource badge are omitted independently, including the whole metadata block when both are absent. Live previews retain hidden DOM nodes and update symbols, labels and visibility immediately; both-hidden metadata has no layout spacing. The slot level suffix appears inside the resource badge exclusively for spell_slot. Compact event previews, the master event catalog and both chats share `_event_compact.html`; chat cards show only the name and color, with full details opened through the card link. Player links retain `?character=<uuid>`. All preview values come from the form's bound fields, including after invalid POSTs; user text remains escaped and multiline descriptions keep their line breaks.

Master item and action/spell icons use a single floating dark tooltip outside the scrolling catalog panel, with the name inserted as text and associated through `aria-describedby`. Native `title` and the nested icon label are absent for these icons. Hover and keyboard focus show the tooltip; leaving, scrolling, opening a dialog, viewport changes and HTMX swaps hide it. Long names wrap within the viewport. Other icon contexts retain their existing labels.

Full master item and action/spell cards share the event toolbar: neutral edit/send/delete controls in one row, with the recipient form spanning the row below. Toggling recipients preserves selections. Anchored cards retain their opening point while shown and recompute their position when contents or viewport dimensions change. They stay at least 14px inside viewport edges and scroll internally when content exceeds the available height, preserving focus and scroll during repositioning. Confirmations and error dialogs remain centered.

CharacterForm supplies these iterable helpers, with actual BoundFields (not strings):

* `ability_rows`: `{label, ability, save}` (six rows).
* `skill_rows`: `{label, field}`.
* `slot_rows`: `{label, current, maximum}` (seven rows; Roman labels I–VII).
* `weapon_rows`: `{label, name, attack, damage}` (five rows).
* `item_rows`: `{pk, label, field, icon}` where field is quantity and icon is optional MediaAsset.
* `action_rows`: `{pk, label, field, icon, activation, resource}` where field is bool; activation/resource are display strings, optional.

Dynamic canonical field names are those in the brief. `rage_current`, `rage_maximum`, `wildshape_current`, `wildshape_maximum` are rendered directly. All BoundField errors render beside the field. Non-field errors render at top. Helpers must retain bound input values on invalid POST.

## Page contexts

* `index.html`: `roles=[{label,url}]`, optional `form` for login.
* `master.html`: `characters` (active only), `events`, `items`, `actions`, `messages`, `request_id`, optional `undo_entry` (latest global pending reversal).
* `player.html`: `character`, `resources=[{label,kind,level,current,maximum,pips:[bool]}]`, `ability_rows=[{label,value,save}]`, `skill_rows=[{label,value}]`, `action_sections=[{label,actions:[Action]}]`, `inventory=[InventoryEntry]`, `weapons=[{name,attack,damage}]`, `messages`, `unread`, `request_id`.
* `character_list.html`: `characters` (all).
* `character_form.html`: `form`, optional `object`, `is_edit`.
* `catalog_form.html`: `form`, `kind` (`event/item/action`), optional `object`, `is_edit`.
* `card.html` (dialog fragment): `kind`, `object`, `is_master`, `character` (required for player), `active_characters`, optional `quantity` (player item count; defaults 1), `request_id`. Player card links explicitly include `?character=<uuid>`. Master can edit/delete/send. Player action can apply; player item can remove quantity. Master player details use `active_character.html` with `character` and six `ability_rows`.
* `invitation.html` (dialog fragment): `character`, `link`, `qr_data_uri`.
* `operation_result.html` (dialog fragment): `result={status,message,offered_level,required_level,proposal}`, optional `character`, `action`, `request_id`. Any `offered_level` displays confirmation, never auto-consumes. Submit `confirmed_level` + opaque `proposal` + `request_id` to `apply_action`.
* `confirm.html`: `title`, `message`, `action_url`, `button_label`, optional `hidden_fields=[{name,value}]`, `request_id`; reusable full fallback confirmation.
* `data.html`: optional `form` (upload validation errors), `error`, `counts=[{label,count}]`; upload POST file field `archive`.
* `import_review.html`: `job` (id), `conflicts=[{key,kind,label,candidates:[{value,label,object,json,selected}]}]`, `summary` plain user-facing text, `request_id`. Candidates support any number of versions. Catalog candidate.object is model-like; `.preview_icon` is a staged data URI (fallback `.icon.digest` for existing media). Character candidate.json is pre-prettified text. Each conflict choice field is `choice_<key>`, values `existing` / `incoming_<index>`, required. Set selected true on existing if present, otherwise first candidate. Both commit and cancel POST use job.id.
* `error.html`: `title`, `message`.

## Chat and partials

`chat_messages.html` consumes actual ChatMessage rows (`from_master`, `sender_character`, `recipients.all`, `created_at`, `text`, optional live `event`, `item`, `action`, `metadata.slot_level` or `metadata.used_level`). Operation text should be populated in `text` by backend. References are live links to card dialogs; event card preview shows current name/color using the shared compact template. Master outgoing headers show `Мастер ➤ <recipient names separated by commas>`; incoming headers show only the player sender. There is no separate recipient row. Player headers are unchanged. Action/spell chat links use the shared `activation_symbol` filter from `action_metadata`, backed by `ACTIVATION_SYMBOLS`, also used by the metadata inclusion tag; free activation has no symbol. Text remains autoescaped. Include `character` for player links. Empty states are included.

Master panel wrappers (GridStack nodes) and all filters remain untouched by polls. Poll endpoint returns OOB `innerHTML` swaps only for `#master-players`, `#master-events`, `#master-items`, `#master-actions`, `#master-messages`, and `#chat-recipient` (select OPTION contents). Player targets: `#player-summary` (resources + health display; input separately preserved), `#player-stats`, `#player-actions`, `#player-inventory`, `#player-backstory`, `#player-messages`, `#chat-unread`. Provided `master_poll.html` and `player_poll.html` implement these targets. Endpoint may omit unchanged targets; recommended compare section hashes/query revisions. Do not return complete page shells for polls. `poll_state` and `poll_revision` optional hidden values can carry backend state.

`#poller` sends every 5s only while document visible; visibility return sends `refresh`. Filters (`events_filter`, `items_filter`, `actions_filter`), `active_tab`, and `[name=recipient_id]` included. JS preserves focused/dirty health input, all draft/filter inputs (never OOB replacement targets), chat scroll position unless previously at bottom, and active tab. If a new message arrives with chat visible, JS POSTs `chat_read` using CSRF; otherwise server unread controls badge. Only master incoming activity should count unread. For operations return dialog fragments or `HX-Trigger: {"refresh":true,"closeDialog":true}`; messages close/clear composer only on successful response via JS. Validation/conflict HTTP 422/409 bodies are rendered into dialog; errors are not discarded. Normal form POSTs render regular pages/redirects as fallback.

## Client behavior

The master page alone loads `master_activity.js` configured by `data-activity-url`.
Chat rows expose `data-message-kind` alongside `data-message-id`. Initial rendered
incoming `text`, `action_use`, and `item_remove` IDs establish a per-tab in-memory
baseline; only visible successful message OOB swaps advance it afterwards.
Hidden pages request activity immediately, then 5s after each completed request;
requests time out after 15s. Full HTMX polling remains visible-only. Requests
are guarded at `htmx:beforeRequest` as well, so a queued full master refresh
cannot start after the page hides. Activity responses
replace the count relative to the baseline rather than incrementing it. Errors
preserve the last count and retry; stale responses after visibility changes are
ignored. At positive count, title alternates every 1s between
`🔴 Новых сообщений: N` and `(N) <original title>`. Zero count restores the original
title (including after remote history clearing). Visibility return stops the
background request/timers, clears the count, acknowledges rendered rows and uses
the existing HTMX refresh. Reload resets the state. Browser timer throttling may
delay polling and blinking; no sound, favicon or system notification is used.

`app.js` initializes GridStack 14 only on master, persists geometry by stable panel ids in localStorage; CSS stacks mobile. Native dialogs clamp near the triggering card, confirmations center. Escape/backdrop and explicit close restore focus. Catalog/character previews update in place; file uploads use temporary local object URLs. Filters hide existing rows, never replace/discard fields. All user text is inserted with textContent. Vendor runtime assets are local (HTMX 2.0.11 / GridStack 14.0.0), no runtime CDN/build.

Character editor item/action icons and names request `card?preview=1` into the shared dialog; selection/quantity fields and filters stay mounted. Master-only read-only cards show full catalog content with a close button and no mutations. Ordinary master cards retain their controls. Slot offers show the escaped action name and both required/offered levels before consent. Activity history renders `item_remove.metadata.quantity` and player `health.metadata.old/new`, including zero; master health edit text already includes its values.

Import previews provide `counts={new,exact,conflict}` for normalized-name groups, classified exclusively: multiple distinct candidates are conflicts; one existing candidate is exact; one incoming candidate is new. Bulk keep selects existing or the first incoming candidate when absent. Bulk incoming selects the first displayed distinct incoming candidate. Individual radio choices can override either bulk action; server choice validation is unchanged.

Chat send fields: `text`, master `recipient_id`, player `character_id`. Catalog send checkbox field `recipients` repeated. Health field `health_current`. Inventory remove field `quantity`. Wipe field `categories` repeated, values `events`, `items`, `actions`, `characters`; starts entirely unchecked.

Capability blocks keep their inputs mounted; hidden inputs are disabled to avoid irrelevant browser validation, and re-enabled on toggling the capability on. Poll skips player-summary while health is focused/dirty and skips any target containing an open local dialog; it catches up on the next poll after completion/close.

## Review round 1 refinements

Character action selection combines the text input with activation and resource selectors (`data-filter=action-choices`). Rows carry activation_key and resource_key; slot keys are spell_slot_1 through spell_slot_7, and each row shows its required slot level. Filters only change hidden and keep all checkbox inputs/values mounted.

Chat-read requests send URL-encoded `through_id` equal to the maximum rendered data-message-id, or 0 for an empty history. The backend must not mark later, unseen messages as read.

Health form uses novalidate so negative/empty blur submissions reach the server's Russian validation result. A 422 opens the centered result dialog, restores the input's last confirmed data-saved-value, and clears its dirty flag without applying the invalid value. Successful responses update the confirmed value; focus returns to the health input after closing the result.

Master chat keeps «Мастер ↔ игроки» on the right of its heading. The `.chat-composer` container holds the send form and a separate `.chat-footer`: keyboard help on the left, a standalone clear form on the right. The «⦸ Очистить чат» button uses `button secondary small`, matching the neutral event-delete button and its symbol. Help text wraps within its own column on narrow screens; the button stays on the right without overlap. The clear form is not nested in the send form, so an empty required message cannot block clearing or cause a draft to be sent. Players retain the same composer appearance without a clear button. The existing «Важное решение» dialog warns that all history, including events and operation logs, will be removed from every chat while inventory, learned actions/spells and current resources remain. The form opts into `data-confirm-field="confirmed"`: JavaScript submits `confirmed=1` only after acceptance. Without JavaScript, the first POST renders `confirm.html` and the confirmation form posts `confirmed=1` with the same request UUID; master panels stack vertically to keep forms accessible without GridStack initialization. Cancellation is read-only. Polls replace only message bodies and unread indicators, preserving drafts and recipient selection.

Both chats render expense notes through `_chat_resource_note.html` from recorded `message.metadata.resource`, never the current action card: `spell_slot` shows «Потрачена ячейка заклинаний N уровня», `rage` shows «Потрачена ячейка ярости», and `wildshape` shows «Потрачена ячейка дикого облика». Spell notes use a positive `slot_level`, falling back to a positive `used_level`. Legacy messages without a resource kind but with a positive slot level use the spell note. Zero levels, absent expense metadata, `none` and unknown resource kinds produce no spell note; rage/wildshape omit the level. Editing the catalog action does not change historical expenses. The successful-action dialog keeps its existing copy.
