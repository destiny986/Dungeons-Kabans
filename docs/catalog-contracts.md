# Catalog, character forms and protected PNG storage

`get_catalog_form(kind, data=None, files=None, instance=None)` accepts `event`,
`item`, `action`. `CharacterForm` has the same constructor arguments. These are
ordinary Django forms: use `is_valid()`, render field/non-field errors, then
`save()`. Save returns the persisted model. Invalid save raises `ValueError`.
Existing objects require the hidden `revision` field. Initial validation detects
outdated revisions with a Russian non-field error; the atomic save rechecks the
database and raises `StaleEdit` (a `ValidationError`) for a concurrent change.
Map that exception to HTTP 409. Each successful edit increments revision,
including relationship-only edits. All operation services must do likewise.

No media is stored by validation. New item/action forms require `icon`, edits
retain the existing icon when no file is supplied. Portrait is optional and
retained on edits; there is no portrait-clear field in this contract. Names use
the shared Unicode normalization and independent uniqueness namespaces. Tags
are comma-separated in the form and become a trimmed, nonempty, deduplicated
list (case-sensitive tag identity).

Dynamic character fields and BoundField helpers match `ui-contracts.md`.
`item_rows` additionally supplies `search_text`. `action_rows` includes display
`activation` and `resource`, raw `activation_key` and `resource_key`,
`slot_level`, and `search_text`. All catalog rows are always represented.
Disabled resource controls ignore posted values and preserve the snapshot.
Enabled resource fields are required; initial values for new forms are zero.
There are always nine persisted resource rows, five weapon rows, six signed
abilities/saves and eighteen signed skills. Inventory zero deletes membership;
unchecked actions delete membership. Master edits to existing health create
one `health` message, `from_master=True`, with the edited character as recipient
and metadata `{previous, current}`.

`delete_catalog(kind, pk)` and `delete_character(pk)` own atomic deletion blocks.
FK cascades remove structured catalog messages and memberships. Character
signals remove messages that lose their final recipient, retain messages with
other recipients, and never rewrite message text. Register `game.signals` in
`GameConfig.ready()`; queryset and import deletions then share the lifecycle.

## Media APIs and transaction boundary

`prepare_png(upload, *, size)` returns immutable `PreparedPNG(data, digest,
width, height)` without writing files or database rows. It verifies actual PNG,
rejects files above 5 MiB, dimensions above 4096 in either direction, animated,
truncated and decompression-bomb images. It decodes, center-crops, and resizes
to square RGBA PNG. Use size 256 for icons, 512 for portraits.

`store_prepared(prepared)` returns a digest-deduplicated `MediaAsset` and stores
`<sha256>.png` through a same-directory temporary file and atomic replacement.
Directories use mode 0755 and files 0644, subject to platform support/umask.
`store_png(upload, *, size)` prepares and stores. `asset_path(asset)` resolves
under MEDIA_ROOT and rejects traversal, absolute paths and symlink escapes;
delivery must use this helper behind authorization, never a public media URL.

`release_assets(asset_ids)` schedules post-commit cleanup, rechecking the DB in
a write transaction. Item/action/portrait references protect a file. Active
ImportJobs (staged/review/committing) protect digest keys in `payload['assets']`;
the implementation also conservatively recognizes IDs, digests and paths in
payload/preview. Import cancellation/completion should release candidate asset
IDs after changing status. A rollback discards release callbacks. Deletion
uses the same transaction lock as acquisition; imported references must be
saved transactionally before scheduling releases.

Form save owns its atomic block and compensates newly written files if that
block raises. Views must not wrap successful form saves in an outer transaction
that can later roll back: Django has no rollback callback. Import services
which call storage inside their own transaction must catch failures outside
that block and call `cleanup_unreferenced_file(prepared)` for their prepared
outputs. This helper obtains a write lock and only unlinks the digest file if
there is no corresponding MediaAsset, preserving shared files. Filesystem
permission/disk errors propagate; callers should display a generic error.
