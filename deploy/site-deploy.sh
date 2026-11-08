#!/usr/bin/env bash
# Deploy the mmbox service on a host of one's own as Quadlet units (deploy/host/redis.sh, deploy/host/gateway.sh),
# with the values of a site folder: who you are and where you deploy never live in this repository.
#
#   deploy/site-deploy.sh [--site DIR] [--print] redis|gateway|authentik
#
# The site folder (site.example/README.md): --site, else $MMBOX_SITE_DIR, else ./site at the root of this repository
# (ignored by git; a folder of your own or a link to your infrastructure's repository). It holds:
#   site.env   the settings (MMBOX_SSH_TARGET, MMBOX_DOMAIN, MMBOX_ALLOW, MMBOX_OPERATORS…), never a secret
#   hooks.sh   optional shell functions run on the host: site_init, secret_get / secret_set (a secrets manager),
#              site_post_gateway (DNS…)
# The host receives, on stdin of `ssh $MMBOX_SSH_TARGET bash -s`: deploy/host/lib.sh, site.env, hooks.sh, the
# MMBOX_* variables of this shell (they win over site.env), then the script. --print writes that bundle to stdout
# instead (to read it, or to run it another way). Without MMBOX_SSH_TARGET, it runs here with sudo.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE="${MMBOX_SITE_DIR:-}"
PRINT=0
usage() { sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --site) SITE="${2:?--site DIR}"; shift 2 ;;
        --site=*) SITE="${1#--site=}"; shift ;;
        --print) PRINT=1; shift ;;
        -h|--help) usage ;;
        redis|gateway|authentik) WHAT="$1"; shift ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done
[[ -n "${WHAT:-}" ]] || usage
SITE="${SITE:-${ROOT}/site}"
[[ -f "${SITE}/site.env" ]] || {
    echo "no site.env in ${SITE}: copy ${ROOT}/site.example to ${ROOT}/site (or set MMBOX_SITE_DIR) and fill it in" >&2
    exit 2
}

bundle() {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    printf '\n# ---- deploy/host/lib.sh\n'; cat "${ROOT}/deploy/host/lib.sh"
    printf '\n# ---- %s/site.env\n' "$SITE"; cat "${SITE}/site.env"
    if [[ -f "${SITE}/hooks.sh" ]]; then printf '\n# ---- %s/hooks.sh\n' "$SITE"; cat "${SITE}/hooks.sh"; fi
    printf '\n# ---- the MMBOX_* variables of the deploying shell\n'
    while IFS= read -r name; do
        [[ "$name" == MMBOX_SITE_DIR ]] && continue
        printf 'export %s=%q\n' "$name" "${!name}"
    done < <(compgen -e | grep '^MMBOX_' || true)
    if [[ -f "${ROOT}/deploy/host/${WHAT}-config.py" ]]; then  # its configuration step, run on the host by the script
        printf "\n# ---- deploy/host/%s-config.py\nread -r -d '' MMBOX_CONFIG_PY <<'MMBOX_CONFIG_PY_EOF' || true\n" "$WHAT"
        cat "${ROOT}/deploy/host/${WHAT}-config.py"
        printf 'MMBOX_CONFIG_PY_EOF\n'
    fi
    printf '\n# ---- deploy/host/%s.sh\n' "$WHAT"; cat "${ROOT}/deploy/host/${WHAT}.sh"
}

if [[ "$PRINT" -eq 1 ]]; then bundle; exit 0; fi
TARGET="$(set +u; # shellcheck disable=SC1091
          source "${SITE}/site.env" >/dev/null 2>&1; printf '%s' "${MMBOX_SSH_TARGET:-}")"
TARGET="${MMBOX_SSH_TARGET:-$TARGET}"
if [[ -n "$TARGET" ]]; then
    echo "deploying ${WHAT} on ${TARGET} with the site ${SITE}" >&2
    bundle | ssh -o ConnectTimeout=30 "$TARGET" 'bash -s'
else
    echo "deploying ${WHAT} here (no MMBOX_SSH_TARGET) with the site ${SITE}" >&2
    bundle | sudo bash -s
fi
