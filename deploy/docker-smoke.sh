#!/usr/bin/env bash
set -euo pipefail
image=${1:?image required}
suffix="${GITHUB_RUN_ID:-local}-$$"
container="dk-ci-$suffix"
volumes=("$container-data" "$container-media" "$container-imports")
temporary=$(mktemp -d)
cleanup() {
    status=$?
    if (( status != 0 )); then docker logs "$container" 2>/dev/null || true; fi
    docker rm -f "$container" >/dev/null 2>&1 || true
    for volume in "${volumes[@]}"; do docker volume rm "$volume" >/dev/null 2>&1 || true; done
    rm -f -- "$temporary/runtime.env"
    rmdir -- "$temporary"
    exit "$status"
}
trap cleanup EXIT
umask 077
cat > "$temporary/runtime.env" <<'ENV'
DJANGO_ENV=production
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=ci-only-isolated-smoke-test-secret-0123456789-abcdefghijk
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,185.232.205.216
DJANGO_CSRF_TRUSTED_ORIGINS=https://185.232.205.216:9443
PUBLIC_BASE_URL=https://185.232.205.216:9443
MASTER_PASSWORD=ci-only-master-password-0123456789
DATA_DIR=/data
MEDIA_ROOT=/media
IMPORT_ROOT=/imports
ENV
for volume in "${volumes[@]}"; do docker volume create "$volume" >/dev/null; done
options=(--env-file "$temporary/runtime.env" --read-only --tmpfs /tmp:size=128m,mode=1777
    --cap-drop ALL --security-opt no-new-privileges
    -v "${volumes[0]}:/data" -v "${volumes[1]}:/media" -v "${volumes[2]}:/imports")
# Fresh Docker volumes need the same ownership as production's prepared bind mounts.
docker run --rm --user 0 --cap-add CHOWN "${options[@]}" "$image" \
    sh -c 'chown 10001:10001 /data /media /imports'
docker run --rm "${options[@]}" "$image" python manage.py migrate --noinput
docker run --rm "${options[@]}" "$image" python manage.py bootstrap_master
docker run --rm "${options[@]}" "$image" python manage.py check --deploy --fail-level WARNING
docker run -d --name "$container" "${options[@]}" "$image" >/dev/null
[[ "$(docker exec "$container" id -u)" == 10001 ]]
for attempt in {1..30}; do
    if docker exec "$container" python -c \
        "import json,urllib.request; req=urllib.request.Request('http://127.0.0.1:8000/healthz/',headers={'Host':'185.232.205.216','X-Forwarded-Proto':'https'}); response=urllib.request.urlopen(req,timeout=3); assert response.status==200; assert json.load(response)['status']=='ok'"; then
        exit 0
    fi
    sleep 2
done
echo 'Isolated Docker smoke test failed.' >&2
exit 1
