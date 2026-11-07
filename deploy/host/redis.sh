#!/usr/bin/env bash
# mmbox host deployment: a dedicated Redis 8 (Streams) as a rootless Quadlet of a podman user, private to the host
# (127.0.0.1:6390): the mmbox gateway (gateway.sh) is its only client. Two passwords, made on the first run and kept
# where the site says (secret_get / secret_set: a root-only file by default, a secrets manager through the site's
# hooks.sh): MMBOX_REDIS_ADMIN_PASSWORD (user mmbox-admin, every command) and MMBOX_REDIS_GATEWAY_PASSWORD (user
# mmbox-gateway, stream / hash / string commands on mmbox:* keys only). The ACL reaches the container as a podman
# secret, never as a file in a home; the default user is off. Messages survive a restart and are never evicted: AOF
# every second, RDB snapshots, maxmemory-policy noeviction. Needs podman >= 4.7 and the user MMBOX_PODMAN_USER.
# Run: deploy/site-deploy.sh redis          (with a site folder: site.example/README.md)
#      or on the host itself, as root: bash deploy/host/redis.sh
# Settings (environment or site.env): MMBOX_REDIS_IMAGE, MMBOX_REDIS_PORT (6390), MMBOX_REDIS_MAXMEMORY (6gb),
# MMBOX_PODMAN_USER (podman).

set -euo pipefail
MMBOX_TAG=mmbox-redis
# shellcheck source=lib.sh
declare -F mmbox_host_init >/dev/null || source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

IMAGE="${MMBOX_REDIS_IMAGE:-docker.io/library/redis:8-alpine}"
PORT="${MMBOX_REDIS_PORT:-6390}"
MAXMEMORY="${MMBOX_REDIS_MAXMEMORY:-6gb}"
UNIT="mmbox-redis"
VOLUME="mmbox-redis-data"
SECRET="mmbox_redis_acl"

log() { mmbox_log "$@"; }
err() { mmbox_err "$@"; }

mmbox_host_init openssl sha256sum curl jq
APP_DIR="${PODMAN_HOME}/mmbox-redis"
UNIT_FILE="${QUADLET_DIR}/${UNIT}.container"
ACL_SUM="${APP_DIR}/acl.sha256"   # a hash of the ACL, to know when to restart: never the ACL itself

loginctl enable-linger "$PODMAN_USER" >/dev/null 2>&1 || true
[[ -d "/run/user/${PODMAN_UID}" ]] || systemctl start "user@${PODMAN_UID}.service" 2>/dev/null || true
install -d -o "$PODMAN_USER" -g "$PODMAN_USER" -m 0755 "$QUADLET_DIR"
install -d -o "$PODMAN_USER" -g "$PODMAN_USER" -m 0700 "$APP_DIR"

# ---------------------------------------------------------------- secrets (the site's store) as a podman secret
secret_of() { # NAME: the value kept in the site's store, made (64 hex) and stored there on the first run
    local v
    v="$(secret_get "$1" 2>/dev/null </dev/null || true)"
    if [[ -z "$v" ]]; then
        v="$(openssl rand -hex 32)"
        secret_set "$1" "$v" </dev/null >/dev/null || { err "could not store $1 in $(secret_where): nothing made"; return 1; }
        log "$1 made, kept in $(secret_where)"
    fi
    printf '%s' "$v"
}
ADMIN_PW="$(secret_of MMBOX_REDIS_ADMIN_PASSWORD)"
GATEWAY_PW="$(secret_of MMBOX_REDIS_GATEWAY_PASSWORD)"
GATEWAY_CMDS="-@all +ping +hello +auth +client|setname +client|id +select +xadd +xreadgroup +xread +xack +xclaim +xautoclaim"
GATEWAY_CMDS+=" +xpending +xrange +xrevrange +xinfo|stream +xinfo|groups +xinfo|consumers +xgroup|create"
GATEWAY_CMDS+=" +xgroup|createconsumer +xgroup|delconsumer +xgroup|setid +xdel +xtrim +xlen +get +set +del +unlink +exists"
GATEWAY_CMDS+=" +expire +pexpire +ttl +pttl +hset +hsetnx +hget +hmget +hgetall +hdel +hexists +hexpire +scan +multi +exec +discard +watch"
GATEWAY_CMDS+=" +info +memory|usage +dbsize"  # the memory figures of /v1/usage (read only)
ACL="user default off
user mmbox-admin on >${ADMIN_PW} ~* &* +@all
user mmbox-gateway on >${GATEWAY_PW} ~mmbox:* &mmbox:* ${GATEWAY_CMDS}"
acl_sum="$(printf '%s' "$ACL" | sha256sum | cut -d' ' -f1)"
printf '%s\n' "$ACL" | podu secret create --replace "$SECRET" - >/dev/null
acl_changed=1
if [[ -f "$ACL_SUM" ]] && [[ "$(cat "$ACL_SUM")" == "$acl_sum" ]]; then acl_changed=0; fi
printf '%s\n' "$acl_sum" > "$ACL_SUM"
chown "$PODMAN_USER:$PODMAN_USER" "$ACL_SUM"
chmod 600 "$ACL_SUM"

# ---------------------------------------------------------------- the unit
# The image's entrypoint chowns /data and drops to its redis user (uid 999): the secret is mounted for that uid.
NEW_UNIT="$(cat <<UNIT_EOF
[Unit]
Description=mmbox mailbox transport (Redis 8 Streams)
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=${UNIT}
Image=${IMAGE}
Exec=redis-server --aclfile /run/secrets/${SECRET} --appendonly yes --appendfsync everysec --save "3600 1 300 100" --maxmemory ${MAXMEMORY} --maxmemory-policy noeviction --protected-mode yes --tcp-keepalive 60
Secret=${SECRET},type=mount,uid=999,gid=999,mode=0400
Volume=${VOLUME}:/data
PublishPort=127.0.0.1:${PORT}:6379
# no ReadOnly=true: runc cannot create the secret's mount point on a read-only rootfs (podman 5.4, tested)
Tmpfs=/tmp
NoNewPrivileges=true
HealthCmd=sh -c 'redis-cli PING 2>&1 | grep -qE "PONG|NOAUTH"'
HealthInterval=30s

[Service]
Restart=always
RestartSec=5s
TimeoutStartSec=120

[Install]
WantedBy=default.target
UNIT_EOF
)"
unit_changed=1
if [[ -f "$UNIT_FILE" ]] && [[ "$(cat "$UNIT_FILE")" == "$NEW_UNIT" ]]; then unit_changed=0; fi
printf '%s\n' "$NEW_UNIT" > "$UNIT_FILE"
chown "$PODMAN_USER:$PODMAN_USER" "$UNIT_FILE"
podu pull --quiet "$IMAGE" </dev/null >/dev/null
run_user systemctl --user daemon-reload

restart=0
if [[ "$unit_changed" -eq 1 || "$acl_changed" -eq 1 ]]; then restart=1; fi
run_user systemctl --user --quiet is-active "${UNIT}.service" 2>/dev/null || restart=1
new_img="$(podu image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || true)"
cur_img="$(podu inspect --format '{{.Image}}' "$UNIT" 2>/dev/null || true)"
if [[ -n "$new_img" && "$new_img" != "$cur_img" ]]; then restart=1; fi
if [[ "$restart" -eq 1 ]]; then
    log "(re)starting ${UNIT} (unit_changed=${unit_changed}, acl_changed=${acl_changed})"
    run_user systemctl --user restart "${UNIT}.service"
else
    log "unit, ACL and image unchanged and already running: no restart"
fi

# ---------------------------------------------------------------- health: unauthenticated refused, gateway user limited
ok=0
for _ in $(seq 1 30); do
    if podu exec "$UNIT" redis-cli PING </dev/null 2>&1 | grep -q NOAUTH; then ok=1; break; fi
    sleep 1
done
if [[ "$ok" -ne 1 ]]; then
    err "${UNIT} does not answer (or answers without authentication) — recent logs:"
    podu logs --tail 40 "$UNIT" 2>&1 | tail -40 >&2 || true
    exit 1
fi
# passwords go through a pipe, never a command line
out="$(printf 'AUTH mmbox-gateway %s\nXADD mmbox:selftest * deploy ok\nDEL mmbox:selftest\nFLUSHALL\n' "$GATEWAY_PW" \
       | podu exec -i "$UNIT" redis-cli 2>&1 || true)"
if ! grep -q '^OK' <<<"$out" || ! grep -qi 'NOPERM' <<<"$out"; then
    err "ACL check failed (expected AUTH OK, XADD and DEL allowed, FLUSHALL refused)"
    exit 1
fi
conf="$(printf 'AUTH mmbox-admin %s\nCONFIG GET appendonly\nCONFIG GET maxmemory-policy\n' "$ADMIN_PW" \
        | podu exec -i "$UNIT" redis-cli 2>&1 | tail -n +2 | paste -sd' ')"
log "healthy on 127.0.0.1:${PORT}: default user off, mmbox-gateway limited to mmbox:* (FLUSHALL refused); ${conf}"
run_user systemctl --user --no-pager status "${UNIT}.service" 2>/dev/null | head -8 >&2 || true
