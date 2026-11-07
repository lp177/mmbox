#!/usr/bin/env bash
# mmbox-server.sh: an mmbox service (Redis 8 + the mmbox gateway) in containers, with podman or docker, on this
# computer or on a server reached by SSH. Its passwords are made on the target and never leave it.
#
#   mmbox-server.sh check     [--engine podman|docker] [--ssh user@host]      what is missing (exit 10: dependencies)
#   mmbox-server.sh up        [--name mmbox] [--engine E] [--bind 127.0.0.1] [--port 8095] [--tls-domain D]
#                             [--data DIR] [--maxmemory 6gb] [--no-boot] [--ssh user@host [--ssh-opt OPT]...]
#                             [--collector [--collector-days 7]] [--operator ID]...
#                             --operator: an mmbox console's id (`mmbox_console.py --whoami`): it reads every box of
#                             the table of active boxes, and the collector's journal with --collector (who sends what
#                             to whom; the content only as each computer chooses, sealed for the first operator's key)
#   mmbox-server.sh status | logs [--tail N] | down           (down: containers removed; data, secrets, images kept)
#   mmbox-server.sh uninstall [--purge-data --yes]           (containers killed and removed, then their images;
#                                                             the data only with --purge-data --yes)
#
# Secrets: two random Redis passwords made on the target at the first `up`, kept there in <data>/secrets (0600, never
# printed); the Redis ACL file holds their SHA-256 only. The gateway gets its password as a podman secret (podman) or
# an env file (docker). Nothing secret leaves the target, nothing is stored on this computer.
# SSH: BatchMode (never a password prompt here). A key or a certificate works as is (ssh-agent, ~/.ssh/config). A
# server that wants a password: open a shared connection yourself, in your own terminal (the password is typed there,
# never stored, never given to an agent), then pass its ControlPath with --ssh-opt (exit 11 says how).
# Exit codes: 0 ok; 2 usage; 10 dependencies missing (listed, with the command to install them); 11 SSH access needs
# you; 12 the container engine needs root here; 13 the service did not come up (logs shown).
set -euo pipefail

usage() { sed -n '2,/^set -euo/p' "$SELF" | grep '^#' | sed 's/^# \{0,1\}//'; }
SELF="${BASH_SOURCE[0]}"  # the real file, through the links a skill folder holds
while [[ -L "$SELF" ]]; do d="$(cd "$(dirname "$SELF")" && pwd)"; SELF="$(readlink "$SELF")"; [[ "$SELF" == /* ]] || SELF="$d/$SELF"; done
ACTION="${1:-}"
[[ -n "$ACTION" ]] && shift || true
case "$ACTION" in -h|--help|help) usage; exit 0 ;; esac
NAME="mmbox"; ENGINE=""; BIND="127.0.0.1"; PORT="8095"; TLS_DOMAIN=""; DATA=""; MAXMEM="6gb"
SSH_TARGET=""; SSH_OPTS=(); PURGE=0; YES=0; REMOTE=0; TAIL=60; BOOT=1; VIA=""; COLLECTOR=0; COL_DAYS=7; OPERATORS=()
FWD=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) NAME="$2"; FWD+=("$1" "$2"); shift 2 ;;
        --engine) ENGINE="$2"; FWD+=("$1" "$2"); shift 2 ;;
        --bind) BIND="$2"; FWD+=("$1" "$2"); shift 2 ;;
        --port) PORT="$2"; FWD+=("$1" "$2"); shift 2 ;;
        --tls-domain) TLS_DOMAIN="$2"; FWD+=("$1" "$2"); shift 2 ;;
        --data) DATA="$2"; FWD+=("$1" "$2"); shift 2 ;;
        --maxmemory) MAXMEM="$2"; FWD+=("$1" "$2"); shift 2 ;;
        --tail) TAIL="$2"; FWD+=("$1" "$2"); shift 2 ;;
        --purge-data) PURGE=1; FWD+=("$1"); shift ;;
        --yes) YES=1; FWD+=("$1"); shift ;;
        --no-boot) BOOT=0; FWD+=("$1"); shift ;;
        --collector) COLLECTOR=1; FWD+=("$1"); shift ;;
        --collector-days) COL_DAYS="$2"; FWD+=("$1" "$2"); shift 2 ;;
        --operator) [[ "$2" =~ ^m[0-9a-z]{12}$ ]] || { echo "--operator: an mmbox id (m + 12 characters)" >&2; exit 2; }
                    OPERATORS+=("$2"); FWD+=("$1" "$2"); shift 2 ;;
        --ssh) SSH_TARGET="$2"; shift 2 ;;
        --ssh-opt) SSH_OPTS+=("$2"); shift 2 ;;
        --remote-run) REMOTE=1; shift ;;
        --via) VIA="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done
case "$ACTION" in check|up|status|logs|down|uninstall) ;; *) usage >&2; exit 2 ;; esac
[[ "$NAME" =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]] || { echo "--name: lowercase letters, digits and dashes" >&2; exit 2; }
[[ "$PORT" =~ ^[0-9]{2,5}$ ]] || { echo "--port: a number" >&2; exit 2; }

say() { printf '\033[1;32m[mmbox-server]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[mmbox-server] WARN:\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[mmbox-server] ERROR:\033[0m %s\n' "$1" >&2; exit "${2:-1}"; }

HERE="$(cd "$(dirname "$SELF")" && pwd)"
if [[ -f "$HERE/mmbox_gateway.py" ]]; then SRC="$HERE"            # the skills' copy: everything in one folder
elif [[ -f "$HERE/../gateway/mmbox_gateway.py" ]]; then SRC="$(cd "$HERE/../gateway" && pwd)"   # the mmbox repo
else fail "the gateway's source (mmbox_gateway.py, Containerfile, requirements.txt) is not next to this script"; fi

# ---------------------------------------------------------------- on a server reached by SSH: copy, then run there
if [[ -n "$SSH_TARGET" && "$REMOTE" -eq 0 ]]; then
    command -v ssh >/dev/null || fail "ssh is not installed on this computer" 10
    command -v tar >/dev/null || fail "tar is not installed on this computer" 10
    SSHC=(ssh -o BatchMode=yes -o ConnectTimeout=15 ${SSH_OPTS[@]+"${SSH_OPTS[@]}"} "$SSH_TARGET")
    if ! "${SSHC[@]}" true </dev/null 2>/dev/null; then
        cat >&2 <<EOF
[mmbox-server] ssh $SSH_TARGET does not work without you (no usable key or certificate, a password is asked, the
host key is not known yet, or the host is unreachable). Nothing was done. Either:
  - a host key not known yet: connect once yourself (ssh $SSH_TARGET) and check its fingerprint before saying yes;
  - a key or a certificate: load it (ssh-add ~/.ssh/<key>; a certificate <key>-cert.pub beside it is used as is),
    or name it in ~/.ssh/config; 'ssh -G $SSH_TARGET | grep -i -e identityfile -e certificatefile' shows what is used;
  - a password: open a shared connection yourself, in your own terminal (you type the password there; it is never
    stored nor seen by the agent):
        ssh -fNM -o ControlPersist=30m -o ControlPath=~/.ssh/mmbox-%C $SSH_TARGET
    then run this again with:  --ssh-opt -oControlPath=~/.ssh/mmbox-%C
    and close it afterwards:   ssh -O exit -o ControlPath=~/.ssh/mmbox-%C $SSH_TARGET
EOF
        exit 11
    fi
    RDIR=".mmbox-server/$NAME/src"
    if [[ "$ACTION" == "up" || "$ACTION" == "check" ]]; then
        tar -chf - -C "$HERE" "$(basename "$SELF")" -C "$SRC" mmbox_gateway.py requirements.txt Containerfile \
            | "${SSHC[@]}" "mkdir -p ~/$RDIR && tar -xf - -C ~/$RDIR"
    fi
    say "running '$ACTION' on $SSH_TARGET"
    ARGS=""; [[ ${#FWD[@]} -gt 0 ]] && ARGS="$(printf '%q ' "${FWD[@]}")"
    exec "${SSHC[@]}" "test -f ~/$RDIR/mmbox-server.sh || { echo 'no mmbox server deployed there under --name $NAME' >&2; exit 2; }; bash ~/$RDIR/mmbox-server.sh $ACTION --remote-run --via $(printf '%q' "$SSH_TARGET") $ARGS" </dev/null
fi

# ---------------------------------------------------------------- dependencies
os_id() { . /etc/os-release 2>/dev/null && echo "${ID:-unknown}" || { [[ "$(uname)" == Darwin ]] && echo macos || echo unknown; }; }
install_hint() { # what the user would run to install $*
    case "$(os_id)" in
        debian|ubuntu|linuxmint|pop|raspbian) echo "sudo apt install -y $*" ;;
        fedora|rhel|centos|rocky|almalinux) echo "sudo dnf install -y $*" ;;
        arch|manjaro|endeavouros) echo "sudo pacman -S --needed $*" ;;
        alpine) echo "sudo apk add $*" ;;
        opensuse*|sles) echo "sudo zypper install -y $*" ;;
        macos) echo "brew install $*" ;;
        *) echo "install with this system's package manager: $*" ;;
    esac
}
missing=()
if [[ -z "$ENGINE" ]]; then
    if command -v podman >/dev/null; then ENGINE=podman; elif command -v docker >/dev/null; then ENGINE=docker; fi
fi
[[ -n "$ENGINE" ]] && command -v "$ENGINE" >/dev/null || missing+=("${ENGINE:-podman}")
SHA=""
if command -v sha256sum >/dev/null; then SHA="sha256sum"; elif command -v shasum >/dev/null; then SHA="shasum -a 256"
elif command -v openssl >/dev/null; then SHA="openssl dgst -sha256 -r"; else missing+=(coreutils); fi
HTTP=""
if command -v curl >/dev/null; then HTTP=curl; elif command -v wget >/dev/null; then HTTP=wget; else missing+=(curl); fi
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "MISSING: ${missing[*]} on $(hostname)" >&2
    echo "INSTALL: $(install_hint "${missing[@]}")" >&2
    echo "(ask the user whether to install them; with sudo they type their password in their own terminal)" >&2
    exit 10
fi
if ! "$ENGINE" info >/dev/null 2>&1; then
    if [[ "$ENGINE" == docker ]]; then
        fail "docker answers only to root here: add this user to the docker group (sudo usermod -aG docker $USER, then log in again), use podman (rootless), or run this script as root yourself" 12
    fi
    fail "$ENGINE does not work for this user ($ENGINE info failed)" 12
fi
if [[ "$ACTION" == "check" ]]; then
    say "ok on $(hostname): $ENGINE $("$ENGINE" --version | head -1 | awk '{print $NF}'), ${SHA%% *}, $HTTP"
    exit 0
fi

E="$ENGINE"
DATA="${DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/mmbox-server/$NAME}"
OPTS_FILE="$DATA/options"
if [[ "$ACTION" != up && -f "$OPTS_FILE" ]]; then  # status/logs/down/uninstall: where `up` put it
    # shellcheck disable=SC1090
    . "$OPTS_FILE"
fi
NET="$NAME-net"; RC="$NAME-redis"; GC="$NAME-gateway"; CC="$NAME-caddy"; VOL="$NAME-redis-data"
IMG="localhost/$NAME-gateway:latest"; REDIS_IMG="docker.io/library/redis:8-alpine"; CADDY_IMG="docker.io/library/caddy:2"
RESTART="unless-stopped"; [[ "$E" == podman ]] && RESTART="always"
exists() { "$E" container inspect "$1" >/dev/null 2>&1; }
health_url() { local h="$BIND"; [[ "$h" == 0.0.0.0 || "$h" == "::" ]] && h=127.0.0.1; echo "http://$h:$PORT/v1/health"; }
fetch() { if [[ "$HTTP" == curl ]]; then curl -fsS --max-time 3 "$1"; else wget -qO- -T 3 "$1"; fi; }

case "$ACTION" in
status)
    "$E" ps -a --format '{{.Names}}  {{.Status}}  {{.Image}}' | grep -E "^$NAME-(redis|gateway|caddy) " || echo "no container named $NAME-* here"
    if fetch "$(health_url)" 2>/dev/null; then echo; else echo "health: no answer at $(health_url)"; fi
    exit 0 ;;
logs)
    for c in "$GC" "$RC" "$CC"; do exists "$c" && { echo "== $c"; "$E" logs --tail "$TAIL" "$c" 2>&1 | tail -n "$TAIL"; }; done
    exit 0 ;;
down)
    for c in "$CC" "$GC" "$RC"; do exists "$c" && "$E" rm -f "$c" >/dev/null && say "$c removed"; done
    say "data ($VOL, $DATA) and images kept: 'up' starts it again"
    exit 0 ;;
uninstall)
    [[ "$PURGE" -eq 0 || "$YES" -eq 1 ]] || fail "--purge-data deletes the stored messages and the passwords for good: add --yes once the user agreed (nothing was done)" 2
    for c in "$CC" "$GC" "$RC"; do
        if exists "$c"; then "$E" kill "$c" >/dev/null 2>&1 || true; "$E" rm -f "$c" >/dev/null && say "$c killed and removed"; fi
    done
    for i in "$IMG" "$REDIS_IMG" "$CADDY_IMG"; do
        if "$E" image inspect "$i" >/dev/null 2>&1; then
            if "$E" rmi "$i" >/dev/null 2>&1; then say "image $i removed"; else warn "image $i kept: another container uses it"; fi
        fi
    done
    "$E" network rm "$NET" >/dev/null 2>&1 && say "network $NET removed" || true
    if [[ "$REMOTE" -eq 1 ]]; then rm -rf -- "$HOME/.mmbox-server/$NAME"; say "copy of the script removed from this server"; fi
    [[ "$E" == podman ]] && podman secret rm "$NAME-redis-gw" >/dev/null 2>&1 || true
    if [[ "$PURGE" -eq 1 ]]; then
        "$E" volume rm "$VOL" "$NAME-caddy-data" >/dev/null 2>&1 || true
        rm -rf -- "$DATA"
        say "data deleted ($VOL, $DATA)"
    else
        say "data kept ($VOL, $DATA): --purge-data --yes deletes it"
    fi
    exit 0 ;;
esac

# ---------------------------------------------------------------- up
mkdir -p "$DATA/secrets"; chmod 700 "$DATA" "$DATA/secrets"
rand() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
for f in admin gateway; do
    [[ -s "$DATA/secrets/$f.pw" ]] || { (umask 077; rand > "$DATA/secrets/$f.pw"); say "Redis password '$f' made, kept in $DATA/secrets (never shown)"; }
done
hash_of() { tr -d '\n' < "$1" | $SHA | awk '{print $1}'; }
CMDS="-@all +ping +hello +auth +client|setname +client|id +select +xadd +xread +xrange +xrevrange +xdel +xtrim +xlen"
CMDS+=" +get +set +del +unlink +exists +expire +pexpire +ttl +pttl +hset +hsetnx +hget +hmget +hgetall +hdel +hexists"
CMDS+=" +scan +multi +exec +discard +info +memory|usage +dbsize"
printf 'user default off\nuser mmbox-admin on #%s ~* &* +@all\nuser mmbox-gateway on #%s ~mmbox:* &mmbox:* %s\n' \
    "$(hash_of "$DATA/secrets/admin.pw")" "$(hash_of "$DATA/secrets/gateway.pw")" "$CMDS" > "$DATA/users.acl.new"
chmod 644 "$DATA/users.acl.new"   # hashes only: Redis (another uid in its container) must read it
acl_changed=1; cmp -s "$DATA/users.acl.new" "$DATA/users.acl" 2>/dev/null && acl_changed=0
mv -f "$DATA/users.acl.new" "$DATA/users.acl"

"$E" network inspect "$NET" >/dev/null 2>&1 || "$E" network create "$NET" >/dev/null
"$E" volume inspect "$VOL" >/dev/null 2>&1 || "$E" volume create "$VOL" >/dev/null

NEW_OPTS="BIND=$BIND
PORT=$PORT
TLS_DOMAIN=$TLS_DOMAIN
MAXMEM=$MAXMEM"
opts_changed=0
if [[ -f "$OPTS_FILE" ]] && [[ "$(cat "$OPTS_FILE")" != "$NEW_OPTS" ]]; then opts_changed=1; fi
printf '%s\n' "$NEW_OPTS" > "$OPTS_FILE"
if [[ "$opts_changed" -eq 1 ]]; then  # a new bind, port, domain or memory limit: those containers are made again
    for c in "$CC" "$GC"; do exists "$c" && "$E" rm -f "$c" >/dev/null; done
    say "options changed: gateway made again"
fi
if exists "$RC" && { [[ "$acl_changed" -eq 1 ]] || [[ "$opts_changed" -eq 1 ]]; }; then "$E" rm -f "$RC" >/dev/null; fi
if [[ -r /proc/meminfo ]]; then  # a cap above the RAM: the kernel kills Redis before it refuses a write
    mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
    cap_kb=$(awk -v m="${MAXMEM,,}" 'BEGIN { n = m + 0; u = m; sub(/^[0-9.]+/, "", u)
        f = (u ~ /^g/) ? 1048576 : (u ~ /^m/) ? 1024 : (u ~ /^k/) ? 1 : 1/1024; printf "%d", n * f }')
    if [[ -n "$mem_kb" && "$cap_kb" -gt $(( mem_kb * 2 / 3 )) ]]; then
        say "WARNING: --maxmemory $MAXMEM is over two thirds of this server's RAM ($(( mem_kb / 1024 )) MB): Redis may be killed for lack of memory (it needs room to rewrite its AOF) before it refuses a write; give a lower --maxmemory"
    fi
fi
if ! exists "$RC"; then
    "$E" run -d --name "$RC" --network "$NET" --restart "$RESTART" -v "$VOL:/data" \
        -v "$DATA/users.acl:/etc/redis/users.acl:ro,Z" "$REDIS_IMG" redis-server --aclfile /etc/redis/users.acl \
        --appendonly yes --appendfsync everysec --save "3600 1 300 100" --maxmemory "$MAXMEM" \
        --maxmemory-policy noeviction --protected-mode yes --tcp-keepalive 60 >/dev/null
    say "$RC started ($REDIS_IMG, data in volume $VOL, maxmemory $MAXMEM, no eviction)"
else
    "$E" start "$RC" >/dev/null 2>&1 || true
fi

SUM="$(cat "$SRC/mmbox_gateway.py" "$SRC/requirements.txt" "$SRC/Containerfile" | $SHA | cut -c1-12)"
built="$("$E" image inspect --format '{{ index .Config.Labels "src.sha" }}' "$IMG" 2>/dev/null || true)"
old_img="$("$E" image inspect --format '{{.Id}}' "$IMG" 2>/dev/null || true)"  # removed once the new one answers
if [[ "$built" != "$SUM" ]]; then
    say "building $IMG from $SRC ($SUM)"
    "$E" build -q --label "src.sha=$SUM" -t "$IMG" -f "$SRC/Containerfile" "$SRC" >/dev/null
fi
SECRET_ARGS=()
if [[ "$E" == podman ]]; then
    podman secret rm "$NAME-redis-gw" >/dev/null 2>&1 || true
    tr -d '\n' < "$DATA/secrets/gateway.pw" | podman secret create "$NAME-redis-gw" - >/dev/null
    SECRET_ARGS=(--secret "$NAME-redis-gw,type=mount,target=/run/secrets/mmbox_redis_gateway,uid=10001,mode=0400")
else
    (umask 077; printf 'MMBOX_REDIS_PASSWORD=%s\n' "$(tr -d '\n' < "$DATA/secrets/gateway.pw")" > "$DATA/secrets/gateway.env")
    SECRET_ARGS=(--env-file "$DATA/secrets/gateway.env" -e MMBOX_REDIS_PASSWORD_FILE=/nonexistent)
fi
OPS="$(IFS=,; echo "${OPERATORS[*]:-}")"
OPT_ENV=(-e "MMBOX_OPERATORS=$OPS")
[[ "$COLLECTOR" -eq 1 ]] && OPT_ENV+=(-e MMBOX_COLLECTOR=1 -e "MMBOX_COLLECTOR_DAYS=$COL_DAYS")
OPT_SUM="$(printf '%s;' "$COLLECTOR" "$COL_DAYS" "$OPS" | $SHA | cut -c1-12)"
cur="$("$E" container inspect --format '{{.Image}}' "$GC" 2>/dev/null || true)"
new="$("$E" image inspect --format '{{.Id}}' "$IMG" 2>/dev/null || true)"
cur_opt="$("$E" container inspect --format '{{ index .Config.Labels "opt.sum" }}' "$GC" 2>/dev/null || true)"
if exists "$GC" && { [[ "$cur" != "$new" ]] || [[ "$acl_changed" -eq 1 ]] || [[ "$cur_opt" != "$OPT_SUM" ]]; }; then "$E" rm -f "$GC" >/dev/null; fi
if ! exists "$GC"; then
    "$E" run -d --name "$GC" --network "$NET" --restart "$RESTART" -p "$BIND:$PORT:8095" --label "opt.sum=$OPT_SUM" \
        -e "MMBOX_REDIS_URL=redis://$RC:6379/0" -e MMBOX_REDIS_USER=mmbox-gateway "${OPT_ENV[@]}" "${SECRET_ARGS[@]}" "$IMG" >/dev/null
    say "$GC started on $BIND:$PORT"
else
    "$E" start "$GC" >/dev/null 2>&1 || true
fi
ok=0
for _ in $(seq 1 40); do fetch "$(health_url)" 2>/dev/null | grep -q '"redis": "up"' && { ok=1; break; }; sleep 1; done
if [[ "$ok" -ne 1 ]]; then
    warn "no healthy answer at $(health_url) - recent logs:"
    "$E" logs --tail 30 "$GC" 2>&1 | tail -30 >&2 || true
    "$E" logs --tail 10 "$RC" 2>&1 | tail -10 >&2 || true
    exit 13
fi
if [[ -n "$old_img" && "$old_img" != "$new" && -z "$("$E" ps -a -q --filter "ancestor=$old_img" 2>/dev/null)" ]]; then
    "$E" rmi "$old_img" >/dev/null 2>&1 && say "previous image ${old_img:0:12} removed" || true
fi
URL="http://$([[ "$BIND" == 0.0.0.0 || "$BIND" == "::" ]] && hostname -f 2>/dev/null || echo "$BIND"):$PORT"
if [[ -n "$TLS_DOMAIN" ]]; then
    exists "$CC" || "$E" run -d --name "$CC" --network "$NET" --restart "$RESTART" -p 80:80 -p 443:443 \
        -v "$NAME-caddy-data:/data" "$CADDY_IMG" caddy reverse-proxy --from "$TLS_DOMAIN" --to "$GC:8095" >/dev/null \
        || fail "caddy could not start: ports 80 and 443 free and bindable by this user? (rootless podman: sysctl net.ipv4.ip_unprivileged_port_start=80)"
    URL="https://$TLS_DOMAIN"
    say "TLS by caddy for $TLS_DOMAIN (its DNS must point here; certificates are made on the first request)"
fi
if [[ "$E" == podman && "$BOOT" -eq 1 ]] && command -v systemctl >/dev/null; then  # docker restarts them by itself
    if systemctl --user is-enabled podman-restart.service >/dev/null 2>&1 || systemctl --user enable podman-restart.service >/dev/null 2>&1; then
        say "containers come back at boot (podman-restart.service; without a login it also needs: loginctl enable-linger $USER)"
    else
        warn "they will not come back at boot by themselves: systemctl --user enable podman-restart.service, and loginctl enable-linger $USER"
    fi
fi
if [[ "$BIND" == 0.0.0.0 || "$BIND" == "::" ]]; then
    if [[ -n "$TLS_DOMAIN" ]]; then
        warn "--tls-domain with --bind $BIND also opens plain HTTP on port $PORT on every interface: keep --bind 127.0.0.1 behind caddy"
    else
        warn "listening on every interface in plain HTTP: requests are signed and messages encrypted, but anyone on the way sees who talks to whom; prefer --bind <VPN or LAN address>, or --tls-domain"
    fi
fi
say "up: $URL"
[[ ${#OPERATORS[@]} -gt 0 ]] && say "operators: $OPS$([[ "$COLLECTOR" -eq 1 ]] && echo "; collector on, $COL_DAYS days")"
[[ "$COLLECTOR" -eq 1 && ${#OPERATORS[@]} -eq 0 ]] && warn "--collector without --operator: nobody can read it"
echo "MMBOX_SERVER_URL=$URL"
printf 'MMBOX_SERVER_DEPLOY={"where":"%s","engine":"%s","name":"%s","data":"%s","at":"%s"}\n' \
    "$( [[ "$REMOTE" -eq 1 ]] && echo "ssh:${VIA:-$(whoami)@$(hostname)}" || echo "local:$(hostname)")" "$E" "$NAME" "$DATA" "$(date -u +%FT%TZ)"
