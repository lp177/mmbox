# mmbox host deployment: what redis.sh and gateway.sh share. Sourced by them when run on the host itself, or put in
# front of them by deploy/site-deploy.sh, followed by the site's site.env and hooks.sh (which may override any function
# below). Runs as root on the target, from stdin (bash -s): every command that could read stdin gets </dev/null.
# shellcheck shell=bash

: "${MMBOX_PODMAN_USER:=podman}"   # the rootless user that owns the Quadlet units (made by the site, not here)
: "${MMBOX_SECRETS_FILE:=/etc/mmbox/secrets.env}"

mmbox_log() { printf '\033[1;32m[%s]\033[0m %s\n' "${MMBOX_TAG:-mmbox}" "$*" >&2; }
mmbox_warn() { printf '\033[1;33m[%s] WARN:\033[0m %s\n' "${MMBOX_TAG:-mmbox}" "$*" >&2; }
mmbox_err() { printf '\033[1;31m[%s] ERROR:\033[0m %s\n' "${MMBOX_TAG:-mmbox}" "$*" >&2; }

# Secrets: by default a root-only file on the host (NAME=value lines, 0600 in a 0700 folder), made on the first run.
# A site keeps them in its secrets manager instead by defining secret_get and secret_set in its hooks.sh
# (site.example/hooks.sh.example shows Infisical). A value never goes on a command line nor in a log.
secret_get() { # NAME -> the value on stdout, nothing when unknown
    [[ -f "$MMBOX_SECRETS_FILE" ]] || return 0
    sed -n "s/^$1=//p" "$MMBOX_SECRETS_FILE" </dev/null | tail -n 1
}
secret_set() { # NAME VALUE
    local dir tmp
    dir="$(dirname "$MMBOX_SECRETS_FILE")"
    install -d -m 0700 "$dir"
    tmp="$(mktemp "${dir}/.secrets.XXXXXX")"
    { [[ -f "$MMBOX_SECRETS_FILE" ]] && grep -v "^$1=" "$MMBOX_SECRETS_FILE" </dev/null; printf '%s=%s\n' "$1" "$2"; } > "$tmp" || true
    chmod 0600 "$tmp"
    mv -f "$tmp" "$MMBOX_SECRETS_FILE"
}
secret_where() { echo "${MMBOX_SECRETS_FILE}"; }   # for the messages: where the secrets are kept

mmbox_host_init() { # the checks and the podman user's facts, then the site's own preparation (site_init, optional)
    [[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "must run as root" >&2; exit 1; }
    for b in "$@"; do command -v "$b" >/dev/null || { mmbox_err "$b required"; exit 1; }; done
    id -u "$MMBOX_PODMAN_USER" >/dev/null 2>&1 \
        || { mmbox_err "user ${MMBOX_PODMAN_USER} missing: create it (useradd -m ${MMBOX_PODMAN_USER}) or set MMBOX_PODMAN_USER"; exit 1; }
    PODMAN_USER="$MMBOX_PODMAN_USER"
    PODMAN_UID="$(id -u "$PODMAN_USER")"
    PODMAN_HOME="$(getent passwd "$PODMAN_USER" | cut -d: -f6)"
    QUADLET_DIR="${PODMAN_HOME}/.config/containers/systemd"
    cd /   # never sit in a 0700 home: `sudo -u podman` would fail to chdir
    if declare -F site_init >/dev/null; then site_init; fi
}

run_user() {
    sudo -u "$PODMAN_USER" \
        XDG_RUNTIME_DIR="/run/user/${PODMAN_UID}" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${PODMAN_UID}/bus" \
        "$@"
}
podu() { run_user podman "$@"; }
