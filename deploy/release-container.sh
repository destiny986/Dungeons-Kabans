#!/usr/bin/env bash
set -euo pipefail
# Only the release phase migrates. Gunicorn startup never modifies the schema.
compose_file=${1:?compose file required}
image_env=${2:?image environment required}
compose=(docker compose --project-name dungeons-kabans --env-file "$image_env" -f "$compose_file")
"${compose[@]}" pull app
"${compose[@]}" run --rm --no-deps app python manage.py migrate --noinput
"${compose[@]}" run --rm --no-deps app python manage.py bootstrap_master
"${compose[@]}" up -d --no-deps app
for attempt in {1..30}; do
    if curl --fail --silent --show-error --max-time 5 \
        -H 'Host: 185.232.205.216' -H 'X-Forwarded-Proto: https' \
        http://127.0.0.1:18081/healthz/ >/dev/null; then
        exit 0
    fi
    sleep 2
done
echo 'Application health check failed; deployment stopped. Inspect logs manually.' >&2
exit 1
