"""mmbox gateway: the mailbox transport over Redis Streams, in front of a private Redis.

Every request is signed with the Ed25519 key of an mmbox identity: no account, no token, the identity is the key.
The gateway only moves ciphertext: messages are encrypted and signed end to end by the clients (see PROTOCOL.md).

  X-Mmbox-Id     the identity (m + 12 base32)
  X-Mmbox-Ts     unix seconds, within MAX_SKEW of the gateway's clock
  X-Mmbox-Nonce  16-64 hex characters, never twice within NONCE_TTL (replay)
  X-Mmbox-Sig    base64 Ed25519 over  "mmbox/2\\n<METHOD>\\n<path>\\n<ts>\\n<nonce>\\n<sha256 hex of the body>"

Keys (all under mmbox:, the only prefix the gateway's Redis user may touch):
  card:<id>             the identity's self-signed card (first registration binds the id to its key)
  links:<id>            hash: the identities allowed to write to <id> (its peers, kept by its client)
  in:<id>               stream: the box of <id>; an entry holds `from` and `env` (the signed, encrypted envelope)
  take:<id>:<entry>     who took an entry (SET NX: one taker, atomically), for RECEIPT_TTL
  recover:<id>:<entry>  who recovers an entry left by a taker that died
  pair:<inv>            hash invite / join / done / fail of a pairing in progress, PAIR_TTL
  alerts:<id>           stream of the identity's alerts (text without any message content), 30 days
  nonce:<id>:<nonce>    replay guard
  lock:<user>:<name>    a lock of a memories user (SET NX with a TTL), e.g. the weekly reminder: one notice per TTL
  chan:<id>             stream: the ephemeral channel of <id> (a machine box: every session of its computer reads
                        it). An entry holds `from`, `env` (signed, encrypted envelope for <id>) and `exp_ms`; read
                        without being consumed, each reader with its own cursor; dead after exp_ms (CHAN_TTL_S by
                        default, CHAN_MAX_TTL_S at most). The gateway itself adds `hint` entries (`from` gateway).
  watch:<id>            a listener is waiting on <id> right now (set by each long poll, a few seconds more)
  machine:<id>          the machine box of session box <id>, as its own listener said (kept only when that
                        machine box lists <id> among its peers)
  hinted:<id>           an arrival hint for <id> was given less than HINT_EVERY_S ago
  chanrate:<signer>     stream: the posts of the last minute of one signer (rate limit, no INCR in the ACL)
  seen                  hash: the table of active boxes, <id> -> last request (ms). Every signed request of an
                        identity refreshes it (a closed box excepted); an entry idle for BOX_TTL_S is dropped
  box:<id>              what a box declared of itself (/v1/box/open): role, machine box, project, work project,
                        label, host, a title (three words at most, fixed from its opening to its closing) and what
                        its session is doing (/v1/box/activity); `closed_ms` once it closed (/v1/box/close). Kept as
                        long as its card
  colconf               hash: the collector's settings, changed by an operator (/v1/col/config): keep_ms, and a hold
                        (hold_until_ms, hold_minid) under which nothing is trimmed (``reset the TTLs to 48 h'')
  stats                 stream: one entry per message or post (its size only), 7 days: the network's traffic (/v1/stats)
  col                   stream: the collector's journal (MMBOX_COLLECTOR on): metadata of each send, refused send (once per
                        message every 10 min), take, done, post, box opened / closed / expired, plus the client's annotation sealed for the operator's
                        key (never readable by the service). Read by the operators only (MMBOX_OPERATORS)

Nothing grows without end: card, links and box of an identity expire after IDLE_TTL without any request from it (it
registers again on its next one); an entry older than KEEP_MS is cut from its box at the next send; a box holds
MAX_ENTRIES entries at most (the sender then falls back to memories); /v1/close removes a closed session's box. A
channel keeps CHAN_MAX_TTL_S of entries at most, MAX_CHAN_ENTRIES at most, and disappears CHAN_MAX_TTL_S + 60 s after its last post.

Active boxes: the `seen` table says, without any message, which boxes use this service now. A box that comes back
after it expired is active again at its next request; a closed box stays out of it until it opens again. Every
identity may read the table for itself and its peers (`/v1/boxes`); an operator reads all of it.

Collector (optional, off by default): MMBOX_COLLECTOR=1 keeps a journal of what the service sees (who sends to whom,
when, how much, what was taken and consumed) for MMBOX_COLLECTOR_DAYS, for the operators named by MMBOX_OPERATORS
(identity ids, the first one being the key annotations are sealed for). A client adds an annotation only when its
user enabled it for this service, pinning the operator's fingerprint (`mmbox.py collector enable`): kind, project,
subject and body as chosen, encrypted end to end for the operator.

Questions to the operator (with MMBOX_OPERATORS): any box of the service may write to an operator, and an operator
may write to any box whose card the service holds, without the links a pairing gives. That is how a session that must
ask its user (rights `ask`) puts the question in the operator's console, and how the console's answer comes back:
both are ordinary end-to-end encrypted envelopes, signed by their sender; the client accepts an answer only from the
operator key its user pinned (`mmbox.py collector enable`).

Arrival hints: a message stored in a box nobody listens to (no watch:<id>) whose machine box is known adds a hint
{kind: arrival, box, entry, sender} to that machine's channel, so that the other sessions of the computer relay it
without waiting for their periodic round. Metadata only, never any content.
"""
from __future__ import annotations

import base64, hashlib, json, os, re, time

from aiohttp import web
import redis.asyncio as aioredis
from redis.exceptions import ResponseError as RedisError
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

REDIS_URL = os.environ.get("MMBOX_REDIS_URL", "redis://127.0.0.1:6390/0")
REDIS_USER = os.environ.get("MMBOX_REDIS_USER", "mmbox-gateway")
REDIS_PASSWORD_FILE = os.environ.get("MMBOX_REDIS_PASSWORD_FILE", "/run/secrets/mmbox_redis_gateway")
LISTEN = os.environ.get("MMBOX_LISTEN", "0.0.0.0:8095")
MAX_SKEW = int(os.environ.get("MMBOX_MAX_SKEW_S", "300"))
NONCE_TTL = 2 * MAX_SKEW
RECEIPT_TTL = int(float(os.environ.get("MMBOX_RECEIPT_DAYS", "7")) * 86400)
PAIR_TTL = 900
MAX_BODY = 24_000_000  # one request (a send carries one envelope)
MAX_ENV = 20_000_000  # one envelope: 10 MiB of attachments, base64-encoded twice, and a body
LIST_BUDGET = 24_000_000  # the envelopes one /v1/list answer carries at most (always one at least)
MAX_BLOCK_MS = 30_000
MAX_LINKS = 1000
IDLE_TTL = int(float(os.environ.get("MMBOX_IDLE_DAYS", "30")) * 86400)
KEEP_MS = RECEIPT_TTL * 1000 + 86_400_000  # a day more than the clients keep a message
MAX_ENTRIES = int(os.environ.get("MMBOX_MAX_ENTRIES", "1000"))
MAX_LOCK_TTL = 7 * 86400
ALERT_KEEP_MS = int(float(os.environ.get("MMBOX_ALERT_DAYS", "30")) * 86_400_000)
CHAN_TTL_S = int(os.environ.get("MMBOX_CHAN_TTL_S", "60"))  # how long a post lives, unless it asks otherwise
CHAN_MAX_TTL_S = int(os.environ.get("MMBOX_CHAN_MAX_TTL_S", "600"))
CHAN_MIN_TTL_S = 5
MAX_CHAN_ENV = 32_000
MAX_CHAN_POSTS = 50  # channels one request posts to (a post to the network goes to every machine's channel)
CHAN_RATE = int(os.environ.get("MMBOX_CHAN_RATE", "120"))  # posts per minute and per signer
MAX_CHAN_ENTRIES = 1000
WATCH_SLACK_S = 10  # a listener counts as present this long after its long poll should have ended
HINT_TTL_S = max(CHAN_TTL_S, 180)  # a hint must outlive the 2 min a silent session is given before its relay
HINT_EVERY_S = 30  # one hint per box at most this often
BOX_TTL_S = int(os.environ.get("MMBOX_BOX_TTL_S", "7200"))  # an active box with no request for this long is dropped
BOX_SWEEP_S = 60
COLLECTOR = os.environ.get("MMBOX_COLLECTOR", "").strip().lower() in ("1", "on", "yes", "true")
COL_KEEP_MS = int(float(os.environ.get("MMBOX_COLLECTOR_DAYS", "7")) * 86_400_000)
COL_MAX = int(os.environ.get("MMBOX_COLLECTOR_MAX", "200000"))
MAX_NOTE = 80_000  # one sealed annotation
FEATURES = ["chan", "watch", "hints", "boxes"] + (["collector"] if COLLECTOR else [])
LOCK_RE = re.compile(r"[a-z0-9-]{1,40}")
B32 = "0123456789abcdefghjkmnpqrstvwxyz"
ID_RE = re.compile(r"m[0-9a-z]{12}")
ENTRY_RE = re.compile(r"\d{1,20}-\d{1,20}")
INV_RE = re.compile(r"[0-9a-z]{4}")
OPERATORS = [o for o in re.split(r"[,\s]+", os.environ.get("MMBOX_OPERATORS", "")) if ID_RE.fullmatch(o)]
if OPERATORS:  # a box may ask the first operator's console a question, and the console answers it (no pairing)
    FEATURES.append("questions")
FEATURES.append("stats")
FEATURES.append("usage")
_USAGE: dict = {"at": 0.0, "data": None}
STATS_KEEP_MS = 7 * 86_400_000  # the traffic counted per minute, for 7 days whatever the collector keeps
_STATS: dict = {"at": 0.0, "data": None}


def canon(o) -> bytes:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def fingerprint(sign_pub: bytes, box_pub: bytes) -> str:
    d = hashlib.sha256(b"mmbox-fp" + sign_pub + box_pub).digest()
    n = int.from_bytes(d[:10], "big")
    s = "".join(B32[(n >> (5 * i)) & 31] for i in reversed(range(16))).upper()
    return "-".join(s[i:i + 4] for i in range(0, 16, 4))


def verify(pub_b64: str, sig_b64: str, data: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64)).verify(base64.b64decode(sig_b64), data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def card_ok(card: dict) -> bool:
    """Self-signed by its own key, fingerprint matching its keys, a well-formed id."""
    try:
        if not ID_RE.fullmatch(str(card.get("id"))):
            return False
        if fingerprint(base64.b64decode(card["sign_pub"]), base64.b64decode(card["box_pub"])) != card.get("fingerprint"):
            return False
        body = {k: v for k, v in card.items() if k != "sig"}
        return verify(card["sign_pub"], card.get("sig", ""), canon(body))
    except (KeyError, ValueError, TypeError):
        return False


def _http(status: int, why: str):
    cls = {400: web.HTTPBadRequest, 401: web.HTTPUnauthorized, 403: web.HTTPForbidden, 404: web.HTTPNotFound,
           409: web.HTTPConflict, 410: web.HTTPGone, 413: web.HTTPRequestEntityTooLarge, 429: web.HTTPTooManyRequests,
           503: web.HTTPServiceUnavailable}[status]
    if status == 413:
        return cls(max_size=MAX_BODY, actual_size=0, text=json.dumps({"error": why}), content_type="application/json")
    return cls(text=json.dumps({"error": why}), content_type="application/json")


def ok(data: dict | None = None, status: int = 200) -> web.Response:
    return web.json_response({"ok": True, **(data or {})}, status=status)


# ---------------------------------------------------------------- authentication
async def authenticate(request: web.Request, body: bytes, card_in_body: dict | None = None) -> str:
    """The identity that signed this request. A card in the body (registration) is used when none is stored."""
    r: aioredis.Redis = request.app["redis"]
    mid = request.headers.get("X-Mmbox-Id", "")
    ts = request.headers.get("X-Mmbox-Ts", "")
    nonce = request.headers.get("X-Mmbox-Nonce", "")
    sig = request.headers.get("X-Mmbox-Sig", "")
    if not ID_RE.fullmatch(mid) or not ts.isdigit() or not re.fullmatch(r"[0-9a-f]{16,64}", nonce) or not sig:
        raise _http(401, "missing or malformed X-Mmbox-* headers")
    if abs(time.time() - int(ts)) > MAX_SKEW:
        raise _http(401, f"clock skew over {MAX_SKEW} s: fix this computer's clock")
    async with r.pipeline(transaction=False) as p:
        p.get(f"mmbox:card:{mid}")
        p.get(f"mmbox:box:{mid}")
        raw, box_raw = await p.execute()
    card = json.loads(raw) if raw else card_in_body
    if not card or card.get("id") != mid:
        raise _http(401, "unknown identity: register its card first (POST /v1/card)")
    msg = "\n".join(["mmbox/2", request.method, request.path, ts, nonce, hashlib.sha256(body).hexdigest()]).encode()
    if not verify(card["sign_pub"], sig, msg):
        raise _http(401, "bad signature")
    if not await r.set(f"mmbox:nonce:{mid}:{nonce}", "1", nx=True, ex=NONCE_TTL):
        raise _http(401, "replayed request")
    if raw:  # an identity that still talks keeps its card, links and box, and counts as active (unless closed)
        declared = _loads(box_raw) if box_raw else None
        active = mid not in OPERATORS and not (declared or {}).get("closed_ms")
        async with r.pipeline(transaction=False) as p:
            for k in ("card", "links", "in", "box"):
                p.expire(f"mmbox:{k}:{mid}", IDLE_TTL)
            if active:
                p.hset("mmbox:seen", mid, int(time.time() * 1000))
            res = await p.execute()
        if active and res[-1]:  # not in the table a moment ago: new, or back after it expired
            await col(r, {"ev": "active", "box": mid, "declared": "1" if declared else "0"})
    request["card"] = card
    return mid


def _loads(raw):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


_COLCONF = {"at": 0.0, "keep_ms": COL_KEEP_MS, "hold_until_ms": 0, "hold_minid": None}


async def col_settings(r: aioredis.Redis, fresh: bool = False) -> dict:
    """The collector's retention: MMBOX_COLLECTOR_DAYS, unless an operator changed it (colconf), read every 30 s."""
    if fresh or time.monotonic() - _COLCONF["at"] > 30:
        try:
            got = await r.hgetall("mmbox:colconf")
        except Exception:
            got = {}
        _COLCONF.update(at=time.monotonic(), keep_ms=int(got.get("keep_ms") or COL_KEEP_MS),
                        hold_until_ms=int(got.get("hold_until_ms") or 0), hold_minid=got.get("hold_minid") or None)
    return _COLCONF


def col_cutoff(conf: dict, now_ms: int) -> str:
    """The oldest entry kept: keep_ms back, or the hold's floor while the hold lasts."""
    cut = f"{now_ms - conf['keep_ms']}-0"
    if conf.get("hold_minid") and conf.get("hold_until_ms", 0) > now_ms:
        cut = min(cut, conf["hold_minid"], key=lambda e: tuple(int(x) for x in e.split("-")))
    return cut


async def col(r: aioredis.Redis, fields: dict) -> None:
    """One event in the collector's journal (MMBOX_COLLECTOR on): metadata the service sees anyway, and an annotation
    sealed by the client for the operator, if any. Kept COL_KEEP_MS."""
    if not COLLECTOR:
        return
    now_ms = int(time.time() * 1000)
    clean = {k: (v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))) for k, v in fields.items()
             if v is not None}
    try:
        conf = await col_settings(r)
        await r.xadd("mmbox:col", {**clean, "t": str(now_ms)}, minid=col_cutoff(conf, now_ms), approximate=True)
    except Exception:  # the journal never stops a message
        pass


async def stat(r: aioredis.Redis, kind: str, size: int) -> None:
    """One message (`m`) or post (`p`) through this service: its size only, kept STATS_KEEP_MS (`/v1/stats`)."""
    try:
        await r.xadd("mmbox:stats", {"k": kind, "s": str(size)},
                     minid=f"{int(time.time() * 1000) - STATS_KEEP_MS}-0", approximate=True)
    except Exception:
        pass


async def stats(request: web.Request) -> web.Response:
    """The traffic of the last 7 days: [start ms, messages, posts, bytes, span ms] per 10 s over the last hour, per
    minute before, for each span that had any. Counts only, never who or what. Any registered identity may read it;
    recomputed at most every 10 s."""
    await signed_json(request)
    now = time.time()
    if _STATS["data"] is None or now - _STATS["at"] > 10:
        r = request.app["redis"]
        now_ms = int(now * 1000)
        buckets: dict[int, list] = {}
        start = f"{now_ms - STATS_KEEP_MS}-0"
        while True:
            rows = await r.xrange("mmbox:stats", start, "+", count=5000)
            for eid, f in rows:
                t = int(eid.split("-")[0])
                span = 10_000 if now_ms - t < 3_600_000 else 60_000
                b = buckets.setdefault(t // span * span, [t // span * span, 0, 0, 0, span])
                b[1 if f.get("k") == "m" else 2] += 1
                b[3] += int(f.get("s") or 0)
            if len(rows) < 5000:
                break
            start = "(" + rows[-1][0]
        _STATS.update(at=now, data={"buckets": sorted(buckets.values()), "keep_s": STATS_KEEP_MS // 1000, "now_ms": now_ms})
    return ok(_STATS["data"])


async def _scan(r: aioredis.Redis, pattern: str) -> list[str]:
    return [k async for k in r.scan_iter(match=pattern, count=1000)]


async def _sizes(r: aioredis.Redis, keys: list[str]) -> int | None:
    """What these keys take in Redis's memory (MEMORY USAGE), in bytes; None when the gateway's Redis user may not
    ask (an ACL older than the `usage` feature: +info +memory|usage +dbsize)."""
    total = 0
    try:
        for i in range(0, len(keys), 500):
            async with r.pipeline(transaction=False) as p:
                for k in keys[i:i + 500]:
                    p.memory_usage(k)
                total += sum(int(x or 0) for x in await p.execute())
    except RedisError:
        return None
    return total


BOX_KEYS = ("card", "links", "in", "alerts", "chan", "watch", "machine", "hinted", "box")  # what a box is, here


async def usage(request: web.Request) -> web.Response:
    """The service's memory, for an operator: Redis's own figures (used, peak, limit, eviction policy) and what the
    boxes, the channels, the collector and the traffic counts take. Recomputed at most every 30 s, unless `fresh`.
    `empty`: every box removed, the operators' own excepted (their questions stay): its card, links, declaration,
    channel, alerts and what waits in it, taken or not, for good. A box still in use registers again by itself at its
    next call (its client answers "unknown identity" with its card and links); a message written to it meanwhile waits
    in its sender's queue. The collector notes who did it, how many boxes and entries went."""
    me, data = await signed_json(request)
    if me not in OPERATORS:
        raise _http(403, "the service's memory is for its operators only (MMBOX_OPERATORS)")
    r = request.app["redis"]
    emptied = None
    if data.get("empty"):
        ids = set(await r.hgetall("mmbox:seen"))
        for kind in ("card", "in", "box", "links"):
            ids |= {k.split(":", 2)[2] for k in await _scan(r, f"mmbox:{kind}:*")}
        ids = sorted(i for i in ids if ID_RE.fullmatch(i) and i not in OPERATORS)
        entries = 0
        for i in range(0, len(ids), 200):
            async with r.pipeline(transaction=False) as p:
                for b in ids[i:i + 200]:
                    p.xlen(f"mmbox:in:{b}")
                entries += sum(int(x or 0) for x in await p.execute())
            async with r.pipeline(transaction=False) as p:
                for b in ids[i:i + 200]:
                    p.delete(*(f"mmbox:{k}:{b}" for k in BOX_KEYS), f"mmbox:chanrate:{b}")
                    p.hdel("mmbox:seen", b)
                await p.execute()
        receipts = [k for k in await _scan(r, "mmbox:take:*") + await _scan(r, "mmbox:recover:*")
                    if k.split(":")[2] not in OPERATORS]
        for i in range(0, len(receipts), 500):
            await r.delete(*receipts[i:i + 500])
        emptied = {"boxes": len(ids), "entries": entries}
        await col(r, {"ev": "empty", "by": me, "boxes": str(len(ids)), "entries": str(entries)})
        _USAGE["at"] = 0.0
    now = time.time()
    if _USAGE["data"] is None or now - _USAGE["at"] > 30 or data.get("fresh"):
        try:
            mem, nkeys, acl = await r.info("memory"), await r.dbsize(), None
        except RedisError as e:  # NOPERM: the boxes' counts still come, Redis's own figures do not
            mem, nkeys, acl = {}, None, f"the gateway's Redis user may not read the memory figures ({e}): " \
                                        "deploy Redis again with the current ACL (+info +memory|usage +dbsize)"
        boxes = [k for k in await _scan(r, "mmbox:in:*") if k.split(":", 2)[2] not in OPERATORS]  # what `empty` takes
        chans = await _scan(r, "mmbox:chan:*")
        known = sum(1 for k in await _scan(r, "mmbox:card:*") if k.split(":", 2)[2] not in OPERATORS)
        waiting = 0
        for i in range(0, len(boxes), 500):
            async with r.pipeline(transaction=False) as p:
                for k in boxes[i:i + 500]:
                    p.xlen(k)
                waiting += sum(int(x or 0) for x in await p.execute())
        _USAGE.update(at=now, data={
            "used": int(mem.get("used_memory") or 0), "peak": int(mem.get("used_memory_peak") or 0),
            "max": int(mem.get("maxmemory") or 0), "policy": mem.get("maxmemory_policy"),
            "dataset": int(mem.get("used_memory_dataset") or 0), "keys": nkeys, "acl": acl,
            "boxes": {"count": known, "entries": waiting, "bytes": await _sizes(r, boxes)},  # bytes: what waits
            "channels": {"count": len(chans), "bytes": await _sizes(r, chans)},
            "collector": {"entries": await r.xlen("mmbox:col"), "bytes": await _sizes(r, ["mmbox:col"])},
            "stats": {"bytes": await _sizes(r, ["mmbox:stats"])}})
    return ok({**_USAGE["data"], "emptied": emptied, "at_ms": int(_USAGE["at"] * 1000)})


def note_of(data: dict, me: str) -> str | None:
    """The client's annotation for the collector: an envelope of type `col` from the signer to the operator key, kept
    as is (the service cannot open it). Anything else is dropped, never refused."""
    n = data.get("col")
    if not COLLECTOR or not OPERATORS or not isinstance(n, dict):
        return None
    if n.get("type") != "col" or n.get("from") != me or n.get("to") != OPERATORS[0]:
        return None
    raw = json.dumps(n, ensure_ascii=False, separators=(",", ":"))
    return raw if len(raw) <= MAX_NOTE else None


async def signed_json(request: web.Request, card_field: bool = False) -> tuple[str, dict]:
    body = await request.read()
    if len(body) > MAX_BODY:
        raise _http(413, "body too large")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        raise _http(400, "body is not JSON")
    if not isinstance(data, dict):
        raise _http(400, "body is not a JSON object")
    card = data.get("card") if card_field and isinstance(data.get("card"), dict) else None
    return await authenticate(request, body, card), data


def need(data: dict, key: str, pattern: re.Pattern | None = None) -> str:
    v = data.get(key)
    if not isinstance(v, str) or (pattern and not pattern.fullmatch(v)):
        raise _http(400, f"`{key}` missing or malformed")
    return v


def own_box(me: str, data: dict) -> str:
    box = need(data, "box", ID_RE)
    if box != me:
        raise _http(403, "a box is read and emptied by its own identity only (sign with the box's key)")
    return box


def entry_of(data: dict, key: str, allowed: tuple = ("0", "$")) -> str:
    v = str(data.get(key) or "0")
    if v not in allowed and not ENTRY_RE.fullmatch(v):
        raise _http(400, f"`{key}`: an entry id, or " + " or ".join(allowed))
    return v


def int_of(data: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(data[key]) if data.get(key) is not None else default
    except (TypeError, ValueError):
        raise _http(400, f"`{key}`: a number")
    return max(lo, min(v, hi))


async def present(r: aioredis.Redis, box: str, block_ms: int) -> None:
    """A listener waits on `box` for block_ms: it counts as present a little longer than that (WATCH_SLACK_S)."""
    await r.set(f"mmbox:watch:{box}", "1", ex=-(-block_ms // 1000) + WATCH_SLACK_S)


async def chan_add(r: aioredis.Redis, box: str, fields: dict, ttl_s: int) -> str:
    """One entry on the channel of `box`, dead after ttl_s; what is older than CHAN_MAX_TTL_S is cut."""
    now_ms = int(time.time() * 1000)
    key = f"mmbox:chan:{box}"
    async with r.pipeline(transaction=False) as p:
        p.xadd(key, {**fields, "exp_ms": str(now_ms + ttl_s * 1000)}, minid=f"{now_ms - CHAN_MAX_TTL_S * 1000}-0",
               approximate=True)
        p.xtrim(key, maxlen=MAX_CHAN_ENTRIES, approximate=True)
        p.expire(key, CHAN_MAX_TTL_S + 60)
        res = await p.execute()
    return res[0]


async def chan_rate_ok(r: aioredis.Redis, me: str, n: int) -> bool:
    """At most CHAN_RATE posts per minute per signer, counted as the entries of a stream of its own (the gateway's
    Redis user has no INCR)."""
    now_ms = int(time.time() * 1000)
    key = f"mmbox:chanrate:{me}"
    async with r.pipeline(transaction=False) as p:
        p.xtrim(key, minid=f"{now_ms - 60_000}-0", approximate=False)
        p.xlen(key)
        _, count = await p.execute()
    if count + n > CHAN_RATE:
        return False
    async with r.pipeline(transaction=False) as p:
        for _ in range(n):
            p.xadd(key, {"n": "1"})
        p.expire(key, 120)
        await p.execute()
    return True


def chan_entry(eid: str, f: dict) -> dict:
    def load(k):
        try:
            return json.loads(f[k]) if f.get(k) else None
        except ValueError:
            return None
    stored = int(eid.split("-")[0])
    return {"entry": eid, "from": f.get("from"), "stored_ms": stored,
            "exp_ms": int(f.get("exp_ms") or stored + CHAN_TTL_S * 1000), "env": load("env"), "hint": load("hint")}


# ---------------------------------------------------------------- handlers
async def health(request: web.Request) -> web.Response:
    try:
        r = request.app["redis"]
        await r.ping()
        out = {"redis": "up", "time": time.time(), "features": FEATURES, "chan_ttl_s": CHAN_TTL_S,
               "chan_max_ttl_s": CHAN_MAX_TTL_S, "box_ttl_s": BOX_TTL_S,
               "operators": OPERATORS}  # ids only: a console signs as one of them, or as a box
        if OPERATORS:  # whom questions go to, and whose answers the clients accept: the first operator's key
            card = _loads(await r.get(f"mmbox:card:{OPERATORS[0]}")) or {}
            out["operator"] = {"id": OPERATORS[0], "fingerprint": card.get("fingerprint"),
                               "registered": card_ok(card) if card else False}
        if COLLECTOR and OPERATORS:  # where annotations go: the first operator's key, as its own card says
            card = _loads(await r.get(f"mmbox:card:{OPERATORS[0]}")) or {}
            conf = await col_settings(r)
            out["collector"] = {"id": OPERATORS[0], "fingerprint": card.get("fingerprint"),
                                "registered": card_ok(card) if card else False,
                                "keep_days": round(conf["keep_ms"] / 86_400_000, 3)}
        return ok(out)
    except Exception:
        raise _http(503, "redis unreachable")


async def card_put(request: web.Request) -> web.Response:
    me, data = await signed_json(request, card_field=True)
    card = data.get("card")
    if not isinstance(card, dict) or card.get("id") != me or not card_ok(card):
        raise _http(400, "the card must be the signer's own, self-signed, with a matching fingerprint")
    r = request.app["redis"]
    raw = await r.get(f"mmbox:card:{me}")
    if raw and json.loads(raw).get("sign_pub") != card["sign_pub"]:
        raise _http(409, "this id is already bound to another key")
    await r.set(f"mmbox:card:{me}", json.dumps(card, ensure_ascii=False), ex=IDLE_TTL)
    return ok({"registered": me, "new": not raw})


async def card_get(request: web.Request) -> web.Response:
    _, data = await signed_json(request)
    raw = await request.app["redis"].get(f"mmbox:card:{need(data, 'id', ID_RE)}")
    if not raw:
        raise _http(404, "no such identity")
    return ok({"card": json.loads(raw)})


async def links_put(request: web.Request) -> web.Response:
    """Replace the set of identities allowed to write to the signer's box (its peers)."""
    me, data = await signed_json(request)
    peers = data.get("peers")
    if not isinstance(peers, list) or len(peers) > MAX_LINKS or not all(isinstance(p, str) and ID_RE.fullmatch(p) for p in peers):
        raise _http(400, "`peers`: a list of identity ids")
    peers = [q for q in peers if q != me]  # nobody writes to itself: no self-link, no box filled from itself
    r = request.app["redis"]
    async with r.pipeline(transaction=True) as p:
        p.delete(f"mmbox:links:{me}")
        if peers:
            p.hset(f"mmbox:links:{me}", mapping={q: int(time.time()) for q in set(peers)})
            p.expire(f"mmbox:links:{me}", IDLE_TTL)
        await p.execute()
    return ok({"links": len(set(peers))})


REFUSED_EVERY_S = 600  # a send its client retries at each pass is journaled once per message in this time


async def refused(r: aioredis.Redis, me: str, to: str, env: dict, data: dict, why: str) -> None:
    """A send refused while its client keeps it queued (the recipient does not list the sender yet, or its box is
    full): one `refused` event in the collector's journal, so that the operator's console shows the message held, from
    whichever computer it comes. At most once per message every REFUSED_EVERY_S, whatever the retries."""
    if not COLLECTOR:
        return
    mid = str(env.get("id") or "")
    try:
        if not await r.set(f"mmbox:refused:{me}:{to}:{mid}", "1", nx=True, ex=REFUSED_EVERY_S):
            return
    except Exception:
        return
    await col(r, {"ev": "refused", "from": me, "to": to, "msg": mid, "ts": str(env.get("ts") or ""), "why": why,
                  "note": note_of(data, me)})


async def send(request: web.Request) -> web.Response:
    me, data = await signed_json(request)
    to = need(data, "to", ID_RE)
    if to == me:
        raise _http(400, "a box does not write to itself")
    env = data.get("env")
    if not isinstance(env, dict) or env.get("from") != me or env.get("to") != to:
        raise _http(400, "`env` must be an envelope from the signer to `to`")
    raw = json.dumps(env, ensure_ascii=False, separators=(",", ":"))
    if len(raw) > MAX_ENV:
        raise _http(413, "message too large")
    r = request.app["redis"]
    if me in OPERATORS:  # the console's answer to a box's question: any box the service knows, still open
        if not await r.exists(f"mmbox:card:{to}"):
            raise _http(404, "no such box on this service (removed, or expired)")
        if (_loads(await r.get(f"mmbox:box:{to}")) or {}).get("closed_ms"):
            raise _http(410, "this box closed: write to its machine box")
    elif to not in OPERATORS and not await r.hexists(f"mmbox:links:{to}", me):
        await refused(r, me, to, env, data, "not_listed")
        raise _http(403, "the recipient does not list the sender among its peers (not paired, or its links are not synced)")
    box = f"mmbox:in:{to}"
    if await r.xlen(box) >= MAX_ENTRIES:
        await refused(r, me, to, env, data, "full")
        raise _http(429, f"the recipient's box is full ({MAX_ENTRIES} entries waiting): the sender keeps it queued")
    minid = f"{int(time.time() * 1000) - KEEP_MS}-0"  # what nobody took within the clients' keep time goes
    entry = await r.xadd(box, {"from": me, "env": raw}, minid=minid, approximate=True)
    await r.expire(box, IDLE_TTL)
    if not await r.exists(f"mmbox:watch:{to}"):  # nobody listens there: the other sessions of its computer are told
        m = await r.get(f"mmbox:machine:{to}")
        if m and m != to and ID_RE.fullmatch(m) and await r.hexists(f"mmbox:links:{m}", to) \
                and await r.set(f"mmbox:hinted:{to}", "1", nx=True, ex=HINT_EVERY_S):
            await chan_add(r, m, {"from": "gateway", "hint": json.dumps({"kind": "arrival", "box": to, "entry": entry,
                                                                         "sender": me})}, HINT_TTL_S)
    await stat(r, "m", len(raw))
    await col(r, {"ev": "send", "from": me, "to": to, "entry": entry, "msg": str(env.get("id") or ""),
                  "ts": str(env.get("ts") or ""), "size": str(len(raw)), "note": note_of(data, me)})
    return ok({"entry": entry})


async def list_box(request: web.Request) -> web.Response:
    """What waits in the signer's box after `since` ("0": everything), each entry with who took it, if anyone.
    With block_ms, waits up to that long for something new (the listener's wake-up)."""
    me, data = await signed_json(request)
    box = own_box(me, data)
    since = str(data.get("since") or "0")
    if since not in ("0", "$") and not ENTRY_RE.fullmatch(since):
        raise _http(400, "`since`: an entry id, 0 or $")
    block = max(0, min(int(data.get("block_ms") or 0), MAX_BLOCK_MS))
    count = max(1, min(int(data.get("count") or 500), 1000))
    if block:  # a listener (an older client's long poll): the box counts as listened to
        await present(request.app["redis"], box, block)
    r = request.app["blocking"] if block else request.app["redis"]
    res = await r.xread({f"mmbox:in:{box}": since}, count=count, block=block or None)
    entries = res[0][1] if res else []
    takes = []
    if entries:
        async with request.app["redis"].pipeline(transaction=False) as p:
            for eid, _ in entries:
                p.get(f"mmbox:take:{box}:{eid}")
            takes = await p.execute()
    out, used = [], 0
    for (eid, fields), take in zip(entries, takes):
        raw = fields.get("env", "{}")
        used += len(raw)
        if out and used > LIST_BUDGET:  # large attachments: the rest comes at the next read
            break
        try:
            env = json.loads(raw)
        except ValueError:
            env = None
        out.append({"entry": eid, "from": fields.get("from"), "env": env, "stored_ms": int(eid.split("-")[0]),
                    "taken": json.loads(take) if take else None})
    return ok({"entries": out, "more": len(out) < len(entries), "now_ms": int(time.time() * 1000)})


async def watch(request: web.Request) -> web.Response:
    """The listener's wake-up: waits up to block_ms for an entry after `since_in` in the signer's box or after
    `since_chan` on its channel, and answers their ids (the client then reads them). The box counts as listened to
    meanwhile; `machine` names the machine box of a session box, where arrival hints for it go (kept only when that
    machine box lists the session). `quiet`: a tool's own wait (e.g. for a lock event), which neither counts as a
    listener nor says anything about the box. `leave`: the listener stops (presence dropped at once)."""
    me, data = await signed_json(request)
    box = own_box(me, data)
    since_in, since_chan = entry_of(data, "since_in"), entry_of(data, "since_chan")
    block = int_of(data, "block_ms", 0, 0, MAX_BLOCK_MS)
    r = request.app["redis"]
    if data.get("leave"):  # the listener stops: from now on, arrivals here are hinted to the other sessions
        await r.delete(f"mmbox:watch:{box}")
        return ok({"left": box})
    if not data.get("quiet"):
        if block:
            await present(r, box, block)
        machine = data.get("machine")  # kept only when that machine box lists this box (its own session)
        if isinstance(machine, str) and ID_RE.fullmatch(machine) and machine != box \
                and await r.hexists(f"mmbox:links:{machine}", box):
            await r.set(f"mmbox:machine:{box}", machine, ex=IDLE_TTL)
    rb = request.app["blocking"] if block else r
    res = await rb.xread({f"mmbox:in:{box}": since_in, f"mmbox:chan:{box}": since_chan}, count=100, block=block or None)
    now_ms = int(time.time() * 1000)
    got = {"in": [], "chan": []}
    cursor = {"in": since_in, "chan": since_chan}
    for key, entries in res or []:
        which = "chan" if key == f"mmbox:chan:{box}" else "in"
        for eid, f in entries:
            cursor[which] = eid
            if which == "chan" and chan_entry(eid, f)["exp_ms"] < now_ms:
                continue  # dead already: only moves the cursor
            got[which].append(eid)
    return ok({"in": got["in"], "chan": got["chan"], "cursor_in": cursor["in"], "cursor_chan": cursor["chan"],
               "now_ms": now_ms})


async def chan_post(request: web.Request) -> web.Response:
    """Post on channels: each post is an envelope from the signer to the channel's owner (a machine box, read by
    every session of its computer), accepted when the owner lists the signer among its peers. Kept ttl_s
    (CHAN_TTL_S by default), never consumed: each reader keeps its own cursor."""
    me, data = await signed_json(request)
    posts = data.get("posts")
    if not isinstance(posts, list) or not 1 <= len(posts) <= MAX_CHAN_POSTS:
        raise _http(400, f"`posts`: a list of 1 to {MAX_CHAN_POSTS} {{box, env, ttl_s}}")
    r = request.app["redis"]
    if not await chan_rate_ok(r, me, len(posts)):
        raise _http(429, f"over {CHAN_RATE} posts in a minute: wait a little")
    out = []
    for item in posts:
        box = item.get("box") if isinstance(item, dict) else None
        env = item.get("env") if isinstance(item, dict) else None
        if not isinstance(box, str) or not ID_RE.fullmatch(box):
            out.append({"box": box if isinstance(box, str) else None, "error": "`box`: an identity id", "status": 400})
            continue
        if not isinstance(env, dict) or env.get("from") != me or env.get("to") != box:
            out.append({"box": box, "error": "`env` must be an envelope from the signer to `box`", "status": 400})
            continue
        raw = json.dumps(env, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode()) > MAX_CHAN_ENV:
            out.append({"box": box, "error": f"post over {MAX_CHAN_ENV} bytes", "status": 413})
            continue
        if box == me or not await r.hexists(f"mmbox:links:{box}", me):
            out.append({"box": box, "error": "the channel's owner does not list the sender among its peers (and no "
                        "identity posts to its own channel)", "status": 403})
            continue
        try:
            ttl = max(CHAN_MIN_TTL_S, min(int(item.get("ttl_s") or CHAN_TTL_S), CHAN_MAX_TTL_S))
        except (TypeError, ValueError):
            ttl = CHAN_TTL_S
        out.append({"box": box, "entry": await chan_add(r, box, {"from": me, "env": raw}, ttl), "ttl_s": ttl,
                    "msg": str(env.get("id") or ""), "ts": str(env.get("ts") or "")})
    done_ = [o for o in out if o.get("entry")]
    if done_:
        await stat(r, "p", len(json.dumps(posts[0].get("env") or {})))  # one publication, however many channels
        await col(r, {"ev": "chan", "from": me, "boxes": [o["box"] for o in done_], "entry": done_[0]["entry"],
                      "msg": done_[0]["msg"], "ts": done_[0]["ts"], "ttl": str(done_[0]["ttl_s"]), "note": note_of(data, me)})
    for o in done_:
        o.pop("msg", None), o.pop("ts", None)
    return ok({"posted": out, "now_ms": int(time.time() * 1000)})


async def chan_read(request: web.Request) -> web.Response:
    """The signer's channel after `since` (exclusive; "0": all of it), live entries only. Nothing is consumed:
    `cursor` (the last entry looked at, dead or alive) is where the reader starts next time. `include_expired` (a
    reader that has a cursor: a session whose listener was restarting): dead entries after `since` too, marked
    `expired`, as long as the channel keeps them (CHAN_MAX_TTL_S)."""
    me, data = await signed_json(request)
    box = own_box(me, data)
    since = entry_of(data, "since", ("0",))
    count = int_of(data, "count", 200, 1, 500)
    dead_too = bool(data.get("include_expired")) and since != "0"
    got = await request.app["redis"].xrange(f"mmbox:chan:{box}", "-" if since == "0" else f"({since}", "+", count=count)
    now_ms = int(time.time() * 1000)
    out, cursor = [], since
    for eid, f in got:
        cursor = eid
        e = chan_entry(eid, f)
        if e["exp_ms"] >= now_ms:
            out.append(e)
        elif dead_too:
            out.append({**e, "expired": True})
    return ok({"entries": out, "cursor": cursor, "more": len(got) == count, "now_ms": now_ms})


async def take(request: web.Request) -> web.Response:
    """Take one entry: one taker only (SET NX), then its envelope. The taker deletes it with /v1/done."""
    me, data = await signed_json(request)
    box = own_box(me, data)
    entry = need(data, "entry", ENTRY_RE)
    holder = {"holder": str(data.get("holder", ""))[:200], "purpose": str(data.get("purpose", ""))[:40],
              "at_ms": int(time.time() * 1000)}
    r = request.app["redis"]
    key = f"mmbox:take:{box}:{entry}"
    if not await r.set(key, json.dumps(holder), nx=True, ex=RECEIPT_TTL):
        cur = await r.get(key)
        return ok({"won": False, "taken": json.loads(cur) if cur else None, "now_ms": holder["at_ms"]})
    got = await r.xrange(f"mmbox:in:{box}", entry, entry)
    if not got:
        await r.delete(key)
        return ok({"won": False, "gone": True})
    fields = got[0][1]
    await col(r, {"ev": "take", "box": box, "entry": entry, "purpose": holder["purpose"]})
    return ok({"won": True, "from": fields.get("from"), "env": json.loads(fields.get("env", "{}")), "now_ms": holder["at_ms"]})


async def recover(request: web.Request) -> web.Response:
    """Recover an entry whose taker died: one recoverer only. Never handed over again: the client archives it."""
    me, data = await signed_json(request)
    box = own_box(me, data)
    entry = need(data, "entry", ENTRY_RE)
    won = await request.app["redis"].set(f"mmbox:recover:{box}:{entry}", str(data.get("holder", ""))[:200], nx=True, ex=600)
    if won:
        await col(request.app["redis"], {"ev": "recover", "box": box, "entry": entry})
    return ok({"won": bool(won)})


async def release(request: web.Request) -> web.Response:
    """Give back an entry taken but not consumed (a relay that could not verify it): untaken again."""
    me, data = await signed_json(request)
    box = own_box(me, data)
    await request.app["redis"].delete(f"mmbox:take:{box}:{need(data, 'entry', ENTRY_RE)}")
    return ok()


async def done(request: web.Request) -> web.Response:
    me, data = await signed_json(request)
    box = own_box(me, data)
    entries = data.get("entries")
    if not isinstance(entries, list) or not all(isinstance(e, str) and ENTRY_RE.fullmatch(e) for e in entries) or len(entries) > 1000:
        raise _http(400, "`entries`: a list of entry ids")
    if entries:
        async with request.app["redis"].pipeline(transaction=True) as p:
            p.xdel(f"mmbox:in:{box}", *entries)
            for e in entries:
                p.delete(f"mmbox:take:{box}:{e}", f"mmbox:recover:{box}:{e}")
            await p.execute()
        handled = data.get("handled") if isinstance(data.get("handled"), dict) else {}
        handled = {e: str(v)[:20] for e, v in handled.items() if e in entries and isinstance(v, str)}
        await col(request.app["redis"], {"ev": "done", "box": box, "entries": entries, "handled": handled or None})
    return ok({"done": len(entries)})


async def status(request: web.Request) -> web.Response:
    """For a sender: which of its entries in `to`'s box still wait (taken or not) and which are consumed."""
    me, data = await signed_json(request)
    to = need(data, "to", ID_RE)
    entries = data.get("entries")
    if not isinstance(entries, list) or not all(isinstance(e, str) and ENTRY_RE.fullmatch(e) for e in entries) or len(entries) > 1000:
        raise _http(400, "`entries`: a list of entry ids")
    r = request.app["redis"]
    out = {}
    for e in entries:
        got = await r.xrange(f"mmbox:in:{to}", e, e)
        if not got:
            out[e] = "consumed"
        elif got[0][1].get("from") != me:
            out[e] = "unknown"
        else:
            out[e] = "taken" if await r.exists(f"mmbox:take:{to}:{e}") else "waiting"
    return ok({"status": out})


async def pair_invite(request: web.Request) -> web.Response:
    me, data = await signed_json(request)
    inv = need(data, "inv", INV_RE)
    invite = data.get("invite")
    if not isinstance(invite, dict) or invite.get("from") != me:
        raise _http(400, "`invite` must come from the signer")
    r = request.app["redis"]
    if not await r.hsetnx(f"mmbox:pair:{inv}", "invite", json.dumps(invite)):
        raise _http(409, "this invitation code is already in use: make another")
    await r.expire(f"mmbox:pair:{inv}", PAIR_TTL)
    return ok({"inv": inv, "ttl": PAIR_TTL})


async def pair_get(request: web.Request) -> web.Response:
    _, data = await signed_json(request)
    got = await request.app["redis"].hgetall(f"mmbox:pair:{need(data, 'inv', INV_RE)}")
    if not got:
        raise _http(404, "no such invitation (expired, used, or a typo)")
    return ok({k: json.loads(v) for k, v in got.items()})


async def pair_join(request: web.Request) -> web.Response:
    me, data = await signed_json(request)
    inv = need(data, "inv", INV_RE)
    join = data.get("join")
    if not isinstance(join, dict) or join.get("from") != me:
        raise _http(400, "`join` must come from the signer")
    r = request.app["redis"]
    if not await r.exists(f"mmbox:pair:{inv}"):
        raise _http(404, "no such invitation (expired, used, or a typo)")
    if not await r.hsetnx(f"mmbox:pair:{inv}", "join", json.dumps(join)):
        raise _http(409, "someone already answered this invitation: it is burned, ask for a new code")
    return ok({"joined": inv})


async def pair_end(request: web.Request) -> web.Response:
    me, data = await signed_json(request)
    inv = need(data, "inv", INV_RE)
    r = request.app["redis"]
    raw = await r.hget(f"mmbox:pair:{inv}", "invite")
    if not raw or json.loads(raw).get("from") != me:
        raise _http(403, "only the inviting identity ends its invitation")
    field = "done" if isinstance(data.get("done"), dict) else "fail" if isinstance(data.get("fail"), dict) else None
    if not field:
        raise _http(400, "`done` or `fail` expected")
    await r.hset(f"mmbox:pair:{inv}", field, json.dumps(data[field]))
    return ok({field: inv})


async def alert(request: web.Request) -> web.Response:
    me, data = await signed_json(request)
    text = str(data.get("text", ""))[:20_000]
    if not text:
        raise _http(400, "`text` expected")
    minid = f"{int(time.time() * 1000) - ALERT_KEEP_MS}-0"
    r = request.app["redis"]
    await r.xadd(f"mmbox:alerts:{me}", {"text": text}, minid=minid, approximate=True)
    await r.xtrim(f"mmbox:alerts:{me}", maxlen=1000, approximate=True)
    await r.expire(f"mmbox:alerts:{me}", IDLE_TTL)
    return ok()


async def alerts(request: web.Request) -> web.Response:
    """The signer's own alerts, newest first (kept ALERT_KEEP_MS)."""
    me, data = await signed_json(request)
    box = own_box(me, data)
    count = max(1, min(int(data.get("count") or 50), 500))
    got = await request.app["redis"].xrevrange(f"mmbox:alerts:{box}", "+", "-", count=count)
    return ok({"alerts": [{"at_ms": int(eid.split("-")[0]), "text": f.get("text", "")} for eid, f in got]})


async def close(request: web.Request) -> web.Response:
    """A closed session removes its box: card, links, entries and alerts. Its id may never be used again by anyone
    else in practice (random, 60 bits), and its peers verify messages against the key they keep, not this card."""
    me, data = await signed_json(request)
    own_box(me, data)
    r = request.app["redis"]
    n = await r.delete(*(f"mmbox:{k}:{me}" for k in BOX_KEYS))
    await r.hdel("mmbox:seen", me)
    await col(r, {"ev": "removed", "box": me})
    return ok({"removed": n})


def _s(v, n: int) -> str | None:
    return str(v)[:n] if isinstance(v, (str, int, float)) and str(v) else None


ACTIVITY_MAX = 600  # two or three sentences


def title_of(v) -> str | None:
    """A box's title: three words at most."""
    if not isinstance(v, str):
        return None
    words = v.split()[:3]
    return " ".join(words)[:60] or None


BOX_ROLES = ("machine", "session", "project", "butler")  # a project box and the butler are children of a machine box


def inventory_of(v) -> dict | None:
    """What a butler's box says its computer runs (its agents, their versions and models), cut to size."""
    if not isinstance(v, dict):
        return None
    agents = []
    for a in (v.get("agents") if isinstance(v.get("agents"), list) else [])[:12]:
        if not isinstance(a, dict) or not _s(a.get("agent"), 40):
            continue
        agents.append({"agent": _s(a.get("agent"), 40), "provider": _s(a.get("provider"), 40),
                       "version": _s(a.get("version"), 80), "available": bool(a.get("available")),
                       "logged_in": a.get("logged_in") if isinstance(a.get("logged_in"), bool) else None,
                       "can_start": bool(a.get("can_start")), "models_source": _s(a.get("models_source"), 80),
                       "models": [m[:80] for m in (a.get("models") if isinstance(a.get("models"), list) else [])[:40]
                                  if isinstance(m, str) and m]})
    at = v.get("at")
    return {"at": float(at) if isinstance(at, (int, float)) else None, "tmux": bool(v.get("tmux")), "agents": agents}


def box_info(info) -> dict:
    """What a box may declare of itself, cut to size: nothing here is trusted beyond being said by the box's key."""
    if not isinstance(info, dict):
        raise _http(400, "`info`: an object")
    host = info.get("host") if isinstance(info.get("host"), dict) else {}
    client = info.get("client") if isinstance(info.get("client"), dict) else {}
    out = {"role": info.get("role") if info.get("role") in BOX_ROLES else "session",
           "machine": info.get("machine") if isinstance(info.get("machine"), str) and ID_RE.fullmatch(info["machine"]) else None,
           "project": _s(info.get("project"), 80), "work": _s(info.get("work"), 80), "label": _s(info.get("label"), 120),
           "agent": _s(info.get("agent"), 40), "user": _s(info.get("user"), 80),
           "title": title_of(info.get("title")), "activity": _s(info.get("activity"), ACTIVITY_MAX),
           "provider": _s(info.get("provider"), 40), "model": _s(info.get("model"), 80),
           "host": {k: _s(host.get(k), 120) for k in ("hostname", "os", "machine_hash") if _s(host.get(k), 120)},
           "client": {"v": _s(client.get("v"), 20), "caps": [str(c)[:20] for c in (client.get("caps") or [])[:20]
                                                             if isinstance(c, str)]},
           "inventory": inventory_of(info.get("inventory")) if info.get("role") == "butler" else None}
    return {k: v for k, v in out.items() if v not in (None, {}, "")}


async def box_open(request: web.Request) -> web.Response:
    """A box says what it is (role, its machine box, its project) and that it is open: in the table of active boxes
    at once, without any message to anyone. Called again by a box that reopens."""
    me, data = await signed_json(request)
    if me in OPERATORS:
        raise _http(400, "an operator is no box")
    info = box_info(data.get("info"))
    r = request.app["redis"]
    if info.get("role") != "machine" and info.get("machine") and info["machine"] != me:
        info["machine_ok"] = bool(await r.hexists(f"mmbox:links:{info['machine']}", me))
    now_ms = int(time.time() * 1000)
    old = _loads(await r.get(f"mmbox:box:{me}")) or {}
    still_open = old and not old.get("closed_ms")
    rec = {**info, "id": me, "opened_ms": old.get("opened_ms") if still_open else now_ms}
    rec["opened_ms"] = rec["opened_ms"] or now_ms
    if still_open and old.get("title"):  # named once at its opening, until it closes
        rec["title"] = old["title"]
    if rec.get("activity") and rec["activity"] != old.get("activity"):
        rec["activity_ms"] = now_ms
    elif old.get("activity_ms") and rec.get("activity") == old.get("activity"):
        rec["activity_ms"] = old["activity_ms"]
    async with r.pipeline(transaction=False) as p:
        p.set(f"mmbox:box:{me}", json.dumps(rec, ensure_ascii=False), ex=IDLE_TTL)
        p.hset("mmbox:seen", me, now_ms)
        await p.execute()
    if old.get("closed_ms") or not old or {k: old.get(k) for k in info if k != "inventory"} != \
            {k: v for k, v in info.items() if k != "inventory"}:  # a butler's new inventory is no opening
        await col(r, {"ev": "open", "box": me, "info": rec})
    return ok({"box": rec, "ttl_s": BOX_TTL_S})


async def box_activity(request: web.Request) -> web.Response:
    """What the box's session is doing now, in two or three sentences: shown beside its box and its messages."""
    me, data = await signed_json(request)
    own_box(me, data)
    act = _s(data.get("activity"), ACTIVITY_MAX)
    if not act:
        raise _http(400, "`activity`: two or three sentences")
    r = request.app["redis"]
    rec = _loads(await r.get(f"mmbox:box:{me}"))
    if not rec or rec.get("closed_ms"):
        raise _http(409, "open the box first (/v1/box/open)")
    if rec.get("activity") != act:
        rec.update(activity=act, activity_ms=int(time.time() * 1000))
        await r.set(f"mmbox:box:{me}", json.dumps(rec, ensure_ascii=False), ex=IDLE_TTL)
        await col(r, {"ev": "activity", "box": me, "info": {"activity": act, "title": rec.get("title")}})
    return ok({"activity": act})


async def box_close(request: web.Request) -> web.Response:
    """A box closed (a session gone idle): out of the table of active boxes until it opens again."""
    me, data = await signed_json(request)
    own_box(me, data)
    r = request.app["redis"]
    rec = _loads(await r.get(f"mmbox:box:{me}")) or {"id": me}
    rec["closed_ms"] = int(time.time() * 1000)
    if isinstance(data.get("reason"), str):
        rec["closed_reason"] = data["reason"][:200]
    async with r.pipeline(transaction=False) as p:
        p.set(f"mmbox:box:{me}", json.dumps(rec, ensure_ascii=False), ex=IDLE_TTL)
        p.hdel("mmbox:seen", me)
        await p.execute()
    await col(r, {"ev": "close", "box": me, "reason": rec.get("closed_reason")})
    return ok({"closed": me})


async def boxes(request: web.Request) -> web.Response:
    """The table of active boxes: what each declared, its card's label, when it was last heard of, whether a listener
    waits on it now and how many entries wait in it. An operator sees every box; any other identity sees itself, the
    identities it lists as its peers, and the sessions whose machine box is one of those (verified by that machine)."""
    me, _ = await signed_json(request)
    r = request.app["redis"]
    seen = await r.hgetall("mmbox:seen")
    if me not in OPERATORS:
        allowed = {me} | set(await r.hgetall(f"mmbox:links:{me}"))
    else:
        allowed = None
    ids = sorted(seen)
    async with r.pipeline(transaction=False) as p:
        for i in ids:
            p.get(f"mmbox:box:{i}")
            p.get(f"mmbox:card:{i}")
            p.exists(f"mmbox:watch:{i}")
            p.xlen(f"mmbox:in:{i}")
        res = await p.execute()
    now_ms = int(time.time() * 1000)
    out = []
    for n, i in enumerate(ids):
        b, c, w, q = res[4 * n:4 * n + 4]
        b, c = _loads(b) or {}, _loads(c) or {}
        if allowed is not None and i not in allowed and not (b.get("machine") in allowed and b.get("machine_ok")):
            continue
        out.append({**{k: v for k, v in b.items() if k != "closed_ms"}, "id": i, "declared": bool(b),
                    "label": b.get("label") or c.get("label"), "agent": b.get("agent") or c.get("agent"),
                    "user": b.get("user") or c.get("user"), "fingerprint": c.get("fingerprint"),
                    "last_ms": int(seen[i]), "idle_s": max(0, (now_ms - int(seen[i])) // 1000),
                    "listening": bool(w), "waiting": int(q or 0)})
    return ok({"boxes": out, "operator": allowed is None, "ttl_s": BOX_TTL_S, "now_ms": now_ms})


async def col_read(request: web.Request) -> web.Response:
    """The collector's journal, for an operator: after `since` (exclusive), or the last `tail` events."""
    me, data = await signed_json(request)
    if me not in OPERATORS:
        raise _http(403, "the collector is read by the service's operators only (MMBOX_OPERATORS)")
    if not COLLECTOR:
        raise _http(404, "no collector on this service (MMBOX_COLLECTOR)")
    r = request.app["redis"]
    count = int_of(data, "count", 2000, 1, 5000)
    if data.get("tail"):
        got = list(reversed(await r.xrevrange("mmbox:col", "+", "-", count=int_of(data, "tail", 2000, 1, 20000))))
    else:
        since = entry_of(data, "since", ("0",))
        got = await r.xrange("mmbox:col", "-" if since == "0" else f"({since}", "+", count=count)
    evs = []
    for eid, f in got:
        e = {"entry": eid}
        for k, v in f.items():
            e[k] = _loads(v) if k in ("note", "boxes", "entries", "handled", "info") else v
        evs.append(e)
    return ok({"events": evs, "cursor": got[-1][0] if got else data.get("since") or "0", "more": len(got) == count,
               "now_ms": int(time.time() * 1000)})


async def col_config(request: web.Request) -> web.Response:
    """The collector's retention, for an operator: read it with its figures, change keep_days, hold every entry 48 h
    more (`hold_hours`), or purge it all (`purge`). `stats` counts what goes within `soon_s` (e.g. 30 % of keep)."""
    me, data = await signed_json(request)
    if me not in OPERATORS:
        raise _http(403, "the collector is set by the service's operators only (MMBOX_OPERATORS)")
    if not COLLECTOR:
        raise _http(404, "no collector on this service (MMBOX_COLLECTOR)")
    r = request.app["redis"]
    now_ms = int(time.time() * 1000)
    changed = {}
    if data.get("keep_days") is not None:
        try:
            keep = float(data["keep_days"])
        except (TypeError, ValueError):
            raise _http(400, "`keep_days`: a number of days")
        if not 0.04 <= keep <= 366:
            raise _http(400, "`keep_days`: from one hour (0.04) to 366 days")
        changed["keep_ms"] = str(int(keep * 86_400_000))
    if data.get("hold_hours") is not None:
        hours = int_of(data, "hold_hours", 48, 1, 24 * 30)
        first = await r.xrange("mmbox:col", "-", "+", count=1)
        changed.update(hold_until_ms=str(now_ms + hours * 3_600_000), hold_minid=first[0][0] if first else f"{now_ms}-0")
    if changed:
        await r.hset("mmbox:colconf", mapping=changed)
        await col(r, {"ev": "colconf", "by": me, "info": changed})
    if data.get("purge"):
        n = await r.xlen("mmbox:col")
        await r.delete("mmbox:col")
        await r.hdel("mmbox:colconf", "hold_until_ms", "hold_minid")
        changed["purged"] = str(n)
    conf = await col_settings(r, fresh=True)
    out = {"keep_ms": conf["keep_ms"], "keep_days": round(conf["keep_ms"] / 86_400_000, 3),
           "hold_until_ms": conf["hold_until_ms"] if conf["hold_until_ms"] > now_ms else None, "changed": changed,
           "count": await r.xlen("mmbox:col"), "now_ms": now_ms}
    first = await r.xrange("mmbox:col", "-", "+", count=1)
    out["oldest_ms"] = int(first[0][0].split("-")[0]) if first else None
    if data.get("stats"):
        soon = int_of(data, "soon_s", int(conf["keep_ms"] / 1000 * 0.3), 0, 400 * 86400)
        cut = int(col_cutoff(conf, now_ms + soon * 1000).split("-")[0])  # what the cutoff passes within soon_s
        msgs = total = 0
        for eid, f in await r.xrange("mmbox:col", "-", f"({cut}-0", count=COL_MAX):
            total += 1
            msgs += f.get("ev") in ("send", "chan")
        out.update(soon_s=soon, expiring=total, expiring_messages=msgs)
    return ok(out)


async def sweeper(app: web.Application) -> None:
    """Every BOX_SWEEP_S: the boxes idle for BOX_TTL_S leave the table of active boxes; the operators' cards are kept
    (their keys are what clients seal annotations for); the journal is held to COL_MAX events."""
    import asyncio
    r = app["redis"]
    while True:
        try:
            now_ms = int(time.time() * 1000)
            seen = await r.hgetall("mmbox:seen")
            stale = [i for i, t in seen.items() if not str(t).isdigit() or now_ms - int(t) > BOX_TTL_S * 1000]
            if stale:
                await r.hdel("mmbox:seen", *stale)
                for i in stale:
                    await col(r, {"ev": "expire", "box": i})
            for o in OPERATORS:  # an operator's card and inbox stay while its console is away
                await r.expire(f"mmbox:card:{o}", IDLE_TTL)
                await r.expire(f"mmbox:in:{o}", IDLE_TTL)
            if COLLECTOR:
                await r.xtrim("mmbox:col", maxlen=COL_MAX, approximate=True)
                await r.xtrim("mmbox:col", minid=col_cutoff(await col_settings(r), now_ms), approximate=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(BOX_SWEEP_S)


async def lock(request: web.Request) -> web.Response:
    """A lock of the signer's memories user (from its card), held `ttl` seconds: won by one caller only."""
    me, data = await signed_json(request)
    name = need(data, "name", LOCK_RE)
    ttl = int(data.get("ttl") or 0)
    if not 0 < ttl <= MAX_LOCK_TTL:
        raise _http(400, f"`ttl`: 1 to {MAX_LOCK_TTL} seconds")
    user = re.sub(r"[^A-Za-z0-9._@-]", "_", str(request["card"].get("user", "")))[:80]
    if not user:
        raise _http(400, "the signer's card names no user")
    r = request.app["redis"]
    key = f"mmbox:lock:{user}:{name}"
    won = await r.set(key, json.dumps({"by": me, "at": int(time.time())}), nx=True, ex=ttl)
    return ok({"won": bool(won), "ttl": await r.ttl(key), "held_by": None if won else json.loads(await r.get(key) or "{}")})


# ---------------------------------------------------------------- app
def redis_client(max_connections: int) -> aioredis.Redis:
    password = None
    if os.path.exists(REDIS_PASSWORD_FILE):
        with open(REDIS_PASSWORD_FILE, encoding="utf-8") as f:
            password = f.read().strip()
    else:
        password = os.environ.get("MMBOX_REDIS_PASSWORD")
    return aioredis.from_url(REDIS_URL, username=REDIS_USER, password=password, decode_responses=True,
                             max_connections=max_connections, health_check_interval=30)


async def on_startup(app: web.Application) -> None:
    app["redis"] = redis_client(50)
    app["blocking"] = redis_client(500)  # long polls hold a connection each
    import asyncio
    app["sweeper"] = asyncio.get_running_loop().create_task(sweeper(app))


async def on_cleanup(app: web.Application) -> None:
    app["sweeper"].cancel()
    await app["redis"].aclose()
    await app["blocking"].aclose()


def make_app() -> web.Application:
    app = web.Application(client_max_size=MAX_BODY)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/v1/health", health)
    for path, fn in (("/v1/card", card_put), ("/v1/card/get", card_get), ("/v1/links", links_put), ("/v1/send", send),
                     ("/v1/list", list_box), ("/v1/take", take), ("/v1/recover", recover), ("/v1/release", release), ("/v1/done", done),
                     ("/v1/status", status), ("/v1/pair/invite", pair_invite), ("/v1/pair/get", pair_get),
                     ("/v1/pair/join", pair_join), ("/v1/pair/end", pair_end), ("/v1/alert", alert),
                     ("/v1/close", close), ("/v1/lock", lock), ("/v1/alerts", alerts), ("/v1/watch", watch),
                     ("/v1/chan/post", chan_post), ("/v1/chan/read", chan_read), ("/v1/box/open", box_open),
                     ("/v1/box/close", box_close), ("/v1/boxes", boxes), ("/v1/col/read", col_read),
                     ("/v1/box/activity", box_activity), ("/v1/col/config", col_config), ("/v1/stats", stats),
                     ("/v1/usage", usage)):
        app.router.add_post(path, fn)
    return app


def health_probe(port: str) -> int:
    """`--health`: the container's healthcheck (0 when /v1/health answers 200, so Redis is up too). It lives here so
    that the unit's HealthCmd needs no quoting: Quadlet's quote handling broke an inline `python -c "..."`."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=4) as r:
            return 0 if r.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    import sys
    host, port = LISTEN.rsplit(":", 1)
    if sys.argv[1:] == ["--health"]:
        sys.exit(health_probe(port))
    web.run_app(make_app(), host=host, port=int(port), access_log=None)
