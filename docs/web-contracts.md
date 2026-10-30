# HTTP journal integration

`game.urls` includes access routes and the real journal routes inside namespace
`game`. `config.urls` exposes that namespace plus healthz. GameConfig.ready registers
catalog lifecycle signals as well as the SQLite connection hook.

All journal mutations require POST and Django CSRF middleware. Master controllers
use `master_required`; player routes use `character_required`, including polling,
read acknowledgements, health, actions and inventory. Master capability never
provides a player grant. Chat chooses its capability from the explicit
`character_id` player field or master `recipient_id` field. Services recheck
capabilities inside their transactions. Ordinary denials redirect to index;
HTMX denials retain the access module's HX-Redirect response.

The `card(kind,pk)` endpoint defaults to master controls. An explicit
`?preview=1` requires master access and renders a read-only full card, without
send/edit/delete/apply/remove controls. Existing character-grant checks still
apply if a `character` query is also supplied. An explicit
`?character=<UUID>` always requires that browser's own grant and renders player
controls, even in a mixed-role browser. Item/action cards require ownership or a
received live chat reference; events require a received reference. Invalid kinds,
UUIDs, foreign jobs and unknown rows return safe 404/denial responses.
`kind=character` is a master-only active-character fragment, separate from the
player sheet. Integration template changes are limited to these master links,
that fragment, and visible numeric resource counts.

Master/player contexts use actual service message querysets with chronological
full history and live references. Polls render only the provided OOB fragments;
filters are normalized with normalize_search and search_text contains matching.
No pagination truncates history. Player contexts provide six abilities/saves,
eighteen skills, named weapons, positive inventory and grouped actions. Enabled
nonzero resources include current/maximum and at most 128 pips. Existing app.js
preserves drafts, focused health, dialog, tab and scroll state.

Forms call is_valid/save directly, outside any wrapping transaction. Stale form
validation or save returns 409 with bound values; ordinary invalid forms return
422. PNG storage and lifecycle stay in the catalog/forms/media services.
Operations use game.operations without duplicating mutation logic. Supplied
request UUIDs are validated; plain form fallback gets a generated UUID which is
retained in a higher-slot offer. Offers/stale conflicts return 409, expected
validation errors 422, and successes redirect or trigger refresh/closeDialog.
Only successful 2xx operations allow the frontend to clear drafts/renew IDs.

All data routes require master capability. Export returns ZIP bytes with a dated
Content-Disposition filename. Upload uses archive, review defaults to existing
candidate or first candidate, and commit passes exact choice_<key> fields to the
strict archive service. Every job is additionally selected by browser session,
including cancel/commit/review. StaleImport rerenders rebuilt review with 409;
other archive errors render the data error page. Commit/cancel success redirects
to data, using HX-Redirect for HTMX. Wipe validates a nonempty known categories
list and deletes selected querysets in one atomic block, preserving credentials
and using the normal signals/media lifecycle.

Verification: `python -m pytest game/test_web.py -q` exercises actual Django
Client/service flows, uploads and archives, including role separation, CSRF,
private messages, stale forms/imports, OOB fragments, card authorization,
request IDs, higher-slot consent, quantity removal, read/revoke, empty wipe,
resource caps and template reverse-name coverage. No final mocks or skips.

Successful HTMX apply-action responses retain a centered `confirmation` dialog
with an explicit success heading and the consumed resource icon/label. The
resource kind and actual used spell level come from the persisted service result,
so higher-slot confirmation reports the level actually spent and idempotent
replays return the same acknowledgement. The response triggers refresh without
closeDialog; other successful operations retain refresh/closeDialog behavior.
Plain form action fallback retains its player-page redirect. Existing app.js
centers confirmation fragments and refreshes the player OOB state; no JS change.
