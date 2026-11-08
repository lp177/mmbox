#!/usr/bin/env bash
# mmbox host deployment: Authentik, the identity provider of mmbox accounts (client/ACCOUNTS.md), as rootless Quadlets
# of a podman user: PostgreSQL, the server and the worker on a network of their own (never the host's), the server
# published on 127.0.0.1 only. Its secrets (the secret key, the database password, the first admin's password and API
# token) are made on the first run and kept in the site's store (secret_get / secret_set: Infisical for the team). With
# MMBOX_AUTH_DOMAIN and nginx on the host, a vhost puts it at https://<domain>, allowed addresses only; the site's hook
# site_post_authentik then runs (its DNS). The image is pinned (no automatic update): change MMBOX_AUTHENTIK_TAG to move.
# Run: deploy/site-deploy.sh authentik   (with a site folder: site.example/README.md)
# Settings (environment or site.env; no secret among them):
#   MMBOX_AUTH_DOMAIN (none: no vhost), MMBOX_AUTH_ALLOW (default MMBOX_ALLOW; none: open to all), MMBOX_AUTH_PORT (9110),
#   MMBOX_AUTHENTIK_TAG (2026.8.5), MMBOX_PODMAN_USER (podman), MMBOX_NGINX_INCLUDE, MMBOX_NGINX_SERVER_EXTRA (as gateway.sh)
# Secrets it reads or makes: MMBOX_AUTHENTIK_SECRET_KEY, MMBOX_AUTHENTIK_PG_PASSWORD, MMBOX_AUTHENTIK_ADMIN_PASSWORD
# (user akadmin), MMBOX_AUTHENTIK_API_TOKEN (that admin's API token, for the configuration).

set -euo pipefail
MMBOX_TAG=mmbox-authentik
# shellcheck source=lib.sh
declare -F mmbox_host_init >/dev/null || source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DOMAIN="${MMBOX_AUTH_DOMAIN:-}"
ALLOW="${MMBOX_AUTH_ALLOW-${MMBOX_ALLOW:-}}"
PORT="${MMBOX_AUTH_PORT:-9110}"
TAG="${MMBOX_AUTHENTIK_TAG:-2026.8.5}"
IMAGE="ghcr.io/goauthentik/server:${TAG}"
PG_IMAGE="docker.io/library/postgres:16-alpine"
NET="mmbox-auth"

log() { mmbox_log "$@"; }
warn() { mmbox_warn "$@"; }
err() { mmbox_err "$@"; }

mmbox_host_init curl openssl
[[ "$TAG" =~ ^20[0-9]{2}\.[0-9]+\.[0-9]+$ ]] || { err "MMBOX_AUTHENTIK_TAG: a release such as 2026.8.5"; exit 1; }
[[ "$PORT" =~ ^[0-9]+$ ]] || { err "MMBOX_AUTH_PORT: a port"; exit 1; }
if ss -ltnH "( sport = :${PORT} )" 2>/dev/null </dev/null | grep -q . \
        && ! run_user systemctl --user --quiet is-active mmbox-auth-server.service 2>/dev/null; then
    err "127.0.0.1:${PORT} is taken by something else: set MMBOX_AUTH_PORT"
    exit 1
fi
install -d -o "$PODMAN_USER" -g "$PODMAN_USER" -m 0755 "$QUADLET_DIR"

# ---------------------------------------------------------------- secrets: made once, kept in the site's store
ensure_secret() { # NAME LENGTH -> made when missing; never printed
    local v
    v="$(secret_get "$1" 2>/dev/null </dev/null || true)"
    if [[ -z "$v" ]]; then
        v="$(openssl rand -base64 $(( $2 * 2 )) | tr -dc 'A-Za-z0-9' | head -c "$2")"
        secret_set "$1" "$v" </dev/null >&2
        log "$1 made and kept in $(secret_where)"
    fi
    printf '%s' "$v" | podu secret create --replace "$(echo "$1" | tr 'A-Z' 'a-z')" - >/dev/null
    printf '%s' "$v" | sha256sum | cut -c1-12
}
sum="$(ensure_secret MMBOX_AUTHENTIK_SECRET_KEY 60)$(ensure_secret MMBOX_AUTHENTIK_PG_PASSWORD 40)"
sum+="$(ensure_secret MMBOX_AUTHENTIK_ADMIN_PASSWORD 32)$(ensure_secret MMBOX_AUTHENTIK_API_TOKEN 64)"
sum="$(printf '%s' "$sum" | sha256sum | cut -c1-16)"

# ---------------------------------------------------------------- the units
write_unit() { # FILE CONTENT -> 1 when changed
    local f="${QUADLET_DIR}/$1"
    if [[ -f "$f" ]] && [[ "$(cat "$f")" == "$2" ]]; then echo 0; return; fi
    printf '%s\n' "$2" > "$f"
    chown "$PODMAN_USER:$PODMAN_USER" "$f"
    echo 1
}
changed=0
c="$(write_unit "${NET}.network" "[Network]
NetworkName=${NET}
Internal=false")"; changed=$(( changed + c ))
for v in pg data; do
    c="$(write_unit "mmbox-auth-${v}.volume" "[Volume]
VolumeName=mmbox-auth-${v}")"; changed=$(( changed + c ))
done
c="$(write_unit "mmbox-auth-pg.container" "[Unit]
Description=mmbox accounts: Authentik's PostgreSQL

[Container]
ContainerName=mmbox-auth-pg
Image=${PG_IMAGE}
Network=${NET}.network
Volume=mmbox-auth-pg.volume:/var/lib/postgresql/data
Environment=POSTGRES_DB=authentik
Environment=POSTGRES_USER=authentik
Secret=mmbox_authentik_pg_password,type=env,target=POSTGRES_PASSWORD
Label=secret.sum=${sum}
HealthCmd=pg_isready -d authentik -U authentik
HealthInterval=30s
NoNewPrivileges=true

[Service]
Restart=always
RestartSec=5s

[Install]
WantedBy=default.target")"; changed=$(( changed + c ))
for role in server worker; do
    publish=""
    [[ "$role" == server ]] && publish="PublishPort=127.0.0.1:${PORT}:9000"
    c="$(write_unit "mmbox-auth-${role}.container" "[Unit]
Description=mmbox accounts: Authentik ${role}
After=mmbox-auth-pg.service
Requires=mmbox-auth-pg.service

[Container]
ContainerName=mmbox-auth-${role}
Image=${IMAGE}
Exec=${role}
Network=${NET}.network
${publish}
Volume=mmbox-auth-data.volume:/data
ShmSize=512m
Environment=AUTHENTIK_POSTGRESQL__HOST=mmbox-auth-pg
Environment=AUTHENTIK_POSTGRESQL__NAME=authentik
Environment=AUTHENTIK_POSTGRESQL__USER=authentik
Environment=AUTHENTIK_ERROR_REPORTING__ENABLED=false
Environment=AUTHENTIK_DISABLE_UPDATE_CHECK=true
Environment=AUTHENTIK_DISABLE_STARTUP_ANALYTICS=true
Secret=mmbox_authentik_secret_key,type=env,target=AUTHENTIK_SECRET_KEY
Secret=mmbox_authentik_pg_password,type=env,target=AUTHENTIK_POSTGRESQL__PASSWORD
Secret=mmbox_authentik_admin_password,type=env,target=AUTHENTIK_BOOTSTRAP_PASSWORD
Secret=mmbox_authentik_api_token,type=env,target=AUTHENTIK_BOOTSTRAP_TOKEN
Label=secret.sum=${sum}
NoNewPrivileges=true

[Service]
Restart=always
RestartSec=10s

[Install]
WantedBy=default.target")"; changed=$(( changed + c ))
done
run_user systemctl --user daemon-reload
podu pull "$PG_IMAGE" </dev/null >/dev/null
podu pull "$IMAGE" </dev/null >/dev/null
for u in mmbox-auth-pg mmbox-auth-server mmbox-auth-worker; do
    if [[ "$changed" -gt 0 ]] || ! run_user systemctl --user --quiet is-active "${u}.service" 2>/dev/null; then
        run_user systemctl --user restart "${u}.service"
        log "${u} (re)started"
    fi
done
ok=0
for _ in $(seq 1 90); do  # the first start migrates the database: a minute or two
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${PORT}/-/health/ready/" </dev/null || true)"
    if [[ "$code" == 200 || "$code" == 204 ]]; then ok=1; break; fi
    sleep 2
done
if [[ "$ok" -ne 1 ]]; then
    err "Authentik not ready on 127.0.0.1:${PORT} — recent logs:"
    podu logs --tail 30 mmbox-auth-server </dev/null 2>&1 | tail -30 >&2 || true
    podu logs --tail 15 mmbox-auth-worker </dev/null 2>&1 | tail -15 >&2 || true
    exit 1
fi
log "Authentik ${TAG} ready on 127.0.0.1:${PORT} (admin: akadmin, its password and API token in $(secret_where))"

# ---------------------------------------------------------------- its configuration for mmbox (authentik-config.py)
CONF_PY="${MMBOX_CONFIG_PY:-}"
[[ -n "$CONF_PY" ]] || CONF_PY="$(cat "$(dirname "${BASH_SOURCE[0]}")/authentik-config.py" 2>/dev/null || true)"
if [[ -z "$CONF_PY" ]] || ! command -v python3 >/dev/null; then
    warn "authentik-config.py or python3 missing: Authentik is not configured for mmbox"
else
    for _ in $(seq 1 30); do  # the API token is made by the worker's first run: wait for it to be accepted
        code="$(AK_TOKEN="$(secret_get MMBOX_AUTHENTIK_API_TOKEN </dev/null)" python3 -c '
import os, urllib.request
r = urllib.request.Request("http://127.0.0.1:'"${PORT}"'/api/v3/core/users/me/", headers={"Authorization": "Bearer " + os.environ["AK_TOKEN"]})
try:
    print(urllib.request.urlopen(r, timeout=5).status)
except Exception as e:
    print(getattr(e, "code", 0))' 2>/dev/null </dev/null || true)"
        [[ "$code" == 200 ]] && break
        sleep 4
    done
    env AK_URL="http://127.0.0.1:${PORT}" AK_PUBLIC="https://${DOMAIN:-127.0.0.1:${PORT}}" \
        AK_TOKEN="$(secret_get MMBOX_AUTHENTIK_API_TOKEN </dev/null)" \
        MMBOX_AUTH_DISCORD_CLIENT_ID="$(secret_get MMBOX_AUTH_DISCORD_CLIENT_ID </dev/null || true)" \
        MMBOX_AUTH_DISCORD_CLIENT_SECRET="$(secret_get MMBOX_AUTH_DISCORD_CLIENT_SECRET </dev/null || true)" \
        MMBOX_AUTH_GOOGLE_CLIENT_ID="$(secret_get MMBOX_AUTH_GOOGLE_CLIENT_ID </dev/null || true)" \
        MMBOX_AUTH_GOOGLE_CLIENT_SECRET="$(secret_get MMBOX_AUTH_GOOGLE_CLIENT_SECRET </dev/null || true)" \
        python3 - <<<"$CONF_PY" 2>&1 | while IFS= read -r line; do log "config: ${line}"; done
fi

# ---------------------------------------------------------------- nginx: allowed addresses only, websockets
if [[ -z "$DOMAIN" || "${MMBOX_NGINX:-1}" == 0 ]] || ! command -v nginx >/dev/null; then
    log "no vhost (MMBOX_AUTH_DOMAIN unset or no nginx): Authentik answers on 127.0.0.1:${PORT}"
else
    VH="server {
    listen 443 ssl;
    server_name ${DOMAIN};"
    if [[ -n "${MMBOX_NGINX_SERVER_EXTRA:-}" ]]; then
        while IFS= read -r line; do [[ -n "$line" ]] && VH+=$'\n'"    ${line}"; done <<<"$MMBOX_NGINX_SERVER_EXTRA"
    fi
    VH+=$'\n'
    if [[ -n "$ALLOW" ]]; then
        for ip in $ALLOW; do VH+=$'\n'"    allow ${ip};"; done
        VH+=$'\n'"    deny  all;"$'\n'
    fi
    VH+="
    client_max_body_size 8m;

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_read_timeout 120s;"
    VH+='
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $http_connection;'
    if [[ -n "${MMBOX_NGINX_INCLUDE:-}" ]]; then
        VH+=$'\n'"        include ${MMBOX_NGINX_INCLUDE};"
    else
        VH+='
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;'
    fi
    VH+="
    }
}"
    AV="/etc/nginx/sites-available/${DOMAIN}.conf" EN="/etc/nginx/sites-enabled/${DOMAIN}.conf"
    if [[ -f "$AV" ]] && [[ "$(cat "$AV")" == "$VH" ]]; then
        log "nginx vhost already current"
    else
        BAK=""
        if [[ -f "$AV" ]]; then BAK="${AV}.bak.$$"; cp -a "$AV" "$BAK"; fi
        printf '%s\n' "$VH" > "$AV"
        ln -sfn "$AV" "$EN"
        if nginx -t 2>/dev/null; then
            systemctl reload nginx
            [[ -n "$BAK" ]] && rm -f "$BAK"
            log "nginx vhost ${DOMAIN} updated and reloaded"
        else
            if [[ -n "$BAK" ]]; then mv -f "$BAK" "$AV"; else rm -f "$AV" "$EN"; fi
            err "nginx -t failed; previous vhost restored; Authentik still answers on 127.0.0.1:${PORT}"
            exit 1
        fi
    fi
fi
if declare -F site_post_authentik >/dev/null; then site_post_authentik; fi
if [[ -n "$DOMAIN" ]] && command -v nginx >/dev/null; then
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/-/health/ready/" </dev/null || true)"
    log "https://${DOMAIN}/-/health/ready/ from this host: HTTP ${code} (200/204 from an allowed address, 403 from elsewhere)"
fi
