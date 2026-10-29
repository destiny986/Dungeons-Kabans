#!/usr/bin/env bash
set -euo pipefail
image=${1:?image required}
suffix="${GITHUB_RUN_ID:-local}-$$"
container="dk-ci-$suffix"
proxy="$container-proxy"
network="$container-network"
volumes=("$container-data" "$container-media" "$container-imports")
temporary=$(mktemp -d)
cleanup() {
    status=$?
    # Do not dump logs on failure: a logging regression could expose the probe token.
    if (( status != 0 )); then echo 'Inspect isolated CI resources; logs withheld for credential safety.' >&2; fi
    docker rm -f "$proxy" >/dev/null 2>&1 || true
    docker rm -f "$container" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
    for volume in "${volumes[@]}"; do docker volume rm "$volume" >/dev/null 2>&1 || true; done
    rm -f -- "$temporary/runtime.env" "$temporary/nginx.conf" "$temporary/fullchain.pem" \
        "$temporary/privkey.pem" "$temporary/app.log" "$temporary/proxy.log" "$temporary/probe.json" \
        "$temporary/upstream-failure.log"
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
docker network create "$network" >/dev/null
options=(--env-file "$temporary/runtime.env" --read-only --tmpfs /tmp:size=128m,mode=1777
    --cap-drop ALL --security-opt no-new-privileges
    -v "${volumes[0]}:/data" -v "${volumes[1]}:/media" -v "${volumes[2]}:/imports")
# Fresh Docker volumes need the same ownership as production's prepared bind mounts.
docker run --rm --user 0 --cap-add CHOWN "${options[@]}" "$image" \
    sh -c 'chown 10001:10001 /data /media /imports'
docker run --rm "${options[@]}" "$image" python manage.py migrate --noinput
docker run --rm "${options[@]}" "$image" python manage.py bootstrap_master
docker run --rm "${options[@]}" "$image" python manage.py check --deploy --fail-level WARNING
docker run -d --name "$container" --network "$network" --network-alias app "${options[@]}" "$image" >/dev/null
[[ "$(docker exec "$container" id -u)" == 10001 ]]
for attempt in {1..30}; do
    if docker exec "$container" python -c \
        "import json,urllib.request; req=urllib.request.Request('http://127.0.0.1:8000/healthz/',headers={'Host':'185.232.205.216','X-Forwarded-Proto':'https'}); response=urllib.request.urlopen(req,timeout=3); assert response.status==200; assert json.load(response)['status']=='ok'"; then
        healthy=true
        break
    fi
    sleep 2
done
[[ ${healthy:-false} == true ]] || { echo 'Isolated Docker smoke test failed.' >&2; exit 1; }
# Exercise the actual dedicated nginx site and image command with live invitations.
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
    -keyout "$temporary/privkey.pem" -out "$temporary/fullchain.pem" >/dev/null 2>&1
sed -e 's@http://127.0.0.1:18081@http://app:8000@' \
    -e 's@/etc/letsencrypt/live/185.232.205.216/@/test-certs/@g' \
    deploy/nginx.conf > "$temporary/nginx.conf"
docker run -d --name "$proxy" --network "$network" --network-alias logging-proxy \
    -v "$temporary/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
    -v "$temporary/fullchain.pem:/test-certs/fullchain.pem:ro" \
    -v "$temporary/privkey.pem:/test-certs/privkey.pem:ro" nginx:stable-alpine >/dev/null
for attempt in {1..30}; do
    if docker exec "$proxy" wget -q --no-check-certificate -O /dev/null https://127.0.0.1:9443/healthz/; then
        proxy_healthy=true
        break
    fi
    sleep 1
done
[[ ${proxy_healthy:-false} == true ]] || { echo 'CI logging proxy failed health check.' >&2; exit 1; }
docker exec -e DELIVERY_LOG_PROBE=isolated-ci-only "$container" \
    python deploy/logging_probe.py > "$temporary/probe.json"
# Check that an nginx upstream error cannot log the credential-bearing URI either.
docker stop "$container" >/dev/null
if docker exec "$proxy" wget --no-check-certificate -O /dev/null \
    https://127.0.0.1:9443/join/CI-INVITATION-CREDENTIAL-LOG-MARKER-invalid/ \
    > "$temporary/upstream-failure.log" 2>&1; then
    echo 'Expected isolated upstream failure after stopping test app.' >&2
    exit 1
fi
grep -q '502' "$temporary/upstream-failure.log" || {
    echo 'Isolated proxy did not report the expected upstream 502.' >&2; exit 1;
}
sleep 1
docker logs "$container" > "$temporary/app.log" 2>&1
docker logs "$proxy" > "$temporary/proxy.log" 2>&1
python3 - "$temporary" <<'PY'
import json
from pathlib import Path
import sys
directory = Path(sys.argv[1])
markers = json.loads((directory / 'probe.json').read_text())
for name in ('app.log', 'proxy.log'):
    content = (directory / name).read_text()
    if any(marker in content for marker in markers):
        raise SystemExit(f'Invitation credentials appeared in {name}; logs withheld')
print('Valid/invalid invitation URI, query and Referer absent from app/proxy logs.')
PY
