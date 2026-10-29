# Journal UI contracts

All templates live under `game/`. The stable base is `game/base.html`. All URL names use the `game` namespace and the names in the task UI brief. All mutations are POST with CSRF. File forms use multipart encoding. A `request_id` hidden UUID accompanies operations; JS renews it after successful completion, preserves it after network/validation errors, and preserves proposal tokens for higher-slot confirmation.

## Forms

Catalog form fields: `name`, `description`, `tags`, event `color`, item/action `icon` (PNG upload), action `activation`, `resource`, `slot_level`. Character base fields match model names, portrait is a PNG upload. Login field: `password` (a Django form is optional).

CharacterForm supplies these iterable helpers, with actual BoundFields (not strings):

* `ability_rows`: `{label, ability, save}` (six rows).
* `skill_rows`: `{label, field}`.
* `slot_rows`: `{label, current, maximum}` (seven rows; Roman labels I–VII).
* `weapon_rows`: `{label, name, attack, damage}` (five rows).
* `item_rows`: `{label, field, icon}` where field is quantity and icon is optional MediaAsset.
* `action_rows`: `{label, field, icon, activation, resource}` where field is bool; activation/resource are display strings, optional.

Dynamic canonical field names are those in the brief. `rage_current`, `rage_maximum`, `wildshape_current`, `wildshape_maximum` are rendered directly. All BoundField errors render beside the field. Non-field errors render at top. Helpers must retain bound input values on invalid POST.

## Page contexts

* `index.html`: `roles=[{label,url}]`, optional `form` for login.
* `master.html`: `characters` (active only), `events`, `items`, `actions`, `messages`, `request_id`.
* `player.html`: `character`, `resources=[{label,kind,level,current,maximum,pips:[bool]}]`, `ability_rows=[{label,value,save}]`, `skill_rows=[{label,value}]`, `action_sections=[{label,actions:[Action]}]`, `inventory=[InventoryEntry]`, `weapons=[{name,attack,damage}]`, `messages`, `unread`, `request_id`.
* `character_list.html`: `characters` (all).
* `character_form.html`: `form`, optional `object`, `is_edit`.
* `catalog_form.html`: `form`, `kind` (`event/item/action`), optional `object`, `is_edit`.
* `card.html` (dialog fragment): `kind`, `object`, `is_master`, `character` (required for player), `active_characters`, optional `quantity` (player item count; defaults 1), `request_id`. Player card links explicitly include `?character=<uuid>`. Master can edit/delete/send. Player action can apply; player item can remove quantity. Master player details use `active_character.html` with `character`.
* `invitation.html` (dialog fragment): `character`, `link`, `qr_data_uri`.
* `operation_result.html` (dialog fragment): `result={status,message,offered_level,required_level,proposal}`, optional `character`, `action`, `request_id`. Any `offered_level` displays confirmation, never auto-consumes. Submit `confirmed_level` + opaque `proposal` + `request_id` to `apply_action`.
* `confirm.html`: `title`, `message`, `action_url`, `button_label`, optional `hidden_fields=[{name,value}]`, `request_id`; reusable full fallback confirmation.
* `data.html`: optional `form` (upload validation errors), `error`, `counts=[{label,count}]`; upload POST file field `archive`.
* `import_review.html`: `job` (id), `conflicts=[{key,kind,label,candidates:[{value,label,object,json,selected}]}]`, `summary` plain user-facing text, `request_id`. Candidates support any number of versions. Catalog candidate.object is model-like; `.preview_icon` is a staged data URI (fallback `.icon.digest` for existing media). Character candidate.json is pre-prettified text. Each conflict choice field is `choice_<key>`, values `existing` / `incoming_<index>`, required. Set selected true on existing if present, otherwise first candidate. Both commit and cancel POST use job.id.
* `error.html`: `title`, `message`.

## Chat and partials

`chat_messages.html` consumes actual ChatMessage rows (`from_master`, `sender_character`, `recipients.all`, `created_at`, `text`, optional live `event`, `item`, `action`, `metadata.slot_level` or `metadata.used_level`). Operation text should be populated in `text` by backend. References are live links to card dialogs; event card preview shows current name/description/color. Text remains autoescaped. Include `character` for player links. Empty states are included.

Master panel wrappers (GridStack nodes) and all filters remain untouched by polls. Poll endpoint returns OOB `innerHTML` swaps only for `#master-players`, `#master-events`, `#master-items`, `#master-actions`, `#master-messages`, and `#chat-recipient` (select OPTION contents). Player targets: `#player-summary` (resources + health display; input separately preserved), `#player-stats`, `#player-actions`, `#player-inventory`, `#player-backstory`, `#player-messages`, `#chat-unread`. Provided `master_poll.html` and `player_poll.html` implement these targets. Endpoint may omit unchanged targets; recommended compare section hashes/query revisions. Do not return complete page shells for polls. `poll_state` and `poll_revision` optional hidden values can carry backend state.

`#poller` sends every 5s only while document visible; visibility return sends `refresh`. Filters (`events_filter`, `items_filter`, `actions_filter`), `active_tab`, and `[name=recipient_id]` included. JS preserves focused/dirty health input, all draft/filter inputs (never OOB replacement targets), chat scroll position unless previously at bottom, and active tab. If a new message arrives with chat visible, JS POSTs `chat_read` using CSRF; otherwise server unread controls badge. Only master incoming activity should count unread. For operations return dialog fragments or `HX-Trigger: {"refresh":true,"closeDialog":true}`; messages close/clear composer only on successful response via JS. Validation/conflict HTTP 422/409 bodies are rendered into dialog; errors are not discarded. Normal form POSTs render regular pages/redirects as fallback.

## Client behavior

`app.js` initializes GridStack 14 only on master, persists geometry by stable panel ids in localStorage; CSS stacks mobile. Native dialogs clamp near the triggering card, confirmations center. Escape/backdrop and explicit close restore focus. Catalog/character previews update in place; file uploads use temporary local object URLs. Filters hide existing rows, never replace/discard fields. All user text is inserted with textContent. Vendor runtime assets are local (HTMX 2.0.11 / GridStack 14.0.0), no runtime CDN/build.

Chat send fields: `text`, master `recipient_id`, player `character_id`. Catalog send checkbox field `recipients` repeated. Health field `health_current`. Inventory remove field `quantity`. Wipe field `categories` repeated, values `events`, `items`, `actions`, `characters`; starts entirely unchecked.

Capability blocks keep their inputs mounted; hidden inputs are disabled to avoid irrelevant browser validation, and re-enabled on toggling the capability on. Poll skips player-summary while health is focused/dirty and skips any target containing an open local dialog; it catches up on the next poll after completion/close.

## Review round 1 refinements

Character action selection combines the text input with activation and resource selectors (`data-filter=action-choices`). Rows carry activation_key and resource_key; slot keys are spell_slot_1 through spell_slot_7, and each row shows its required slot level. Filters only change hidden and keep all checkbox inputs/values mounted.

Chat-read requests send URL-encoded `through_id` equal to the maximum rendered data-message-id, or 0 for an empty history. The backend must not mark later, unseen messages as read.

Health form uses novalidate so negative/empty blur submissions reach the server's Russian validation result. A 422 opens the centered result dialog, restores the input's last confirmed data-saved-value, and clears its dirty flag without applying the invalid value. Successful responses update the confirmed value; focus returns to the health input after closing the result.
