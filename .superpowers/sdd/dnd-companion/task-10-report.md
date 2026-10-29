# Task 10 delivery infrastructure report

Status: implementation complete in owned files; integrated app and actual Docker/
server execution remain coordinator/operator validation gates.

## Scope delivered

- Non-root Python 3.12 image, image-time WhiteNoise collectstatic, Gunicorn two
  workers/two threads; startup does not migrate. Explicit production environment.
- Dedicated production Compose project with loopback 18081 binding, persistent
  data/media/import bind mounts, raw private env file, read-only root, tmpfs,
  dropped capabilities, no-new-privileges, DB-backed health and bounded logs.
- Dedicated nginx TLS vhost on 9443, existing IP SAN certificate, protected internal
  media alias. No existing service/vhost/renewal/challenge changes. Validate baseline
  nginx and staged first-install site before reload; remove invalid new candidate.
- CI on all push/PR events: Python 3.12 pytest, Django check, migration drift,
  production check; independent Docker build and isolated-volume bootstrap/health
  smoke test; no tests in the deployed app container.
- Release only on release-branch push after tests/smoke: build/push SHA-tagged
  DockerHub image, verified SSH/SCP, locked migration/bootstrap once, container
  start, local and external verified HTTPS health. Workflow concurrency never
  cancels running releases; host flock also serializes releases.
- Host ED25519 keyscan compared against independently trusted supplied fingerprint,
  followed by strict known-host checks. JSON secrets travel through SSH stdin;
  sshpass uses SSHPASS; shell special characters never become executable text.
  Generated runtime secrets are 0600, staging 0700, isolated temporary registry
  auth leaves root's existing Docker configuration untouched.
- Operator setup/secrets, local Docker smoke bootstrap, existing-services
  preservation, diagnostics, cleanup-import scheduling command, manual ZIP scope,
  and backwards-compatible migration caveat documented.
- No automatic backups, data deletion/reinitialization, rollback, older-image
  restart, repository push, workflow run, or server connection was performed.

## Evidence and tests

Used test-first checks for secret serialization and actual shell release flow.
Initial missing implementation produced expected failing boundary tests. Rejection
tests were strengthened to require the explicit validation error, rather than
accepting a missing script as a successful rejection. Git Bash initially prepended
its native curl/sleep before test doubles, causing a slow first run; fixed by
setting PATH inside the already-started shell, and imposed a 15-second harness
timeout. Corrected focused tests complete in approximately 3 seconds.

Final focused test command: `.venv/Scripts/python.exe -m pytest tests/test_deployment.py -q`
— 9 tests covering literal quotes/dollar/hash/backslash secrets, empty/newline/CR/
NUL rejection with no partial runtime file, POSIX 0600 permissions when on POSIX,
production environment/CSRF settings, exactly-once successful release order, and
migration/bootstrap/health failures without destructive recovery.

Shell syntax: Git Bash `bash -n deploy/release.sh deploy/release-container.sh
deploy/docker-smoke.sh` passed. Python `py_compile` for both delivery Python scripts
passed. No local Docker executable is available, so Compose validation, image build
and real container health could not be executed locally. CI includes these checks
but was not run. YAML was reviewed manually; PyYAML is not installed locally.

Full-suite observations while foundation was in flight: an earlier run passed
53 tests; the later run had 55 passed / 1 failed,
`game/test_validation.py::test_windows_root_relative_path_cannot_escape_media_root`,
which is foundation's actively introduced regression test. Foundation owns the
validator fix. Coordinator will rerun the final integrated suite after app changes
stabilize. Production check initially found security.W004/W008; foundation is
adding the agreed proxy/redirect policy and documenting why HSTS stays disabled
on the IP shared by other ports. Task10 does not modify those settings.

Official upstream action pins verified directly with `git ls-remote` on 2026-10-29:
checkout v4.2.2 `11bd71901bbe5b1630ceea73d27597364c9af683`, setup-python v5.6.0
`a26af69be951a213d495a4c3e4e4022e16d87065`. Official release pages and Docker's
raw-env-file documentation were also opened; references are in deployment docs.

## Self-review and operational limits

Reviewed owned files against the brief, especially credential quoting, strict SSH
verification, root Docker config isolation, nginx only-on-9443 behavior, internal
media visibility, failure ordering, and absence of backup/rollback/data deletion.
Runtime must use DJANGO_ENV=production as required by the foundation contract.
PNG files must remain readable by nginx (0644 under the prepared 0755 media dir).

GitHub concurrency retains one pending job and can supersede an older pending
release; cancel-in-progress false plus host flock preserve running release safety.
Registry policy should enforce immutable SHA tags; rerunning an already published
SHA can then fail the push, and is deliberately not overwritten automatically.
Base Python patch image is not digest pinned; record published app image digest.
Secrets reject embedded newline/CR/NUL; all ordinary shell punctuation is literal.

The operator must provision directories/privileges, registry/environment secrets,
independently verify SSH fingerprint, allow 9443, and retain valid certificate
renewal. Existing nginx site changes are refused rather than silently replaced.
Failed post-migration releases may leave the new schema/container in place, with no
automatic recovery. Schema changes must remain compatible with the currently
serving previous image. Historical per-SHA private runtime configuration remains
on disk for diagnostics; no automatic retention/data pruning has been added.

Commit SHA is supplied in the task handoff after committing this report and owned
files together.

## Fix round 1 — access-log credentials and hourly cleanup (2026-10-30)

Read and verified task-10-review.md against the invitation route and access
contract. The review's Important finding was confirmed: the image's original
Gunicorn access-log argument and inherited nginx access log recorded complete
request lines. Fixed both production access streams explicitly: Gunicorn now uses
`--access-logfile /dev/null`; the dedicated nginx vhost now uses `access_log off`.
Successful requests, invalid invitations, queries and Referer values therefore
cannot enter either access stream. Kept useful Gunicorn/application/nginx error
logging per coordinator instruction, with upstream-error suppression scoped only
to the credential-bearing nginx `/join/` location. Error logs still require care
when sharing other diagnostic context. An existing dedicated nginx
site must receive the reviewed operator-installed update before release is safe;
the release script still refuses silently replacing changed site content.

Added meaningful live verification rather than source-string assertions:

- A POSIX pytest launches the Dockerfile's real Gunicorn command, substituting only
  its WSGI fixture and ephemeral bind address, sends successful and unsuccessful
  invitation-shaped requests with a known marker in URI/query/Referer, and checks
  both captured output streams. It is explicitly skipped on Windows: importing
  Gunicorn there fails because `fcntl` is unavailable. No claim is made that this
  new test ran locally or that its red/green behavior was demonstrated locally.
- The existing isolated Docker smoke now adds a disposable nginx proxy using the
  actual vhost configuration with only test cert and isolated upstream addresses
  substituted. An opt-in CI-only probe creates a real character/invitation and
  checks actual valid 302, invalid 404 and health 200 responses with marker
  query/Referer values. Both application and proxy Docker logs are captured
  privately and searched for the real token and marker. Probe tokens and unsafe
  logs are withheld even on failure. The nginx test certificate is self-signed;
  insecure TLS is restricted to this disposable isolated proxy and does not change
  production health checks. This end-to-end Docker check remains unrun locally
  because Docker is unavailable and no workflow was executed. The smoke also stops
  only its test app, verifies nginx returns 502 for a marked invitation path, and
  checks that this upstream failure does not put the marker in proxy output.

Aligned cleanup guidance to hourly runs with a 24-hour age cutoff. Added concrete
systemd oneshot/timer files and operator installation, verification, enable and
journal commands. The service uses `config/current`, the same host release lock,
and `cleanup_imports`; it does not pull, migrate, restart, back up or restore.
The timer is hourly with <=5-minute jitter and downtime catch-up. No timer was
installed or started. systemd verification requires the eventual Linux host and
was not fabricated on this Windows workstation.

The coordinator explicitly extended ownership to `game/logging.py`,
`game/test_logging.py` and a minimal `config/settings.py` LOGGING hook. Added a
central filter that renders msg/args before replacing invitation paths, plus a
formatter that sanitizes the final exception/traceback text. Django request and
dev-server logs keep warning/error levels, response statuses, useful messages and
tracebacks; non-invitation logs are unchanged and request paths are never mutated.
Three new tests were observed failing first (missing filter module, real invalid
404 absent from the safe output, dev-server marker leaked), then passing: real
invalid invitation request through the configured Django handler, interpolated
dev-server request record, and exception/context preservation with no credential.

Fresh focused verification: 12 passed / 1 explicit POSIX-only skip in 3.12s.
Production check: no issues, 1 intentional HSTS check silenced. Probe Python
compilation passed. Self-review caught that `bash -n file1 file2 ...` only parses
the first file: corrected CI to loop over each shell script and locally reran the
same per-file loop successfully. Other agents are changing shared app files, so
the coordinator retains the stable integrated full-suite gate.

After the logging extension landed, a fresh full-suite run completed with
144 passed, 2 skipped and 30 subtests passed in 24.49s. The one warning is the
intentional duplicate ZIP member fixture in
`ArchiveTests::test_duplicate_zip_symlink_and_expanded_limit`. The two skips are
the existing Windows symlink privilege case and the new POSIX Gunicorn test.
Production check again reported no issues (1 intentional HSTS check silenced).
Per-file Bash syntax and Python compilation of the logging/probe modules passed.
Actual Docker/nginx/container smoke, Linux systemd unit verification and external
deployment checks remain unrun, as already explained above.

No server connection, workflow execution, push, automatic backup, rollback or
persistent data deletion occurred. Commit is limited to owned delivery files and
this report plus the explicitly authorized central logging extension; unrelated
shared app edits remain untouched.
