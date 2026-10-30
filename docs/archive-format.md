# Portable archive version 1

Archives contain root `manifest.json` (`{"schema_version":1}`), `events.json`,
`items.json`, `actions.json`, `characters.json` arrays and flat PNG files. The
explicit strict schemas are in `game/archives/schema.py`; every property is
required and additional properties are rejected. Numeric domain values are
integers, never booleans or floating point numbers. Files are UTF-8 JSON. IDs
are nonempty opaque strings of at most 128 characters, unique within each kind;
exports use UUID strings. IDs only connect records inside this archive.

Signed abilities, saves and skills accept integers without a 32-bit range bound,
matching the JSON model/form domain. Nonnegative database integer fields retain
their existing bounds. Files are still subject to the archive byte limits.

Catalog records contain `id,name,description,tags`, with `color` for events,
`icon` for items and `icon,activation,resource,slot_level` for actions. Characters
contain all exported fields listed by `CHARACTER` in the schema: statistics,
five weapons, portrait, resources, inventory quantities and action membership.
Inventory item IDs and action IDs must resolve inside the same archive.
Duplicate resources, IDs and memberships (including normalized-name aliases)
are rejected. Each character requires exactly nine resources: spell slots 1–7,
rage/0 and wildshape/0. Resource levels and model domain constraints are validated
before staging. Exports fill absent legacy/raw-created resource rows with zero
current/maximum in the DTO without modifying the database, so their own archives
remain importable. Optional portrait and non-spell action slot level use JSON null.
Icons and portraits reference root PNG filenames.

Archives exclude revision/timestamp/search metadata, active flags, invitations,
grants, passwords, sessions, chat, operations and import jobs. Exports acquire a
consistent database snapshot and read referenced image bytes within that
transaction. Missing files fail export with a Russian error identifying the
missing image digest.

`game.archives` exports these service functions:

* `export_archive() -> bytes`; HTTP chooses a dated `.zip` download filename.
* `create_import(upload, session_key) -> ImportJob` accepts a binary readable
  upload and a 32-character browser session key. Only tracking metadata and
  staging files are written. No catalog, character or MediaAsset is created.
* `get_preview(job_or_id, session_key) -> dict` returns `conflicts` and Russian
  `summary`. Conflict keys are stable kind/name digests. Candidates contain
  `value,label,object,json,selected`; objects support catalog card attributes
  and `preview_icon` data URIs. Values are `existing` or `incoming_<index>`.
  Identical semantic candidates collapse; groups retain every distinct version.
* `commit_import(job_id, session_key, choices) -> dict` requires exactly every
  conflict key and a valid candidate value. Returns `summary,changed` and stores
  that result for idempotent completed retries.
* `cancel_import(job_id, session_key) -> None` removes staging and releases asset
  protection. Completed jobs cannot be cancelled.

Every job operation checks browser ownership. HTTP wrappers must additionally
enforce master authorization. Errors use Django `ValidationError` with Russian
messages. `StaleImport` subclasses it: another domain change causes the stored
preview and baseline to refresh without domain writes. The original upload and
normalized staging assets remain available, and the caller must display the
new review before submitting fresh choices. All other validation or commit
errors fail the job, clear payload/preview and remove its temporary directory.

Matching uses NFC/trim/casefold names, independently for each record kind.
Semantic comparison ignores archive IDs, revisions and image filenames;
normalized image content digests identify pictures. Character relationships
compare catalog names and quantities. Existing versions are selected by
default. Keeping an existing version preserves its complete state. Replacing
one preserves its database UUID, chat links, active status and invitations/
grants, increments revision and replaces only exported fields and memberships.
New characters are inactive. Choosing a catalog version maps every incoming
alias to the retained database identity.

ZIP input is limited to 50 MiB compressed, 250 MiB actually streamed expanded
bytes and 5,000 members. Duplicate member names, encryption, symlinks, absolute
or nested paths, Windows roots/backslashes and unexpected file types fail.
Extraction never uses `extractall`. Every PNG is decoded through `prepare_png`
(5 MiB / 4096-pixel source limits); icons normalize to 256 and portraits to 512.
Staging uses only verified digest filenames under `IMPORT_ROOT/<job UUID>`.
Payload `assets` maps lowercase SHA256 digests to `{filename,width,height}`.
This protects shared stored assets while a job is staged/review/committing.

Commit revalidates data, all choices and the canonical domain/image fingerprint
under its own write transaction. Image promotion is immutable/deduplicated.
Any exception rolls back domain rows and compensates promoted files with
`cleanup_unreferenced_file`. Old media release runs only after successful
commit and respects current rows and other active import jobs. There is no
transaction held while the user reviews a preview.

Run `python manage.py cleanup_imports` hourly. It write-locks each job older
than 24 hours, rechecks its timestamp and removes abandoned review/staged or
terminal staging directories. Staging holds an OS file-lock execution claim at
`IMPORT_ROOT/.<job UUID>.execution.lock` throughout validation and publication,
outside the removable upload/PNG directory and without a long
database transaction. Cleanup takes this claim nonblockingly and skips a live
worker even if suspended past 24 hours. Process exit releases the kernel lock;
the leftover file does not protect an abandoned job. Publication reloads and
locks the job, checks it is still staged, and cannot resurrect cancelled work.
Real cancellation immediately removes upload/PNG staging even while a worker
holds the external claim. The suspended worker subsequently observes the
cancelled status, cannot publish review, and retires its unlocked claim file.
Unlocked terminal claims are also removed by normal/abandoned cleanup.
Fresh and committing jobs are protected; commit's
`committing` status is inside the atomic transaction and normally cannot remain
after a process crash. Directory metadata is revalidated against the exact job
UUID and resolved import root before deletion; tampered paths are refused.
Before terminal transitions or stale-preview replacement, cleanup candidates
include all asset IDs/digests/paths referenced by either payload or preview.
Post-commit media release retains files used by current rows or other live jobs.
