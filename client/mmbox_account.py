"""mmbox accounts (client/ACCOUNTS.md): a root key, device keys, and the device chain that says which devices belong.

No network here: making and verifying links, and the chain a computer pinned. Used by the client (an `op` from a
device of the pinned account), the daemon (its cache of the chain) and the console (making the account, approving,
revoking, the daily head).

A link: {"v": 1, "seq", "prev" (hash of the previous link, "" for the first), "ts" (unix seconds), "op": "add" |
"revoke" | "head", "device": {"id", "sign_pub", "box_pub", "label", "host"} (add, revoke: the device concerned), "by"
(the signing device's id, or "root"), "root": {"sign_pub"} (first link only), "sig"}. The first link adds the first
device and is signed by the root key; any later one by the root key or by a device in the chain and not revoked.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time

HEAD_MAX_AGE_S = 7 * 86400  # a chain whose newest link is older: refused (a service may be hiding a revocation)


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def b64d(s: str) -> bytes:
    return base64.b64decode(s)


def canon(o) -> bytes:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def link_hash(link: dict) -> str:
    return hashlib.sha256(canon(link)).hexdigest()


def account_id(root_sign_pub: bytes) -> str:
    """The account's id and fingerprint: from its root's public key."""
    alphabet = "0123456789abcdefghjkmnpqrstvwxyz"
    h = hashlib.sha256(root_sign_pub).digest()
    n = int.from_bytes(h[:8], "big")
    out = ""
    for _ in range(12):
        out += alphabet[n & 31]
        n >>= 5
    return "a" + out


def root_fingerprint(root_sign_pub: bytes) -> str:
    h = hashlib.sha256(b"mmbox-account-root:" + root_sign_pub).hexdigest().upper()
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def _pub(sign_pub_b64: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    return Ed25519PublicKey.from_public_bytes(b64d(sign_pub_b64))


def make_link(chain: list[dict], op: str, device: dict | None, by: str, sign_key, root_pub: bytes | None = None,
              ts: float | None = None) -> dict:
    """The next link of `chain`, signed by `sign_key` (the root's, or the device `by`'s private Ed25519 key)."""
    prev = chain[-1] if chain else None
    link = {"v": 1, "seq": (prev["seq"] + 1) if prev else 0, "prev": link_hash(prev) if prev else "",
            "ts": int(ts if ts is not None else time.time()), "op": op, "by": by}
    if device is not None:
        link["device"] = {k: device[k] for k in ("id", "sign_pub", "box_pub", "label", "host", "user") if k in device}
    if not chain:
        if root_pub is None:
            raise ValueError("the first link names the root key")
        link["root"] = {"sign_pub": b64e(root_pub)}
    link["sig"] = b64e(sign_key.sign(canon(link)))
    return link


class ChainError(ValueError):
    pass


def verify(chain: list[dict], pinned_fingerprint: str | None = None, now: float | None = None,
           max_age: float | None = HEAD_MAX_AGE_S) -> dict:
    """The state a chain says, every link checked: {account, fingerprint, root_pub, devices: {id: device}, revoked:
    {id}, head_ts, length}. Raises ChainError when a link does not hold, when the root is not the pinned one, or when
    the newest link is older than `max_age`."""
    if not chain:
        raise ChainError("an empty chain")
    first = chain[0]
    try:
        root_pub = b64d(first["root"]["sign_pub"])
    except (KeyError, TypeError, ValueError):
        raise ChainError("the first link names no root key") from None
    fp = root_fingerprint(root_pub)
    if pinned_fingerprint and fp != pinned_fingerprint:
        raise ChainError(f"the chain's root is {fp}, not the pinned {pinned_fingerprint}")
    devices, revoked, prev = {}, set(), None
    for i, link in enumerate(chain):
        body = {k: v for k, v in link.items() if k != "sig"}
        if link.get("v") != 1 or link.get("seq") != i:
            raise ChainError(f"link {i}: wrong sequence")
        if link.get("prev") != (link_hash(prev) if prev else ""):
            raise ChainError(f"link {i}: does not follow link {i - 1}")
        if i > 0 and "root" in link:
            raise ChainError(f"link {i}: only the first link names the root")
        by = link.get("by")
        if i == 0 and by != "root":
            raise ChainError("the first link is signed by the root key")
        if by == "root":
            signer = root_pub
        elif by in devices and by not in revoked:
            signer = b64d(devices[by]["sign_pub"])
        else:
            raise ChainError(f"link {i}: signed by {by}, no device of the chain (or revoked)")
        try:
            from cryptography.exceptions import InvalidSignature
            _pub(b64e(signer)).verify(b64d(link["sig"]), canon(body))
        except (InvalidSignature, KeyError, ValueError):
            raise ChainError(f"link {i}: its signature does not verify") from None
        op, dev = link.get("op"), link.get("device") or {}
        if op == "add":
            if not dev.get("id") or not dev.get("sign_pub") or not dev.get("box_pub"):
                raise ChainError(f"link {i}: an add names a device and its keys")
            if dev["id"] in revoked:
                raise ChainError(f"link {i}: {dev['id']} was revoked: a revoked device never comes back")
            devices[dev["id"]] = dev
        elif op == "revoke":
            if dev.get("id") not in devices:
                raise ChainError(f"link {i}: revokes an unknown device")
            revoked.add(dev["id"])
        elif op != "head":
            raise ChainError(f"link {i}: unknown op {op}")
        prev = link
    head_ts = chain[-1]["ts"]
    if max_age is not None and (now if now is not None else time.time()) - head_ts > max_age:
        raise ChainError(f"the chain's newest link is {((now or time.time()) - head_ts) / 86400:.1f} days old: refused "
                         f"(a revocation may be withheld; a device of the account signs a head every day)")
    return {"account": account_id(root_pub), "fingerprint": fp, "root_pub": b64e(root_pub),
            "devices": {k: v for k, v in devices.items() if k not in revoked}, "revoked": revoked,
            "head_ts": head_ts, "length": len(chain)}


def extends(old: list[dict], new: list[dict]) -> bool:
    """Whether `new` keeps every link of `old` (a cache only ever moves forward: no fork, no going back)."""
    return len(new) >= len(old) and all(link_hash(a) == link_hash(b) for a, b in zip(old, new))


def newer(cached: list[dict] | None, offered: list[dict], pinned_fingerprint: str) -> list[dict] | None:
    """The chain to keep when `offered` arrives (with an op, or from the service): it when valid and extending the
    cached one; None when it must be refused (forks or goes back)."""
    verify(offered, pinned_fingerprint, max_age=None)
    if cached and not extends(cached, offered):
        return None if not extends(offered, cached) else cached
    return offered


def selftest() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    root = Ed25519PrivateKey.generate()
    rpub = root.public_key().public_bytes(**raw)
    keys = {n: Ed25519PrivateKey.generate() for n in ("d1", "d2")}
    dev = {n: {"id": n, "sign_pub": b64e(k.public_key().public_bytes(**raw)), "box_pub": b64e(b"x" * 32), "label": n}
           for n, k in keys.items()}
    c = [make_link([], "add", dev["d1"], "root", root, rpub)]
    c.append(make_link(c, "add", dev["d2"], "d1", keys["d1"]))
    st = verify(c, root_fingerprint(rpub))
    assert set(st["devices"]) == {"d1", "d2"}
    old = list(c)
    c.append(make_link(c, "revoke", dev["d2"], "d1", keys["d1"]))
    assert set(verify(c)["devices"]) == {"d1"}
    assert newer(c, old, root_fingerprint(rpub)) == c  # going back: the cache keeps the revocation
    forged = c[:-1] + [make_link(c[:-1], "add", {**dev["d2"], "id": "d3"}, "d2", keys["d2"])]
    assert newer(c, forged, root_fingerprint(rpub)) is None  # a fork by the revoked device: refused
    bad = list(c) + [make_link(c, "add", {**dev["d2"], "id": "d4"}, "d2", keys["d2"])]
    try:
        verify(bad)
        raise AssertionError("a revoked device signed a link")
    except ChainError:
        pass
    try:
        verify(c, "0000-0000-0000-0000")
        raise AssertionError("another root accepted")
    except ChainError:
        pass
    try:
        verify(c, now=time.time() + 8 * 86400)
        raise AssertionError("a stale chain accepted")
    except ChainError:
        pass
    return "account chain ok: add, approve, revoke, no going back, no fork, root pinned, stale refused"


if __name__ == "__main__":
    print(selftest())
