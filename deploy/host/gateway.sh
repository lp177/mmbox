#!/usr/bin/env bash
# mmbox host deployment: the gateway (the mailbox's HTTP front) as a rootless Quadlet of a podman user, next to the
# Redis of redis.sh (127.0.0.1:6390), which it reaches as the limited user mmbox-gateway (its password from the site's
# store, as a podman secret). The image is built on the host from the repository (git; an ssh URL with the host's
# deploy key), or from MMBOX_SRC_DIR when the source was copied there beforehand. With MMBOX_DOMAIN and nginx on the
# host, an nginx vhost puts it at https://<domain> (allowed addresses only, when MMBOX_ALLOW lists some); the site's
# hook site_post_gateway then runs (its DNS, for instance).
# Run: deploy/site-deploy.sh gateway        (after redis; with a site folder: site.example/README.md)
#      or on the host itself, as root: bash deploy/host/gateway.sh
# Settings (environment or site.env; no secret among them):
#   MMBOX_REPO (https://github.com/lp177/mmbox.git), MMBOX_BRANCH (master), MMBOX_DEPLOY_KEY (/root/.ssh/mmbox_deploy),
#   MMBOX_SRC_DIR, MMBOX_GATEWAY_PORT (8095), MMBOX_REDIS_PORT (6390), MMBOX_LOOPBACK_IP (169.254.1.3),
#   MMBOX_PODMAN_USER (podman),
#   MMBOX_OPERATORS=<console id>[,<id>]   mmbox consoles that read the whole table of active boxes
#   MMBOX_OPERATOR_ACCOUNTS=<root fingerprint>[,…]   accounts whose active devices are operators (client/ACCOUNTS.md)
#   MMBOX_COLLECTOR=1 [MMBOX_COLLECTOR_DAYS=7]   the collector's journal, read by those operators
#   MMBOX_DOMAIN (none: no vhost), MMBOX_NGINX (1; 0: no vhost), MMBOX_ALLOW ("10.8.0.0/24 192.0.2.7": the allowed
#   addresses; none: open to all), MMBOX_NGINX_SERVER_EXTRA (lines for the server block: ssl_certificate…),
#   MMBOX_NGINX_INCLUDE (a file included in the location instead of the default proxy headers)

set -euo pipefail
MMBOX_TAG=mmbox-gateway
# shellcheck source=lib.sh
declare -F mmbox_host_init >/dev/null || source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DOMAIN="${MMBOX_DOMAIN:-}"
REPO_URL="${MMBOX_REPO:-https://github.com/lp177/mmbox.git}"
BRANCH="${MMBOX_BRANCH:-master}"
SRC_OVERRIDE="${MMBOX_SRC_DIR:-}"
IMAGE="localhost/mmbox-gateway"
PORT="${MMBOX_GATEWAY_PORT:-8095}"
REDIS_PORT="${MMBOX_REDIS_PORT:-6390}"
ALLOW="${MMBOX_ALLOW:-}"
DEPLOY_KEY="${MMBOX_DEPLOY_KEY:-/root/.ssh/mmbox_deploy}"
LOOPBACK_IP="${MMBOX_LOOPBACK_IP:-169.254.1.3}"
UNIT="mmbox-gateway"
SECRET="mmbox_redis_gateway"

log() { mmbox_log "$@"; }
warn() { mmbox_warn "$@"; }
err() { mmbox_err "$@"; }

mmbox_host_init git curl
APP_DIR="${PODMAN_HOME}/mmbox"
UNIT_FILE="${QUADLET_DIR}/${UNIT}.container"
NGINX_AVAIL="/etc/nginx/sites-available/${DOMAIN}.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/${DOMAIN}.conf"

run_user systemctl --user --quiet is-active mmbox-redis.service 2>/dev/null \
    || { err "mmbox-redis is not running: deploy redis.sh first"; exit 1; }
install -d -o "$PODMAN_USER" -g "$PODMAN_USER" -m 0755 "$QUADLET_DIR" "$APP_DIR"

# ---------------------------------------------------------------- source and image
if [[ -n "$SRC_OVERRIDE" ]]; then
    [[ -f "${SRC_OVERRIDE}/gateway/Containerfile" ]] || { err "MMBOX_SRC_DIR=${SRC_OVERRIDE}: no gateway/Containerfile there"; exit 1; }
    BUILD_FROM="${SRC_OVERRIDE}/gateway"
    NEW_SHA="src-$(find "$BUILD_FROM" -type f ! -path '*/__pycache__/*' -print0 | sort -z | xargs -0 cat | sha256sum | cut -c1-12)"
    log "building from ${BUILD_FROM} (${NEW_SHA}), not from git"
else
    g() {  # an ssh URL uses the deploy key when the host has it; an https URL needs none
        if [[ -f "$DEPLOY_KEY" ]]; then
            GIT_SSH_COMMAND="ssh -i ${DEPLOY_KEY} -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
                git -C "$APP_DIR" -c safe.directory="$APP_DIR" "$@" </dev/null
        else
            GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
                git -C "$APP_DIR" -c safe.directory="$APP_DIR" "$@" </dev/null
        fi
    }
    [[ -d "${APP_DIR}/.git" ]] || { g init -q; g remote add origin "$REPO_URL" 2>/dev/null || true; }
    g remote set-url origin "$REPO_URL"
    if ! g fetch --prune origin "$BRANCH"; then
        err "git fetch of ${REPO_URL} failed: for a private repository, give ${DEPLOY_KEY}.pub read access to it,"
        err "or copy the source and set MMBOX_SRC_DIR (see the header of this script)"
        exit 1
    fi
    g reset -q --hard "origin/${BRANCH}"
    chown -R "$PODMAN_USER:$PODMAN_USER" "$APP_DIR"
    NEW_SHA="$(g rev-parse --short HEAD)"
    BUILD_FROM="${APP_DIR}/gateway"
fi
built="$(podu image inspect --format '{{ index .Config.Labels "src.sha" }}' "${IMAGE}:latest" 2>/dev/null </dev/null || true)"
old_img="$(podu image inspect --format '{{.Id}}' "${IMAGE}:latest" 2>/dev/null </dev/null || true)"  # removed once the new one is healthy
if [[ "$built" == "$NEW_SHA" ]]; then
    log "image already built from ${NEW_SHA}"
else
    CTX="$(sudo -u "$PODMAN_USER" mktemp -d /tmp/mmbox-gateway-build.XXXXXX)"
    cp -a "${BUILD_FROM}/." "$CTX/"
    rm -rf "$CTX/__pycache__"
    chown -R "$PODMAN_USER:$PODMAN_USER" "$CTX"
    podu build --pull=missing --label "src.sha=${NEW_SHA}" -t "${IMAGE}:latest" "$CTX" </dev/null >/dev/null
    rm -rf "$CTX"
    log "image ${IMAGE}:latest built from ${NEW_SHA}"
fi

# ---------------------------------------------------------------- the Redis password, as a podman secret
GW_PW="$(secret_get MMBOX_REDIS_GATEWAY_PASSWORD 2>/dev/null </dev/null || true)"
[[ -n "$GW_PW" ]] || { err "MMBOX_REDIS_GATEWAY_PASSWORD missing from $(secret_where): run redis.sh first"; exit 1; }
printf '%s' "$GW_PW" | podu secret create --replace "$SECRET" - >/dev/null
pw_sum="$(printf '%s' "$GW_PW" | sha256sum | cut -c1-16)"
unset GW_PW

# ---------------------------------------------------------------- the unit
OPERATORS="${MMBOX_OPERATORS:-}"
OP_ACCOUNTS="${MMBOX_OPERATOR_ACCOUNTS:-}"
[[ -z "$OP_ACCOUNTS" || "$OP_ACCOUNTS" =~ ^[0-9A-F]{4}(-[0-9A-F]{4}){3}(,[0-9A-F]{4}(-[0-9A-F]{4}){3})*$ ]] \
    || { err "MMBOX_OPERATOR_ACCOUNTS: account root fingerprints, comma separated"; exit 1; }
[[ -z "$OPERATORS" || "$OPERATORS" =~ ^m[0-9a-z]{12}(,m[0-9a-z]{12})*$ ]] || { err "MMBOX_OPERATORS: mmbox ids, comma separated"; exit 1; }
COL_ON="$([[ "${MMBOX_COLLECTOR:-}" =~ ^(1|on|yes|true)$ ]] && echo 1 || echo 0)"
COL_DAYS="${MMBOX_COLLECTOR_DAYS:-7}"
[[ "$COL_DAYS" =~ ^[0-9]+([.][0-9]+)?$ ]] || { err "MMBOX_COLLECTOR_DAYS: a number of days"; exit 1; }
# HealthCmd takes no quotes: an inline python -c "..." came out of Quadlet as an unbalanced string for the
# container's sh (always unhealthy), so the probe lives in the gateway (--health). The heredoc below expands
# $ and backquotes: keep both out of its comments.
NEW_UNIT="$(cat <<UNIT_EOF
[Unit]
Description=mmbox gateway (mailbox transport, Redis Streams)
After=network-online.target mmbox-redis.service
Wants=network-online.target mmbox-redis.service

[Container]
ContainerName=${UNIT}
Image=${IMAGE}:latest
Label=secret.sum=${pw_sum}
PublishPort=127.0.0.1:${PORT}:8095
# pasta maps the host's 127.0.0.1 (where mmbox-redis listens) to ${LOOPBACK_IP} inside the container
Network=pasta:--map-host-loopback,${LOOPBACK_IP}
Environment=MMBOX_REDIS_URL=redis://${LOOPBACK_IP}:${REDIS_PORT}/0
Environment=MMBOX_REDIS_USER=mmbox-gateway
Environment=MMBOX_OPERATORS=${OPERATORS}
Environment=MMBOX_OPERATOR_ACCOUNTS=${OP_ACCOUNTS}
Environment=MMBOX_COLLECTOR=${COL_ON}
Environment=MMBOX_COLLECTOR_DAYS=${COL_DAYS}
Secret=${SECRET},type=mount,target=/run/secrets/mmbox_redis_gateway,uid=10001,mode=0400
NoNewPrivileges=true
HealthCmd=python mmbox_gateway.py --health
HealthInterval=30s

[Service]
Restart=always
RestartSec=5s

[Install]
WantedBy=default.target
UNIT_EOF
)"
unit_changed=1
if [[ -f "$UNIT_FILE" ]] && [[ "$(cat "$UNIT_FILE")" == "$NEW_UNIT" ]]; then unit_changed=0; fi
printf '%s\n' "$NEW_UNIT" > "$UNIT_FILE"
chown "$PODMAN_USER:$PODMAN_USER" "$UNIT_FILE"
run_user systemctl --user daemon-reload
restart=0
if [[ "$unit_changed" -eq 1 ]]; then restart=1; fi
run_user systemctl --user --quiet is-active "${UNIT}.service" 2>/dev/null || restart=1
new_img="$(podu image inspect --format '{{.Id}}' "${IMAGE}:latest" 2>/dev/null </dev/null || true)"
cur_img="$(podu inspect --format '{{.Image}}' "$UNIT" 2>/dev/null </dev/null || true)"
if [[ -n "$new_img" && "$new_img" != "$cur_img" ]]; then restart=1; fi
if [[ "$restart" -eq 1 ]]; then
    log "(re)starting ${UNIT} (unit_changed=${unit_changed}, source ${NEW_SHA})"
    run_user systemctl --user restart "${UNIT}.service"
else
    log "unit and image unchanged and running: no restart"
fi
ok=0
for _ in $(seq 1 40); do
    if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/v1/health" </dev/null 2>/dev/null | grep -q '"redis": "up"'; then ok=1; break; fi
    sleep 1
done
if [[ "$ok" -ne 1 ]]; then
    err "${UNIT} not healthy on 127.0.0.1:${PORT} (or Redis unreachable from it) — recent logs:"
    podu logs --tail 40 "$UNIT" </dev/null 2>&1 | tail -40 >&2 || true
    err "aborting before the proxy's changes"
    exit 1
fi
log "healthy on 127.0.0.1:${PORT}, Redis reachable"
if [[ -n "$old_img" && "$old_img" != "$new_img" && -z "$(podu ps -a -q --filter "ancestor=${old_img}" </dev/null 2>/dev/null)" ]]; then
    podu rmi "$old_img" </dev/null >/dev/null 2>&1 && log "previous image ${old_img:0:12} removed" || true
fi

# ---------------------------------------------------------------- nginx: allowed addresses only, long polls up to 30 s
if [[ -z "$DOMAIN" || "${MMBOX_NGINX:-1}" == 0 ]] || ! command -v nginx >/dev/null; then
    log "no vhost (MMBOX_DOMAIN unset, MMBOX_NGINX=0 or no nginx): the gateway answers on 127.0.0.1:${PORT}"
else
    NEW_VHOST="server {
    listen 443 ssl;
    server_name ${DOMAIN};"
    if [[ -n "${MMBOX_NGINX_SERVER_EXTRA:-}" ]]; then
        while IFS= read -r line; do [[ -n "$line" ]] && NEW_VHOST+=$'\n'"    ${line}"; done <<<"$MMBOX_NGINX_SERVER_EXTRA"
    fi
    NEW_VHOST+=$'\n'
    if [[ -n "$ALLOW" ]]; then
        for ip in $ALLOW; do NEW_VHOST+=$'\n'"    allow ${ip};"; done
        NEW_VHOST+=$'\n'"    deny  all;"$'\n'
    fi
    NEW_VHOST+="
    client_max_body_size 24m;

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_read_timeout 90s;
        proxy_buffering off;"
    if [[ -n "${MMBOX_NGINX_INCLUDE:-}" ]]; then
        NEW_VHOST+=$'\n'"        include ${MMBOX_NGINX_INCLUDE};"
    else
        NEW_VHOST+='
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;'
    fi
    NEW_VHOST+="
    }
}"
    vhost_changed=1
    if [[ -f "$NGINX_AVAIL" ]] && [[ "$(cat "$NGINX_AVAIL")" == "$NEW_VHOST" ]]; then vhost_changed=0; fi
    if [[ "$vhost_changed" -eq 1 ]]; then
        NGINX_BAK=""
        if [[ -f "$NGINX_AVAIL" ]]; then NGINX_BAK="${NGINX_AVAIL}.bak.$$"; cp -a "$NGINX_AVAIL" "$NGINX_BAK"; fi
        printf '%s\n' "$NEW_VHOST" > "$NGINX_AVAIL"
        ln -sfn "$NGINX_AVAIL" "$NGINX_ENABLED"
        if nginx -t 2>/dev/null; then
            systemctl reload nginx
            if [[ -n "$NGINX_BAK" ]]; then rm -f "$NGINX_BAK"; fi
            log "nginx vhost ${DOMAIN} updated and reloaded"
        else
            if [[ -n "$NGINX_BAK" ]]; then mv -f "$NGINX_BAK" "$NGINX_AVAIL"; else rm -f "$NGINX_AVAIL" "$NGINX_ENABLED"; fi
            err "nginx -t failed; previous vhost restored; the gateway still runs on 127.0.0.1:${PORT}"
            exit 1
        fi
    else
        log "nginx vhost already current"
    fi
fi

# ---------------------------------------------------------------- the site's own last steps (its DNS…), optional
if declare -F site_post_gateway >/dev/null; then site_post_gateway; fi
if [[ -n "$DOMAIN" && "${MMBOX_NGINX:-1}" != 0 ]] && command -v nginx >/dev/null; then
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/v1/health" </dev/null || true)"
    log "https://${DOMAIN}/v1/health from this host: HTTP ${code} (200 expected from an allowed address, 403 from elsewhere)"
fi
