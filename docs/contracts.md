# Domain and foundation contract

Django 5.2 project `config`, app `game`. Call `full_clean()` on untrusted input before saving; Django `save()` does not run validation. QuerySet `update()`/`bulk_create()` bypass normalization hooks: callers must normalize explicitly. Mutations and revision increments belong to later services, not model saves.

## Shared validation and naming

`game.constants`: `ABILITY_CHOICES/ABILITY_KEYS` (`str,dex,con,int,wis,cha`), `SKILL_CHOICES/SKILL_KEYS` (18 stable English keys with Russian labels), `COLOR_CHOICES`, `ACTIVATION_CHOICES`, `RESOURCE_CHOICES`, `ACTION_RESOURCE_CHOICES`, `MESSAGE_KINDS`, `IMPORT_STATUSES`.

`game.validators`: `normalize_name` = Unicode NFC, strip, casefold; `normalize_search` also collapses whitespace. Reusable `validate_abilities`, `validate_skills`, `validate_weapons`, `validate_tags`, `validate_json_dict`, `validate_digest`, `validate_relative_path`. JSON signed values reject booleans/floats/text. Weapons contain exactly five `{name,attack,damage}` rows; each row all filled or all empty, each value at most 30 characters. Paths are storage-relative and reject traversal and Windows absolute paths.

## Models

All UUID primary keys are named `id`. Every catalog inherits `name` (30), unique derived `normalized_name`, required `description`, string-list `tags`, derived normalized `search_text`, `revision` (default 1, minimum 1), and `updated_at`. Search includes name, description and tags. Each catalog has its own name uniqueness namespace. `Event`: `color` in red/blue/green/orange/yellow/cyan. `Item`: required `icon` -> MediaAsset PROTECT (`items`). `Action`: required `icon` PROTECT (`actions`), `activation` action/bonus/reaction, `resource` none/spell_slot/rage/wildshape, nullable `slot_level` (1–7 required exclusively for spell_slot).

`MediaAsset`: UUID, unique 64 lowercase hex `digest`, relative `path` under MEDIA_ROOT, positive `width,height`, `created_at`. No bytes in DB; media are served through protected delivery, not a public Django media route.

`Character`: UUID, `name`, unique `normalized_name`, `revision`, `updated_at`; required `character_class` (30); nullable `portrait` -> MediaAsset SET_NULL (`portraits`); blank `backstory,traits`; six-key signed JSON `abilities,saves`; eighteen-key signed JSON `skills`; five-row `weapons`; nonnegative `health_current,health_max,speed,armor_class,proficiency_bonus`; health current may exceed maximum. `can_cast,can_rage,can_wildshape` false; `spellcasting_ability` six ability choices, required when can_cast and blank otherwise; nonnegative `spell_save_dc,spell_attack_bonus`; `is_active` false; `invitation_generation` default 1. No automatic D&D calculation.

| Model | Fields and reverse relations |
|---|---|
| CharacterResource | character CASCADE (`resources`), kind spell_slot/rage/wildshape, level (1–7 for spell slots; 0 otherwise), nonnegative current/maximum. DB checks current<=maximum and valid kind/level; unique character/kind/level. |
| InventoryEntry | character CASCADE (`inventory`), item CASCADE (`inventory_entries`), positive quantity (default 1), unique character/item. |
| CharacterAction | character CASCADE (`action_grants`), action CASCADE (`character_grants`), unique character/action. |
| CharacterInvitation | UUID, character CASCADE (`invitations`), unique token_digest SHA256, generation default1, revoked false, created_at. |
| CharacterGrant | UUID, indexed session_key (40), character CASCADE (`browser_grants`), invitation CASCADE (`browser_grants`), generation default1, revoked false, last_read_message_id default0 nonnegative, created_at. Unique session_key/character. |
| ChatMessage | BigAutoField id cursor; kind text/event/item_grant/action_grant/action_use/item_remove/health/rest; from_master false; nullable sender_character CASCADE (`sent_messages`); blank text; nullable event/item/action CASCADE (each `chat_messages`); object metadata; created_at; recipients M2M Character (`received_messages`) through MessageRecipient. |
| MessageRecipient | message CASCADE (`recipient_links`), character CASCADE (`message_receipts`), unique message/character. Removing recipient character only removes their link; deleting a structured catalog FK removes the message. No substring rewriting of text. |
| Operation | UUID, scope (255), key UUID, fingerprint (64), object result, created_at; unique scope/key. Durable idempotency only; no service yet. |
| ImportJob | UUID, indexed session_key (40), status staged/review/committing/complete/cancelled/failed, relative directory, objects payload/preview/choices, base_digest (64, blank permitted), created_at, updated_at. Temporary tracking excluded from domain exports. |

Implicit IDs for resource, inventory, action grant and recipient rows are BigAutoField. Other UUID fields and message IDs are as listed.

## Runtime

Environment variables: DJANGO_ENV (`local` default; use `production` when deployed), DJANGO_DEBUG (local only), DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, DJANGO_CSRF_TRUSTED_ORIGINS, PUBLIC_BASE_URL, MASTER_PASSWORD, DATA_DIR, MEDIA_ROOT, IMPORT_ROOT, STATIC_ROOT, TIME_ZONE. Production requires explicit secret and hosts. `.env.example` documents values; `.env` loading belongs to the process launcher. SQLite database DATA_DIR/db.sqlite3 persists; WAL on connection, timeout20 and IMMEDIATE transactions; no ATOMIC_REQUESTS. DB sessions use dk_sessionid and dk_csrftoken, 30 days. Public collected static files use WhiteNoise.

GET `/healthz/` SELECTs the database and returns `{status:ok}` or generic `{status:unavailable}` with503; other methods405. No product routes, permission services, import services or operation service are provided in this foundation.

Production HTTPS: `SECURE_SSL_REDIRECT` is enabled outside local mode; only `^healthz/$` is exempt for internal Docker probes. `SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO","https")` trusts the loopback reverse proxy. HSTS stays disabled (`SECURE_HSTS_SECONDS=0`, intentional `security.W004` suppression): unrelated services share the IP on other ports, while HSTS applies to the entire hostname/IP and cannot be scoped by port. No includeSubDomains or preload policy is enabled. Deployment checks require a random production secret of at least 50 characters.
