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
