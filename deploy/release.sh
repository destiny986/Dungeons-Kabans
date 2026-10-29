#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Release requires root through sudo -n.' >&2; exit 1; }
stage=$(realpath -e "${1:?staging directory required}")
sha=${2:?commit SHA required}
registry_user=${3:?registry user required}
[[ "$stage" =~ ^/srv/dungeons-kabans/incoming/[0-9]+-[0-9]+$ ]]
[[ "$sha" =~ ^[a-f0-9]{40}$ ]]
[[ "$registry_user" =~ ^[a-z0-9][a-z0-9_-]{0,38}$ ]]
base=/srv/dungeons-kabans
exec 9>"$base/release.lock"
flock -x 9
export DOCKER_CONFIG
DOCKER_CONFIG=$(mktemp -d "$base/config/docker-auth.XXXXXXXX")
cleanup() {
    rm -f -- "$stage/secrets.json" "$DOCKER_CONFIG/config.json"
    rmdir -- "$DOCKER_CONFIG" 2>/dev/null || true
}
trap cleanup EXIT
# Verify the pre-existing nginx configuration before touching our new vhost.
nginx -t
install -d -m 700 -o 10001 -g 10001 "$base/data" "$base/imports"
install -d -m 755 -o 10001 -g 10001 "$base/media"
candidate="$base/config/releases/$sha"
install -d -m 700 "$candidate"
install -m 600 "$stage/compose.production.yaml" "$candidate/compose.production.yaml"
python3 "$stage/runtime_config.py" write "$stage/secrets.json" "$candidate"
printf 'APP_IMAGE=%s/dungeons-kabans:%s\nRUNTIME_ENV_FILE=%s/runtime.env\n' \
    "$registry_user" "$sha" "$candidate" > "$candidate/image.env"
chmod 600 "$candidate/image.env"
python3 "$stage/runtime_config.py" login "$stage/secrets.json"
bash "$stage/release-container.sh" "$candidate/compose.production.yaml" "$candidate/image.env"
# Never replace an existing vhost silently; this dedicated file is stable across releases.
site=/etc/nginx/sites-enabled/dungeons-kabans.conf
installed="$base/config/nginx.conf"
if [[ -e "$site" || -L "$site" ]]; then
    [[ -L "$site" && "$(readlink "$site")" == "$installed" ]] || {
        echo 'Dedicated nginx site already exists with unexpected ownership; inspect manually.' >&2; exit 1;
    }
    cmp -s "$stage/nginx.conf" "$installed" || {
        echo 'Nginx site changed; operator must review and install that change manually.' >&2; exit 1;
    }
    nginx -t
else
    install -m 600 "$stage/nginx.conf" "$installed"
    ln -s "$installed" "$site"
    if ! nginx -t; then
        rm -f -- "$site" "$installed"
        echo 'Candidate nginx site failed validation and was removed.' >&2
        exit 1
    fi
fi
systemctl reload nginx
curl --fail --silent --show-error --max-time 15 https://185.232.205.216:9443/healthz/ >/dev/null
# Diagnostics point to the most recently healthy release. This is never an automatic rollback target.
ln -sfn "$candidate" "$base/config/current"
echo "Release $sha healthy on https://185.232.205.216:9443"
